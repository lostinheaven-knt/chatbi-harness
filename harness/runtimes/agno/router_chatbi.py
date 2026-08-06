"""``/api/chatbi/v1/*`` router for the Agno target (module 5, MR-D2/MR-D3).

Implements the impl-doc §8.2 endpoint table on top of the Agno workflow:

| Method | Path                                   | responsibility              |
| ------ | -------------------------------------- | --------------------------- |
| POST   | /workflows/{id}/runs                   | start run (SSE)             |
| POST   | /workflows/{id}/runs/{run_id}/continue | resume (approval re-verify FIRST) |
| POST   | /workflows/{id}/runs/{run_id}/cancel   | cancel                      |
| GET    | /runs/{run_id}                         | Kernel-derived terminal state (ADR-002) |
| GET    | /runs                                  | run list + cursor pagination |
| GET    | /runs/{run_id}/events?cursor=          | event replay                |
| GET    | /sessions/{session_id}                 | session view + evidence index |
| POST   | /evidence/reindex                      | rebuild index from .chatbi  |
| GET    | /approvals?status=                     | approval list (superuser)   |
| POST   | /approvals/{id}/resolve                | superuser resolve (ChatBI constraints) |
| GET    | /capabilities                          | probe manifest              |

ChatBI constraints live in this adapter; every judgment calls the Kernel
(invariant 2). Approval subjects come ONLY from the trusted auth resolver
(JWT sub or an equivalent verified server-side resolver — the endpoint never
accepts a subject from the body; a missing/unverifiable subject is
fail-closed). MVP single superuser (adjudication five).

The run controller keeps the product run state (Kernel-derived terminal
state, ADR-002) in the runtime state dir; standard events are persisted by
``events.EventLog`` (replayable by cursor, deduplicable by event_id) and the
``.chatbi`` Evidence tree is the governance authority (ADR-003).

Applicable rules: MR-005, ADR-002/003, SEM-003, SEC-001, HOOK-001, PORT-001,
invariant 2/3/5.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from chatbi_governance.config import load_effective_config
from chatbi_governance.evidence import (
    EvidenceEntry,
    GateError,
    compute_candidate_sha,
    validate_request,
)
from chatbi_governance.gates import GateDecision
from chatbi_governance.harness_state import _safe_session_id
from chatbi_runtime_contract.events import is_delivery_completion
from chatbi_runtime_contract.types import RunState

from .approvals import ChatBIApprovalCoordinator
from .config import DeploymentConfig, load_deployment_config
from .events import EventLog, iter_standard_events
from .evidence_index import EvidenceIndex
from .probe import probe_agno
from .workflow_registry import ALL_WORKFLOW_IDS, workflow_approval_action

#: All nine workflows the agno target implements after module 6 (stage E).
#: analyze (module 5) + the eight generic IR workflows (workflow_registry).
SUPPORTED_WORKFLOWS = ALL_WORKFLOW_IDS

#: Envelope charset for run/session ids is enforced by the contract validator.
def _new_run_id() -> str:
    return uuid.uuid4().hex[:32]


#: Trusted authenticated subject. Defined in ``runtimes.agno.auth`` (kept
#: fastapi-free so the auth boundary imports on the system python during
#: build-product validation); re-exported here for the router surface.
from .auth import AuthSubject  # noqa: E402


#: Injectable trusted-auth resolver: ``(Request) -> AuthSubject | None``.
#: ``None`` = authentication is unavailable -> approvals fail closed.
AuthResolver = Callable[[Request], AuthSubject | None]


def _stub_auth_resolver(subject: str) -> AuthResolver:
    """Spike/test stub authentication (never used for production; the
    deployment must wire a verified JWT boundary, module 6)."""

    def _resolve(_request: Request) -> AuthSubject | None:
        if not subject:
            return None
        return AuthSubject(subject=subject, is_agent=False)

    return _resolve


def _intent_sha(action_type: str, actor: str, request: Mapping[str, Any]) -> str:
    """Deterministic candidate SHA binding a protected-action intent."""
    return compute_candidate_sha(
        {"action": action_type, "actor": actor, "request": dict(request)}
    )


def _preflight_request(request: Mapping[str, Any], workflow_id: str) -> None:
    """Kernel preflight before any run resource is created (fail-closed).

    ``chatbi-analyze`` validates the request schema with the Kernel
    ``validate_request`` (module-5 contract). The eight other workflows
    validate inside their IR first steps (parse/args/request steps call the
    Kernel); this shape check refuses malformed bodies early so no run
    resource is created for garbage input.
    """
    if workflow_id == "chatbi-analyze":
        try:
            validate_request(request)
        except GateError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "request rejected by the governance kernel",
                    "decision": error.decision.to_dict(),
                },
            ) from error
    elif not isinstance(request, Mapping) or not request:
        raise HTTPException(
            status_code=422,
            detail="request body must be a non-empty object",
        )


def _decision_from_error_events(agno_events: list[Any]) -> dict | None:
    """A workflow step that raised a kernel ``GateError`` yields an error
    event whose message IS the GateDecision JSON (GateError.__str__). Recover
    the decision so the terminal state carries the real rule_ids (HOOK-001)."""
    for ev in agno_events:
        if getattr(ev, "event", None) not in ("WorkflowError", "StepError"):
            continue
        message = getattr(ev, "error", None) or ""
        if not isinstance(message, str):
            continue
        try:
            decision = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(decision, dict) and decision.get("status") in (
            "pass", "block", "warn",
        ):
            return decision
    return None


def _terminal_from_ctx(ctx: Mapping[str, Any], run_status: Any,
                       raised: BaseException | None = None) -> tuple[str, dict | None]:
    """Derive the ChatBI terminal state from the Kernel context (ADR-002).

    Returns ``(final_status, delivery_decision_dict | None)`` where
    final_status ∈ completed | blocked | stopped | failed | paused.

    A run whose delivery gate did not PASS ends ``blocked`` — never
    ``completed`` — even when the Agno runtime reports completed.
    """
    if ctx.get("stop"):
        return "stopped", None
    delivery = ctx.get("delivery")
    if isinstance(delivery, Mapping) and delivery.get("status") == "pass":
        return "completed", delivery
    if isinstance(delivery, Mapping) and delivery.get("status") == "block":
        return "blocked", delivery
    if isinstance(ctx.get("policy_decision"), Mapping) and (
        ctx["policy_decision"].get("status") == "block"
    ):
        return "blocked", ctx["policy_decision"]
    if raised is not None:
        if isinstance(raised, GateError):
            return "blocked", raised.decision.to_dict()
        return "failed", None
    if run_status is not None and getattr(run_status, "value", run_status) == "failed":
        return "failed", None
    return "paused", None


class ChatBIRunController:
    """Run lifecycle owner: preflight -> run.created -> workflow run -> event
    mapping -> Kernel-derived terminal state -> approval bridge -> continue."""

    def __init__(
        self,
        *,
        workflow: Any,
        workflows: dict[str, Any] | None = None,
        deployment: DeploymentConfig,
        workspace_root: Path,
        state_dir: Path,
        event_log: EventLog,
        evidence_index: EvidenceIndex,
        coordinator: ChatBIApprovalCoordinator,
        harness_release: str,
        config: Any,
        approval_action_type: str | None = None,
        approval_actions: dict[str, str | None] | None = None,
        approval_step_ids: dict[str, str] | None = None,
        rate_limiter: Any = None,
        monitoring: Any = None,
    ) -> None:
        self.workflow = workflow
        #: workflow_id -> agno Workflow (all nine after module 6).
        self.workflows = workflows or {"chatbi-analyze": workflow}
        self.deployment = deployment
        self.workspace_root = Path(workspace_root).resolve()
        self.state_dir = Path(state_dir)
        self.event_log = event_log
        self.evidence_index = evidence_index
        self.coordinator = coordinator
        self.harness_release = harness_release
        self.config = config
        self.approval_action_type = approval_action_type
        #: workflow_id -> protected action of its IR human_approval step
        #: (None when the workflow declares none; analyze uses the module-5
        #: deployment seam value).
        self.approval_actions = approval_actions or {}
        #: workflow_id -> step id of the human_approval step.
        self.approval_step_ids = approval_step_ids or {}
        #: Rate limiter (module 6; disabled policy = allow everything).
        self.rate_limiter = rate_limiter
        #: Monitoring hook points (module 6; NullHooks default).
        self.monitoring = monitoring
        self._runs_dir = self.state_dir / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._paused_outputs: dict[str, Any] = {}
        self._ctxs: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Run record (product state)
    # ------------------------------------------------------------------

    def _run_path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not run_id or "/" in run_id:
            raise ValueError(f"invalid run_id: {run_id!r}")
        return self._runs_dir / f"{run_id}.json"

    def _save_run(self, record: Mapping[str, Any]) -> None:
        payload = (
            json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        path = self._run_path(record["run_id"])
        tmp = path.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        path = self._run_path(run_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def list_runs(
        self, cursor: int | str | None = 0, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Run list + cursor pagination (module 6 hardening).

        ``cursor`` may be an int (legacy, created_at only) or the composite
        ``"<created_at>:<run_id>"`` emitted by the router — same-second runs
        paginate without skips or duplicates (ordering = (created_at,
        run_id), deterministic)."""
        records: list[tuple[int, str, dict[str, Any]]] = []
        for path in sorted(self._runs_dir.glob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            record = self.get_run(path.stem)
            if record is None:
                continue
            created = int(record.get("created_at", 0))
            records.append((created, record.get("run_id", ""), record))
        records.sort(key=lambda item: (item[0], item[1]))
        if isinstance(cursor, str) and ":" in cursor:
            parts = cursor.split(":", 1)
            try:
                cursor_created = int(parts[0])
            except ValueError:
                cursor_created = 0
            cursor_run_id = parts[1]
            filtered = [
                record for created, run_id, record in records
                if (created, run_id) > (cursor_created, cursor_run_id)
            ]
        else:
            cursor_created = int(cursor or 0)
            filtered = [
                record for created, _run_id, record in records
                if created > cursor_created
            ]
        return filtered[:limit]

    def _workflow_for(self, run_id: str) -> Any:
        """The agno Workflow that owns a run (module 6: one per workflow id)."""
        record = self.get_run(run_id) or {}
        workflow_id = record.get("workflow_id") or "chatbi-analyze"
        return self.workflows.get(workflow_id) or self.workflow

    # ------------------------------------------------------------------
    # Evidence + ctx callbacks
    # ------------------------------------------------------------------

    def record_evidence(self, run_id: str, step_id: str,
                        entry: EvidenceEntry) -> None:
        """Persist one EvidenceEntry under .chatbi and index it (ADR-003).
        The session id is resolved from the run record (single writer)."""
        if not run_id:
            return
        record = self.get_run(run_id)
        if record is None:
            return
        safe_sid = record["session_id"]
        import chatbi_governance.harness_state as _hs

        written = _hs.write_state(
            self.workspace_root, safe_sid,
            f"evidence-{step_id}-{run_id[:8]}.json", entry.to_dict(),
        )
        self.evidence_index.add(written)

    def record_ctx(self, run_id: str, ctx: Mapping[str, Any]) -> None:
        """Workflow context sink: the steps report their full context after
        every step (run_id threaded via agno's session_state)."""
        if not run_id:
            return
        self._ctxs[run_id] = dict(ctx)

    def record_tool(self, run_id: str, step_id: str, tool_name: str,
                    allowed: bool) -> None:
        """Tool-call events from the agent-step allowlist boundary (MAJOR-2):
        allowed -> tool.requested + tool.completed; blocked -> tool.blocked
        (the call was refused by the IR allowlist and never executed)."""
        if not run_id:
            return
        record = self.get_run(run_id)
        if record is None:
            return
        session_id = record["session_id"]
        index = self.event_log.next_index(run_id)
        event_type = "tool.blocked" if not allowed else "tool.requested"
        self._emit({
            "schema_version": "chatbi.event/v1",
            "event_id": f"evt_{run_id}_{index}",
            "event_index": index,
            "trace_id": f"tr_{run_id}",
            "session_id": session_id,
            "run_id": run_id,
            "workflow_id": record["workflow_id"],
            "step_id": step_id,
            "event_type": event_type,
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runtime": {"name": "agno", "native_run_id": run_id},
            "payload": {"tool": tool_name,
                        "reason": "not on the IR allowlist" if not allowed
                        else "allowlisted"},
            "evidence_refs": [],
        })
        if allowed:
            index = self.event_log.next_index(run_id)
            self._emit({
                "schema_version": "chatbi.event/v1",
                "event_id": f"evt_{run_id}_{index}",
                "event_index": index,
                "trace_id": f"tr_{run_id}",
                "session_id": session_id,
                "run_id": run_id,
                "workflow_id": record["workflow_id"],
                "step_id": step_id,
                "event_type": "tool.completed",
                "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runtime": {"name": "agno", "native_run_id": run_id},
                "payload": {"tool": tool_name, "reason": "allowlisted"},
                "evidence_refs": [],
            })

    def _emit(self, envelope: Mapping[str, Any]) -> None:
        self.event_log.append(envelope)

    # ------------------------------------------------------------------
    # Start / continue / cancel
    # ------------------------------------------------------------------

    def start_run(
        self,
        *,
        request: Mapping[str, Any],
        workflow_id: str,
        session_id: str | None = None,
        actor: str = "operator",
        purpose: str = "",
        scenario_id: str = "run",
        policy_request_type: str | None = None,
    ) -> dict[str, Any]:
        """Preflight -> run.created -> workflow run -> terminal state.

        All nine IR workflows are dispatched here (module 6); the Kernel
        preflight is per workflow (analyze validates the request schema; the
        other workflows validate inside their IR steps — fail-closed before
        any run resource is created).
        """
        if workflow_id not in SUPPORTED_WORKFLOWS:
            raise HTTPException(
                status_code=404,
                detail=f"workflow {workflow_id!r} is not implemented on the "
                       "agno target",
            )
        _preflight_request(request, workflow_id)
        if self.rate_limiter is not None and not self.rate_limiter.allow(
            actor or "anonymous"
        ):
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded (fail-closed)",
            )
        workflow = self.workflows[workflow_id]

        run_id = _new_run_id()
        if not session_id:
            session_id = f"ses_{run_id}"
        safe_sid = _safe_session_id(session_id)
        created_at = int(time.time())

        record: dict[str, Any] = {
            "run_id": run_id,
            "session_id": safe_sid,
            "workflow_id": workflow_id,
            "status": RunState.CREATED.value,
            "final_status": None,
            "created_at": created_at,
            "actor": actor,
            "purpose": purpose,
            "scenario_id": scenario_id,
        }
        self._save_run(record)

        # run.created (event_index 0).
        self._emit({
            "schema_version": "chatbi.event/v1",
            "event_id": f"evt_{run_id}_0",
            "event_index": 0,
            "trace_id": f"tr_{run_id}",
            "session_id": safe_sid,
            "run_id": run_id,
            "workflow_id": workflow_id,
            "step_id": None,
            "event_type": "run.created",
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_at)),
            "runtime": {"name": "agno", "native_run_id": run_id},
            "payload": {"actor": actor},
            "evidence_refs": [],
        })
        self.monitoring.run_started(run_id, workflow_id, actor)

        self._ctxs[run_id] = {}
        run_input: dict[str, Any] = {
            "request": dict(request),
            "chatbi_scenario": scenario_id,
            "chatbi_policy_request_type": policy_request_type or "discover",
            "run_id": run_id,
            "chatbi_session_id": safe_sid,
        }

        raised: BaseException | None = None
        agno_events: list[Any] = []
        try:
            # Streaming execution: the iterator yields every WorkflowRunEvent
            # (step started/completed, paused, error, completed) — the raw
            # material for the standard-event mapping. A paused run ends the
            # iterator after the pause events.
            for event in workflow.run(
                input=run_input,
                run_id=run_id,
                session_id=safe_sid,
                stream=True,
                stream_events=True,
            ):
                agno_events.append(event)
        except Exception as error:  # noqa: BLE001 - kernel/agno failure -> fail-closed
            raised = error

        saw_pause = any(
            getattr(ev, "event", None) in ("WorkflowPaused", "StepPaused")
            for ev in agno_events
        )
        saw_error = any(
            getattr(ev, "event", None) in ("WorkflowError", "StepError")
            for ev in agno_events
        )
        ctx = self._ctxs.get(run_id, {})
        # Per-IR approval decision (module 6): the workflow's human_approval
        # step pauses when its ``owner.pending(<action>)`` condition holds.
        # analyze keeps the module-5 deployment seam (approval_action_type).
        approval_action = self.approval_actions.get(workflow_id)
        needs_approval = approval_action is not None and (
            workflow_id == "chatbi-analyze"
            or bool(ctx.get("protected_pending"))
        )
        auto_resume = raised is None and saw_pause and not needs_approval

        # Map the agno events into standard envelopes (persisted + returned).
        # An auto-resumed (condition-false) pause never surfaces run.paused.
        standard_events = iter_standard_events(
            agno_events,
            session_id=safe_sid,
            run_id=run_id,
            workflow_id=workflow_id,
            event_log=self.event_log,
            exclude_event_types=("run.paused",) if auto_resume else (),
        )
        # Dedup: a confirmation StepPaused is followed by WorkflowPaused;
        # surface one run.paused (the approval bridge) per pause cycle.
        deduped: list[dict[str, Any]] = []
        paused_seen = False
        for event in standard_events:
            if event["event_type"] == "run.paused":
                if paused_seen:
                    continue
                paused_seen = True
            deduped.append(event)

        # Recover the persisted run output (db-backed) for pause/continue.
        try:
            run_output = workflow.get_run_output(run_id=run_id, session_id=safe_sid)
        except Exception:  # noqa: BLE001
            run_output = None
        run_status = None if raised is not None else getattr(run_output, "status", None)

        if raised is None and saw_pause:
            if needs_approval:
                # The workflow paused for human approval (approval bridge).
                self._bridge_approval(
                    run_id=run_id, session_id=safe_sid, workflow_id=workflow_id,
                    request=request, actor=actor,
                    step_id=self.approval_step_ids.get(
                        workflow_id, "human_approval"),
                )
                self._paused_outputs[run_id] = run_output
                record["status"] = RunState.PAUSED.value
                record["final_status"] = "paused"
                self._save_run(record)
                self.monitoring.run_terminal(run_id, workflow_id, "paused")
                return {"run_id": run_id, "session_id": safe_sid,
                        "status": "paused", "final_status": "paused",
                        "events": deduped}
            # Auto-resume (registered target extension, module 6): the IR
            # owner.pending condition is false — the engine-level pause is
            # confirmed and continued WITHOUT approval (no approval events,
            # no run.resumed, no run.paused). The resumed steps then flow to
            # the delivery gate.
            try:
                resumed = self._auto_continue_pause(
                    run_id=run_id, session_id=safe_sid,
                    workflow_id=workflow_id, run_output=run_output,
                )
            except Exception as error:  # noqa: BLE001 - fail-closed
                raised = error
            else:
                standard_events = [*deduped, *resumed["standard_events"]]
                agno_events = [*agno_events, *resumed["agno_events"]]
                ctx = self._ctxs.get(run_id, {})
                run_status = resumed["run_status"]

        final_status, delivery = _terminal_from_ctx(ctx, run_status, raised)
        if raised is None and not ctx.get("stop") and final_status in (
            "paused", "failed",
        ):
            # Recover a kernel GateDecision that a step raised (its JSON is
            # the error message) — fail-closed instead of an opaque failure.
            decision = _decision_from_error_events(agno_events)
            if decision is not None and decision.get("status") == "block":
                final_status = "blocked"
                delivery = decision
            elif not saw_error and final_status == "paused":
                # No delivery decision and not stopped: the run completed
                # without a gate verdict — fail-closed (ADR-002).
                final_status = "blocked"
                delivery = GateDecision.block(
                    rule_ids=("HOOK-004", "REV-003"),
                    evidence_refs=("evidence:delivery-gate",),
                    reason=(
                        "workflow finished without a delivery gate verdict; "
                        "a runtime completion is never ChatBI completion "
                        "(ADR-002)"
                    ),
                    recovery="inspect the run events and re-run the governed flow",
                ).to_dict()

        # The response event list: deduped start events (or, for an
        # auto-resumed pause, the continued segment too).
        response_events = deduped
        if auto_resume:
            response_events = standard_events

        if final_status == "completed":
            decision_payload = {
                "gate": "delivery",
                "decision": "pass",
                "rule_ids": list(delivery.get("rule_ids", ())) if delivery else [],
            }
            index = self.event_log.next_index(run_id)
            completed = {
                "schema_version": "chatbi.event/v1",
                "event_id": f"evt_{run_id}_{index}",
                "event_index": index,
                "trace_id": f"tr_{run_id}",
                "session_id": safe_sid,
                "run_id": run_id,
                "workflow_id": workflow_id,
                "step_id": "delivery_gate",
                "event_type": "run.completed",
                "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runtime": {"name": "agno", "native_run_id": run_id},
                "payload": decision_payload,
                "evidence_refs": [],
            }
            if not is_delivery_completion(completed):
                raise RuntimeError("delivery completion violates the contract")
            self._emit(completed)
            response_events.append(completed)
            record["status"] = RunState.COMPLETED.value
            record["final_status"] = "completed"
            record["delivery_decision"] = delivery
        elif final_status == "blocked":
            decision_payload = {
                "gate": "delivery",
                "decision": "block",
                "rule_ids": list(delivery.get("rule_ids", ())) if delivery else [],
            }
            index = self.event_log.next_index(run_id)
            blocked = {
                "schema_version": "chatbi.event/v1",
                "event_id": f"evt_{run_id}_{index}",
                "event_index": index,
                "trace_id": f"tr_{run_id}",
                "session_id": safe_sid,
                "run_id": run_id,
                "workflow_id": workflow_id,
                "step_id": "delivery_gate",
                "event_type": "gate.blocked",
                "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runtime": {"name": "agno", "native_run_id": run_id},
                "payload": {
                    "gate": "delivery",
                    "decision": dict(delivery) if isinstance(delivery, Mapping) else {},
                },
                "evidence_refs": [],
            }
            self._emit(blocked)
            response_events.append(blocked)
            record["status"] = RunState.BLOCKED.value
            record["final_status"] = "blocked"
            record["delivery_decision"] = delivery
        else:
            record["status"] = RunState.FAILED.value if final_status == "failed" else (
                RunState.BLOCKED.value if final_status == "blocked" else "stopped"
            )
            record["final_status"] = final_status
            if raised is not None:
                record["error"] = f"{type(raised).__name__}: {raised}"

        self._save_run(record)
        if raised is not None:
            self.monitoring.error(run_id, workflow_id, str(raised)[:512])
        self.monitoring.run_terminal(run_id, workflow_id, final_status)
        return {"run_id": run_id, "session_id": safe_sid,
                "status": record["status"], "final_status": final_status,
                "events": response_events}

    def _auto_continue_pause(
        self, *, run_id: str, session_id: str, workflow_id: str,
        run_output: Any,
    ) -> dict[str, Any]:
        """Auto-resume an approval-step engine pause whose IR
        ``owner.pending`` condition is false (module-6 registered extension).

        The agno 2.6.22 engine pauses UNCONDITIONALLY for a
        ``requires_confirmation`` step, so the adapter confirms the
        requirement and continues WITHOUT creating any approval (no
        approval.* events, no ``run.resumed``, and ``run.paused`` was
        excluded from the standard stream). The resumed steps flow to the
        delivery gate; the Kernel remains the terminal authority (ADR-002).
        """
        workflow = self.workflows[workflow_id]
        requirements = list(run_output.step_requirements or [])
        for req in requirements:
            if getattr(req, "requires_confirmation", False) or getattr(
                req, "needs_confirmation", False
            ):
                req.confirm()
        pre_pause_ctx = self._ctxs.get(run_id)
        if isinstance(pre_pause_ctx, dict):
            try:
                session = workflow.get_session(session_id=session_id)
                if session is not None:
                    session_data = session.session_data or {}
                    session_state = session_data.setdefault("session_state", {})
                    session_state["_ctx"] = pre_pause_ctx
                    workflow.save_session(session=session)
            except Exception:  # noqa: BLE001 - in-memory ctx still flows
                pass
        agno_events: list[Any] = []
        for event in workflow.continue_run(
            run_response=run_output,
            run_id=run_id,
            session_id=session_id,
            stream=True,
            stream_events=True,
        ):
            agno_events.append(event)
        standard_events = iter_standard_events(
            agno_events,
            session_id=session_id,
            run_id=run_id,
            workflow_id=workflow_id,
            event_log=self.event_log,
        )
        try:
            final_output = workflow.get_run_output(
                run_id=run_id, session_id=session_id
            )
        except Exception:  # noqa: BLE001
            final_output = None
        return {
            "agno_events": agno_events,
            "standard_events": standard_events,
            "run_status": getattr(final_output, "status", None),
        }

    def _bridge_approval(
        self, *, run_id: str, session_id: str, workflow_id: str,
        request: Mapping[str, Any], actor: str, step_id: str = "human_approval",
    ) -> None:
        """Bridge a workflow pause to the ChatBI ApprovalCoordinator.

        The protected action comes from the workflow's IR human_approval
        step (``owner.pending(<action>)``) for the eight module-6 workflows,
        or from the module-5 deployment seam (``approval_action_type``) for
        analyze — never from the request body (adjudication five).
        """
        action_type = self.approval_actions.get(workflow_id) or (
            self.approval_action_type
        )
        if not action_type:
            return
        candidate_sha = _intent_sha(action_type, actor, request)
        from .approvals import ApprovalContext

        context = ApprovalContext(
            workflow_id=workflow_id, run_id=run_id, session_id=session_id,
            step_id=step_id,
        )
        self.coordinator.request_approval(
            context=context,
            action_type=action_type,
            requester_subject=actor,
            candidate_sha=candidate_sha,
            evidence_refs=(
                # The Evidence file name mirrors the coordinator's persistence
                # (approval-<approval_id>.json where approval_id =
                # ap_<run_id>_<step_id>) so resolve's evidence re-verification
                # (ADR-003) can locate the exact file.
                f".chatbi/runs/{_safe_session_id(session_id)}/approval-"
                f"ap_{run_id}_{step_id}.json",
            ),
            reason=f"Protected action {action_type} requires human owner approval",
        )

    def continue_run(
        self, *, run_id: str, session_id: str, workflow_id: str,
    ) -> dict[str, Any]:
        """Resume a paused run. The CALLER must have run the Kernel approval
        re-verification first (先验后续 — the router does this before calling
        this method); this method only confirms the resolved requirement and
        continues the Agno run.

        Idempotent (MAJOR-1 fix): when the approval resolve already continued
        the run through its on_approved hook (or the run is already
        terminal), this returns the CURRENT state + the replayed standard
        events instead of failing — a client that calls /continue after a
        successful resolve gets the final state, never a 409."""
        record = self.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        if record.get("final_status") in (
            "completed", "blocked", "failed", "cancelled",
        ):
            replay = self.event_log.replay(run_id)
            return {
                "run_id": run_id,
                "session_id": session_id,
                "status": record["status"],
                "final_status": record["final_status"],
                "events": list(replay.events),
            }
        workflow = self._workflow_for(run_id)
        paused_output = self._paused_outputs.get(run_id)
        if paused_output is None:
            try:
                paused_output = workflow.get_run_output(
                    run_id=run_id, session_id=session_id
                )
            except Exception:  # noqa: BLE001
                paused_output = None
        if paused_output is None or not getattr(paused_output, "is_paused", False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "the run is not paused or its paused state is unavailable; "
                    "re-run the workflow (MVP single instance, design §10.2)"
                ),
            )
        # Confirm the active confirmation requirement (the approval gate).
        requirements = list(paused_output.step_requirements or [])
        active = [req for req in requirements if not req.is_resolved]
        for req in active:
            if getattr(req, "requires_confirmation", False) or getattr(
                req, "needs_confirmation", False
            ):
                req.confirm()

        # Re-seed the persisted session state with the pre-pause context:
        # agno's paused-session snapshot can lag the in-memory merges (the
        # context steps already executed), so inject the controller's ctx so
        # the resumed steps continue the same evidence chain (design §6.3
        # resume semantics).
        pre_pause_ctx = self._ctxs.get(run_id)
        if isinstance(pre_pause_ctx, dict):
            try:
                session = workflow.get_session(session_id=session_id)
                if session is not None:
                    session_data = session.session_data or {}
                    session_state = session_data.setdefault("session_state", {})
                    session_state["_ctx"] = pre_pause_ctx
                    workflow.save_session(session=session)
            except Exception:  # noqa: BLE001 - in-memory ctx still flows
                pass
        # LOW-3 fix: run.resumed is persisted FIRST (approval re-verification
        # already passed — 先验后续), then the resumed steps' events follow;
        # the persisted stream therefore reads resume -> steps -> terminal.
        index = self.event_log.next_index(run_id)
        resumed_event = {
            "schema_version": "chatbi.event/v1",
            "event_id": f"evt_{run_id}_{index}",
            "event_index": index,
            "trace_id": f"tr_{run_id}",
            "session_id": session_id,
            "run_id": run_id,
            "workflow_id": workflow_id,
            "step_id": None,
            "event_type": "run.resumed",
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runtime": {"name": "agno", "native_run_id": run_id},
            "payload": {"reason": "approval resolved"},
            "evidence_refs": [],
        }
        self._emit(resumed_event)

        agno_events: list[Any] = []
        try:
            for event in workflow.continue_run(
                run_response=paused_output,
                run_id=run_id,
                session_id=session_id,
                stream=True,
                stream_events=True,
            ):
                agno_events.append(event)
        except Exception as error:  # noqa: BLE001 - fail-closed
            record = self.get_run(run_id) or {}
            record["status"] = RunState.FAILED.value
            record["final_status"] = "failed"
            record["error"] = f"{type(error).__name__}: {error}"
            self._save_run(record)
            raise HTTPException(
                status_code=409,
                detail={"error": "continue failed", "reason": str(error)},
            ) from error
        self._paused_outputs.pop(run_id, None)
        standard_events = iter_standard_events(
            agno_events,
            session_id=session_id,
            run_id=run_id,
            workflow_id=workflow_id,
            event_log=self.event_log,
        )
        events = [resumed_event, *standard_events]

        ctx = self._ctxs.get(run_id, {})
        final_status, delivery = _terminal_from_ctx(ctx, None, None)
        record = self.get_run(run_id) or {
            "run_id": run_id, "session_id": session_id,
            "workflow_id": workflow_id, "created_at": int(time.time()),
            "actor": "", "purpose": "",
        }
        if final_status == "completed":
            index = self.event_log.next_index(run_id)
            completed = {
                "schema_version": "chatbi.event/v1",
                "event_id": f"evt_{run_id}_{index}",
                "event_index": index,
                "trace_id": f"tr_{run_id}",
                "session_id": session_id,
                "run_id": run_id,
                "workflow_id": workflow_id,
                "step_id": "delivery_gate",
                "event_type": "run.completed",
                "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runtime": {"name": "agno", "native_run_id": run_id},
                "payload": {
                    "gate": "delivery", "decision": "pass",
                    "rule_ids": list(delivery.get("rule_ids", ())) if delivery else [],
                },
                "evidence_refs": [],
            }
            self._emit(completed)
            events.append(completed)
            record["status"] = RunState.COMPLETED.value
            record["final_status"] = "completed"
        elif final_status == "blocked":
            index = self.event_log.next_index(run_id)
            blocked = {
                "schema_version": "chatbi.event/v1",
                "event_id": f"evt_{run_id}_{index}",
                "event_index": index,
                "trace_id": f"tr_{run_id}",
                "session_id": session_id,
                "run_id": run_id,
                "workflow_id": workflow_id,
                "step_id": "delivery_gate",
                "event_type": "gate.blocked",
                "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runtime": {"name": "agno", "native_run_id": run_id},
                "payload": {"gate": "delivery", "decision": dict(delivery) if isinstance(delivery, Mapping) else {}},
                "evidence_refs": [],
            }
            self._emit(blocked)
            events.append(blocked)
            record["status"] = RunState.BLOCKED.value
            record["final_status"] = "blocked"
        else:
            record["status"] = RunState.BLOCKED.value
            record["final_status"] = final_status
        self._save_run(record)
        return {"run_id": run_id, "session_id": session_id,
                "status": record["status"], "final_status": final_status,
                "events": events}

    def cancel_run(self, *, run_id: str, session_id: str, workflow_id: str) -> dict[str, Any]:
        record = self.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        index = self.event_log.next_index(run_id)
        cancelled = {
            "schema_version": "chatbi.event/v1",
            "event_id": f"evt_{run_id}_{index}",
            "event_index": index,
            "trace_id": f"tr_{run_id}",
            "session_id": session_id,
            "run_id": run_id,
            "workflow_id": workflow_id,
            "step_id": None,
            "event_type": "run.paused",
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runtime": {"name": "agno", "native_run_id": run_id},
            "payload": {"cancelled": True, "reason": "cancelled by operator"},
            "evidence_refs": [],
        }
        self._emit(cancelled)
        self._paused_outputs.pop(run_id, None)
        record["status"] = RunState.CANCELLED.value
        record["final_status"] = "cancelled"
        self._save_run(record)
        return {"run_id": run_id, "status": RunState.CANCELLED.value,
                "final_status": "cancelled", "event": cancelled}


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def get_chatbi_router(
    *,
    config_path: str | Path | None = None,
    prefix: str = "",
    workflows_dir: str | Path,
    db: Any = None,
    workspace_root: str | Path | None = None,
    harness_release: str = "dev",
    auth_resolver: AuthResolver | None = None,
    agent_runner: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_runner: Any = None,
    native_runner: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    approval_action_type: str | None = None,
    main_agent: Any = None,
    harness_config_path: str | Path | None = None,
    local_config_path: str | Path | None = None,
) -> tuple[APIRouter, dict[str, Any]]:
    """Build the ``/api/chatbi/v1`` router + shared runtime wiring.

    Returns ``(router, components)`` where ``components`` exposes the
    controller/coordinator/index for tests and the app factory.

    Trusted auth (module 6): when the deployment config sets
    ``auth_mode == "jwt"`` and no resolver is injected, the verified JWT
    boundary (``runtimes.agno.auth``) is wired — a missing secret refuses
    startup (fail-closed, MR-005). The ``stub`` mode remains the explicit
    spike/test boundary only.
    """
    deployment = load_deployment_config(config_path)
    if auth_resolver is None and deployment.auth_mode == "jwt":
        from .auth import (  # noqa: PLC0415
            jwt_secret_from_deployment,
            make_jwt_auth_resolver,
        )

        secret = jwt_secret_from_deployment(deployment)
        if not secret:
            raise RuntimeError(
                "auth_mode 'jwt' requires a JWT secret (deployment config "
                "jwt_secret or CHATBI_JWT_SECRET); refusing to start without "
                "a trusted auth boundary (fail-closed, MR-005)"
            )
        auth_resolver = make_jwt_auth_resolver(
            secret=secret,
            superuser_subject=deployment.superuser_subject,
        )
    if workspace_root is None:
        workspace_root = Path.cwd()
    ws = Path(workspace_root).resolve()
    state_dir = ws / deployment.state_dir_name
    state_dir.mkdir(parents=True, exist_ok=True)

    event_log = EventLog(state_dir)
    evidence_index = EvidenceIndex(ws, state_dir)

    # Product-state database (Agno SqliteDb): sessions/runs/events are
    # persisted here; the .chatbi Evidence tree stays the governance
    # authority (ADR-003). The workflow needs the db so a paused run can be
    # reloaded and continued (design §10.1).
    if db is None:
        from . import ensure_agno_unshadowed

        ensure_agno_unshadowed()
        from agno.db.sqlite import SqliteDb  # noqa: PLC0415

        db = SqliteDb(db_file=str(state_dir / "agno.db"))

    from .config import ENV_MODEL, ENV_BASE_URL, ENV_API_KEY  # noqa: PLC0415

    model_config = None
    try:
        model_config = deployment.model_config("default")
    except RuntimeError:
        model_config = None

    config = None
    shared_path = harness_config_path
    if shared_path is None:
        candidate = ws / ".claude" / "chatbi-harness.json"
        if candidate.is_file():
            shared_path = candidate
    if shared_path is not None and Path(shared_path).is_file():
        config = load_effective_config(
            Path(shared_path),
            Path(local_config_path) if local_config_path else None,
        )

    coordinator = ChatBIApprovalCoordinator(
        workspace_root=ws,
        state_dir=state_dir,
        deployment=deployment,
        evidence_index=evidence_index,
        event_log=event_log,
        harness_release=harness_release,
        config=config,
    )

    # Module 6 hardening: rate limiting + monitoring hook points (policy from
    # the deployment config; disabled/null by default — honest FBK-003).
    from .observability import (  # noqa: PLC0415
        MonitoringHooks,
        RateLimitPolicy,
        SlidingWindowRateLimiter,
    )

    rate_policy = RateLimitPolicy.from_config(deployment.rate_limit)
    rate_limiter = SlidingWindowRateLimiter(rate_policy) if rate_policy.enabled else None
    monitoring = MonitoringHooks.null()

    controller = ChatBIRunController(
        workflow=None,
        workflows=None,
        deployment=deployment,
        workspace_root=ws,
        state_dir=state_dir,
        event_log=event_log,
        evidence_index=evidence_index,
        coordinator=coordinator,
        harness_release=harness_release,
        config=config,
        approval_action_type=approval_action_type,
        rate_limiter=rate_limiter,
        monitoring=monitoring,
    )

    from .workflow_registry import build_all_workflows  # noqa: PLC0415

    workflows = build_all_workflows(
        workflows_dir=workflows_dir,
        config=config,
        agent_runner=agent_runner,
        reviewer_runner=reviewer_runner,
        native_runner=native_runner,
        on_evidence=controller.record_evidence,
        on_tool=controller.record_tool,
        on_ctx=controller.record_ctx,
        harness_release=harness_release,
        db=db,
        deployment=deployment,
        workspace_root=ws,
        harness_config_path=shared_path,
        local_config_path=local_config_path,
        main_agent=main_agent,
        approval_action_type=approval_action_type,
    )
    workflow_by_id = {w.id: w for w in workflows}
    workflow = workflow_by_id["chatbi-analyze"]

    # Per-workflow approval actions: the IR human_approval step's
    # ``owner.pending(<action>)`` (module 6); analyze keeps the module-5
    # deployment seam.
    from chatbi_harness_ir.loader import load_workflow  # noqa: PLC0415
    from .workflow_registry import _APPROVAL_STEP_IDS  # noqa: PLC0415

    approval_actions: dict[str, str | None] = {
        "chatbi-analyze": approval_action_type,
    }
    approval_step_ids: dict[str, str] = {"chatbi-analyze": "human_approval"}
    for wid in ALL_WORKFLOW_IDS:
        if wid == "chatbi-analyze":
            continue
        ir = load_workflow(Path(workflows_dir) / f"{wid}.yaml")
        approval_actions[wid] = workflow_approval_action(wid, ir)
        if wid in _APPROVAL_STEP_IDS:
            approval_step_ids[wid] = _APPROVAL_STEP_IDS[wid]

    controller.workflow = workflow
    controller.workflows = workflow_by_id
    controller.approval_actions = approval_actions
    controller.approval_step_ids = approval_step_ids
    # Approval PASS -> continue the run (先验后续: resolve already verified).
    coordinator.on_approved = (
        lambda record: _continue_after_approval(controller, record)
    )

    components = {
        "deployment": deployment,
        "event_log": event_log,
        "evidence_index": evidence_index,
        "coordinator": coordinator,
        "controller": controller,
        "workflow": workflow,
        "workflows": workflow_by_id,
    }
    router = _build_router(
        controller=controller,
        coordinator=coordinator,
        evidence_index=evidence_index,
        event_log=event_log,
        deployment=deployment,
        auth_resolver=auth_resolver,
        prefix=prefix,
    )
    return router, components


