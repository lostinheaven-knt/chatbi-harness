"""Tool-level hooks + run-level guardrails for the ChatBI agent (module B).

The single governance agent carries its deterministic enforcement on two
edges (design §2, modification §6.1):

- **tool_hooks** (tool boundary, synchronous — equivalent to CC
  PreToolUse/PostToolUse): a six-layer chain per tool call. The agno 2.6.22
  nested-chain mechanism (``tools/function.py:1021-1090``) reverses the list
  and reduces it, so **the list HEAD is the outermost hook (executes
  first)** — empirically verified on 2.6.22 (the design doc's "列表尾=最外层"
  is inverted; M7 registration). Semantic order outer->inner:

    1. ``sanitize_hook``    — SEC-003/PORT-001 arg sanitization (+ run scope
                             refresh from ``run_context``);
    2. ``allowlist_hook``   — IR tool-surface allowlist (C011): a tool that
                             is not a governance tool or a read-only file
                             tool is denied;
    3. ``realpath_hook``    — path-typed args: absolute-path escape and
                             undeclared codebase alias -> deny (C010, SEC-001);
    4. ``approval_verify_hook`` — ``@approval`` tools: AgentOS confirmation
                             has passed (the call is about to run) -> Kernel
                             re-verification via the ApprovalCoordinator
                             (module D); failure -> deny + re-apply (C005/6/7);
    5. ``domain_hook``      — per-tool kernel judgments (tier-gap
                             preconditions, candidate SHA binding, review
                             verdict validation, EVAL-004/DOC-004 gates,
                             lint/drift/bootstrap/init chains);
    6. ``event_hook``       — ``tool.requested`` before ``next_func``,
                             ``tool.completed`` after; denies are emitted by
                             the denying hook itself (the inner event hook
                             cannot run once the chain is cut) with the same
                             ``tool.blocked`` shape.

  Axioms (test-pinned): any deny never executes the tool; any hook exception
  fails closed (never an implicit pass, HOOK-004).

- **run-level guardrails** (run boundary, synchronous — equivalent to CC
  SessionStart/Stop hooks). AgentOS server mode runs non-guardrail hooks in
  the background; guardrails always run synchronously and may raise
  ``InputCheckError``/``OutputCheckError`` (``agent/_hooks.py:60-95``), so
  ALL run-level blocking edges are BaseGuardrail subclasses:

  - :class:`ChatbiRequestGuardrail` (pre[0]) — structured run input:
    ``evidence.validate_request`` (analyze) with the minimal clarifying
    question on failure; free-text inputs pass (entry is lenient, the
    terminal gate is authoritative);
  - :class:`ChatbiPolicyGuardrail` (pre[1]) — records the run-level trusted
    subject (``run_subject`` contextvar — subject only ever comes from the
    run context, never the input body, SEC-003) + SEM-003 protected-intent
    precheck for structured requests;
  - :class:`ChatbiDeliveryGuardrail` (post[0]) — the ONLY terminal authority
    (ADR-002): reads the run's evidence chain from the evidence index,
    applies the delivery gate (REV-001/002/003 + candidate SHA binding +
    provenance footer F1), emits ``run.completed`` ONLY on PASS, otherwise
    emits ``gate.blocked`` and raises ``OutputCheckError``.

Agent-mode honest registration (M7): step order (T1->T2->T3, clarify,
routing) is runbook soft guidance; the hooks only enforce deterministically
computable edges (evidence preconditions, SHA, approval, allowlist,
realpath, terminal gate) — no claim of runtime step-order enforcement.

Applicable rules: HOOK-001, HOOK-004, MR-005, ADR-002/003, SEC-001/003,
SEM-003, PORT-001, REV-001/002/003, C010/C011 semantics, invariant 2/5.
"""

from __future__ import annotations

import contextvars
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from chatbi_governance.adapters import resolve_executable
from chatbi_governance.adapters.codebase_reader import select_codebase_reader
from chatbi_governance.bootstrap import (
    SourceColumn,
    SourceInventory,
    SourceTable,
    build_mysql_adapter_spec,
    merge_local_config,
    read_source_inventory,
)
from chatbi_governance.build_plan import (
    append_model_registry,
    build_model_entry,
    validate_build_plan,
    validate_layer_dependency,
)
from chatbi_governance.diagnostics import run_init_diagnostic
from chatbi_governance.drift import (
    DriftCandidate,
    classify_finding,
    classify_src002_finding,
    detect_drift,
)
from chatbi_governance.evidence import (
    EvidenceEntry,
    GateError,
    compute_candidate_sha,
    validate_provenance,
    validate_request,
    validate_review,
)
from chatbi_governance.gates import GateDecision, _sanitize_text
from chatbi_governance.harness_state import _safe_session_id, write_state
from chatbi_governance.impact import AffectedAsset, build_impact_manifest
from chatbi_governance.knowledge import lint_reference
from chatbi_governance.policy import PolicyRequest, decide

from .governed_tools import RunScope, evaluate_step_condition
from .tools import StepToolPolicy, TOOL_NAME_MAP

#: agno import: the package __init__ runs the unshadow guard, but a later
#: test module may re-insert ``<root>/runtimes`` into sys.path (re-shadowing
#: the installed agno) while this module is imported fresh in a batch run —
#: re-run the guard explicitly (reviewer.py pattern).
from . import ensure_agno_unshadowed  # noqa: E402

ensure_agno_unshadowed()
from agno.guardrails.base import BaseGuardrail  # noqa: E402

#: Guardrail exceptions are imported lazily below in the guardrail classes to
#: keep the module importable without the agno runtime (module-level import
#: stays agno-free so conformance/unit tests can import the hook builders on
#: any interpreter).

#: Run-level trusted subject (SEC-003): set by ChatbiPolicyGuardrail from the
#: run context ONLY (never from the input body); read by the approval hook.
run_subject: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chatbi_run_subject", default=""
)

#: Review failure-mode rule sets (delivery-gate vocabulary, M5-S6 semantics —
#: mirrors the module-5 delivery-gate vocabulary).
_RULES_UNAVAILABLE = ("HOOK-001", "HOOK-004", "SEC-003")
_RULES_STALE_SHA = ("REV-001", "REV-003")
_RULES_ROUND = ("REV-003", "HOOK-001")
_RULES_NOT_PASS = ("REV-001", "REV-003", "HOOK-001")

#: Tier -> IR when precondition for chatbi_record_evidence.
_TIER_WHEN = {"T2": 'evidence.has_gap("T1")', "T3": 'evidence.has_gap("T2")'}
_TIER_SOURCE = {"T1": "semantic-layer", "T2": "curated-reference",
                "T3": "raw-exploration"}
_TIER_RULE_IDS = {"T1": ("SEM-001", "SEM-002"), "T2": ("RAW-001", "SRC-001"),
                  "T3": ("RAW-003",)}

#: Path-typed argument keys inspected by the realpath hook.
_PATH_KEYS = ("codebase", "ref", "path", "target", "codebase_path")


@dataclass(frozen=True)
class HookOutcome:
    """One hook decision (deny payload carrier)."""

    allowed: bool
    event_type: str | None = None        # tool.blocked etc.
    payload: Mapping[str, Any] = field(default_factory=dict)


