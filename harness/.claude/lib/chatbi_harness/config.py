"""Safe loading for shared ChatBI Harness configuration."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .gates import GateDecision, GateError


MAX_CONFIG_BYTES = 256 * 1024
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "chatbi-harness.schema.json"
_ABSOLUTE_PATH = re.compile(
    r"(?<![:/A-Za-z0-9_.-])/(?!/)[^\s,;)\]}]+"
    r"|\b[A-Za-z]:[\\/][^\s,;)\]}]+"
    r"|\\\\[^\\\s]+\\[^\s,;)\]}]+"
    r"|(?<!:)//[^/\s]+/[^\s,;)\]}]+"
    r"|\bfile:/+[^\s,;)\]}]+"
)
_SECRET_VALUE = re.compile(
    r"(?:\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+|\b(?:sk|pk)-[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)
_SECRET_ARG = re.compile(
    r"^--?(?:api[-_]?key|token|password|secret)(?:[-_]file)?(?:=|$)",
    re.IGNORECASE,
)
_REQUIRED_PROTECTED_ACTIONS = frozenset(
    {
        "approve_metric",
        "change_access_policy",
        "production_publish",
        "destructive_migration",
    }
)


class _DuplicateKey(ValueError):
    pass


class _SchemaViolation(ValueError):
    pass


class _NonFiniteNumber(ValueError):
    pass


def _reject_non_finite_number(value: str) -> None:
    raise _NonFiniteNumber(value)


def _config_gate_error(
    *,
    rule_ids: tuple[str, ...],
    evidence_ref: str,
    reason: str,
    recovery: str,
) -> GateError:
    return GateError(
        GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=(evidence_ref,),
            reason=reason,
            recovery=recovery,
        )
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _load_json(path: Path, source: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise _config_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref=f"config:{source}",
            reason="Configuration file is unavailable",
            recovery="Provide a readable configuration file",
        ) from error
    if len(raw) > MAX_CONFIG_BYTES:
        raise _config_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref=f"config:{source}",
            reason="Configuration exceeds the 256 KiB size limit",
            recovery="Use a smaller configuration file",
        )
    try:
        loaded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_number,
        )
    except _DuplicateKey as error:
        raise _config_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref=f"config:{source}",
            reason="Configuration contains a duplicate JSON key",
            recovery="Use unique JSON object keys",
        ) from error
    except UnicodeDecodeError as error:
        raise _config_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref=f"config:{source}",
            reason="Configuration is not valid UTF-8",
            recovery="Encode the configuration as UTF-8 JSON",
        ) from error
    except json.JSONDecodeError as error:
        raise _config_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref=f"config:{source}",
            reason="Configuration is malformed JSON",
            recovery="Correct the JSON syntax and retry",
        ) from error
    except _NonFiniteNumber as error:
        raise _config_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref=f"config:{source}",
            reason="Configuration contains a non-finite JSON number",
            recovery="Use a finite JSON number or null",
        ) from error
    if not isinstance(loaded, dict):
        raise _config_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref=f"config:{source}",
            reason="Configuration top level must be a JSON object",
            recovery="Wrap the declared configuration fields in one JSON object",
        )
    return loaded


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _contains_matching_string(
    value: Any,
    patterns: tuple[re.Pattern[str], ...],
) -> bool:
    if isinstance(value, dict):
        return any(_contains_matching_string(item, patterns) for item in value.values())
    if isinstance(value, list):
        return any(_contains_matching_string(item, patterns) for item in value)
    return isinstance(value, str) and any(pattern.search(value) for pattern in patterns)


def _contains_secret_argv(local_data: dict[str, Any]) -> bool:
    cli_adapters = local_data.get("cli_adapters")
    if not isinstance(cli_adapters, dict):
        return False
    for adapter in cli_adapters.values():
        if not isinstance(adapter, dict) or not isinstance(adapter.get("argv"), list):
            continue
        if any(
            isinstance(argument, str) and _SECRET_ARG.match(argument)
            for argument in adapter["argv"]
        ):
            return True
    return False


def _matches_type(value: Any, expected: str | list[str]) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _validate_schema(value: Any, schema: dict[str, Any], location: str = "$") -> None:
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        raise _SchemaViolation(f"{location} must have type {expected_type}")
    if "enum" in schema and value not in schema["enum"]:
        raise _SchemaViolation(f"{location} must be one of {schema['enum']}")
    if isinstance(value, float) and not math.isfinite(value):
        raise _SchemaViolation(f"{location} must be a finite number")
    if (
        "minimum" in schema
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value < schema["minimum"]
    ):
        raise _SchemaViolation(
            f"{location} is below the declared minimum {schema['minimum']}"
        )
    if isinstance(value, str) and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            raise _SchemaViolation(f"{location} does not match the declared pattern")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise _SchemaViolation(
                f"{location} must contain at least {schema['minItems']} item(s)"
            )
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True) for item in value]
            if len(rendered) != len(set(rendered)):
                raise _SchemaViolation(f"{location} must contain unique items")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate_schema(item, schema["items"], f"{location}[{index}]")
    if not isinstance(value, dict):
        return

    properties = schema.get("properties", {})
    property_names = schema.get("propertyNames")
    if property_names is not None and "pattern" in property_names:
        pattern = re.compile(property_names["pattern"])
        invalid_names = sorted(key for key in value if pattern.fullmatch(key) is None)
        if invalid_names:
            raise _SchemaViolation(
                f"{location} alias '{invalid_names[0]}' does not match the declared pattern"
            )
    for required in schema.get("required", []):
        if required not in value:
            raise _SchemaViolation(f"{location} is missing required field '{required}'")
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(value) - set(properties))
        if unknown:
            raise _SchemaViolation(
                f"{location} contains unknown field '{unknown[0]}'"
            )
    elif isinstance(schema.get("additionalProperties"), dict):
        child_schema = schema["additionalProperties"]
        for key, child_value in value.items():
            if key not in properties:
                _validate_schema(child_value, child_schema, f"{location}.{key}")
    for key, child_schema in properties.items():
        if key in value:
            _validate_schema(value[key], child_schema, f"{location}.{key}")


def _validate_effective_data(data: dict[str, Any]) -> None:
    schema = _load_json(SCHEMA_PATH, "schema")
    try:
        _validate_schema(data, schema)
    except _SchemaViolation as error:
        raise _config_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="config:schema",
            reason=f"Configuration schema violation: {error}",
            recovery="Remove unknown fields, use documented lowercase aliases, and correct the declared shape",
        ) from error

    missing_actions = sorted(
        _REQUIRED_PROTECTED_ACTIONS - set(data["workspace"]["protected_actions"])
    )
    if missing_actions:
        raise _config_gate_error(
            rule_ids=("SEM-003", "HOOK-004"),
            evidence_ref="config:protected-actions",
            reason=f"Missing protected action: {missing_actions[0]}",
            recovery="Restore every mandatory protected action",
        )
    if data["runtime"]["fail_if_sandbox_unavailable"] is not True:
        raise _config_gate_error(
            rule_ids=("SEC-001", "HOOK-004"),
            evidence_ref="config:sandbox-policy",
            reason="Sandbox unavailability must fail closed",
            recovery="Set runtime.fail_if_sandbox_unavailable to true",
        )
    evaluation = data["evaluation"]
    if (
        evaluation["release_threshold"] is not None
        and (
            evaluation["threshold_owner"] is None
            or not evaluation["threshold_owner"].strip()
        )
    ):
        raise _config_gate_error(
            rule_ids=("EVAL-004", "HOOK-004"),
            evidence_ref="config:evaluation",
            reason="A release threshold has no explicit threshold owner",
            recovery="Configure threshold_owner or leave release_threshold unset",
        )
    adapters = data["adapters"]
    adapter_ids = adapters["semantic"] + adapters["query"]
    fixture_ids = [item for item in adapter_ids if item.startswith("fixture:")]
    fixture_mode_invalid = (
        adapters["fixture_enabled"]
        and (not fixture_ids or len(fixture_ids) != len(adapter_ids))
    ) or (not adapters["fixture_enabled"] and fixture_ids)
    if fixture_mode_invalid:
        raise _config_gate_error(
            rule_ids=("PORT-001", "HOOK-004"),
            evidence_ref="config:fixture-mode",
            reason="Fixture mode cannot be a production adapter fallback",
            recovery="Isolate fixture adapters behind fixture_enabled or disable fixture mode",
        )

    path_refs = [
        codebase["path_ref"] for codebase in data["business_codebases"].values()
    ]
    if len(path_refs) != len(set(path_refs)):
        raise _config_gate_error(
            rule_ids=("SCOPE-001", "PORT-001", "HOOK-004"),
            evidence_ref="config:business-codebases",
            reason="Business Codebase path_ref values are reused",
            recovery="Assign one unique path_ref to each Business Codebase alias",
        )
    declared_path_refs = set(path_refs)
    invalid_bindings = sorted(
        name
        for name, path_value in data["path_bindings"].items()
        if name not in declared_path_refs
        or not (
            Path(path_value).is_absolute()
            or re.match(r"^[A-Za-z]:[\\/]", path_value)
        )
    )
    if invalid_bindings:
        raise _config_gate_error(
            rule_ids=("SCOPE-001", "PORT-001", "HOOK-004"),
            evidence_ref="config:path-bindings",
            reason=f"Invalid local path binding: {invalid_bindings[0]}",
            recovery="Bind only declared path_ref names to absolute local paths",
        )


@dataclass(frozen=True, slots=True)
class EffectiveConfig(Mapping[str, object]):
    """Recursively read-only merged configuration exposed to Harness callers."""

    _data: Mapping[str, object]

    @classmethod
    def _from_dict(cls, data: dict[str, object]) -> "EffectiveConfig":
        return cls(_freeze(data))

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def to_dict(self) -> dict[str, object]:
        return _thaw(self._data)

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def load_effective_config(
    shared_path: Path,
    local_path: Path | None = None,
) -> EffectiveConfig:
    """Load shared configuration and return a deterministic immutable view."""

    data = _load_json(shared_path, "shared")
    misplaced_local_fields = sorted(
        set(data).intersection({"path_bindings", "cli_adapters"})
    )
    if misplaced_local_fields:
        raise _config_gate_error(
            rule_ids=("PORT-001", "HOOK-004"),
            evidence_ref="config:shared-layer",
            reason=f"Local-only fields appear in shared config: {', '.join(misplaced_local_fields)}",
            recovery="Move path_bindings and cli_adapters into the local configuration",
        )
    if _contains_matching_string(data, (_ABSOLUTE_PATH, _SECRET_VALUE)):
        raise _config_gate_error(
            rule_ids=("SEC-003", "PORT-001", "HOOK-004"),
            evidence_ref="config:shared",
            reason="Shared configuration contains an unsafe machine path or secret value",
            recovery="Use logical references in shared config and keep bindings or credential names local",
        )
    local_data = _load_json(local_path, "local") if local_path is not None else {}
    local_unknown = sorted(set(local_data) - {"path_bindings", "cli_adapters"})
    if local_unknown:
        raise _config_gate_error(
            rule_ids=("SEM-003", "HOOK-004"),
            evidence_ref="config:local",
            reason="Local configuration may not override shared or protected policy",
            recovery="Keep local configuration limited to path_bindings and cli_adapters",
        )
    if _contains_matching_string(local_data, (_SECRET_VALUE,)) or _contains_secret_argv(
        local_data
    ):
        raise _config_gate_error(
            rule_ids=("SEC-003", "HOOK-004"),
            evidence_ref="config:local",
            reason="Local configuration contains a secret value",
            recovery="Store only credential environment variable names, never credential values",
        )
    data["path_bindings"] = local_data.get("path_bindings", {})
    data["cli_adapters"] = local_data.get("cli_adapters", {})
    _validate_effective_data(data)
    return EffectiveConfig._from_dict(data)