def _continue_after_approval(controller: ChatBIRunController, record: Any) -> None:
    """Approval PASS hook: continue the paused run (Kernel re-verification
    already passed inside ``resolve`` — 先验后续)."""
    parts = record.approval_id.split("_")
    if len(parts) < 3 or parts[0] != "ap":
        return
    run_id = parts[1]
    run_record = controller.get_run(run_id)
    if run_record is None:
        return
    controller.continue_run(
        run_id=run_id,
        session_id=run_record["session_id"],
        workflow_id=run_record["workflow_id"],
    )


def _build_router(
    *,
    controller: ChatBIRunController,
    coordinator: ChatBIApprovalCoordinator,
    evidence_index: EvidenceIndex,
    event_log: EventLog,
    deployment: DeploymentConfig,
    auth_resolver: AuthResolver | None,
    prefix: str = "",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["ChatBI"])

    @router.post("/workflows/{workflow_id}/runs")
    async def start_run(
        workflow_id: str,
        request: Request,
    ) -> Any:
        try:
            body = await request.json()
        except Exception as error:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"invalid JSON body: {error}")
        if not isinstance(body, Mapping):
            raise HTTPException(status_code=422, detail="request body must be an object")
        params = dict(request.query_params)
        result = controller.start_run(
            request=body,
            workflow_id=workflow_id,
            session_id=params.get("session_id"),
            actor=body.get("actor", "operator"),
            purpose=body.get("purpose", ""),
            scenario_id=params.get("scenario", "run"),
            policy_request_type=params.get("policy_request_type"),
        )
        return _sse_response(result.get("events", []))

    @router.post("/workflows/{workflow_id}/runs/{run_id}/continue")
    async def continue_run(workflow_id: str, run_id: str, request: Request) -> Any:
        run_record = controller.get_run(run_id)
        if run_record is None:
            raise HTTPException(status_code=404, detail="run not found")
        # 先验后续: Kernel approval re-verification runs BEFORE any continue.
        # MAJOR-1: the resolve may already have continued the run through its
        # on_approved hook; controller.continue_run is idempotent and returns
        # the current state + replayed events in that case.
        approval = _pending_approval_for_run(controller, coordinator, run_id)
        if approval is not None:
            subject = _trusted_subject(request, auth_resolver)
            current_sha = _recompute_intent_sha(controller, approval)
            if current_sha is None:
                raise HTTPException(
                    status_code=409,
                    detail={"outcome": "rejected",
                            "reason": "candidate SHA cannot be re-verified "
                                      "(run context unavailable)"},
                )
            result = coordinator.resolve(
                approval.approval_id,
                subject=subject,
                current_candidate_sha=current_sha,
            )
            if result.outcome != "approved":
                raise HTTPException(
                    status_code=409,
                    detail={"approval": result.approval.to_dict(),
                            "outcome": result.outcome, "reason": result.reason},
                )
        result = controller.continue_run(
            run_id=run_id,
            session_id=run_record["session_id"],
            workflow_id=run_record["workflow_id"],
        )
        return _sse_response(result.get("events", []))

    @router.post("/workflows/{workflow_id}/runs/{run_id}/cancel")
    async def cancel_run(workflow_id: str, run_id: str) -> Any:
        run_record = controller.get_run(run_id)
        if run_record is None:
            raise HTTPException(status_code=404, detail="run not found")
        return controller.cancel_run(
            run_id=run_id,
            session_id=run_record["session_id"],
            workflow_id=run_record["workflow_id"],
        )

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> Any:
        record = controller.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        return record

    @router.get("/runs")
    async def list_runs(
        cursor: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
    ) -> Any:
        records = controller.list_runs(cursor=cursor or 0, limit=limit)
        if records:
            last = records[-1]
            next_cursor = f"{last['created_at']}:{last['run_id']}"
        else:
            next_cursor = cursor or 0
        return {"runs": records, "next_cursor": next_cursor}

    @router.get("/runs/{run_id}/events")
    async def replay_events(
        run_id: str, cursor: int | None = Query(None, ge=0)
    ) -> Any:
        result = event_log.replay(run_id, cursor=cursor)
        return {
            "run_id": run_id,
            "events": list(result.events),
            "next_cursor": result.next_cursor,
        }

    @router.get("/sessions/{session_id}")
    async def session_view(session_id: str) -> Any:
        runs = [
            r for r in controller.list_runs(limit=500)
            if r.get("session_id") == session_id
        ]
        rows = evidence_index.lookup(session_id=session_id)
        return {
            "session_id": session_id,
            "run_ids": [r["run_id"] for r in runs],
            "evidence_refs": [row.path for row in rows],
            "evidence_index": [row.to_dict() for row in rows],
        }

    @router.post("/evidence/reindex")
    async def evidence_reindex() -> Any:
        path = evidence_index.build_index()
        rows = evidence_index.scan()
        return {"status": "ok", "rows": len(rows), "index": str(path)}

    @router.get("/approvals")
    async def list_approvals(request: Request,
                             status: str | None = Query(None)) -> Any:
        # MEDIUM-2: the approval list is superuser-only (adjudication five);
        # any other authenticated subject is refused (403), and a missing
        # trusted auth context is fail-closed (401/403).
        subject = _trusted_subject(request, auth_resolver)
        superuser = getattr(deployment, "superuser_subject", None)
        if not superuser or subject != superuser:
            raise HTTPException(
                status_code=403,
                detail="approvals are visible to the configured superuser only",
            )
        return {"approvals": [
            record.to_dict() for record in coordinator.list(status=status)
        ]}

    @router.post("/approvals/{approval_id}/resolve")
    async def resolve_approval(approval_id: str, request: Request) -> Any:
        subject = _trusted_subject(request, auth_resolver)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        # MEDIUM-3: the candidate SHA is recomputed server-side from the
        # run's stored request (the Kernel re-verifies the SHA against the
        # CURRENT intent, never against a client-supplied value).
        approval = coordinator.get(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        current_sha = _recompute_intent_sha(controller, approval)
        if current_sha is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "approval": approval.to_dict(),
                    "outcome": "rejected",
                    "reason": (
                        "the run context is unavailable; the candidate SHA "
                        "cannot be re-verified (fail-closed)"
                    ),
                },
            )
        result = coordinator.resolve(
            approval_id,
            subject=subject,
            current_candidate_sha=current_sha,
            resolution=(
                body.get("resolution", "approved")
                if isinstance(body, Mapping) else "approved"
            ),
        )
        return JSONResponse(
            status_code=200 if result.outcome == "approved" else 409,
            content={"approval": result.approval.to_dict(),
                     "outcome": result.outcome, "reason": result.reason},
        )

    @router.get("/capabilities")
    async def capabilities() -> Any:
        manifest = probe_agno()
        errors = manifest.validate()
        if errors:
            raise HTTPException(status_code=503, detail=errors)
        return {
            "runtime": manifest.runtime,
            "runtime_version": manifest.runtime_version,
            "capabilities": {
                name: {"status": entry.status.value, "modes": list(entry.modes)}
                for name, entry in manifest.capabilities.items()
            },
        }

    return router