def _sanitize_args(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {key: _sanitize_args(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_sanitize_args(item) for item in value]
    return value


def _is_absolute_path(value: str) -> bool:
    if value.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", value)):
        return True
    return Path(value).is_absolute()


def _deny_payload(
    name: str,
    *,
    rule_ids: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    reason: str,
    recovery: str,
) -> dict[str, Any]:
    """Standard deny payload (tool.blocked shape, CC-同构)."""
    return {
        "status": "blocked",
        "tool": name,
        "rule_ids": list(rule_ids),
        "evidence_refs": list(evidence_refs),
        "reason": _sanitize_text(reason),
        "recovery": _sanitize_text(recovery),
    }


def _emit_tool_blocked(
    event_log: Any, scope: RunScope, name: str, payload: Mapping[str, Any],
) -> None:
    from .events import emit_standard_event

    emit_standard_event(
        event_log,
        run_id=scope.run_id or "run",
        session_id=scope.session_id or "session",
        workflow_id=scope.workflow_id or "chatbi-analyze",
        step_id=name,
        event_type="tool.blocked",
        payload=dict(payload),
        evidence_refs=tuple(payload.get("evidence_refs", []) or ()),
    )


def _record_evidence_file(
    *,
    scope: RunScope,
    evidence_index: Any,
    workspace_root: Path,
    harness_release: str,
    entry: EvidenceEntry,
    step_id: str,
) -> Path:
    """Persist one EvidenceEntry under .chatbi and index it (ADR-003)."""
    safe_sid = _safe_session_id(scope.session_id or "session")
    written = write_state(
        workspace_root, safe_sid,
        f"evidence-{step_id}-{(scope.run_id or 'run')[:8]}.json",
        entry.to_dict(),
    )
    evidence_index.add(written)
    return written


def _emit_evidence_recorded(
    event_log: Any, scope: RunScope, entry: EvidenceEntry,
) -> None:
    from .events import emit_standard_event

    emit_standard_event(
        event_log,
        run_id=scope.run_id or "run",
        session_id=scope.session_id or "session",
        workflow_id=scope.workflow_id or "chatbi-analyze",
        step_id=None,
        event_type="evidence.recorded",
        payload={
            "source_tier": entry.source_tier,
            "evidence_source": entry.evidence_source,
            "content_sha256": entry.content_sha256,
            "rule_ids": list(entry.rule_ids),
        },
        evidence_refs=(entry.evidence_source,),
    )


# ---------------------------------------------------------------------------
# build_tool_hooks
# ---------------------------------------------------------------------------


def build_tool_hooks(
    *,
    specs_by_name: Mapping[str, Any],
    ir_workflows: Mapping[str, Any],
    config: Any,
    approvals: Any,
    evidence_index: Any,
    event_log: Any,
    workspace_root: Path,
    harness_release: str,
    run_scope: RunScope | None = None,
    reviewer_runner: Any = None,
    native_runner: Callable[..., Any] | None = None,
    deployment: Any = None,
    clock: Any = None,
) -> list[Callable[..., Any]]:
    """Build the agent.tool_hooks chain (design §2.1, six layers).

    Returns the list in OUTERMOST-first order: the agno mechanism
    (``tools/function.py:1047-1049`` — reverse + reduce) makes the list HEAD
    the outermost hook that executes first (empirically verified on 2.6.22:
    ``tool_hooks=[A, B]`` runs A-before -> B-before -> tool -> B-after ->
    A-after; the design doc's "列表尾=最外层" is inverted, M7 registration).
    Hook signature: ``(name, func, args)`` — ``func`` is the next_func
    continuation (call ``func(**args)`` to run the chain; NOT calling it
    denies the tool).
    """
    from .approvals import (
        ApprovalContext,
        bridge_request_approval,
        reverify_before_execute,
    )

    scope = run_scope if run_scope is not None else RunScope()

    # -- layer 2: allowlist policy -----------------------------------------
    # The agent's ACTUAL surface is governance tools + read-only file tools
    # (design R2: the agent holds no bare Write/Edit/Bash). The IR deny
    # lists (Task/WebFetch/WebSearch/…) and the step-level write denies are a
    # second layer — anything on any IR deny list is blocked (C011).
    deny: set[str] = set()
    for workflow in ir_workflows.values():
        tools_spec = getattr(workflow, "tools", None)
        if tools_spec is None:
            continue
        deny.update(getattr(tools_spec, "deny", ()) or ())
    allow = set(specs_by_name.keys()) | {"Read", "Grep", "Glob"}
    allow = {name for name in allow if name not in deny}
    allowlist_policy = StepToolPolicy(allow=allow, deny=deny)

    def _check_allowlist(name: str) -> bool:
        # tool_name normalization: agno composite names -> IR vocabulary.
        return allowlist_policy.check(TOOL_NAME_MAP.get(name, name))

    # -- layer 1: sanitize + scope refresh ---------------------------------
    def sanitize_hook(name: str, func: Callable[..., Any],
                      args: Mapping[str, Any],
                      run_context: Any = None) -> Any:
        if run_context is not None:
            scope.run_id = getattr(run_context, "run_id", "") or scope.run_id
            scope.session_id = (
                getattr(run_context, "session_id", "") or scope.session_id
            )
            scope.workflow_id = (
                getattr(run_context, "workflow_id", "") or scope.workflow_id
            )
        clean = _sanitize_args(dict(args))
        return func(**clean)

    # -- layer 2: allowlist -------------------------------------------------
    def allowlist_hook(name: str, func: Callable[..., Any],
                       args: Mapping[str, Any]) -> Any:
        if not _check_allowlist(name):
            decision = GateDecision.block(
                rule_ids=("HOOK-001", "SEC-001"),
                evidence_refs=(f"tool:not-allowlisted:{name}",),
                reason=(
                    f"tool {name!r} is not on the IR tool surface; "
                    "unregistered tools are blocked (C011)"
                ),
                recovery="Use a governance tool or a read-only file tool",
            )
            payload = _deny_payload(name, rule_ids=decision.rule_ids,
                                    evidence_refs=decision.evidence_refs,
                                    reason=decision.reason,
                                    recovery=decision.recovery)
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        return func(**args)

    # -- layer 3: realpath --------------------------------------------------
    def realpath_hook(name: str, func: Callable[..., Any],
                      args: Mapping[str, Any]) -> Any:
        for key in _PATH_KEYS:
            value = args.get(key)
            if not isinstance(value, str) or not value:
                continue
            if _is_absolute_path(value):
                decision = GateDecision.block(
                    rule_ids=("SEC-001", "PORT-001"),
                    evidence_refs=(f"path:absolute:{key}",),
                    reason=(
                        f"argument {key!r} contains an absolute path; "
                        "workspace-relative paths only (PORT-001)"
                    ),
                    recovery="Pass a workspace-relative path or a configured "
                             "codebase alias",
                )
                payload = _deny_payload(name, rule_ids=decision.rule_ids,
                                        evidence_refs=decision.evidence_refs,
                                        reason=decision.reason,
                                        recovery=decision.recovery)
                _emit_tool_blocked(event_log, scope, name, payload)
                return payload
            if key == "codebase" and config is not None:
                selection = select_codebase_reader(config, alias=value)
                if selection.status == "stopped":
                    decision = selection.stop_decision
                    payload = _deny_payload(
                        name, rule_ids=decision.rule_ids,
                        evidence_refs=decision.evidence_refs,
                        reason=decision.reason, recovery=decision.recovery)
                    _emit_tool_blocked(event_log, scope, name, payload)
                    return payload
        return func(**args)

    # -- layer 4: approval re-verification (module D) -----------------------
    def _approval_action_type(name: str) -> str:
        request = scope.request or {}
        action = request.get("action_type")
        if isinstance(action, str) and action:
            return action
        # IR human_approval step when condition: owner.pending(<action>).
        for workflow in ir_workflows.values():
            for step in getattr(workflow, "steps", ()) or ():
                when = getattr(step, "when", None)
                if isinstance(when, str) and "owner.pending" in when:
                    match = re.search(r"owner\.pending\(([A-Za-z0-9_-]+)\)", when)
                    if match:
                        return match.group(1)
        return "approve_metric"

    def approval_verify_hook(name: str, func: Callable[..., Any],
                             args: Mapping[str, Any]) -> Any:
        from .events import emit_standard_event

        spec = specs_by_name.get(name)
        if spec is None or spec.approval != "required":
            return func(**args)
        requester = run_subject.get()
        if not requester:
            # MED-3 (eval round 1): the requester subject must come from the
            # run context (SEC-003). An empty subject is fail-closed — never
            # a fabricated literal ("operator") in the audit chain.
            payload = _deny_payload(
                name, rule_ids=("SEC-003",),
                reason="approval requester subject is missing; the run has "
                       "no authenticated user (fail-closed, SEC-003)",
                recovery="Authenticate the run user and re-request the "
                         "protected action")
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        action_type = _approval_action_type(name)
        candidate_sha = scope.candidate_sha or compute_candidate_sha(
            {"action": action_type, "actor": requester})
        context = ApprovalContext(
            workflow_id=scope.workflow_id or "chatbi-maintain-model",
            run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            step_id=name,
        )
        try:
            handle = bridge_request_approval(
                coordinator=approvals,
                context=context,
                action_type=action_type,
                requester_subject=requester,
                candidate_sha=candidate_sha,
                evidence_refs=tuple(
                    e.get("evidence_source", "")
                    for e in scope.evidence_chain if isinstance(e, Mapping)
                ),
            )
        except Exception as error:  # policy block (SEM-003) etc.
            rule_ids = ("SEM-003", "DOC-004")
            reason = (
                f"approval request blocked: {type(error).__name__}: {error}"
            )
            if isinstance(error, GateError):
                rule_ids = error.decision.rule_ids
                reason = error.decision.reason
            payload = _deny_payload(name, rule_ids=rule_ids,
                                    reason=reason,
                                    recovery="Wait for the human owner to "
                                             "approve the protected action")
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        record = approvals.get(handle.approval_id)
        if record is None:
            payload = _deny_payload(name, rule_ids=("HOOK-004",),
                                    reason="approval record missing (fail-closed)",
                                    recovery="Re-request the approval")
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        # Kernel re-verification BEFORE execution (先验后续). The AgentOS
        # confirmation is the transport; the governance judgment treats the
        # confirmation as the configured superuser's action (ADR-002: the
        # Kernel is authoritative) — the run user stays the requester, so
        # requester != resolver is enforced (a superuser-run requesting its
        # own protected action is rejected).
        superuser = getattr(deployment, "superuser_subject", None)
        violations = reverify_before_execute(
            record,
            subject=superuser or "",
            current_candidate_sha=candidate_sha,
            config=config,
            superuser_subject=superuser,
            evidence_index=evidence_index,
            workspace_root=Path(workspace_root),
            clock=clock,
        )
        if violations:
            emit_standard_event(
                event_log,
                run_id=scope.run_id or "run",
                session_id=scope.session_id or "session",
                workflow_id=scope.workflow_id or "chatbi-maintain-model",
                step_id=name,
                event_type="approval.resolved",
                payload={"approval_id": record.approval_id,
                         "resolution": "rejected",
                         "candidate_sha": record.candidate_sha,
                         "reason": "; ".join(violations)},
                evidence_refs=record.evidence_refs,
            )
            payload = _deny_payload(
                name, rule_ids=("SEM-003", "DOC-004"),
                reason="; ".join(violations),
                recovery="Re-apply: the protected action requires a fresh "
                         "human-owner approval")
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        # PASS: the confirmation is valid — record the resolution event
        # (the record stays coordinator-authoritative; the event is the
        # audit trail) then execute the tool.
        emit_standard_event(
            event_log,
            run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-maintain-model",
            step_id=name,
            event_type="approval.resolved",
            payload={"approval_id": record.approval_id,
                     "resolution": "approved",
                     "candidate_sha": record.candidate_sha},
            evidence_refs=record.evidence_refs,
        )
        return func(**args)

    # -- layer 5: per-tool kernel judgments ---------------------------------
    domain_hook = _build_domain_hook(
        scope=scope,
        config=config,
        approvals=approvals,
        evidence_index=evidence_index,
        event_log=event_log,
        workspace_root=Path(workspace_root),
        harness_release=harness_release,
        reviewer_runner=reviewer_runner,
        native_runner=native_runner,
        ir_workflows=ir_workflows,
    )

    # -- layer 6: event envelope (requested / completed) --------------------
    def event_hook(name: str, func: Callable[..., Any],
                   args: Mapping[str, Any]) -> Any:
        from .events import emit_standard_event

        emit_standard_event(
            event_log,
            run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-analyze",
            step_id=name,
            event_type="tool.requested",
            payload={"tool": name},
        )
        result = func(**args)
        emit_standard_event(
            event_log,
            run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-analyze",
            step_id=name,
            event_type="tool.completed",
            payload={"tool": name},
        )
        return result

    # OUTERMOST first (list head executes first, empirically verified on
    # agno 2.6.22 — the design doc's "列表尾=最外层" is inverted).
    # Ordering note (M7): realpath runs BEFORE sanitize so escape detection
    # sees the RAW argument values (sanitize would redact an absolute path
    # into "[REDACTED_PATH]" and the C010 deny could never fire). Sanitize
    # still precedes allowlist/approval/domain/event (design §2.4 chain
    # assertion).
    return [
        realpath_hook,
        sanitize_hook,
        allowlist_hook,
        approval_verify_hook,
        domain_hook,
        event_hook,
    ]


# ---------------------------------------------------------------------------
# Domain hook (layer 5): per-governance-tool kernel judgments
# ---------------------------------------------------------------------------


def _build_domain_hook(
    *,
    scope: RunScope,
    config: Any,
    approvals: Any,
    evidence_index: Any,
    event_log: Any,
    workspace_root: Path,
    harness_release: str,
    reviewer_runner: Any,
    native_runner: Callable[..., Any] | None,
    ir_workflows: Mapping[str, Any] | None = None,
) -> Callable[..., Any]:
    """Dispatch per governance tool; every judgment goes through the Kernel."""

    def _deny(name: str, decision: GateDecision) -> dict[str, Any]:
        payload = _deny_payload(name, rule_ids=decision.rule_ids,
                                evidence_refs=decision.evidence_refs,
                                reason=decision.reason,
                                recovery=decision.recovery)
        _emit_tool_blocked(event_log, scope, name, payload)
        return payload

    def _deny_raw(name: str, *, rule_ids: tuple[str, ...], reason: str,
                  recovery: str, evidence_refs: tuple[str, ...] = ()) -> dict:
        payload = _deny_payload(name, rule_ids=rule_ids,
                                evidence_refs=evidence_refs, reason=reason,
                                recovery=recovery)
        _emit_tool_blocked(event_log, scope, name, payload)
        return payload

    def _record(name: str, step_id: str, entry: EvidenceEntry) -> None:
        _record_evidence_file(scope=scope, evidence_index=evidence_index,
                              workspace_root=workspace_root,
                              harness_release=harness_release,
                              entry=entry, step_id=step_id)
        _emit_evidence_recorded(event_log, scope, entry)

    def _request() -> dict[str, Any]:
        return dict(scope.request or {})

    # --- chatbi_record_request -------------------------------------------
    def _record_request(name: str, func: Callable[..., Any],
                        args: Mapping[str, Any]) -> Any:
        request = args.get("request")
        payload_request = dict(request) if isinstance(request, Mapping) else _request()
        try:
            validate_request(payload_request)
        except GateError as error:
            return _deny(name, error.decision)  # recovery = 最小澄清问题
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="request",
            rule_ids=("REQ-001", "HOOK-001"), payload=payload_request,
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "request", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "validated": True}
        return result

    # --- chatbi_record_evidence -------------------------------------------
    def _record_evidence(name: str, func: Callable[..., Any],
                         args: Mapping[str, Any]) -> Any:
        tier = str(args.get("tier", ""))
        content = args.get("content")
        if tier not in ("T1", "T2", "T3") or content is None:
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason=f"record_evidence requires tier T1|T2|T3 and content",
                recovery="Provide tier and content")
        # Tier-gap precondition (IR when): T2 needs a recorded T1 gap, T3 a
        # recorded T2 gap (C002/C003/C004 semantics).
        when_expr = _TIER_WHEN.get(tier)
        if when_expr and not evaluate_step_condition(
            when_expr, evidence_chain=tuple(scope.evidence_chain),
            request=_request(),
        ):
            return _deny_raw(
                name, rule_ids=("SEM-001", "HOOK-004"),
                reason=(
                    f"tier {tier} requires a recorded "
                    f"{'T1' if tier == 'T2' else 'T2'} gap; no gap evidence "
                    "was recorded (SEM-001)"
                ),
                recovery="Record the upper-tier gap evidence first, or stay "
                         "on the covered tier")
        payload: dict[str, Any] = (
            dict(content) if isinstance(content, Mapping)
            else {"content": content}
        )
        entry = EvidenceEntry.create(
            source_tier=tier, evidence_source=_TIER_SOURCE[tier],
            rule_ids=_TIER_RULE_IDS[tier], payload=payload,
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        scope.evidence_chain.append(entry.to_dict())
        step_id = {"T1": "t1_semantic", "T2": "t2_curated", "T3": "t3_raw"}[tier]
        _record(name, step_id, entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "evidence_source": _TIER_SOURCE[tier],
                    "content_sha256": entry.content_sha256, "recorded": True}
        return result

    # --- chatbi_submit_candidate ------------------------------------------
    def _submit_candidate(name: str, func: Callable[..., Any],
                          args: Mapping[str, Any]) -> Any:
        content = args.get("content")
        if content is None:
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason="submit_candidate requires content",
                             recovery="Provide the final candidate content")
        try:
            sha = compute_candidate_sha(content)
        except (TypeError, ValueError) as error:
            return _deny_raw(name, rule_ids=("SEC-003", "HOOK-001"),
                             reason=f"candidate is not JSON-serializable: {error}",
                             recovery="Provide a JSON-serializable candidate")
        scope.candidate_sha = sha
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="candidate-bind",
            rule_ids=("REV-001",),
            payload={"candidate_sha": sha, "content": content},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "candidate_bind", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "candidate_sha": sha, "frozen": True}
        return result

    # --- chatbi_review -----------------------------------------------------
    def _review(name: str, func: Callable[..., Any],
                args: Mapping[str, Any]) -> Any:
        from .events import emit_standard_event

        candidate_sha = str(args.get("candidate_sha") or scope.candidate_sha or "")
        emit_standard_event(
            event_log, run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-analyze",
            step_id=name, event_type="review.started",
            payload={"candidate_sha": candidate_sha},
        )
        def _fail(rule_ids: tuple[str, ...], reason: str,
                  recovery: str) -> dict[str, Any]:
            entry = EvidenceEntry.create(
                source_tier="T2", evidence_source="candidate-review",
                rule_ids=("REV-001", "REV-002", "REV-003"),
                payload={"status": "BLOCKED", "round": scope.review_round,
                         "candidate_sha": candidate_sha,
                         "findings": list(rule_ids), "reason": reason},
                runtime_name="agno", native_run_id=scope.run_id or "",
                harness_release=harness_release,
            )
            _record(name, "candidate_review", entry)
            emit_standard_event(
                event_log, run_id=scope.run_id or "run",
                session_id=scope.session_id or "session",
                workflow_id=scope.workflow_id or "chatbi-analyze",
                step_id=name, event_type="review.completed",
                payload={"status": "BLOCKED", "candidate_sha": candidate_sha,
                         "rule_ids": list(rule_ids)},
            )
            return _deny_raw(name, rule_ids=rule_ids, reason=reason,
                             recovery=recovery)

        try:
            result = func(**args)  # tool body invoked the reviewer runner
        except Exception as error:  # noqa: BLE001 - reviewer unavailable
            return _fail(_RULES_UNAVAILABLE,
                         f"reviewer unavailable: {type(error).__name__} "
                         f"(fail-closed, HOOK-004)",
                         "Restore the reviewer and re-review")
        verdict = (result or {}).get("verdict") if isinstance(result, Mapping) else None

        if not isinstance(verdict, Mapping):
            return _fail(_RULES_UNAVAILABLE,
                         "reviewer verdict is not a JSON object (fail-closed)",
                         "Correct the reviewer output and re-review")
        try:
            validate_review(verdict)
        except GateError as error:
            return _fail(_RULES_UNAVAILABLE,
                         f"review verdict violates review.schema.json: "
                         f"{error.decision.reason}",
                         "Correct the payload to match the declared schema")
        if verdict.get("candidate_sha") != candidate_sha:
            return _fail(
                _RULES_STALE_SHA,
                "Reviewer PASS is only valid for the exact candidate SHA "
                f"(verdict {verdict.get('candidate_sha')!r} != "
                f"{candidate_sha!r}); the candidate changed and must be "
                "re-reviewed (REV-001)",
                "Re-review the current candidate")
        round_no = int(verdict.get("round", 1) or 1)
        scope.review_round = max(scope.review_round, round_no)
        if round_no >= 4:
            return _fail(_RULES_ROUND,
                         "review round exceeded the limit (REV-003)",
                         "Do not keep re-reviewing indefinitely")
        findings = verdict.get("findings", []) or []
        blocking = [f for f in findings
                    if isinstance(f, Mapping) and f.get("severity") == "block"]
        if verdict.get("status") != "PASS" or blocking:
            return _fail(_RULES_NOT_PASS,
                         "Review verdict is not a clean PASS for the frozen "
                         "candidate",
                         "Address every blocking finding and re-review")
        # PASS: record the auditable review evidence.
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="candidate-review",
            rule_ids=("REV-001", "REV-002", "REV-003"),
            payload={"status": "PASS", "round": round_no,
                     "candidate_sha": candidate_sha, "findings": [],
                     "reason": "Independent reviewer PASS for the exact "
                               "candidate SHA"},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "candidate_review", entry)
        emit_standard_event(
            event_log, run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-analyze",
            step_id=name, event_type="review.completed",
            payload={"status": "PASS", "candidate_sha": candidate_sha,
                     "round": round_no},
        )
        if isinstance(result, Mapping):
            return {**dict(result), "review": {
                "status": "PASS", "round": round_no,
                "candidate_sha": candidate_sha,
                "reason": "Independent reviewer PASS"}}
        return result

    # --- chatbi_crosscheck -------------------------------------------------
    def _crosscheck(name: str, func: Callable[..., Any],
                    args: Mapping[str, Any]) -> Any:
        codebase = str(args.get("codebase") or "")
        business = {}
        if config is not None:
            business = config.get("business_codebases") or {}
        if not business or not codebase:
            # Vacuously satisfied when no external Business Codebases are
            # configured (analyze command prose §Historical SQL).
            result = func(**args)
            if isinstance(result, Mapping):
                return {**dict(result), "crosscheck": {"vacuous": True}}
            return result
        selection = select_codebase_reader(config, alias=codebase)
        if selection.status == "stopped":
            return _deny(name, selection.stop_decision)
        try:
            evidence = selection.reader.read(
                alias=codebase, target=str(args.get("query") or ""))
        except Exception as error:  # noqa: BLE001 - fail-closed (HOOK-004)
            return _deny_raw(
                name, rule_ids=("SRC-002", "HOOK-004"),
                reason=f"codebase cross-check failed: {type(error).__name__}",
                recovery="Resolve the codebase read error and re-run")
        # A blocked/error cross-check is RECORDED as evidence (NOT denied at
        # the tool edge): the SRC-002 route decision belongs to the
        # build-plan hook / delivery gate (classify_src002_finding routes
        # blocked evidence to route A — owner adjudication, E010 semantics).
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="codebase-crosscheck",
            rule_ids=("SRC-002",),
            payload=evidence.to_dict() if hasattr(evidence, "to_dict")
            else {"status": evidence.status},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        scope.evidence_chain.append(entry.to_dict())
        _record(name, "src002_crosscheck", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "crosscheck": {"status": evidence.status}}
        return result

    # --- chatbi_build_plan -------------------------------------------------
    def _bfr_ir_rules() -> tuple[str, ...]:
        wf = ir_workflows.get("chatbi-build-from-requirement")
        if wf is not None and getattr(wf, "gates", None) is not None:
            delivery = getattr(wf.gates, "delivery", None)
            if delivery is not None and getattr(delivery, "rule_ids", ()):
                return tuple(delivery.rule_ids)
        return ("SRC-002", "SEM-003", "REQ-001", "REQ-002")

    def _build_plan(name: str, func: Callable[..., Any],
                    args: Mapping[str, Any]) -> Any:
        from chatbi_governance.build_plan import BuildPlan, build_model_entry

        requirement = args.get("requirement")
        req = dict(requirement) if isinstance(requirement, Mapping) else _request()
        try:
            # SRC-002 route decision (E010: a blocked cross-check -> route A
            # -> owner adjudication; the delivery gate blocks with the IR
            # rule set).
            crosscheck = None
            for entry in reversed(scope.evidence_chain):
                if isinstance(entry, Mapping) and entry.get(
                    "evidence_source"
                ) == "codebase-crosscheck":
                    crosscheck = entry
                    break
            if crosscheck is not None:
                payload = crosscheck.get("payload") or {}
                if isinstance(payload, Mapping):
                    # CodebaseEvidence semantics: a blocked/error status, an
                    # error_category, or a nested block decision all mean the
                    # cross-check did NOT pass (route A, E010).
                    decision = None
                    nested = payload.get("payload")
                    if isinstance(nested, Mapping):
                        decision = (nested.get("data") or {}).get("decision")
                    blocked = (
                        payload.get("status") in ("blocked", "error")
                        or bool(payload.get("error_category"))
                        or (isinstance(decision, Mapping)
                            and decision.get("status") == "block")
                    )
                    if blocked:
                        return _deny_raw(
                            name, rule_ids=_bfr_ir_rules(),
                            reason=payload.get("reason")
                            or "SRC-002 cross-check blocked -> route A "
                               "(domain-owner adjudication, REQ-001/002)",
                            recovery="Ask the domain owner for the correct "
                                     "alias/path")
            entries = []
            for raw in (req.get("models") or []):
                if not isinstance(raw, Mapping):
                    continue
                entries.append(build_model_entry(
                    name=str(raw.get("name", "")),
                    layer=str(raw.get("layer", "dwd")),
                    change_kind=str(raw.get("change_kind", "create")),
                    created_rev=harness_release,
                    owner=str(raw.get("owner",
                                      req.get("actor", "operator"))),
                    upstream_deps=tuple(raw.get("upstream_deps", ()) or ()),
                    join_or_aggregate_summary=str(
                        raw.get("join_or_aggregate_summary", "")),
                ))
            plan = BuildPlan(
                schema_version=1,
                session_id=scope.session_id or "session",
                models=tuple(entries),
            )
            validate_build_plan(plan, layer_rules=(), known_models=frozenset())
            validate_layer_dependency(plan, layer_rules=())
        except GateError as error:
            return _deny(name, error.decision)
        except Exception as error:  # noqa: BLE001 - fail-closed (HOOK-004)
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason=f"build plan derivation failed: "
                                    f"{type(error).__name__}",
                             recovery="Correct the requirement and re-run")
        plan_entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="build-plan",
            rule_ids=_bfr_ir_rules(),
            payload={"models": [entry.to_dict() for entry in entries],
                     "status": "pass"},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "build_plan", plan_entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "build_plan": {
                "models": [entry.to_dict() for entry in entries]}}
        return result

    # --- chatbi_impact_manifest --------------------------------------------
    def _impact_manifest(name: str, func: Callable[..., Any],
                         args: Mapping[str, Any]) -> Any:
        request = _request()
        entry_raw = args.get("model_entry")
        if isinstance(entry_raw, Mapping):
            request = {**request, **dict(entry_raw)}
        assets = []
        for spec in request.get("affected_assets", []) or []:
            if isinstance(spec, Mapping):
                assets.append(AffectedAsset(
                    asset_kind=str(spec.get("asset_kind", "")),
                    asset_ref=str(spec.get("asset_ref", "")),
                    change_required=bool(spec.get("change_required", False)),
                    synced=bool(spec.get("synced", False)),
                ))
        try:
            manifest = build_impact_manifest(
                run_id=scope.run_id or "run",
                change_kind=str(request.get("change_kind", "")),
                target=str(request.get("target", "")),
                affected_assets=assets,
                evidence_state=str(request.get("evidence_state", "")),
                p0_eval_failed=bool(request.get("p0_eval_failed", False)),
                protected_action=bool(request.get("protected", False)),
                candidate_payload=request.get("candidate_payload"),
                created_rev=harness_release,
            )
        except GateError as error:
            return _deny(name, error.decision)
        if manifest.has_blocking_drift():
            return _deny_raw(
                name, rule_ids=("DOC-004",),
                reason="DOC-004 full-sync gate not passed: " + "; ".join(
                    list(manifest.blocking_reasons())[:3]),
                recovery="Sync every affected asset with sufficient evidence "
                         "and no P0 evaluation failure")
        scope.impact = manifest.to_dict()
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="impact-manifest",
            rule_ids=("DOC-004",),
            payload={"status": "pass", "target": str(request.get("target", ""))},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "impact_manifest", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "impact": manifest.to_dict()}
        return result

    # --- chatbi_registry_append --------------------------------------------
    def _registry_append(name: str, func: Callable[..., Any],
                         args: Mapping[str, Any]) -> Any:
        from chatbi_governance.policy import PolicyDecision

        request = _request()
        entry_raw = args.get("entry")
        if isinstance(entry_raw, Mapping):
            request = {**request, **dict(entry_raw)}
        # DOC-004 sync gate: only after a passing impact manifest.
        impact = getattr(scope, "impact", None) or {}
        if impact and impact.get("has_blocking_drift"):
            return _deny_raw(
                name, rule_ids=("DOC-004",),
                reason="DOC-004 sync gate not passed; a failed-sync model is "
                       "not recorded (fail-closed)",
                recovery="Sync every affected asset and re-run")
        try:
            entry = build_model_entry(
                name=str(request.get("target", "")),
                layer=str(request.get("layer", "dwd")),
                change_kind=str(request.get("change_kind", "create")),
                created_rev=harness_release,
                owner=str(request.get("actor", "operator")),
                upstream_deps=tuple(request.get("upstream_deps", ()) or ()),
                join_or_aggregate_summary=str(request.get("summary", "")),
            )
            registry_path = workspace_root / ".chatbi" / "model_registry.json"
            append_model_registry(registry_path, entry)
        except GateError as error:
            return _deny(name, error.decision)
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="model-registry",
            rule_ids=("DOC-004", "SEM-003"),
            payload={"appended": True,
                     "name": str(request.get("target", ""))},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "registry_append", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "registry_appended": True}
        return result

    # --- chatbi_lint_reference ---------------------------------------------
    def _lint_reference(name: str, func: Callable[..., Any],
                        args: Mapping[str, Any]) -> Any:
        ref = str(args.get("ref", ""))
        issues = lint_reference(ref)
        if issues:
            return _deny_raw(
                name, rule_ids=("DOC-002", "DOC-003"),
                reason="reference lint found issues: " + "; ".join(
                    f"{i.field}: {i.message}" for i in issues[:3]),
                recovery="Resolve the lint issues via the governed reference "
                         "authoring flow")
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="knowledge-lint",
            rule_ids=("DOC-002", "DOC-003"),
            payload={"ready": True, "issue_count": 0},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "lint", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "lint": {"ready": True}}
        return result

    # --- chatbi_evaluate ---------------------------------------------------
    def _evaluate(name: str, func: Callable[..., Any],
                  args: Mapping[str, Any]) -> Any:
        from chatbi_governance.evaluator import (
            GroundTruthVault,
            build_evaluation_run,
            validate_evaluation,
        )

        request = _request()
        suite = args.get("suite_request")
        if isinstance(suite, Mapping):
            request = {**request, **dict(suite)}
        answers = request.get("answers")
        if not answers:
            return _deny_raw(name, rule_ids=("EVAL-001", "HOOK-004"),
                             reason="evaluation requires isolated ground-truth "
                                    "answers",
                             recovery="Provide the owner-isolated answers")
        try:
            vault = GroundTruthVault(dict(answers))
            actuals = {}
            if native_runner is not None:
                native = native_runner("chatbi-evaluate", "run_suite",
                                       dict(request))
                if isinstance(native, Mapping):
                    actuals = native.get("actuals") or {}
            run = build_evaluation_run(
                run_id=scope.run_id or "run",
                skill_version=str(request.get("skill_version",
                                              "chatbi-evaluation@1")),
                model_id=str(request.get("model_id", "")),
                vault=vault,
                actuals=actuals,
                tokens=int(request.get("tokens", 0)),
                latency_ms=int(request.get("latency_ms", 0)),
                seen=bool(request.get("seen", True)),
                threshold_owner_confirmed=bool(
                    request.get("threshold_owner_confirmed", False)),
                release=bool(request.get("release", False)),
                release_threshold=(
                    float(request["release_threshold"])
                    if request.get("release_threshold") is not None else None
                ),
                content_payload=request.get("content_payload", {}),
            )
            validate_evaluation(run.to_dict())  # EVAL-004 fail-closed
        except GateError as error:
            return _deny(name, error.decision)
        run_dict = run.to_dict()
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="evaluation-run",
            rule_ids=("EVAL-003", "EVAL-004", "FBK-003"),
            payload={"passed": run.passed_count,
                     "total": run_dict.get("total_count", len(run.assertions)),
                     "all_passed": run.all_passed,
                     "release": bool(request.get("release", False)),
                     "fbk_003_statement": run_dict.get("fbk_003_statement", "")},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "evaluation", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "evaluation": {
                "passed": run.passed_count,
                "total": run_dict.get("total_count", len(run.assertions)),
                "all_passed": run.all_passed,
                "fbk_003_statement": run_dict.get("fbk_003_statement", "")}}
        return result

    # --- chatbi_correction -------------------------------------------------
    def _correction(name: str, func: Callable[..., Any],
                    args: Mapping[str, Any]) -> Any:
        from chatbi_governance.evaluator import (
            build_correction_record,
            validate_correction,
        )

        request = _request()
        corr = args.get("correction")
        if isinstance(corr, Mapping):
            request = {**request, **dict(corr)}
        action = str(request.get("action_type", "approve_metric"))
        actor = str(request.get("actor", "operator"))
        if config is not None:
            decision = decide(
                config,
                PolicyRequest(request_type=action, target_entity="",
                              actor=actor, purpose="governed protected action"),
            )
            if decision.status == "block":
                return _deny_raw(name, rule_ids=("SEM-003",),
                                 reason=decision.reason,
                                 recovery="Wait for the human owner to approve "
                                          "the protected action")
        try:
            record = build_correction_record(
                correction_id=str(request.get("correction_id", "")),
                fix_kind=str(request.get("fix_kind", "")),
                fix_target=str(request.get("fix_target", "")),
                fix_change_summary=str(request.get("fix_change_summary", "")),
                eval_case_assertion_id=str(
                    request.get("eval_case_assertion_id", "")),
                eval_case_expected_hash=str(
                    request.get("eval_case_expected_hash", "")),
                rule_ids=tuple(request.get("rule_ids",
                                           ("FBK-001", "FBK-002"))),
                owner_approved=False,
                description=str(request.get("description", "")),
            )
            validate_correction(record)
        except GateError as error:
            return _deny(name, error.decision)
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="correction-record",
            rule_ids=("FBK-001", "FBK-002", "FBK-003", "ABL-001"),
            payload={"correction_id": str(request.get("correction_id", "")),
                     "validated": True,
                     "owner_approved": False},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "correction", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "correction_validated": True}
        return result

    # --- chatbi_drift_report -----------------------------------------------
    def _drift_report(name: str, func: Callable[..., Any],
                      args: Mapping[str, Any]) -> Any:
        request = _request()
        fresh = args.get("fresh_inventory")
        if isinstance(fresh, Mapping):
            request = {**request, "fresh_inventory": dict(fresh)}
        scope_name = str(request.get("scope", "all"))
        if scope_name not in ("references", "sources", "models", "all"):
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason=f"unknown drift scope: {scope_name!r}",
                             recovery="Use one of references|sources|models|all")
        try:
            fresh_obj = None
            raw_fresh = request.get("fresh_inventory")
            if isinstance(raw_fresh, Mapping):
                fresh_obj = SourceInventory(
                    source_database=str(raw_fresh.get("source_database", "")),
                    tables=tuple(
                        SourceTable(
                            name=str(t.get("name", "")),
                            columns=tuple(
                                SourceColumn(
                                    name=str(c.get("name", "")),
                                    data_type=str(c.get("data_type", "")),
                                    is_primary_key=bool(
                                        c.get("is_primary_key", False)),
                                )
                                for c in t.get("columns", [])
                            ),
                        )
                        for t in raw_fresh.get("tables", [])
                    ),
                )
            report = detect_drift(
                workspace_root, config, scope=scope_name,
                since=request.get("since"),
                fresh_source_inventory=fresh_obj,
            )
        except GateError as error:
            # Missing baseline -> hard STOP (class-2 precondition).
            return _deny(name, error.decision)
        candidates = [
            c for class_candidates in report.classes.values()
            for c in class_candidates
        ]
        routes = []
        triage = False
        for candidate in candidates:
            decision = classify_finding(candidate)
            routes.append(decision.to_dict())
            if decision.target_command in ("owner", "STOP human triage"):
                triage = True
        persisted = write_state(
            workspace_root, _safe_session_id(scope.session_id or "drift"),
            "drift_report.json", report.to_dict(),
        )
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="drift-report",
            rule_ids=("HOOK-001", "PORT-001"),
            payload={"status": report.status, "scope": report.scope,
                     "candidate_count": len(candidates),
                     "routes": routes, "triage": triage},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "audit_drift", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "drift": {
                "status": report.status, "candidate_count": len(candidates),
                "routes": routes, "triage": triage,
                "persisted": str(persisted)}}
        return result

    # --- chatbi_init_diagnostic --------------------------------------------
    def _init_diagnostic(name: str, func: Callable[..., Any],
                         args: Mapping[str, Any]) -> Any:
        from .probe import probe_agno

        request = _request()
        payload = args.get("request")
        if isinstance(payload, Mapping):
            request = {**request, **dict(payload)}
        shared = request.get("shared_config") or request.get(
            "shared_config_path")
        if not shared:
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason="init requires a shared configuration path",
                             recovery="Provide a workspace-relative "
                                      "shared_config in the run request")
        shared_rel = Path(str(shared))
        shared_abs = (shared_rel if shared_rel.is_absolute()
                      else workspace_root / shared_rel)
        local = request.get("local_config") or request.get("local_config_path")
        local_abs = None
        if local:
            local_rel = Path(str(local))
            local_abs = (local_rel if local_rel.is_absolute()
                         else workspace_root / local_rel)
        manifest = probe_agno()
        runtime_ok = manifest.runtime_version != "unavailable"
        try:
            result = run_init_diagnostic(
                shared_abs, local_abs,
                probe=(lambda: _agno_capability_snapshot(runtime_ok)),
                workspace_root=workspace_root,
            )
        except Exception as error:  # noqa: BLE001 - fail-closed (HOOK-004)
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason=f"init diagnostic failed: "
                                    f"{type(error).__name__}",
                             recovery="Inspect the diagnostic chain and re-run")
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="init-diagnostic",
            rule_ids=("PORT-001", "SEC-003", "HOOK-004"),
            payload={"status": result.status,
                     "production_ready": result.production_ready,
                     "recovery_actions": list(result.recovery_actions)},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "init_diagnostic", entry)
        out = func(**args)
        if isinstance(out, Mapping):
            return {**dict(out), "diagnostic": {
                "status": result.status,
                "production_ready": result.production_ready,
                "checks": [check.to_dict() for check in result.checks],
                "recovery_actions": list(result.recovery_actions)}}
        return out

    # --- chatbi_bootstrap --------------------------------------------------
    def _bootstrap(name: str, func: Callable[..., Any],
                   args: Mapping[str, Any]) -> Any:
        request = _request()
        spec_raw = args.get("spec")
        if isinstance(spec_raw, Mapping):
            request = {**request, **dict(spec_raw)}
        try:
            spec = build_mysql_adapter_spec(
                str(request.get("host", "")),
                int(request.get("port", 0)),
                str(request.get("user", "")),
                database=str(request.get("database", "")),
                credential_env_name=request.get("credential_env_name"),
            )
            merged = merge_local_config(
                request.get("local_config")
                if isinstance(request.get("local_config"), dict) else None,
                path_bindings=request.get("path_bindings"),
                cli_adapters=request.get("cli_adapters"),
            )
            allowlist = tuple(request.get("cli_allowlist", []) or ())
            exe = resolve_executable("mysql", allowlist)
            if exe is None:
                return _deny_raw(
                    name, rule_ids=("SEC-001", "PORT-001"),
                    reason="resolve_executable failed (fail-closed)",
                    recovery="Confirm the mysql executable on the "
                             "operator allowlist")
            if native_runner is None:
                return _deny_raw(
                    name, rule_ids=("HOOK-004",),
                    reason="bootstrap requires a wired native runner "
                           "(fail-closed, FBK-003)",
                    recovery="Wire the runtime native runner before "
                             "bootstrapping")
            native = native_runner("chatbi-bootstrap", "run_mysql",
                                   {**request, "spec": spec,
                                    "local_config": merged,
                                    "executable": str(exe)})
            inventory_path = (native or {}).get("inventory_path") if isinstance(
                native, Mapping) else None
            if not inventory_path:
                return _deny_raw(name, rule_ids=("HOOK-004",),
                                 reason="mysql introspection produced no "
                                        "source inventory",
                                 recovery="Re-run the mysql introspection")
            inventory = read_source_inventory(Path(str(inventory_path)))
        except GateError as error:
            return _deny(name, error.decision)
        except Exception as error:  # noqa: BLE001 - fail-closed
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason=f"bootstrap failed: {type(error).__name__}",
                             recovery="Inspect the bootstrap chain and re-run")
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="bootstrap-inventory",
            rule_ids=("PORT-001", "SEC-003", "SEM-003"),
            payload={"source_database": inventory.source_database,
                     "table_count": len(inventory.tables)},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "bootstrap", entry)
        out = func(**args)
        if isinstance(out, Mapping):
            return {**dict(out), "bootstrap": {
                "status": "planned",
                "source_db": str(request.get("database", "")),
                "table_count": len(inventory.tables)}}
        return out

    _DISPATCH: dict[str, Callable[..., Any]] = {
        "chatbi_record_request": _record_request,
        "chatbi_record_evidence": _record_evidence,
        "chatbi_submit_candidate": _submit_candidate,
        "chatbi_review": _review,
        "chatbi_crosscheck": _crosscheck,
        "chatbi_build_plan": _build_plan,
        "chatbi_impact_manifest": _impact_manifest,
        "chatbi_registry_append": _registry_append,
        "chatbi_lint_reference": _lint_reference,
        "chatbi_evaluate": _evaluate,
        "chatbi_correction": _correction,
        "chatbi_drift_report": _drift_report,
        "chatbi_init_diagnostic": _init_diagnostic,
        "chatbi_bootstrap": _bootstrap,
    }

    def domain_hook(name: str, func: Callable[..., Any],
                    args: Mapping[str, Any]) -> Any:
        handler = _DISPATCH.get(name)
        if handler is None:
            return func(**args)
        try:
            return handler(name, func, args)
        except GateError as error:
            # Any kernel GateError escapes a handler -> fail-closed deny.
            return _deny(name, error.decision)
        except Exception as error:  # noqa: BLE001 - HOOK-004 fail-closed
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason=f"governance hook failed: {type(error).__name__}",
                recovery="Inspect the sanitized evidence and correct the "
                         "tool input")

    return domain_hook


