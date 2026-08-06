"""Atomic, sanitized runtime evidence for governed analysis (Cycle 3 Task 1).

Provides :class:`RunRecord` (binds a run to a candidate via SHA-256) and
:class:`EvidenceEntry` (atomic, sanitized evidence carrying ``source_tier``
T1/T2/T3, ``evidence_source``, ``rule_ids``, ``payload``, and a content
SHA-256). Sanitization REUSES ``gates._sanitize_text`` to strip secret values,
bearer tokens, and machine absolute Workspace paths (SEC-003, PORT-001),
extended with PII email redaction. Missing evidence or sanitization failure
raises :class:`GateError` (fail-closed); the Harness never degrades a block
to an empty placeholder (HOOK-001, HOOK-004).

Also provides JSON Schema validators for the request, review, and provenance
schemas, enforcing the documented JSON Schema subset (HOOK-001 determinism).

Applicable rules: EVID (Cycle 3), QLT-001, SEC-003, PORT-001, HOOK-001,
HOOK-004, SEM-001, SEM-002, RAW-001, RAW-003, REV-002, ANS-002.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

# Sanctioned reuse of Cycle 2 gates sanitization (per Cycle 3 Task 1 ticket:
# "Sanitization REUSES gates sanitization"). These are module-level utilities
# in gates.py; importing them avoids inventing a second error/sanitization
# protocol. gates.py public contract is unchanged.
from .gates import GateDecision, GateError, _sanitize_text, _unique
from .resources import get_schema_dir


_SOURCE_TIERS = frozenset({"T1", "T2", "T3"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# PII email redaction, extending gates sanitization (SEC-003).
# gates.py strips secrets and absolute paths but not email-style PII.
_PII_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Asset root resolution (multi-runtime module 2): replaces the historical
# parents[2] depth derivation (feature-flow §3.1.2); behavior unchanged.
_SCHEMAS_DIR = get_schema_dir()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_sha256(value: str) -> bool:
    return bool(_SHA256_HEX.fullmatch(value))


def _evidence_gate_error(
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


def _sanitize_evidence_text(value: str) -> str:
    """Apply gates sanitization (secrets, paths, URL queries) + PII email redaction."""
    sanitized = _sanitize_text(value)
    return _PII_EMAIL.sub("[REDACTED_PII]", sanitized)


def _sanitize_payload(
    value: Any,
) -> tuple[Any, bool]:
    """Recursively sanitize strings in a JSON-compatible payload.

    Returns ``(sanitized_value, changed)`` where ``changed`` is True if any
    string was modified by redaction.
    """
    if isinstance(value, str):
        sanitized = _sanitize_evidence_text(value)
        return sanitized, sanitized != value
    if isinstance(value, dict):
        changed = False
        result: dict[str, Any] = {}
        for key, val in value.items():
            sanitized_val, val_changed = _sanitize_payload(val)
            result[key] = sanitized_val
            changed = changed or val_changed
        return result, changed
    if isinstance(value, list):
        changed = False
        result_list: list[Any] = []
        for item in value:
            sanitized_item, item_changed = _sanitize_payload(item)
            result_list.append(sanitized_item)
            changed = changed or item_changed
        return result_list, changed
    return value, False


def _verify_sanitization_idempotent(payload: Any) -> None:
    """Re-sanitize an already-sanitized payload and verify nothing changes.

    If a second pass modifies the payload, a sensitive pattern survived the
    first pass. Fail closed (SEC-003, HOOK-001) rather than emitting evidence
    that may still contain a secret, path, or PII value.
    """
    _, changed = _sanitize_payload(payload)
    if changed:
        raise _evidence_gate_error(
            rule_ids=("SEC-003", "HOOK-001"),
            evidence_ref="evidence:sanitization-not-idempotent",
            reason="Sanitization is not idempotent; a sensitive pattern survived the first pass",
            recovery="Inspect the evidence payload and remove the pattern the sanitizer cannot fully redact",
        )


def _content_sha256(payload: Any) -> str:
    """Stable SHA-256 over the canonical JSON encoding of ``payload``."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _freeze_value(value: Any) -> Any:
    """Recursively freeze a JSON-compatible value into immutable containers."""
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_value(val) for key, val in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    """Recursively thaw a frozen value back into mutable JSON containers."""
    if isinstance(value, Mapping):
        return {key: _thaw_value(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Public: compute_candidate_sha
# ---------------------------------------------------------------------------


def compute_candidate_sha(candidate_payload: Any) -> str:
    """Compute the SHA-256 hex of the canonical JSON encoding of ``candidate_payload``.

    The candidate SHA-256 binds a :class:`RunRecord` to a specific analysis
    candidate. Identical input always produces identical output (HOOK-001).
    Raises ``TypeError`` or ``ValueError`` if the payload is not
    JSON-serializable (e.g., bytes, NaN).
    """
    encoded = json.dumps(
        candidate_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# Public: RunRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Immutable record binding a run to a candidate via SHA-256.

    Fields:
        run_id: Stable run identifier.
        round: Positive review round (1-based).
        candidate_sha: SHA-256 hex of the candidate payload.
        created_rev: Revision of the artifact that created this run.
        actor: The operator or agent that initiated the run.
        purpose: The declared purpose of the run.
    """

    run_id: str
    round: int
    candidate_sha: str
    created_rev: str
    actor: str
    purpose: str

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if (
            not isinstance(self.round, int)
            or isinstance(self.round, bool)
            or self.round < 1
        ):
            raise ValueError("round must be a positive integer")
        if not _is_sha256(self.candidate_sha):
            raise ValueError("candidate_sha must be a 64-character hex SHA-256")
        if not self.created_rev:
            raise ValueError("created_rev must not be empty")
        if not self.actor:
            raise ValueError("actor must not be empty")
        if not self.purpose:
            raise ValueError("purpose must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "round": self.round,
            "candidate_sha": self.candidate_sha,
            "created_rev": self.created_rev,
            "actor": self.actor,
            "purpose": self.purpose,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


# ---------------------------------------------------------------------------
# Public: EvidenceEntry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    """Atomic, sanitized runtime evidence entry.

    Fields:
        source_tier: Trust tier of the evidence source (T1, T2, or T3).
        evidence_source: Origin of the evidence (mirrors AdapterEvidence.evidence_source).
        rule_ids: Governed rule IDs cited by this evidence.
        payload: Frozen, sanitized evidence payload.
        sanitized: True if sanitization modified the payload (redaction occurred).
        content_sha256: SHA-256 hex of the sanitized payload (binds evidence content).

    Optional runtime provenance fields (multi-runtime module 5, additive —
    deployment design §10.2 / adjudication 4). All four default to ``None`` so
    old Evidence records (recorded without them) remain fully readable and
    serialize byte-identically: :meth:`to_dict` only emits a field when it is
    non-None. New readers must treat absence as "not recorded" and never
    invent a value (backward compatible, invariant 5: no machine paths).

    Use :meth:`create` to construct an entry; it sanitizes the payload,
    verifies idempotency (fail-closed), computes ``content_sha256``, and
    freezes the payload.
    """

    source_tier: str
    evidence_source: str
    rule_ids: tuple[str, ...]
    payload: Any
    sanitized: bool
    content_sha256: str
    runtime_name: str | None = None
    runtime_version: str | None = None
    native_run_id: str | None = None
    harness_release: str | None = None

    def __post_init__(self) -> None:
        if self.source_tier not in _SOURCE_TIERS:
            raise ValueError(
                f"source_tier must be one of T1, T2, T3; got {self.source_tier!r}"
            )
        if not self.evidence_source:
            raise ValueError("evidence_source must not be empty")
        if not self.rule_ids:
            raise ValueError("rule_ids must not be empty")
        if not _is_sha256(self.content_sha256):
            raise ValueError(
                "content_sha256 must be a 64-character hex SHA-256"
            )
        object.__setattr__(self, "rule_ids", _unique(self.rule_ids))

    @classmethod
    def create(
        cls,
        *,
        source_tier: str,
        evidence_source: str,
        rule_ids: Iterable[str],
        payload: Any,
        runtime_name: str | None = None,
        runtime_version: str | None = None,
        native_run_id: str | None = None,
        harness_release: str | None = None,
    ) -> "EvidenceEntry":
        """Create a sanitized, immutable evidence entry.

        The optional runtime-provenance fields (``runtime_name``/
        ``runtime_version``/``native_run_id``/``harness_release``) are recorded
        verbatim (sanitized with the same text sanitizer) and never influence
        ``content_sha256`` — the content hash binds only the governed payload,
        so the same payload recorded by different Runtimes hashes identically
        (a conformance-equivalence requirement).

        Raises :class:`GateError` (fail-closed) if:
        - ``payload`` is None (missing evidence).
        - ``payload`` is not JSON-serializable (sanitization failure).
        - A sensitive pattern survives the first sanitization pass
          (non-idempotent sanitization).
        """
        if payload is None:
            raise _evidence_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref="evidence:missing-payload",
                reason="Evidence payload is missing",
                recovery="Provide a non-null evidence payload",
            )
        # Verify JSON-serializable and deep-copy in one step (HOOK-001).
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise _evidence_gate_error(
                rule_ids=("SEC-003", "HOOK-001"),
                evidence_ref="evidence:non-serializable-payload",
                reason="Evidence payload is not JSON-serializable",
                recovery="Provide a JSON-serializable evidence payload",
            ) from error
        copied = json.loads(serialized)
        # Sanitize: reuse gates sanitization + PII redaction (SEC-003, PORT-001).
        sanitized_payload, changed = _sanitize_payload(copied)
        # Post-scan: verify idempotency (fail-closed).
        _verify_sanitization_idempotent(sanitized_payload)
        # Compute content SHA-256 from the sanitized payload.
        content_sha = _content_sha256(sanitized_payload)
        # Freeze the payload into immutable containers.
        frozen_payload = _freeze_value(sanitized_payload)
        runtime_fields = {
            "runtime_name": runtime_name,
            "runtime_version": runtime_version,
            "native_run_id": native_run_id,
            "harness_release": harness_release,
        }
        for field_name, value in runtime_fields.items():
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ValueError(f"{field_name} must be a non-empty string")
                sanitized_value = _sanitize_evidence_text(value)
                if sanitized_value != value:
                    raise ValueError(
                        f"{field_name} contains a sensitive or machine-local "
                        "pattern (SEC-003/PORT-001); record sanitized metadata"
                    )
        return cls(
            source_tier=source_tier,
            evidence_source=evidence_source,
            rule_ids=tuple(rule_ids),
            payload=frozen_payload,
            sanitized=changed,
            content_sha256=content_sha,
            runtime_name=runtime_name,
            runtime_version=runtime_version,
            native_run_id=native_run_id,
            harness_release=harness_release,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source_tier": self.source_tier,
            "evidence_source": self.evidence_source,
            "rule_ids": list(self.rule_ids),
            "payload": _thaw_value(self.payload),
            "sanitized": self.sanitized,
            "content_sha256": self.content_sha256,
        }
        # Optional runtime provenance: emitted only when recorded, so old
        # Evidence serializes exactly as before (backward compatible).
        for field in ("runtime_name", "runtime_version", "native_run_id",
                      "harness_release"):
            value = getattr(self, field)
            if value is not None:
                out[field] = value
        return out

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


# ---------------------------------------------------------------------------
# Schema validation (documented JSON Schema subset, HOOK-001 determinism)
# ---------------------------------------------------------------------------


class _SchemaError(ValueError):
    """Schema validation failure (internal)."""


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


def _validate_schema_subset(
    value: Any,
    schema: dict[str, Any],
    location: str = "$",
) -> None:
    """Validate ``value`` against the documented JSON Schema subset.

    Supported keywords (mirrors the config validator's subset):
    type, enum, pattern, minimum, minItems, uniqueItems, items,
    properties, propertyNames, required, additionalProperties.
    """
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        raise _SchemaError(f"{location} must have type {expected_type}")
    if "enum" in schema and value not in schema["enum"]:
        raise _SchemaError(f"{location} must be one of {schema['enum']}")
    if isinstance(value, float) and not math.isfinite(value):
        raise _SchemaError(f"{location} must be a finite number")
    if (
        "minimum" in schema
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value < schema["minimum"]
    ):
        raise _SchemaError(
            f"{location} is below the declared minimum {schema['minimum']}"
        )
    if isinstance(value, str) and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            raise _SchemaError(f"{location} does not match the declared pattern")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise _SchemaError(
                f"{location} must contain at least {schema.get('minItems', 0)} item(s)"
            )
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True) for item in value]
            if len(rendered) != len(set(rendered)):
                raise _SchemaError(f"{location} must contain unique items")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate_schema_subset(item, schema["items"], f"{location}[{index}]")
    if not isinstance(value, dict):
        return

    properties = schema.get("properties", {})
    property_names = schema.get("propertyNames")
    if property_names is not None and "pattern" in property_names:
        pattern = re.compile(property_names["pattern"])
        invalid = sorted(key for key in value if pattern.fullmatch(key) is None)
        if invalid:
            raise _SchemaError(
                f"{location} property name '{invalid[0]}' does not match the declared pattern"
            )
    for required in schema.get("required", []):
        if required not in value:
            raise _SchemaError(f"{location} is missing required field '{required}'")
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(value) - set(properties))
        if unknown:
            raise _SchemaError(f"{location} contains unknown field '{unknown[0]}'")
    elif isinstance(schema.get("additionalProperties"), dict):
        child_schema = schema["additionalProperties"]
        for key, child_value in value.items():
            if key not in properties:
                _validate_schema_subset(child_value, child_schema, f"{location}.{key}")
    for key, child_schema in properties.items():
        if key in value:
            _validate_schema_subset(value[key], child_schema, f"{location}.{key}")


_SCHEMA_CACHE: dict[str, dict[str, Any]] = {}


def _get_schema(name: str) -> dict[str, Any]:
    if name not in _SCHEMA_CACHE:
        path = _SCHEMAS_DIR / name
        try:
            _SCHEMA_CACHE[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise _evidence_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref=f"schema:{name}",
                reason=f"Schema {name} is unavailable or malformed",
                recovery=f"Restore {path}",
            ) from error
    return _SCHEMA_CACHE[name]


def _validate_against_schema(
    payload: Mapping[str, Any],
    schema: dict[str, Any],
    schema_name: str,
) -> None:
    try:
        _validate_schema_subset(payload, schema)
    except _SchemaError as error:
        raise _evidence_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref=f"schema:{schema_name}",
            reason=f"Schema validation failure: {error}",
            recovery="Correct the payload to match the declared schema",
        ) from error


def validate_request(payload: Mapping[str, Any]) -> None:
    """Validate an analysis request against ``request.schema.json``.

    Raises :class:`GateError` (fail-closed) on validation failure.
    """
    _validate_against_schema(payload, _get_schema("request.schema.json"), "request.schema.json")


def validate_review(payload: Mapping[str, Any]) -> None:
    """Validate a review verdict against ``review.schema.json``.

    Raises :class:`GateError` (fail-closed) on validation failure.
    The schema expresses stale SHA (required ``candidate_sha`` field),
    missing coverage (11 required coverage keys), and blocking findings
    (``findings[].severity`` enum includes ``block``).
    """
    _validate_against_schema(payload, _get_schema("review.schema.json"), "review.schema.json")


def validate_provenance(payload: Mapping[str, Any]) -> None:
    """Validate a provenance footer against ``provenance.schema.json``.

    Raises :class:`GateError` (fail-closed) on validation failure.
    """
    _validate_against_schema(payload, _get_schema("provenance.schema.json"), "provenance.schema.json")
