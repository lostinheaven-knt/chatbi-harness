"""Standard event model: ``chatbi.event/v1`` envelope validation (module 3).

The 17 standard events (design §8.4, verbatim) are the vendor-neutral audit
surface: Adapters normalize Runtime-native events into them, the product API
replays them by ``event_index`` (cursor), and consumers dedupe by
``event_id``. Events must be monotonically numbered, replayable and
deduplicable — enforced by :func:`validate_event_stream`.

Critical constraint (design §8.4, ADR-002): ``run.completed`` may only be
emitted after the Delivery Gate PASS — the envelope validator enforces
``payload.gate == "delivery"`` and ``payload.decision == "pass"`` for that
event type, so a Runtime that reports "completed" without passing the
governance delivery gate is rejected at the contract boundary.

Applicable rules: HOOK-001 (determinism), MR-005, invariant 5 (no machine
paths/secrets in events).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "chatbi.event/v1"

#: The 17 standard events (design §8.4).
EVENT_TYPES = (
    "run.created",
    "run.started",
    "step.started",
    "step.progress",
    "tool.requested",
    "tool.blocked",
    "tool.completed",
    "evidence.recorded",
    "gate.blocked",
    "approval.requested",
    "approval.resolved",
    "review.started",
    "review.completed",
    "run.paused",
    "run.resumed",
    "run.failed",
    "run.completed",
)
_EVENT_TYPE_SET = frozenset(EVENT_TYPES)


class EventType(str, Enum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    STEP_STARTED = "step.started"
    STEP_PROGRESS = "step.progress"
    TOOL_REQUESTED = "tool.requested"
    TOOL_BLOCKED = "tool.blocked"
    TOOL_COMPLETED = "tool.completed"
    EVIDENCE_RECORDED = "evidence.recorded"
    GATE_BLOCKED = "gate.blocked"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    REVIEW_STARTED = "review.started"
    REVIEW_COMPLETED = "review.completed"
    RUN_PAUSED = "run.paused"
    RUN_RESUMED = "run.resumed"
    RUN_FAILED = "run.failed"
    RUN_COMPLETED = "run.completed"


#: Envelope fields required on every event (design §5.2 example, all fields).
REQUIRED_FIELDS = (
    "schema_version",
    "event_id",
    "event_index",
    "trace_id",
    "session_id",
    "run_id",
    "workflow_id",
    "step_id",
    "event_type",
    "occurred_at",
    "runtime",
    "payload",
    "evidence_refs",
)

#: Delivery-gate completion contract: run.completed requires these payload
#: fields (design §8.4 last sentence, ADR-002).
_COMPLETION_GATE = "delivery"
_COMPLETION_DECISION = "pass"

_ID_CHARSET_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_OCCURRED_AT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def _validate_id(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not _ID_CHARSET_RE.match(value):
        errors.append(
            f"{field}: must be a non-empty string of [A-Za-z0-9._-], "
            f"got {value!r}"
        )


def validate_envelope(event: Mapping[str, Any]) -> list[str]:
    """Return violation strings for one event envelope ([] = valid)."""
    errors: list[str] = []
    if not isinstance(event, Mapping):
        return ["event must be a mapping"]

    for field in REQUIRED_FIELDS:
        if field not in event:
            errors.append(f"missing required field {field!r}")

    if event.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION!r}, got "
            f"{event.get('schema_version')!r}"
        )

    event_type = event.get("event_type")
    if event_type not in _EVENT_TYPE_SET:
        errors.append(
            f"event_type {event_type!r} is not one of the 17 standard "
            "events"
        )

    event_index = event.get("event_index")
    if isinstance(event_index, bool) or not isinstance(event_index, int):
        errors.append(f"event_index must be a non-negative integer, got {event_index!r}")
    elif event_index < 0:
        errors.append("event_index must be a non-negative integer")

    for field in ("event_id", "trace_id", "session_id", "run_id", "workflow_id"):
        if field in event:
            _validate_id(event[field], field, errors)

    step_id = event.get("step_id")
    if step_id is not None and not isinstance(step_id, str):
        errors.append("step_id must be a string")

    occurred_at = event.get("occurred_at")
    if not isinstance(occurred_at, str) or not _OCCURRED_AT_RE.match(occurred_at):
        errors.append(
            f"occurred_at must be an ISO-8601 timestamp, got {occurred_at!r}"
        )

    runtime = event.get("runtime")
    if not isinstance(runtime, Mapping):
        errors.append("runtime must be a mapping")
    elif not isinstance(runtime.get("name"), str) or not runtime["name"]:
        errors.append("runtime.name must be a non-empty string")

    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        errors.append("payload must be a mapping")

    evidence_refs = event.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not all(
        isinstance(ref, str) for ref in evidence_refs
    ):
        errors.append("evidence_refs must be a list of strings")

    # --- run.completed requires Delivery Gate PASS (design §8.4, ADR-002) ---
    if event_type == "run.completed":
        if not isinstance(payload, Mapping):
            errors.append(
                "run.completed requires a delivery-gate payload "
                "(gate='delivery', decision='pass')"
            )
        else:
            gate = payload.get("gate")
            decision = payload.get("decision")
            if gate != _COMPLETION_GATE or decision != _COMPLETION_DECISION:
                errors.append(
                    "run.completed may only be emitted after the Delivery "
                    "Gate PASS: payload.gate must be 'delivery' and "
                    "payload.decision must be 'pass' "
                    f"(got gate={gate!r}, decision={decision!r})"
                )

    return errors


def validate_event_stream(events: Iterable[Mapping[str, Any]]) -> list[str]:
    """Validate a stream of envelopes plus ordering invariants.

    Every event must pass :func:`validate_envelope`; additionally
    ``event_id`` must be globally unique (deduplication key) and
    ``event_index`` must be strictly monotonically increasing within the
    stream (replay key, design §6.3). Replaying the same stream yields the
    same verdict — replayable by construction.
    """
    errors: list[str] = []
    seen_ids: set[str] = set()
    last_index: int | None = None
    for i, event in enumerate(events):
        errors.extend(validate_envelope(event))
        if not isinstance(event, Mapping):
            continue
        event_id = event.get("event_id")
        if isinstance(event_id, str):
            if event_id in seen_ids:
                errors.append(
                    f"events[{i}]: duplicate event_id {event_id!r} "
                    "(event streams must be deduplicable)"
                )
            seen_ids.add(event_id)
        event_index = event.get("event_index")
        if isinstance(event_index, int) and not isinstance(event_index, bool):
            if last_index is not None and event_index <= last_index:
                errors.append(
                    f"events[{i}]: event_index {event_index} is not strictly "
                    f"greater than previous {last_index} (must be monotonic "
                    "for replay)"
                )
            last_index = event_index
    return errors


def is_delivery_completion(event: Mapping[str, Any]) -> bool:
    """True iff ``event`` is a legal ``run.completed`` (Delivery Gate PASS)."""
    return (
        event.get("event_type") == "run.completed"
        and validate_envelope(event) == []
    )