def _agno_capability_snapshot(runtime_ok: bool) -> Any:
    """Agno-target capability snapshot for run_init_diagnostic injection.

    Honest detection (FBK-003): claude_available stays False (this runtime
    is not Claude Code); the runtime checks report the agno runtime state.
    """
    from chatbi_governance.diagnostics import CapabilitySnapshot

    # The Kernel's CapabilitySnapshot contract is Claude-shaped (module 2
    # kept the diagnostic vocabulary stable): doctor_status must be one of
    # the five Claude doctor states and available_adapters uses the
    # managed/cli/fixture id grammar. The honest Agno projection:
    # claude_available=False and the runtime checks report unavailable when
    # the agno runtime is not importable.
    return CapabilitySnapshot(
        claude_available=False,
        claude_version=None,
        doctor_status="pass" if runtime_ok else "unavailable",
        logged_in=None,
        sandbox_available=runtime_ok,
        available_adapters=("fixture",) if runtime_ok else (),
        evidence_source="synthetic",
    )


# ---------------------------------------------------------------------------
# Run-level guardrails
# ---------------------------------------------------------------------------


def _parse_run_input(content: Any) -> tuple[str, Mapping[str, Any] | None]:
    """Parse a structured run input.

    Returns ``(workflow_id, request)``. An envelope
    ``{"workflow_id": ..., "request": {...}}`` selects the workflow; a bare
    mapping is the request itself (default workflow ``chatbi-analyze``).
    Non-JSON / non-mapping content -> ``("", None)`` (free-text session
    input; entry is lenient, the terminal gate is authoritative).
    """
    if isinstance(content, Mapping):
        raw = dict(content)
    elif isinstance(content, str):
        text = content.strip()
        if not text:
            return "", None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return "", None
        if not isinstance(parsed, Mapping):
            return "", None
        raw = dict(parsed)
    else:
        return "", None
    if isinstance(raw.get("workflow_id"), str) and isinstance(
        raw.get("request"), Mapping
    ):
        return raw["workflow_id"], dict(raw["request"])
    return "chatbi-analyze", raw


