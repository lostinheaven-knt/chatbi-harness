"""Agno RunEvent ↔ ChatBI standard-event mapper + replayable event log
(module 5, MR-D1/MR-D2).

Implements the impl-doc §5.4 mapping table against the real Agno 2.6.22
workflow event types (``agno.run.workflow.WorkflowRunEvent`` and the
corresponding dataclasses):

| Agno native event               | standard event        | constraint                     |
| ------------------------------- | --------------------- | ------------------------------ |
| ``WorkflowStarted``             | ``run.started``       | kernel preflight already ran   |
| ``StepStarted``                 | ``step.started``      | step_id = IR step id (name)    |
| ``StepCompleted``               | ``step.progress``     | sanitized output summary       |
| ``StepPaused`` (requires_conf.) | ``run.paused``        | approval bridge (coordinator)  |
| ``WorkflowPaused``              | ``run.paused``        | deduped after a StepPaused     |
| ``ToolCallStarted``             | ``tool.requested``    | tool allowlist by kernel       |
| ``ToolCallCompleted``           | ``tool.completed``    | —                              |
| ``ToolCallDenied``              | ``tool.blocked``      | —                              |
| ``WorkflowError`` / ``StepError`` | ``run.failed``      | never success                  |
| ``WorkflowCancelled``           | ``run.paused``        | never success (impl §5.4 row)  |
| ``WorkflowCompleted``           | **not mapped**        | controller emits run.completed |
|                                |                       | ONLY after Kernel delivery     |
|                                |                       | gate PASS, else gate.blocked   |
|                                |                       | (ADR-002)                      |

Events are envelopes of ``chatbi.event/v1`` (validated by
``chatbi_runtime_contract.events.validate_envelope``): monotonically numbered
(``event_index``), replayable (cursor) and deduplicable (``event_id``).
The :class:`EventLog` persists each run's standard events as an append-only
JSONL file under the runtime state directory; a disconnect replays from the
last cursor (deployment design §6.3/§17).

Applicable rules: HOOK-001, ADR-002, MR-006, invariant 5 (sanitized payloads).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from chatbi_governance.gates import _sanitize_text
from chatbi_runtime_contract.events import (
    SCHEMA_VERSION as EVENT_SCHEMA_VERSION,
    validate_envelope,
)

#: Agno native event names (agno 2.6.22, run/workflow.py WorkflowRunEvent).
EVT_WORKFLOW_STARTED = "WorkflowStarted"
EVT_WORKFLOW_COMPLETED = "WorkflowCompleted"
EVT_WORKFLOW_PAUSED = "WorkflowPaused"
EVT_WORKFLOW_CANCELLED = "WorkflowCancelled"
EVT_WORKFLOW_ERROR = "WorkflowError"
EVT_STEP_STARTED = "StepStarted"
EVT_STEP_COMPLETED = "StepCompleted"
EVT_STEP_PAUSED = "StepPaused"
EVT_STEP_CONTINUED = "StepContinued"
EVT_STEP_ERROR = "StepError"
EVT_TOOL_CALL_STARTED = "ToolCallStarted"
EVT_TOOL_CALL_COMPLETED = "ToolCallCompleted"
EVT_TOOL_CALL_ERROR = "ToolCallError"

#: Events whose payload values are passed through the kernel sanitizer.
_SANITIZE_KEYS = ("step_name", "content", "error", "reason", "confirmation_message")


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _event_payload(event: Any) -> dict[str, Any]:
    """Extract a sanitized payload summary from an agno event object."""
    payload: dict[str, Any] = {}
    for key in ("step_name", "step_id", "content", "error", "reason",
                "confirmation_message", "requires_confirmation",
                "requires_user_input"):
        value = getattr(event, key, None)
        if value is not None:
            payload[key] = _sanitize(value)
    status = getattr(event, "status", None)
    if isinstance(status, str):
        payload["status"] = status
    return payload


def _occurred_at(event: Any) -> str:
    created_at = getattr(event, "created_at", None)
    if isinstance(created_at, (int, float)) and created_at > 0:
        return (
            datetime.fromtimestamp(created_at, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _native_run_id(event: Any) -> str | None:
    run_id = getattr(event, "run_id", None)
    return run_id if isinstance(run_id, str) and run_id else None


def map_agno_event(
    event: Any,
    *,
    session_id: str,
    run_id: str,
    workflow_id: str,
    event_index: int,
    event_id: str,
    trace_id: str = "",
    runtime_name: str = "agno",
) -> dict[str, Any] | None:
    """Map ONE agno workflow event to a standard ``chatbi.event/v1`` envelope.

    Returns ``None`` for events that must NOT be surfaced directly (most
    importantly ``WorkflowCompleted``: the controller decides
    ``run.completed`` vs ``gate.blocked`` from the Kernel delivery gate —
    ADR-002 — and ``StepContinued``: ``run.resumed`` is emitted by the
    controller only after Kernel approval re-verification PASS).

    Raises ``ValueError`` on an agno event the mapper cannot interpret (the
    controller treats that as fail-closed: stop, do not ignore silently —
    deployment design §13 rule 4).
    """
    event_name = getattr(event, "event", None)
    event_name = event_name.value if hasattr(event_name, "value") else event_name

    event_type: str | None = None
    payload: dict[str, Any] = {}
    step_id: str | None = getattr(event, "step_name", None) or getattr(
        event, "step_id", None
    )

    if event_name == EVT_WORKFLOW_STARTED:
        event_type = "run.started"
        step_id = None
    elif event_name == EVT_STEP_STARTED:
        event_type = "step.started"
    elif event_name == EVT_STEP_COMPLETED:
        event_type = "step.progress"
    elif event_name == EVT_STEP_PAUSED:
        requires_confirmation = bool(getattr(event, "requires_confirmation", False))
        if not requires_confirmation:
            # A step paused for user input only: pause without approval bridge.
            event_type = "run.paused"
        else:
            event_type = "run.paused"
        payload = _event_payload(event)
        payload["approval_required"] = bool(requires_confirmation)
    elif event_name == EVT_WORKFLOW_PAUSED:
        event_type = "run.paused"
        step_id = getattr(event, "paused_step_name", None) or step_id
        payload = _event_payload(event)
    elif event_name == EVT_STEP_ERROR or event_name == EVT_WORKFLOW_ERROR:
        event_type = "run.failed"
        step_id = getattr(event, "step_name", None) or step_id
        payload = _event_payload(event)
    elif event_name == EVT_WORKFLOW_CANCELLED:
        event_type = "run.paused"  # impl §5.4: cancelled is never success
        payload = _event_payload(event)
        payload["cancelled"] = True
    elif event_name == "StepOutput":
        # The per-step result payload event (agno 2.6.22): it carries the
        # step output, which does not affect the gate/state machine — the
        # step lifecycle is already surfaced via step.started/step.progress.
        # Known and deliberately not mapped (design §13 rule 4 applies to
        # gate-affecting unknowns; this one is a registered non-affecting
        # event).
        return None
    elif event_name == EVT_TOOL_CALL_STARTED:
        event_type = "tool.requested"
    elif event_name == EVT_TOOL_CALL_COMPLETED:
        event_type = "tool.completed"
    elif event_name == EVT_TOOL_CALL_ERROR:
        # Real agno 2.6.22 event (run/agent.py RunEvent.tool_call_error):
        # a tool call that failed is surfaced as tool.blocked — the tool did
        # not complete, and the standard vocabulary has no tool.error event.
        # (Allowlist denials are emitted by the adapter's StepToolPolicy,
        # never by a native agno event — agno has no ToolCallDenied.)
        event_type = "tool.blocked"
        payload = _event_payload(event)
        payload["reason"] = "tool call errored (agno ToolCallError)"
    elif event_name == EVT_WORKFLOW_COMPLETED:
        # ADR-002: NEVER mapped directly. The controller emits run.completed
        # only after the Kernel delivery gate PASS.
        return None
    elif event_name == EVT_STEP_CONTINUED:
        # run.resumed is emitted by the controller only after Kernel approval
        # re-verification PASS (先验后续).
        return None
    else:
        raise ValueError(
            f"unmappable agno event {event_name!r}: unknown events that can "
            "affect the gate/state machine must stop the run, not be ignored "
            "(design §13 rule 4)"
        )

    envelope = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "event_index": event_index,
        "trace_id": trace_id or f"tr_{run_id}",
        "session_id": session_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "step_id": step_id,
        "event_type": event_type,
        "occurred_at": _occurred_at(event),
        "runtime": {
            "name": runtime_name,
            "native_run_id": _native_run_id(event) or run_id,
        },
        "payload": payload,
        "evidence_refs": [],
    }
    violations = validate_envelope(envelope)
    if violations:
        raise ValueError(f"mapped event violates the envelope contract: {violations}")
    return envelope


@dataclass(frozen=True)
class ReplayResult:
    """A cursor-based replay of one run's standard events."""

    run_id: str
    events: tuple[dict[str, Any], ...]
    next_cursor: int


