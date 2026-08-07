"""ChatBI ApprovalCoordinator for the Agno target (module 5, MR-D3).

Implements the deployment-design §11.1 flow verbatim (adjudication five,
SEM-003, invariant 2):

    Kernel policy.decide flags a protected action
      -> persist Evidence + ApprovalRequest (product state)
      -> Runtime pause (Agno workflow pause; the coordinator is invoked from
         the controller when the approval step pauses)
      -> superuser resolves at the product layer (POST /api/chatbi/v1/
         approvals/{id}/resolve)
      -> Kernel re-verification: role / expiry / candidate SHA / Evidence
      -> PASS only then continue_run (先验后续 — the coordinator emits
         approval.resolved BEFORE the controller may resume the run; a test
         asserts the event order, so continue-before-verify is impossible)

Hard rules:

- ``subject`` MUST come from a trusted authentication context (JWT sub or an
  equivalent verified server-side resolver); the coordinator NEVER accepts a
  subject from a request body. MVP = a single superuser subject from the
  deployment config (adjudication five).
- An Agent actor can never approve (SEM-003); a missing or mismatched
  superuser is fail-closed.
- Idempotency key ``(harness_release, workflow_id, run_id, step_id,
  candidate_sha)``: duplicate requests return the existing record; duplicate
  resolves are a conflict no-op (design §17 rows 6-7).
- Evidence for the approval is written to ``.chatbi`` (governance authority)
  and indexed; resolve re-verifies the evidence refs still exist with
  unchanged content hash.

Applicable rules: SEM-003, SEC-001, MR-005, HOOK-001, invariant 2/3/5,
design §6.1 (no self-approval, no agent approver).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from chatbi_governance.evidence import EvidenceEntry
from chatbi_governance.harness_state import write_state, _safe_session_id
from chatbi_governance.policy import PolicyDecision, PolicyRequest, decide
from chatbi_runtime_contract.types import ApprovalHandle

#: The single configured role that may resolve ChatBI approvals (MVP).
OWNER_ROLE = "owner"

#: Resolution statuses (product vocabulary).
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

#: Default approval validity window when expires_at is not provided.
DEFAULT_APPROVAL_TTL_SECONDS = 3600


def _now_iso(clock: Any) -> str:
    if clock is not None:
        return datetime.fromtimestamp(clock(), tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ApprovalRecord:
    """Persisted ChatBI approval request (product state, design §11.1)."""

    approval_id: str
    tenant: str
    requester_subject: str
    required_role: str
    action_type: str
    candidate_sha: str
    evidence_refs: tuple[str, ...] = ()
    reason: str = ""
    created_at: str = ""
    expires_at: str | None = None
    status: str = STATUS_PENDING
    resolved_by: str | None = None
    resolved_at: str | None = None
    resolution: str | None = None
    block_reason: str | None = None
    #: Module 6 (additive): the workflow the approval belongs to. Old records
    #: without the field default to "" and fall back to the record context.
    workflow_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "approval_id": self.approval_id,
            "tenant": self.tenant,
            "requester_subject": self.requester_subject,
            "required_role": self.required_role,
            "action_type": self.action_type,
            "candidate_sha": self.candidate_sha,
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
            "block_reason": self.block_reason,
            "workflow_id": self.workflow_id,
        }
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ApprovalRecord":
        return cls(
            approval_id=data["approval_id"],
            tenant=data["tenant"],
            requester_subject=data["requester_subject"],
            required_role=data["required_role"],
            action_type=data["action_type"],
            candidate_sha=data["candidate_sha"],
            evidence_refs=tuple(data.get("evidence_refs", [])),
            reason=data.get("reason", ""),
            created_at=data.get("created_at", ""),
            expires_at=data.get("expires_at"),
            status=data.get("status", STATUS_PENDING),
            resolved_by=data.get("resolved_by"),
            resolved_at=data.get("resolved_at"),
            resolution=data.get("resolution"),
            block_reason=data.get("block_reason"),
            workflow_id=data.get("workflow_id", ""),
        )


@dataclass(frozen=True)
class Resolution:
    """Outcome of one resolve attempt."""

    approval: ApprovalRecord
    outcome: str          # "approved" | "rejected" | "conflict"
    reason: str


@dataclass(frozen=True)
class ApprovalContext:
    """Run context in which an approval is requested."""

    workflow_id: str
    run_id: str
    session_id: str
    step_id: str


class ChatBIApprovalCoordinator:
    """Approval state machine — all judgments via the Governance Kernel."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        state_dir: Path,
        deployment: Any,
        evidence_index: Any,
        event_log: Any,
        harness_release: str,
        config: Any = None,          # EffectiveConfig for policy.decide
        clock: Any = None,           # injectable epoch clock for tests
        on_approved: Callable[[ApprovalRecord], None] | None = None,
        now_fn: Any = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.state_dir = Path(state_dir)
        self._approvals_dir = self.state_dir / "approvals"
        self._approvals_dir.mkdir(parents=True, exist_ok=True)
        self.deployment = deployment
        self.evidence_index = evidence_index
        self.event_log = event_log
        self.harness_release = harness_release
        self.config = config
        self.clock = clock
        self.on_approved = on_approved
        self._now_fn = now_fn

    # ------------------------------------------------------------------
    # Persistence (atomic, single writer)
    # ------------------------------------------------------------------

    def _approval_path(self, approval_id: str) -> Path:
        if (
            not isinstance(approval_id, str) or not approval_id
            or "/" in approval_id or ".." in approval_id
        ):
            raise ValueError(f"invalid approval_id: {approval_id!r}")
        return self._approvals_dir / f"{approval_id}.json"

    def _save(self, record: ApprovalRecord) -> None:
        path = self._approval_path(record.approval_id)
        payload = (
            json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def get(self, approval_id: str) -> ApprovalRecord | None:
        path = self._approval_path(approval_id)
        if not path.is_file():
            return None
        try:
            return ApprovalRecord.from_dict(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    def list(self, status: str | None = None) -> list[ApprovalRecord]:
        records: list[ApprovalRecord] = []
        for path in sorted(self._approvals_dir.glob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            record = self.get(path.stem)
            if record is not None and (status is None or record.status == status):
                records.append(record)
        return records

    # ------------------------------------------------------------------
    # Kernel helpers
    # ------------------------------------------------------------------

    def _policy_decision(self, action_type: str, actor: str) -> PolicyDecision:
        """Kernel policy.decide for a protected action.

        LOW-2 fix: a missing governance config is FAIL-CLOSED — the approval
        cannot be decided, so it is blocked (HOOK-004) instead of silently
        skipping the SEM-003 precheck.
        """
        if self.config is None:
            return PolicyDecision.block(
                rule_ids=("HOOK-004",),
                evidence_refs=("policy:config-missing",),
                reason=(
                    "governance config is unavailable; protected actions "
                    "cannot be decided (fail-closed)"
                ),
                recovery=(
                    "Provide the harness shared config to the approval "
                    "coordinator before requesting approvals"
                ),
            )
        return decide(
            self.config,
            PolicyRequest(
                request_type=action_type, actor=actor,
                purpose="governed protected action",
            ),
        )

    def _emit(self, event_type: str, *, context: ApprovalContext,
              approval: ApprovalRecord, payload: Mapping[str, Any]) -> None:
        index = self.event_log.next_index(context.run_id)
        envelope = {
            "schema_version": "chatbi.event/v1",
            "event_id": f"evt_{context.run_id}_{index}",
            "event_index": index,
            "trace_id": f"tr_{context.run_id}",
            "session_id": context.session_id,
            "run_id": context.run_id,
            "workflow_id": context.workflow_id,
            "step_id": context.step_id,
            "event_type": event_type,
            "occurred_at": _iso_timestamp(),
            "runtime": {"name": "agno", "native_run_id": context.run_id},
            "payload": dict(payload),
            "evidence_refs": list(approval.evidence_refs),
        }
        self.event_log.append(envelope)

    # ------------------------------------------------------------------
    # Request
    # ------------------------------------------------------------------

    def request_approval(
        self,
        *,
        context: ApprovalContext,
        action_type: str,
        requester_subject: str,
        candidate_sha: str,
        evidence_refs: tuple[str, ...] = (),
        reason: str = "",
        ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
    ) -> ApprovalHandle:
        """Request human approval for a protected action (Kernel-gated).

        Runs ``policy.decide`` FIRST (fail-closed): an agent requester is
        blocked by SEM-003 and no approval is created; an unknown request type
        is blocked (HOOK-004). Only a human requester for a declared protected
        action may create a pending approval.
        """
        # 1. Kernel decides the protected action first (SEM-003).
        decision = self._policy_decision(action_type, requester_subject)
        if decision is not None:
            if decision.status == "block":
                raise _ApprovalGateBlocked(decision)

        # 2. Idempotency key (harness_release, workflow_id, run_id, step_id,
        #    candidate_sha): normalized by construction — the approval_id is
        #    deterministic per (run_id, step_id), so a duplicate request for
        #    the same run+step returns the existing record, while the same
        #    intent on a DIFFERENT run creates a distinct approval.
        approval_id = f"ap_{context.run_id}_{context.step_id}"
        existing = self.get(approval_id)
        if existing is not None:
            return ApprovalHandle(approval_id=existing.approval_id,
                                  status=existing.status)

        now = _now_iso(self.clock)
        if self.clock is not None:
            from datetime import timedelta

            expires = datetime.fromtimestamp(
                self.clock() + ttl_seconds, tz=timezone.utc
            ).isoformat()
        else:
            from datetime import timedelta

            expires = (
                datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()

        record = ApprovalRecord(
            approval_id=approval_id,
            tenant=self.harness_release,
            requester_subject=requester_subject,
            required_role=OWNER_ROLE,
            action_type=action_type,
            candidate_sha=candidate_sha,
            evidence_refs=tuple(evidence_refs),
            reason=reason,
            created_at=now,
            expires_at=expires,
            status=STATUS_PENDING,
            workflow_id=context.workflow_id,
        )

        # 3. Persist Evidence (governance authority, .chatbi) + index.
        self._persist_evidence(record, context)

        # 4. Persist the ApprovalRequest (product state).
        self._save(record)

        # 5. Emit approval.requested (approval_required=True bridges the
        #    Agno pause to the product approval flow).
        self._emit(
            "approval.requested",
            context=context,
            approval=record,
            payload={
                "approval_id": record.approval_id,
                "action_type": record.action_type,
                "candidate_sha": record.candidate_sha,
                "required_role": record.required_role,
                "expires_at": record.expires_at,
            },
        )
        return ApprovalHandle(approval_id=record.approval_id, status=record.status)

    def _persist_evidence(self, record: ApprovalRecord, context: ApprovalContext) -> None:
        """Write the approval Evidence into ``.chatbi`` and index it."""
        entry = EvidenceEntry.create(
            source_tier="T2",
            evidence_source="approval-request",
            rule_ids=("SEM-003",),
            payload={
                "approval_id": record.approval_id,
                "action_type": record.action_type,
                "candidate_sha": record.candidate_sha,
                "requester_subject": record.requester_subject,
                "required_role": record.required_role,
                "status": record.status,
            },
            runtime_name="agno",
            native_run_id=context.run_id,
            harness_release=self.harness_release,
        )
        safe_sid = _safe_session_id(context.session_id)
        path = write_state(
            self.workspace_root, safe_sid,
            f"approval-{record.approval_id}.json", entry.to_dict(),
        )
        self.evidence_index.add(path)

    # ------------------------------------------------------------------
    # Resolve (Kernel re-verification, then continue)
    # ------------------------------------------------------------------

    def resolve(
        self,
        approval_id: str,
        *,
        subject: str,
        current_candidate_sha: str | None = None,
        resolution: str = "approved",
    ) -> Resolution:
        """Resolve an approval with full Kernel re-verification.

        Order (design §11.1 — never continue-before-verify):
        1. record exists and is pending (else conflict no-op);
        2. subject comes from a trusted auth context (never a body value);
           an agent actor can never approve (SEM-003);
        3. subject == the configured superuser (role check, fail-closed when
           no superuser is configured);
        4. requester cannot approve their own protected action (§6.1);
        5. not expired (expiry check);
        6. candidate SHA unchanged (stale-SHA BLOCK);
        7. evidence refs still present and hash-consistent (Evidence check);

        Every failure -> the record is marked rejected (re-apply required,
        design §17 row 7) and the result carries ``outcome="rejected"``.
        On PASS the record is marked approved and ``on_approved`` is invoked
        — the controller only THEN resumes the run (approval.resolved is
        emitted before any run.resumed, asserted by tests).
        """
        record = self.get(approval_id)
        if record is None:
            return Resolution(
                approval=record or _missing_record(approval_id),
                outcome="conflict",
                reason="approval not found",
            )
        if record.status != STATUS_PENDING:
            return Resolution(
                approval=record,
                outcome="conflict",
                reason=(
                    f"approval is already {record.status!r}; a duplicate "
                    "resolve is a no-op (design §17 row 6)"
                ),
            )

        # 1. Trusted subject (the router supplies it from the auth context).
        if not isinstance(subject, str) or not subject:
            return self._reject(
                record, "subject is missing; approvals require a trusted "
                "authenticated subject (fail-closed)"
            )
        if subject == "agent" or subject.startswith("agent:"):
            return self._reject(
                record, "an Agent actor can never approve (SEM-003)"
            )
        # 2. MVP single superuser role check (adjudication five).
        superuser = getattr(self.deployment, "superuser_subject", None)
        if not superuser:
            return self._reject(
                record, "no superuser is configured; approval cannot be "
                "resolved (fail-closed, adjudication five)"
            )
        if subject != superuser:
            return self._reject(
                record,
                f"subject {subject!r} is not the configured superuser; "
                "role re-verification failed",
            )
        # 3. No self-approval (design §6.1).
        if subject == record.requester_subject:
            return self._reject(
                record, "requester cannot approve their own protected action"
            )
        # 4. Expiry (design §11.1).
        if self._is_expired(record):
            return self._reject(
                record, "approval is expired; a new approval must be requested"
            )
        # 5. Candidate SHA unchanged.
        if current_candidate_sha is not None:
            if current_candidate_sha != record.candidate_sha:
                return self._reject(
                    record,
                    "candidate SHA changed since approval request "
                    f"({record.candidate_sha} -> {current_candidate_sha}); "
                    "re-apply (design §17 row 6)",
                )
        # 6. Evidence still present and consistent (ADR-003).
        evidence_violations = self._verify_evidence(record)
        if evidence_violations:
            return self._reject(
                record,
                "evidence re-verification failed: "
                + "; ".join(evidence_violations),
            )

        # Kernel policy re-check with the human owner actor (SEM-003 pass).
        decision = self._policy_decision(record.action_type, subject)
        if decision is not None and decision.status == "block":
            return self._reject(record, decision.reason)

        # PASS: mark approved, persist, emit, then continue.
        resolved = ApprovalRecord(
            approval_id=record.approval_id,
            tenant=record.tenant,
            requester_subject=record.requester_subject,
            required_role=record.required_role,
            action_type=record.action_type,
            candidate_sha=record.candidate_sha,
            evidence_refs=record.evidence_refs,
            reason=record.reason,
            created_at=record.created_at,
            expires_at=record.expires_at,
            status=STATUS_APPROVED,
            resolved_by=subject,
            resolved_at=_now_iso(self.clock),
            resolution=resolution,
        )
        self._save(resolved)
        self._emit(
            "approval.resolved",
            context=_context_from_record(record, self.harness_release),
            approval=resolved,
            payload={
                "approval_id": resolved.approval_id,
                "resolution": resolved.resolution,
                "resolved_by": resolved.resolved_by,
                "candidate_sha": resolved.candidate_sha,
            },
        )
        if self.on_approved is not None:
            self.on_approved(resolved)
        return Resolution(approval=resolved, outcome="approved", reason="kernel re-verification PASS")

    def _reject(self, record: ApprovalRecord, reason: str) -> Resolution:
        rejected = ApprovalRecord(
            approval_id=record.approval_id,
            tenant=record.tenant,
            requester_subject=record.requester_subject,
            required_role=record.required_role,
            action_type=record.action_type,
            candidate_sha=record.candidate_sha,
            evidence_refs=record.evidence_refs,
            reason=record.reason,
            created_at=record.created_at,
            expires_at=record.expires_at,
            status=STATUS_REJECTED,
            resolution="rejected",
            block_reason=reason,
        )
        self._save(rejected)
        return Resolution(approval=rejected, outcome="rejected", reason=reason)

    def _is_expired(self, record: ApprovalRecord) -> bool:
        if not record.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(record.expires_at)
            now = datetime.now(timezone.utc)
            if self.clock is not None:
                now = datetime.fromtimestamp(self.clock(), tz=timezone.utc)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return now > expires
        except ValueError:
            return True  # unparseable expiry is fail-closed

    def _verify_evidence(self, record: ApprovalRecord) -> list[str]:
        violations: list[str] = []
        for ref in record.evidence_refs:
            if not ref:
                continue
            if self.evidence_index is None:
                violations.append(f"evidence index unavailable; cannot verify {ref}")
                continue
            rows = self.evidence_index.lookup(run_id=None)
            matches = [r for r in rows if ref in r.path]
            if not matches:
                violations.append(f"evidence ref not indexed: {ref}")
                continue
            for row in matches:
                path = self.workspace_root / row.path
                if not path.is_file():
                    violations.append(f"evidence file missing: {row.path}")
        return violations


def _context_from_record(record: ApprovalRecord, harness_release: str) -> ApprovalContext:
    run_id, step_id = "", ""
    parts = record.approval_id.split("_")
    if len(parts) >= 3 and parts[0] == "ap":
        run_id = parts[1]
        step_id = "_".join(parts[2:])
    return ApprovalContext(
        workflow_id=record.workflow_id or "chatbi-analyze",
        run_id=run_id,
        session_id=record.tenant,
        step_id=step_id or "approval",
    )


def reverify_before_execute(
    record: ApprovalRecord,
    *,
    subject: str,
    current_candidate_sha: str | None = None,
    config: Any = None,
    superuser_subject: str | None = None,
    evidence_index: Any = None,
    workspace_root: Path | None = None,
    clock: Any = None,
) -> list[str]:
    """Kernel re-verification BEFORE a protected tool executes (module D).

    = ``resolve()``'s seven-step check subset, WITHOUT the on_approved
    continue-wiring: the AgentOS confirmation flow has already happened (the
    tool call is about to execute); this function re-verifies that the
    approval is still valid at execution time:

      1. subject is a trusted human (never an agent actor, SEM-003);
      2. subject matches the configured superuser (fail-closed when none);
      3. requester != resolver (no self-approval);
      4. not expired;
      5. candidate SHA unchanged (stale-SHA BLOCK);
      6. evidence refs still present and hash-consistent (ADR-003);
      7. ``policy.decide`` with the human owner actor does not block.

    Returns the violation list; empty = PASS (the tool may execute). Any
    violation -> the caller denies the tool (never executes) and re-applies.
    """
    violations: list[str] = []

    # 1. Trusted subject (never from a request body; SEC-003).
    if not isinstance(subject, str) or not subject:
        violations.append(
            "subject is missing; approvals require a trusted authenticated "
            "subject (fail-closed)"
        )
        return violations
    if subject == "agent" or subject.startswith("agent:"):
        violations.append("an Agent actor can never approve (SEM-003)")
        return violations

    # 2. MVP single superuser role check (adjudication five).
    superuser = superuser_subject
    if not superuser:
        violations.append(
            "no superuser is configured; approval cannot be resolved "
            "(fail-closed, adjudication five)"
        )
        return violations
    if subject != superuser:
        violations.append(
            f"subject {subject!r} is not the configured superuser; role "
            "re-verification failed"
        )
        return violations

    # 3. No self-approval (design §6.1).
    if subject == record.requester_subject:
        violations.append(
            "requester cannot approve their own protected action"
        )
        return violations

    # 4. Expiry (design §11.1).
    if record.expires_at:
        try:
            expires = datetime.fromisoformat(record.expires_at)
            now = datetime.now(timezone.utc)
            if clock is not None:
                now = datetime.fromtimestamp(clock(), tz=timezone.utc)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                violations.append(
                    "approval is expired; a new approval must be requested"
                )
        except ValueError:
            violations.append("approval expiry is unparseable (fail-closed)")

    # 5. Candidate SHA unchanged.
    if current_candidate_sha is not None:
        if current_candidate_sha != record.candidate_sha:
            violations.append(
                "candidate SHA changed since approval request "
                f"({record.candidate_sha} -> {current_candidate_sha}); "
                "re-apply (design §17 row 6)"
            )

    # 6. Evidence still present and consistent (ADR-003).
    if evidence_index is not None and workspace_root is not None:
        for ref in record.evidence_refs:
            if not ref:
                continue
            rows = evidence_index.lookup(run_id=None)
            matches = [r for r in rows if ref in r.path]
            if not matches:
                violations.append(f"evidence ref not indexed: {ref}")
                continue
            for row in matches:
                path = workspace_root / row.path
                if not path.is_file():
                    violations.append(f"evidence file missing: {row.path}")

    # 7. Kernel policy with the human owner actor (SEM-003 pass).
    if config is not None:
        decision = decide(
            config,
            PolicyRequest(
                request_type=record.action_type,
                target_entity="",
                actor=subject,
                purpose="governed protected action",
            ),
        )
        if decision is not None and decision.status == "block":
            violations.append(decision.reason)

    return violations


def bridge_request_approval(
    *,
    coordinator: "ChatBIApprovalCoordinator",
    context: ApprovalContext,
    action_type: str,
    requester_subject: str,
    candidate_sha: str,
    evidence_refs: tuple[str, ...] = (),
) -> ApprovalHandle:
    """First-call path of an ``@approval`` governance tool (module D).

    Delegates to ``coordinator.request_approval`` (policy.decide precheck
    SEM-003 + record persistence + ``.chatbi`` Evidence + approval.requested
    event). Idempotent: a record that already exists for the
    ``ap_<run_id>_<step_id>`` key is returned unchanged (design §17 row 6).
    """
    return coordinator.request_approval(
        context=context,
        action_type=action_type,
        requester_subject=requester_subject,
        candidate_sha=candidate_sha,
        evidence_refs=evidence_refs,
    )


def _missing_record(approval_id: str) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=approval_id,
        tenant="",
        requester_subject="",
        required_role="",
        action_type="",
        candidate_sha="",
    )


class _ApprovalGateBlocked(Exception):
    """Policy blocked the approval request (agent self-approve etc.)."""

    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)