class ChatbiRequestGuardrail(BaseGuardrail):
    """pre_hooks[0]: run-level request preflight (design §5.2 ①).

    Structured run inputs (ChatBI request JSON) are validated against
    ``request.schema.json`` for the analyze workflow; a violation raises
    ``InputCheckError`` carrying the minimal clarifying question (the
    Kernel decision's recovery) + the blocked rule IDs. Free-text session
    inputs pass (entry lenient); the terminal delivery gate re-checks the
    evidence chain for every entry point (③ — never fail-open).
    """

    def __init__(
        self,
        *,
        config: Any,
        event_log: Any,
        run_scope: RunScope | None = None,
        deployment: Any = None,
    ) -> None:
        self.config = config
        self.event_log = event_log
        self.run_scope = run_scope
        self.deployment = deployment

    def check(self, run_input: Any, run_context: Any = None) -> None:
        from agno.exceptions import InputCheckError

        content = getattr(run_input, "input_content", run_input)
        workflow_id, request = _parse_run_input(content)
        if request is None:
            return  # free-text session input (lenient entry)
        if self.run_scope is not None:
            self.run_scope.workflow_id = workflow_id or "chatbi-analyze"
            self.run_scope.request = request
            if run_context is not None:
                self.run_scope.run_id = (
                    getattr(run_context, "run_id", "") or self.run_scope.run_id
                )
                self.run_scope.session_id = (
                    getattr(run_context, "session_id", "")
                    or self.run_scope.session_id
                )
        if workflow_id != "chatbi-analyze":
            return  # other workflows validate inside their tool hooks
        try:
            validate_request(request)
        except GateError as error:
            from .events import emit_standard_event

            run_id = getattr(run_context, "run_id", "") or "run"
            session_id = getattr(run_context, "session_id", "") or "session"
            try:
                emit_standard_event(
                    self.event_log, run_id=run_id, session_id=session_id,
                    workflow_id="chatbi-analyze", step_id="request_preflight",
                    event_type="gate.blocked",
                    payload={"gate": "request_preflight",
                             "decision": error.decision.to_dict()},
                    evidence_refs=error.decision.evidence_refs,
                )
            except ValueError:
                pass  # no event log yet; the raise below is authoritative
            raise InputCheckError(
                message=(
                    "ChatBI request preflight failed: "
                    f"{error.decision.reason} — recovery: "
                    f"{error.decision.recovery}"
                ),
                additional_data={"rule_ids": list(error.decision.rule_ids)},
            )

    async def async_check(self, run_input: Any, run_context: Any = None) -> None:
        self.check(run_input, run_context)