class EventLog:
    """Append-only, replayable store of standard chatbi events (one JSONL
    file per run under the runtime state dir). Single writer per process
    (MVP single instance, adjudication ten); appends are flushed+fsynced."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self._events_dir = self.state_dir / "events"
        self._events_dir.mkdir(parents=True, exist_ok=True)

    def _run_path(self, run_id: str) -> Path:
        if (
            not isinstance(run_id, str) or not run_id
            or "/" in run_id or ".." in run_id
        ):
            raise ValueError(f"invalid run_id: {run_id!r}")
        return self._events_dir / f"{run_id}.jsonl"

    def append(self, event: Mapping[str, Any]) -> None:
        """Persist one envelope (must already carry event_id/event_index)."""
        violations = validate_envelope(event)
        if violations:
            raise ValueError(f"refusing to persist an invalid event: {violations}")
        run_id = event["run_id"]
        line = (
            json.dumps(dict(event), ensure_ascii=False, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        path = self._run_path(run_id)
        with open(path, "ab") as handle:  # noqa: PTH123
            handle.write(line)
            handle.flush()
            try:
                import os

                os.fsync(handle.fileno())
            except OSError:
                pass

    def replay(self, run_id: str, cursor: int | None = None) -> ReplayResult:
        """Replay persisted events strictly after ``cursor`` (default all).

        Dedup: event_id collisions cannot occur by construction (per-run
        monotonic index), and replaying the same cursor twice yields the same
        events — idempotent replay (design §6.3).
        """
        path = self._run_path(run_id)
        events: list[dict[str, Any]] = []
        if path.is_file():
            raw = path.read_bytes()
            if len(raw) > 64 * 1024 * 1024:
                raise RuntimeError(f"event log too large: {path}")
            for line in raw.decode("utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                index = event.get("event_index")
                if isinstance(index, int) and (cursor is None or index > cursor):
                    events.append(event)
        events.sort(key=lambda ev: ev["event_index"])
        next_cursor = events[-1]["event_index"] if events else (cursor or 0)
        return ReplayResult(run_id=run_id, events=tuple(events), next_cursor=next_cursor)

    def next_index(self, run_id: str) -> int:
        """Next monotonic event_index for a run (0 when empty)."""
        result = self.replay(run_id)
        if result.events:
            return result.events[-1]["event_index"] + 1
        return 0


def iter_standard_events(
    agno_events: Iterable[Any],
    *,
    session_id: str,
    run_id: str,
    workflow_id: str,
    event_log: EventLog,
    exclude_event_types: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Map a batch of agno events to standard envelopes, persisting each.

    Assigns monotonic ``event_index`` (continuing the run's log) and unique
    ``event_id`` (``evt_<run_id>_<index>``). Raises on any unmappable event
    (fail-closed, design §13 rule 4).

    ``exclude_event_types`` drops mapped envelopes BEFORE persistence
    (module 6: an approval-step pause that the controller auto-resumes
    because the IR ``owner.pending`` condition is false must not surface a
    ``run.paused`` in the standard event stream or the log).
    """
    index = event_log.next_index(run_id)
    mapped: list[dict[str, Any]] = []
    for agno_event in agno_events:
        envelope = map_agno_event(
            agno_event,
            session_id=session_id,
            run_id=run_id,
            workflow_id=workflow_id,
            event_index=index,
            event_id=f"evt_{run_id}_{index}",
        )
        if envelope is None:
            continue
        if envelope["event_type"] in exclude_event_types:
            continue
        event_log.append(envelope)
        mapped.append(envelope)
        index += 1
    return mapped
