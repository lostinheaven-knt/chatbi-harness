"""YAML → IR model loading for ``chatbi.harness/v1`` workflow declarations.

``load_workflow`` parses one YAML file into the field-level
:class:`~chatbi_harness_ir.schema.Workflow` model with strict shape checks:
unknown fields at every level are rejected, and a ``schema_version`` whose
major is not compatible with ``chatbi.harness/v1`` is rejected outright
(design §13 rule 1 — version-incompatible declarations never load).

``load_all`` loads a whole workflows directory and enforces registry-level
invariants: one file per workflow (filename == ``workflow_id``), globally
unique ``workflow_id``, and route values that only reference loaded
workflows or the ``none`` / ``owner`` sentinels.

This module performs structural loading only; semantic checks
(conditions, prompt hashes, PORT-001/SEC-003 scanning, …) live in
``chatbi_harness_ir.validator``.

Applicable rules: HOOK-001 (determinism), PORT-001 (no machine paths),
MR-002 (nine-workflow coverage), MR-004 (schema version gate).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .schema import (
    SCHEMA_MAJOR,
    SCHEMA_NAME,
    ROUTE_NONE,
    ROUTE_OWNER,
    Compatibility,
    DeliveryGate,
    Entry,
    EvidenceSpec,
    ExecutorKind,
    Gates,
    PromptRef,
    RequirementLevel,
    Step,
    ToolsSpec,
    Workflow,
)


class IrLoadError(ValueError):
    """Raised when a workflow declaration cannot be loaded."""


def _expect_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise IrLoadError(f"{context} must be a mapping, got {type(value).__name__}")
    return value


def _take(
    raw: Mapping[str, Any],
    key: str,
    context: str,
    required: bool = True,
) -> Any:
    if key in raw:
        return raw[key]
    if required:
        raise IrLoadError(f"{context}: missing required field {key!r}")
    return None


def _take_str(
    raw: Mapping[str, Any],
    key: str,
    context: str,
    required: bool = True,
) -> str | None:
    value = _take(raw, key, context, required=required)
    if value is None:
        return None
    if not isinstance(value, str):
        raise IrLoadError(f"{context}.{key} must be a string")
    return value


def _take_str_list(
    raw: Mapping[str, Any],
    key: str,
    context: str,
    required: bool = False,
) -> tuple[str, ...]:
    value = _take(raw, key, context, required=required)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise IrLoadError(f"{context}.{key} must be a list of strings")
    return tuple(value)


def _take_bool(
    raw: Mapping[str, Any],
    key: str,
    context: str,
) -> bool:
    value = _take(raw, key, context, required=False)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise IrLoadError(f"{context}.{key} must be a boolean")
    return value


def _coerce_tools(raw: Mapping[str, Any], context: str) -> ToolsSpec:
    unknown = set(raw) - {"allow", "deny"}
    if unknown:
        raise IrLoadError(f"{context}: unknown fields {sorted(unknown)}")
    return ToolsSpec(
        allow=_take_str_list(raw, "allow", context),
        deny=_take_str_list(raw, "deny", context),
    )


def _coerce_step(raw: Mapping[str, Any], context: str) -> Step:
    unknown = set(raw) - {
        "id", "executor", "function", "when", "inputs", "outputs",
        "tools", "review_required", "review_schema",
    }
    if unknown:
        raise IrLoadError(f"{context}: unknown fields {sorted(unknown)}")
    executor_raw = _take_str(raw, "executor", context)
    try:
        executor = ExecutorKind(executor_raw)
    except ValueError:
        raise IrLoadError(
            f"{context}.executor: unknown executor {executor_raw!r}"
        ) from None
    tools_raw = _take(raw, "tools", context, required=False)
    return Step(
        id=_take_str(raw, "id", context),
        executor=executor,
        function=_take_str(raw, "function", context, required=False),
        when=_take_str(raw, "when", context, required=False),
        inputs=_take_str_list(raw, "inputs", context),
        outputs=_take_str_list(raw, "outputs", context),
        tools=(
            None
            if tools_raw is None
            else _coerce_tools(_expect_mapping(tools_raw, f"{context}.tools"),
                               f"{context}.tools")
        ),
        review_required=_take_bool(raw, "review_required", context),
        review_schema=_take_str(raw, "review_schema", context, required=False),
    )


def _coerce_levels(raw: Mapping[str, Any], context: str) -> dict[str, RequirementLevel]:
    out: dict[str, RequirementLevel] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise IrLoadError(f"{context}: capability/requirement names must be strings")
        if not isinstance(value, str):
            raise IrLoadError(f"{context}.{key}: level must be a string")
        try:
            out[key] = RequirementLevel(value)
        except ValueError:
            raise IrLoadError(
                f"{context}.{key}: unknown level {value!r} "
                f"(expected required|optional|protected_actions|none)"
            ) from None
    return out


def _coerce_workflow(raw: Mapping[str, Any], source: str) -> Workflow:
    unknown = set(raw) - {
        "schema_version", "workflow_id", "workflow_version", "title",
        "description", "entry", "steps", "gates", "requirements",
        "capabilities", "prompts", "tools", "evidence", "routes",
        "compatibility",
    }
    if unknown:
        raise IrLoadError(f"{source}: unknown top-level fields {sorted(unknown)}")

    schema_version = _take_str(raw, "schema_version", source)
    _check_schema_version(schema_version, source)

    workflow_version = _take(raw, "workflow_version", source)
    if isinstance(workflow_version, bool) or not isinstance(workflow_version, int):
        raise IrLoadError(
            f"{source}.workflow_version must be a positive integer"
        )

    entry_raw = _expect_mapping(_take(raw, "entry", source), f"{source}.entry")
    unknown_entry = set(entry_raw) - {"command", "argument_hint"}
    if unknown_entry:
        raise IrLoadError(
            f"{source}.entry: unknown fields {sorted(unknown_entry)}"
        )

    steps_raw = _take(raw, "steps", source)
    if not isinstance(steps_raw, list) or not steps_raw:
        raise IrLoadError(f"{source}.steps must be a non-empty list")
    steps = tuple(
        _coerce_step(
            _expect_mapping(s, f"{source}.steps[{i}]"), f"{source}.steps[{i}]"
        )
        for i, s in enumerate(steps_raw)
    )

    gates = None
    gates_raw = _take(raw, "gates", source, required=False)
    if gates_raw is not None:
        gates_map = _expect_mapping(gates_raw, f"{source}.gates")
        unknown_gates = set(gates_map) - {"delivery"}
        if unknown_gates:
            raise IrLoadError(
                f"{source}.gates: unknown fields {sorted(unknown_gates)}"
            )
        delivery_raw = _take(gates_map, "delivery", f"{source}.gates", required=False)
        if delivery_raw is not None:
            delivery = _expect_mapping(delivery_raw, f"{source}.gates.delivery")
            unknown_delivery = set(delivery) - {"rule_ids", "terminal_only"}
            if unknown_delivery:
                raise IrLoadError(
                    f"{source}.gates.delivery: unknown fields "
                    f"{sorted(unknown_delivery)}"
                )
            gates = Gates(
                delivery=DeliveryGate(
                    rule_ids=_take_str_list(
                        delivery, "rule_ids", f"{source}.gates.delivery"
                    ),
                    terminal_only=_take_bool(
                        delivery, "terminal_only", f"{source}.gates.delivery"
                    ),
                )
            )

    prompts = ()
    prompts_raw = _take(raw, "prompts", source, required=False)
    if prompts_raw is not None:
        if not isinstance(prompts_raw, list):
            raise IrLoadError(f"{source}.prompts must be a list")
        prompts_list = []
        for i, p in enumerate(prompts_raw):
            pmap = _expect_mapping(p, f"{source}.prompts[{i}]")
            unknown_prompt = set(pmap) - {"name", "path", "sha256"}
            if unknown_prompt:
                raise IrLoadError(
                    f"{source}.prompts[{i}]: unknown fields {sorted(unknown_prompt)}"
                )
            prompts_list.append(
                PromptRef(
                    name=_take_str(pmap, "name", f"{source}.prompts[{i}]"),
                    path=_take_str(pmap, "path", f"{source}.prompts[{i}]"),
                    sha256=_take_str(pmap, "sha256", f"{source}.prompts[{i}]"),
                )
            )
        prompts = tuple(prompts_list)

    tools = None
    tools_raw = _take(raw, "tools", source, required=False)
    if tools_raw is not None:
        tools = _coerce_tools(
            _expect_mapping(tools_raw, f"{source}.tools"), f"{source}.tools"
        )

    evidence = None
    evidence_raw = _take(raw, "evidence", source, required=False)
    if evidence_raw is not None:
        emap = _expect_mapping(evidence_raw, f"{source}.evidence")
        unknown_evidence = set(emap) - {"root", "schema_versions"}
        if unknown_evidence:
            raise IrLoadError(
                f"{source}.evidence: unknown fields {sorted(unknown_evidence)}"
            )
        schema_versions: dict[str, str] = {}
        sv_raw = _take(emap, "schema_versions", f"{source}.evidence", required=False)
        if sv_raw is not None:
            sv_map = _expect_mapping(sv_raw, f"{source}.evidence.schema_versions")
            for k, v in sv_map.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise IrLoadError(
                        f"{source}.evidence.schema_versions must map strings "
                        f"to strings"
                    )
                schema_versions[k] = v
        evidence = EvidenceSpec(
            root=_take_str(emap, "root", f"{source}.evidence"),
            schema_versions=schema_versions,
        )

    routes = {}
    routes_raw = _take(raw, "routes", source, required=False)
    if routes_raw is not None:
        routes_map = _expect_mapping(routes_raw, f"{source}.routes")
        for k, v in routes_map.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise IrLoadError(
                    f"{source}.routes must map strings to strings"
                )
            routes[k] = v

    compatibility = None
    compat_raw = _take(raw, "compatibility", source, required=False)
    if compat_raw is not None:
        cmap = _expect_mapping(compat_raw, f"{source}.compatibility")
        unknown_compat = set(cmap) - {"deprecated_fields", "migration"}
        if unknown_compat:
            raise IrLoadError(
                f"{source}.compatibility: unknown fields {sorted(unknown_compat)}"
            )
        compatibility = Compatibility(
            deprecated_fields=_take_str_list(
                cmap, "deprecated_fields", f"{source}.compatibility"
            ),
            migration=_take_str(
                cmap, "migration", f"{source}.compatibility", required=False
            ),
        )

    return Workflow(
        schema_version=schema_version,
        workflow_id=_take_str(raw, "workflow_id", source),
        workflow_version=workflow_version,
        title=_take_str(raw, "title", source),
        description=_take_str(raw, "description", source),
        entry=Entry(
            command=_take_str(entry_raw, "command", f"{source}.entry"),
            argument_hint=_take_str(
                entry_raw, "argument_hint", f"{source}.entry", required=False
            ),
        ),
        steps=steps,
        gates=gates,
        requirements=_coerce_levels(
            _expect_mapping(
                _take(raw, "requirements", source, required=False) or {},
                f"{source}.requirements",
            ),
            f"{source}.requirements",
        ),
        capabilities=_coerce_levels(
            _expect_mapping(
                _take(raw, "capabilities", source, required=False) or {},
                f"{source}.capabilities",
            ),
            f"{source}.capabilities",
        ),
        prompts=prompts,
        tools=tools,
        evidence=evidence,
        routes=routes,
        compatibility=compatibility,
    )


def _check_schema_version(schema_version: str | None, source: str) -> None:
    if not schema_version:
        raise IrLoadError(f"{source}: missing required field 'schema_version'")
    if "/v" not in schema_version:
        raise IrLoadError(
            f"{source}: malformed schema_version {schema_version!r} "
            f"(expected '<name>/v<major>')"
        )
    name, _, major_raw = schema_version.partition("/v")
    if name != SCHEMA_NAME:
        raise IrLoadError(
            f"{source}: incompatible schema name {name!r} "
            f"(this loader only understands {SCHEMA_NAME!r})"
        )
    try:
        major = int(major_raw)
    except ValueError:
        raise IrLoadError(
            f"{source}: malformed schema major in {schema_version!r}"
        ) from None
    if major != SCHEMA_MAJOR:
        raise IrLoadError(
            f"{source}: incompatible schema major {major} "
            f"(expected {SCHEMA_MAJOR}; major mismatch is not auto-upgraded, "
            f"design §13 rule 1)"
        )


def load_workflow(path: Path) -> Workflow:
    """Load one ``chatbi.harness/v1`` workflow declaration from ``path``.

    Raises :class:`IrLoadError` for malformed YAML, unknown fields, bad
    shapes, and schema_version major incompatibility (design §13 rule 1).
    """
    source = str(path)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise IrLoadError(f"{source}: cannot read workflow: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise IrLoadError(f"{source}: invalid YAML: {exc}") from exc
    if raw is None:
        raise IrLoadError(f"{source}: empty workflow file")
    return _coerce_workflow(_expect_mapping(raw, source), source)


def load_all(workflows_dir: Path) -> list[Workflow]:
    """Load every ``*.yaml`` in ``workflows_dir`` with registry invariants.

    In addition to per-file loading this enforces: filename == ``workflow_id``,
    globally unique ``workflow_id``, and route values that are either the
    ``none`` / ``owner`` sentinels or reference a workflow loaded from the
    same directory (design §4.1/§4.2, MR-002).
    """
    directory = Path(workflows_dir)
    if not directory.is_dir():
        raise IrLoadError(f"{directory}: not a directory")
    workflows: list[Workflow] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.yaml")):
        wf = load_workflow(path)
        if wf.workflow_id != path.stem:
            raise IrLoadError(
                f"{path}: workflow_id {wf.workflow_id!r} does not match "
                f"filename {path.stem!r}"
            )
        if wf.workflow_id in seen:
            raise IrLoadError(
                f"{path}: duplicate workflow_id {wf.workflow_id!r}"
            )
        seen.add(wf.workflow_id)
        workflows.append(wf)
    loaded = {wf.workflow_id for wf in workflows}
    for wf in workflows:
        for key, target in wf.routes.items():
            if target in (ROUTE_NONE, ROUTE_OWNER):
                continue
            if target not in loaded:
                raise IrLoadError(
                    f"{wf.workflow_id}.routes.{key}: unknown route target "
                    f"{target!r} (not a loaded workflow, 'none' or 'owner')"
                )
    return workflows