class ChatbiPolicyGuardrail(BaseGuardrail):
    """pre_hooks[1]: trusted subject recording + SEM-003 intent precheck.

    Records the run-level trusted subject into the ``run_subject``
    contextvar — the subject comes from the run context ONLY (the verified
    user_id), never from the request body (SEC-003). For structured requests
    the Kernel ``policy.decide`` prechecks the declared protected intent
    (an agent-declared self-approval is blocked at the boundary, C005); the
    authoritative SEM-003 judgment still happens at the protected tool's
    approval hook.
    """

    def __init__(self, *, config: Any, event_log: Any,
                 deployment: Any = None) -> None:
        self.config = config
        self.event_log = event_log
        self.deployment = deployment

    def check(self, run_input: Any, run_context: Any = None,
              user_id: str | None = None) -> None:
        from agno.exceptions import InputCheckError

        subject = ""
        if isinstance(user_id, str) and user_id:
            subject = user_id
        elif run_context is not None:
            subject = getattr(run_context, "user_id", "") or ""
        if subject:
            run_subject.set(subject)
        content = getattr(run_input, "input_content", run_input)
        workflow_id, request = _parse_run_input(content)
        if request is None or self.config is None:
            return
        action_type = request.get("action_type")
        if not isinstance(action_type, str) or not action_type:
            return
        actor = request.get("actor") or subject or "operator"
        decision = decide(
            self.config,
            PolicyRequest(request_type=action_type, target_entity="",
                          actor=str(actor), purpose="governed protected action"),
        )
        if decision.status == "block":
            raise InputCheckError(
                message=(
                    f"protected-intent precheck blocked: {decision.reason} "
                    f"— recovery: {decision.recovery}"
                ),
                additional_data={"rule_ids": list(decision.rule_ids)},
            )

    async def async_check(self, run_input: Any, run_context: Any = None,
                          user_id: str | None = None) -> None:
        self.check(run_input, run_context, user_id)