def _sse_response(events: list[Mapping[str, Any]]) -> StreamingResponse:
    """SSE stream of standard chatbi events (one data: per envelope)."""

    def _stream():
        for event in events:
            yield (
                f"event: chatbi\ndata: "
                f"{json.dumps(dict(event), ensure_ascii=False, separators=(',', ':'))}\n\n"
            )

    return StreamingResponse(_stream(), media_type="text/event-stream")


def _trusted_subject(request: Request, auth_resolver: AuthResolver | None) -> str:
    """Extract the subject from the trusted auth context ONLY (never a body
    value); missing/unverifiable -> fail-closed (adjudication five)."""
    if auth_resolver is None:
        # Deployment with no verified auth boundary: approvals cannot resolve.
        raise HTTPException(
            status_code=403,
            detail=(
                "no trusted authentication resolver is configured; approvals "
                "cannot be resolved (fail-closed, adjudication five)"
            ),
        )
    subject = auth_resolver(request)
    if subject is None or not subject.subject:
        raise HTTPException(
            status_code=401, detail="unauthenticated: subject is unavailable"
        )
    if subject.is_agent:
        raise HTTPException(
            status_code=403,
            detail="an Agent actor can never approve (SEM-003)",
        )
    return subject.subject


def _recompute_intent_sha(controller: ChatBIRunController,
                           approval: Any) -> str | None:
    """MEDIUM-3: recompute the protected-action intent SHA SERVER-SIDE from
    the run's stored request + actor + action type. ``None`` = the run
    context is unavailable -> fail-closed (the resolve must BLOCK)."""
    parts = approval.approval_id.split("_")
    if len(parts) < 3 or parts[0] != "ap":
        return None
    run_id = parts[1]
    ctx = controller._ctxs.get(run_id)
    request = ctx.get("request") if isinstance(ctx, Mapping) else None
    if not isinstance(request, Mapping):
        return None
    return _intent_sha(approval.action_type, approval.requester_subject,
                       request)


def _pending_approval_for_run(
    controller: ChatBIRunController,
    coordinator: ChatBIApprovalCoordinator,
    run_id: str,
) -> Any | None:
    prefix = f"ap_{run_id}_"
    for record in coordinator.list(status="pending"):
        if record.approval_id.startswith(prefix):
            return record
    return None