class ChatbiDeliveryGuardrail(BaseGuardrail):
    """post_hooks[0]: the ONLY terminal authority (ADR-002).

    ``check(run_output)`` (spike R2 verified: agno 2.6.22
    ``filter_hook_args`` passes ``run_output`` by actual parameter name):

      1. read the run's evidence chain from the evidence index
         (T1/T2/T3/crosscheck/candidate/review/request);
      2. kernel delivery semantics (REV-001/002/003): a review PASS exists
         AND its candidate_sha == ``compute_candidate_sha(最终候选)`` (the
         final output, parsed as JSON when possible); rule_ids per failure
         mode (M5-S6);
      3. ``evidence.validate_provenance`` (17 fields, F1 contract) on the
         assembled provenance footer;
      4. PASS -> emit ``run.completed`` (payload.gate="delivery",
         decision="pass" — the contract validator enforces this) and return;
         failure -> emit ``gate.blocked`` + raise ``OutputCheckError``.

    The agno-native RunCompleted is never mapped to ChatBI completion —
    this kernel judgment is the only source (modification §6.2).
    """

    def __init__(
        self,
        *,
        config: Any,
        event_log: Any,
        evidence_index: Any,
        workspace_root: Path,
        harness_release: str,
        run_scope: RunScope | None = None,
        ir_workflows: Mapping[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.event_log = event_log
        self.evidence_index = evidence_index
        self.workspace_root = Path(workspace_root)
        self.harness_release = harness_release
        self.run_scope = run_scope
        self.ir_workflows = ir_workflows or {}

    # ------------------------------------------------------------------
    def _run_evidence_rows(self, run_id: str) -> list:
        rows = self.evidence_index.lookup(run_id=run_id) if self.evidence_index else []
        return rows or []

    def _read_entry(self, row: Any) -> dict[str, Any] | None:
        try:
            path = self.workspace_root / row.path
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, Mapping) else None
        except (OSError, ValueError, TypeError):
            return None

    def _tier_chain(self, run_id: str) -> tuple[dict[str, Any], ...]:
        """The (source_tier, content_sha256, payload) tier chain, in tier
        order, from the persisted .chatbi evidence (ADR-003 authority)."""
        chain: list[dict[str, Any]] = []
        for row in self._run_evidence_rows(run_id):
            entry = self._read_entry(row)
            if not entry:
                continue
            source = entry.get("evidence_source", "")
            if source in ("semantic-layer", "curated-reference",
                          "raw-exploration", "codebase-crosscheck"):
                chain.append({
                    "source_tier": entry.get("source_tier", ""),
                    "content_sha256": entry.get("content_sha256", ""),
                    "payload": entry.get("payload", {}),
                    "evidence_source": source,
                })
        order = {"T1": 0, "T2": 1, "T3": 2}
        chain.sort(key=lambda e: order.get(e["source_tier"], 9))
        return tuple(chain)

    def _latest_review(self, run_id: str) -> dict[str, Any] | None:
        review: dict[str, Any] | None = None
        for row in self._run_evidence_rows(run_id):
            entry = self._read_entry(row)
            if not entry or entry.get("evidence_source") != "candidate-review":
                continue
            payload = entry.get("payload") or {}
            # A BLOCKED review carries the failure-mode rule_ids in its
            # findings (the review hook writes them there); PASS reviews fall
            # back to the entry's declared rule set.
            findings = payload.get("findings") or []
            rule_ids = tuple(
                f for f in findings if isinstance(f, str)
            ) or tuple(entry.get("rule_ids", ()))
            review = {
                "status": payload.get("status"),
                "round": payload.get("round"),
                "candidate_sha": payload.get("candidate_sha"),
                "rule_ids": rule_ids,
                "reason": payload.get("reason", ""),
            }
        return review

    # ------------------------------------------------------------------
    def _ir_delivery_rules(self, workflow_id: str,
                           default: tuple[str, ...]) -> tuple[str, ...]:
        """The IR workflow's ``gates.delivery.rule_ids`` (fail-closed fallback
        to the module-5 defaults)."""
        workflow = self.ir_workflows.get(workflow_id)
        if workflow is not None and getattr(workflow, "gates", None) is not None:
            delivery = getattr(workflow.gates, "delivery", None)
            if delivery is not None and getattr(delivery, "rule_ids", ()):
                return tuple(delivery.rule_ids)
        return default

    def _run_evidence_sources(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Every recorded evidence entry for the run, keyed by source."""
        sources: dict[str, dict[str, Any]] = {}
        for row in self._run_evidence_rows(run_id):
            entry = self._read_entry(row)
            if not entry:
                continue
            source = entry.get("evidence_source", "")
            if source:
                sources[source] = entry
        return sources

    def _tool_blocked_rules(self, run_id: str) -> tuple[str, ...]:
        """Union of rule_ids across the run's tool.blocked events (the
        domain-hook denies are the specific verdicts for the generic
        workflows)."""
        rules: list[str] = []
        seen: set[str] = set()
        try:
            events = self.event_log.replay(run_id).events
        except Exception:  # noqa: BLE001 - no log -> no blocked signal
            return ()
        for event in events:
            if event.get("event_type") != "tool.blocked":
                continue
            payload = event.get("payload") or {}
            for rule in payload.get("rule_ids", []) or []:
                if isinstance(rule, str) and rule not in seen:
                    seen.add(rule)
                    rules.append(rule)
        return tuple(rules)

    def _approval_resolved(self, run_id: str) -> bool:
        """True when the run's event log carries an approval.resolved event
        with resolution="approved" (the AgentOS HITL confirmation passed
        Kernel re-verification in the approval_verify_hook)."""
        try:
            events = self.event_log.replay(run_id).events
        except Exception:  # noqa: BLE001 - no log -> not resolved
            return False
        for event in events:
            if event.get("event_type") != "approval.resolved":
                continue
            payload = event.get("payload") or {}
            if payload.get("resolution") == "approved":
                return True
        return False

    def _check_generic(self, run_id: str, session_id: str,
                       workflow_id: str) -> tuple[tuple[str, ...], str, str]:
        """Non-analyze delivery verdict (E-series semantics, mirroring the
        module-5 per-workflow verdict dispatch):

        - any tool.blocked deny -> block with its rule_ids;
        - init: the diagnostic evidence status BLOCKED -> block with the IR
          rule set;
        - bootstrap/bfr/evaluate/audit-drift: their recorded evidence
          decides pass/block with the IR rule set;
        - maintain-model/correction: a protected-action run reaching the
          terminal gate without the approval resolution is blocked.
        Returns ``(rule_ids, reason, recovery)``; empty rule_ids = PASS.
        """
        blocked_rules = self._tool_blocked_rules(run_id)
        if blocked_rules:
            return (blocked_rules,
                    "a governance tool was denied (see tool.blocked)",
                    "Resolve the blocked tool's recovery action and re-run")
        sources = self._run_evidence_sources(run_id)
        ir_rules = self._ir_delivery_rules(
            workflow_id, ("PORT-001", "SEC-003", "HOOK-004"))
        if workflow_id == "chatbi-init":
            diag = sources.get("init-diagnostic", {})
            payload = diag.get("payload") or {}
            if payload.get("status") == "BLOCKED":
                return (ir_rules,
                        "init diagnostic reports blocking failures "
                        "(production_ready stays False)",
                        "; ".join(payload.get("recovery_actions", []) or [])
                        or "Fix the blocked checks and re-run init")
            if not diag:
                return (ir_rules, "init diagnostic did not run",
                        "Re-run the init diagnostic")
            return (), "", ""
        if workflow_id == "chatbi-bootstrap":
            if not sources.get("bootstrap-inventory"):
                return (ir_rules,
                        "bootstrap did not produce a validated source "
                        "inventory",
                        "Re-run the bootstrap chain")
            return (), "", ""
        if workflow_id == "chatbi-build-from-requirement":
            if not sources.get("build-plan"):
                return (ir_rules,
                        "SRC-002 route not resolved to a validated build "
                        "plan (route A requires owner adjudication, "
                        "REQ-001/002)",
                        "Resolve the SRC-002 route and re-run")
            return (), "", ""
        if workflow_id == "chatbi-evaluate":
            run_entry = sources.get("evaluation-run", {})
            payload = run_entry.get("payload") or {}
            if not run_entry or not payload.get("all_passed"):
                return (ir_rules,
                        "evaluation release gate not passed (EVAL-004)",
                        "Meet the owner-confirmed release threshold and "
                        "re-run")
            return (), "", ""
        if workflow_id == "chatbi-audit-drift":
            if not sources.get("drift-report"):
                return (ir_rules, "drift audit produced no report",
                        "Fix the drift detection chain and re-run")
            return (), "", ""
        if workflow_id == "chatbi-maintain-knowledge":
            if not sources.get("knowledge-lint"):
                return (ir_rules,
                        "reference lint found issues (DOC-002/003)",
                        "Resolve the lint issues via the governed reference "
                        "authoring flow")
            return (), "", ""
        # maintain-model / correction (M-2, eval round 1): the protected
        # action pauses at the AgentOS HITL boundary; once the human-owner
        # approval is RESOLVED (approval.resolved=approved after Kernel
        # re-verification) and the governed record exists, the workflow has a
        # completion path — never a dead-end block.
        if workflow_id == "chatbi-maintain-model":
            if self._approval_resolved(run_id) and sources.get(
                "model-registry"
            ):
                return (), "", ""
            return (ir_rules,
                    "protected-action approval not resolved or the model "
                    "registry record is missing (DOC-004/SEM-003)",
                    "Resolve the human-owner approval and re-run")
        if workflow_id == "chatbi-correction":
            if self._approval_resolved(run_id) and sources.get(
                "correction-record"
            ):
                return (), "", ""
            return (ir_rules,
                    "protected-action approval not resolved or the "
                    "correction record is missing (FBK-002/SEM-003)",
                    "Resolve the human-owner approval and re-run")
        if not sources:
            return (ir_rules,
                    "no governed evidence was recorded for the run",
                    "Re-run the governed flow with the required evidence")
        return (ir_rules,
                "delivery gate requirement not met for this workflow",
                "Complete the governed flow and re-run")

    def _final_candidate(self, run_output: Any) -> Any:
        content = getattr(run_output, "content", None)
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, Mapping):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return content

    def _assemble_footer(self, run_id: str, workflow_id: str,
                         request: Mapping[str, Any],
                         tier_chain: tuple[dict[str, Any], ...],
                         review: dict[str, Any] | None) -> dict[str, Any]:
        """Assemble the provenance footer (F1, 17 fields) from the evidence
        chain + request — mirrors the old footer_assembly step."""
        tiers = [e["source_tier"] for e in tier_chain if e["source_tier"]]
        source_tier = tiers[-1] if tiers else "T1"
        # C004: raw exploration (T3) is lower-confidence evidence — the
        # semantic is registered by the tier, not a payload marker (the
        # payload hash must stay the golden-pinned content).
        low_confidence = "T3" in tiers
        return {
            "question": request.get("question", ""),
            "time_range": request.get("time_range", ""),
            "entity": request.get("entity", ""),
            "segment": request.get("segment", ""),
            "method": "governed_analysis_agno",
            "source_tier": source_tier,
            "filters": ["time_range:last_month"],
            "inclusions": ["semantic_layer" if source_tier == "T1"
                           else "curated_references"],
            "exclusions": [],
            "denominator": "none",
            "quality": "governed_evidence",
            "limitations": (
                "raw exploration fallback requires high-risk review warning "
                "(ANS-003)" if low_confidence else "governed evidence chain"
            ),
            "review_round": (review or {}).get("round") or 1,
            "freshness": "snapshot_2024_01" if low_confidence else "current",
            "owner": "domain_owner_example",
            "confidence": "low" if low_confidence else "medium",
            "provenance_refs": [f"evidence:run:{run_id}"],
        }

    # ------------------------------------------------------------------
    def check(self, run_output: Any, run_context: Any = None) -> None:
        from agno.exceptions import OutputCheckError

        from .events import emit_standard_event

        run_id = getattr(run_output, "run_id", "") or (
            getattr(run_context, "run_id", "") or "run"
        )
        session_id = getattr(run_output, "session_id", "") or (
            getattr(run_context, "session_id", "") or "session"
        )
        # A native Agent's RunOutput carries no workflow_id (the envelope
        # routing lives in the run scope, set by the RequestGuardrail) —
        # the scope is the authoritative workflow selector (M7 note).
        scope_workflow = ""
        if self.run_scope is not None:
            scope_workflow = self.run_scope.workflow_id or ""
        workflow_id = (
            scope_workflow
            or getattr(run_output, "workflow_id", "")
            or (getattr(run_context, "workflow_id", "") or "")
            or "chatbi-analyze"
        )
        if self.run_scope is not None:
            self.run_scope.run_id = run_id
            self.run_scope.session_id = session_id
            self.run_scope.workflow_id = workflow_id
            self.run_scope.request = self.run_scope.request or {}

        tier_chain = self._tier_chain(run_id)
        review = self._latest_review(run_id)

        rule_ids: tuple[str, ...] = ()
        reason = ""
        recovery = "Re-run the governed flow with a complete evidence chain"
        if workflow_id == "chatbi-analyze":
            if not tier_chain and review is None:
                rule_ids = ("REV-003", "HOOK-004")
                reason = ("no evidence chain and no review were recorded; "
                          "the candidate cannot be delivered (C002)")
            elif review is None:
                rule_ids = ("REV-001", "REV-003")
                reason = "no independent review was recorded (REV-001/002/003)"
            elif review.get("status") != "PASS":
                rule_ids = tuple(review.get("rule_ids") or _RULES_NOT_PASS)
                reason = review.get("reason") or (
                    "review verdict is not a clean PASS for the frozen "
                    "candidate")
                recovery = "Address every blocking finding and re-review"
            else:
                final_candidate = self._final_candidate(run_output)
                try:
                    final_sha = compute_candidate_sha(final_candidate)
                except (TypeError, ValueError):
                    final_sha = ""
                if review.get("candidate_sha") != final_sha:
                    rule_ids = _RULES_STALE_SHA
                    reason = (
                        "final candidate changed after the review PASS; "
                        "REV-001: the answer must be re-reviewed")
                    recovery = "Re-submit the reviewed candidate unchanged"
                else:
                    rule_ids = ()
        else:
            # Generic workflows: per-workflow delivery verdict (E-series).
            rule_ids, reason, recovery = self._check_generic(
                run_id, session_id, workflow_id)

        if rule_ids:
            decision = GateDecision.block(
                rule_ids=rule_ids,
                evidence_refs=("evidence:candidate-review",),
                reason=reason,
                recovery=recovery,
            )
            try:
                emit_standard_event(
                    self.event_log, run_id=run_id, session_id=session_id,
                    workflow_id=workflow_id, step_id="delivery_gate",
                    event_type="gate.blocked",
                    payload={"gate": "delivery",
                             "decision": decision.to_dict()},
                    evidence_refs=decision.evidence_refs,
                )
            except ValueError:
                pass  # the raise below is authoritative
            raise OutputCheckError(
                message=(
                    f"ChatBI delivery gate blocked: {reason} — recovery: "
                    f"{recovery}"
                ),
                additional_data={"rule_ids": list(rule_ids)},
            )

        # PASS: provenance footer (F1 contract) then run.completed (ADR-002).
        # The footer contract is ANALYZE-specific (the E-series workflows
        # carry their own governed evidence; no analyze footer exists).
        if workflow_id == "chatbi-analyze":
            request = self.run_scope.request if self.run_scope is not None else {}
            try:
                footer = self._assemble_footer(
                    run_id, workflow_id, request, tier_chain, review)
                validate_provenance(footer)
            except GateError as error:
                decision = error.decision
                try:
                    emit_standard_event(
                        self.event_log, run_id=run_id, session_id=session_id,
                        workflow_id=workflow_id, step_id="footer_assembly",
                        event_type="gate.blocked",
                        payload={"gate": "delivery",
                                 "decision": decision.to_dict()},
                        evidence_refs=decision.evidence_refs,
                    )
                except ValueError:
                    pass
                raise OutputCheckError(
                    message=(
                        f"ChatBI provenance footer failed: {decision.reason} "
                        f"— recovery: {decision.recovery}"
                    ),
                    additional_data={"rule_ids": list(decision.rule_ids)},
                )

        emit_standard_event(
            self.event_log, run_id=run_id, session_id=session_id,
            workflow_id=workflow_id, step_id="delivery_gate",
            event_type="run.completed",
            payload={"gate": "delivery", "decision": "pass",
                     "candidate_sha": (review or {}).get("candidate_sha", "")},
            evidence_refs=("evidence:candidate-review",),
        )

    async def async_check(self, run_output: Any, run_context: Any = None) -> None:
        self.check(run_output, run_context)
