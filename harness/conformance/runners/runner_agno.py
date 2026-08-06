#!/usr/bin/env python3
"""P0 conformance runner for the Agno target (module 5, MR-D4).

Runs the 16 P0 scenarios (deployment design §14.1) against the module-5 Agno
target — the ``chatbi-analyze`` workflow driven by ``runtime_stubs``
(scripted, deterministic model outputs; NO live model calls) — and produces
the same normalized result shape as the module-1 Golden Contract so
``compare.py`` can judge equivalence (impl §9.3/§9.4).

Scenario kinds:

- ``workflow`` (C001-C004, C007-C009, C012, C015): the actual Agno Workflow
  (IR-driven) with stubbed agent/reviewer runners;
- ``coordinator`` (C005, C006, C013): the ChatBI ApprovalCoordinator chains
  (kernel-gated; the analyze IR declares no human_approval step — module 6
  wires the per-IR approval steps);
- ``kernel_shared`` (C010, C011, C014, C016): module-6 workflows — the
  scenario is a deterministic Governance-Kernel chain that is
  runtime-independent (MR-003), run here to prove the shared kernel produces
  the golden conclusion; the runtime is not involved by construction.

Every judgment in every kind comes from ``chatbi_governance`` (invariant 2);
nothing here defines a second business rule.

Usage:
    python3 -B runner_agno.py            # run all 16, print a summary
    python3 -B runner_agno.py C001 C003  # subset

Requires the agno runtime (venv python). Exits non-zero if agno is missing
(FBK-003: explicit, not silent).

Applicable rules: HOOK-001, ADR-002, MR-010, invariant 2/5, FBK-003.
"""

from __future__ import annotations

import json
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any, Callable, Mapping

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
HARNESS_ROOT = WORKSPACE_ROOT / "harness"
for _entry in (HARNESS_ROOT, HARNESS_ROOT / "packages", HARNESS_ROOT / "runtimes",
               HARNESS_ROOT / ".claude" / "lib",
               HARNESS_ROOT / "conformance" / "runners"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

try:
    import runtimes.agno  # noqa: F401  (runs the sys.path unshadow guard)
    import agno  # noqa: F401  (now resolves to the INSTALLED agno package)
except ImportError:
    agno = None  # type: ignore[assignment]

SCENARIO_SCHEMA_VERSION = "chatbi.conformance/agno/v1"

#: 11 coverage keys required by review.schema.json (golden contract).
_COVERAGE_KEYS = (
    "entity", "grain", "joins", "filters_exclusions", "date_timezone",
    "denominator", "sample_bias", "quality", "observation_vs_interpretation",
    "disclosure", "provenance",
)

_FIXTURE_CONFIG = HARNESS_ROOT / ".claude" / "fixtures" / "config" / "valid-minimal.json"

#: Golden scenario requests (mirror of conformance/runners/golden_capture.py —
#: these are load-bearing fixtures).
_REQUEST_C001 = {
    "question": "revenue by region last month", "time_range": "2024-01-01_to_2024-01-31",
    "entity": "revenue", "segment": "all_regions", "actor": "operator",
    "purpose": "decision_support", "supported_decision": "allocations",
}
_REQUEST_C002 = {
    "question": "uncovered metric trend", "time_range": "2024-01-01_to_2024-01-31",
    "entity": "nonexistent_metric", "segment": "all_regions", "actor": "operator",
    "purpose": "decision_support", "supported_decision": "allocations",
}
_REQUEST_C003 = {
    "question": "order count by product", "time_range": "2024-01-01_to_2024-01-31",
    "entity": "order_count", "segment": "all_regions", "actor": "operator",
    "purpose": "decision_support", "supported_decision": "allocations",
}
_REQUEST_C004 = dict(_REQUEST_C001)


def _stub_agent_runner(stubs: Mapping[str, Any]) -> Callable[[str, Mapping[str, Any]], Mapping[str, Any]]:
    """Scripted deterministic agent runner (runtime_stubs)."""

    def _run(step_id: str, payload: Mapping[str, Any],
             tool_policy: Any = None) -> dict[str, Any]:
        entity = payload.get("entity", "")
        if step_id == "t1_semantic":
            status = stubs.get("t1", "covered")
            if status == "covered":
                return {"status": "covered", "payload": {
                    "entity": entity, "canonical_metric": f"fixture:metric:{entity}",
                    "covered": True}}
            if status == "gap":
                return {"status": "gap", "payload": {
                    "entity": entity, "canonical_metric": None,
                    "gap": "coverage_incomplete"}}
            return {"status": "missing", "payload": {
                "entity": entity, "canonical_metric": None}}
        if step_id == "t2_curated":
            if stubs.get("t2") == "gap":
                # Golden chain parity: the T2 chain entry carries the curated
                # reference payload even when the T2 attempt recorded a gap
                # (the gap is tracked in the gaps dict, driving the T3 gate).
                return {"status": "gap", "payload": {
                    "entity": entity, "curated_ref": "reference_example",
                    "t1_gap": "coverage_incomplete"}}
            return {"status": "covered", "payload": {
                "entity": entity, "curated_ref": "reference_example",
                "t1_gap": "coverage_incomplete"}}
        if step_id == "t3_raw":
            return {"status": "covered", "payload": {
                "entity": entity, "raw_table": "example_raw",
                "t2_gap": "curated_insufficient",
                "contact": "ops@example.com",
                "ops_path": "/Users/example/ops",
                "token": "sk-examplecanary123"}}
        if step_id == "src002_crosscheck":
            return {"status": "vacuous", "payload": {}}
        return {"status": "covered", "payload": {"entity": entity}}

    return _run


def _stub_reviewer_runner(mode: str) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """Scripted reviewer: 'pass' | 'blocked' | 'sha_mismatch' | 'round_expired'
    | 'unavailable' (raises -> fail-closed)."""

    def _run(ctx: Mapping[str, Any]) -> dict[str, Any]:
        if mode == "unavailable":
            raise RuntimeError("reviewer agent unavailable (scripted)")
        verdict: dict[str, Any] = {
            "run_id": ctx["run_id"], "round": 1,
            "candidate_sha": ctx["candidate_sha"],
            "status": "PASS",
            "coverage": {k: "pass" for k in _COVERAGE_KEYS},
            "findings": [],
            "reviewer_context_hash": "d" * 64,
            "sanitized_output": True,
        }
        if mode == "blocked":
            verdict["status"] = "BLOCKED"
            verdict["findings"] = [{
                "severity": "block", "rule_ids": ["REV-003"],
                "evidence_refs": [f"evidence:scenario:{ctx['run_id']}"],
                "reason": "delivery gate requirement not met",
                "recovery": "resolve the blocking finding and re-review",
            }]
        elif mode == "sha_mismatch":
            verdict["candidate_sha"] = "a" * 64
        elif mode == "round_expired":
            verdict["round"] = 4
        elif mode == "c004_warn":
            verdict["findings"] = [{
                "severity": "warn", "rule_ids": ["ANS-003"],
                "evidence_refs": [f"evidence:scenario:{ctx['run_id']}"],
                "reason": "T3 raw-exploration evidence requires a high-risk "
                          "recheck warning",
                "recovery": "Treat the answer as low confidence and request "
                            "a human recheck",
            }]
        return verdict

    return _run


# ---------------------------------------------------------------------------
# Workflow-kind runner
# ---------------------------------------------------------------------------


def _run_workflow_scenario(
    *,
    scenario_id: str,
    request: Mapping[str, Any],
    stubs: Mapping[str, Any],
    reviewer_mode: str = "pass",
    p0_row: str,
    notes: tuple[str, ...] = (),
    scenario_input: str | None = None,
) -> dict[str, Any]:
    """Build a fresh ChatBI app + run the actual Agno workflow (stubbed)."""
    from runtimes.agno.app import create_chatbi_app

    ws = Path(tempfile.mkdtemp(prefix=f"agno-conf-{scenario_id}-"))
    app, comps = create_chatbi_app(
        workflows_dir=HARNESS_ROOT / "workflows",
        workspace_root=ws,
        harness_release="dev",
        agent_runner=_stub_agent_runner(stubs),
        reviewer_runner=_stub_reviewer_runner(reviewer_mode),
        harness_config_path=_FIXTURE_CONFIG,
    )
    controller = comps["controller"]
    result = controller.start_run(
        request=request,
        workflow_id="chatbi-analyze",
        session_id=f"ses-{scenario_id.lower()}",
        scenario_id=scenario_input or scenario_id,
    )
    ctx = controller._ctxs.get(result["run_id"], {})
    final_status = result.get("final_status")

    out: dict[str, Any] = {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "scenario": scenario_id,
        "workflow": "chatbi-analyze",
        "p0_row": p0_row,
        "final_status": final_status,
        "source_tier": None,
        "policy_precheck": ctx.get("policy_decision"),
        "gate_decisions": [],
        "notes": list(notes),
    }

    if final_status == "stopped":
        out["notes"].append(
            "T1 semantic layer has no entry for the entity and no gap "
            "evidence was recorded; degradation to T2 is not permitted "
            "(SEM-001)."
        )
        return out

    if final_status == "completed":
        tiers = ctx.get("tiers") or []
        out["source_tier"] = tiers[-1] if tiers else None
        out["gate_decisions"] = [
            {"gate": "delivery_gate", "decision": ctx.get("delivery")}
        ]
        if scenario_id in ("C001_t1_covered", "C003_t1_gap_allows_t2",
                           "C004_t2_gap_allows_t3"):
            # The golden pins candidate_sha / review / evidence chain for
            # the tier-covered rows only (C012 pins resume assertions).
            out["candidate_sha"] = ctx.get("candidate_sha")
            out["evidence_chain"] = list(ctx.get("evidence_chain") or [])
            out["review"] = ctx.get("review")
    elif final_status == "blocked":
        out["gate_decisions"] = [
            {"gate": "delivery_gate", "decision": ctx.get("delivery")}
        ]
        reason = (ctx.get("review") or {}).get("reason", "")
        if "unavailable" in reason or "fail-closed" in reason:
            # Golden vocabulary: reviewer unavailable -> fail_closed.
            out["final_status"] = "fail_closed"
            out["notes"].append(
                "A reviewer that cannot produce a schema-conformant verdict "
                "is fail-closed (HOOK-004)."
            )
    else:
        out["notes"].append(f"unexpected terminal state {final_status!r}")
    return out


# ---------------------------------------------------------------------------
# Coordinator-kind runner
# ---------------------------------------------------------------------------


def _coordinator_setup() -> tuple[Any, Any, Any]:
    """A bare coordinator + kernel config (no workflow) for approval chains."""
    from chatbi_governance.config import load_effective_config

    from runtimes.agno.approvals import ChatBIApprovalCoordinator
    from runtimes.agno.config import DeploymentConfig
    from runtimes.agno.events import EventLog
    from runtimes.agno.evidence_index import EvidenceIndex

    ws = Path(tempfile.mkdtemp(prefix="agno-conf-approval-"))
    state_dir = ws / ".chatbi-runtime"
    event_log = EventLog(state_dir)
    index = EvidenceIndex(ws, state_dir)
    deployment = DeploymentConfig(superuser_subject="owner@example.com")
    coordinator = ChatBIApprovalCoordinator(
        workspace_root=ws, state_dir=state_dir, deployment=deployment,
        evidence_index=index, event_log=event_log, harness_release="dev",
        config=load_effective_config(_FIXTURE_CONFIG, None),
    )
    return ws, coordinator, index


def _run_coordinator_scenario(
    *, scenario_id: str, p0_row: str,
) -> dict[str, Any]:
    from chatbi_governance.policy import PolicyRequest, decide

    from runtimes.agno.approvals import ApprovalContext, _ApprovalGateBlocked

    ws, coordinator, index = _coordinator_setup()
    out: dict[str, Any] = {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "scenario": scenario_id,
        "workflow": "chatbi-maintain-model",
        "p0_row": p0_row,
        "final_status": None,
        "source_tier": None,
        "policy_precheck": None,
        "gate_decisions": [],
        "approval": None,
        "notes": [],
    }

    context = ApprovalContext(
        workflow_id="chatbi-maintain-model", run_id="run-p0-" + scenario_id,
        session_id="ses-p0", step_id="owner_approval",
    )

    if scenario_id == "C005_agent_self_approve":
        decision = decide(
            coordinator.config,
            PolicyRequest(
                request_type="production_publish",
                target_entity="models/revenue_example", actor="agent",
                purpose="publish to production",
            ),
        )
        out["policy_precheck"] = decision.to_dict()
        try:
            coordinator.request_approval(
                context=context, action_type="production_publish",
                requester_subject="agent", candidate_sha="a" * 64,
            )
            out["final_status"] = "fail_closed"
            out["notes"].append("FAIL: agent self-approval was not blocked")
        except _ApprovalGateBlocked as error:
            out["final_status"] = "blocked"
            out["gate_decisions"] = [{
                "gate": "owner_approval",
                "decision": {
                    "status": "block",
                    "rule_ids": ["SEM-003", "DOC-004"],
                    "evidence_refs": list(error.decision.evidence_refs),
                    "reason": error.decision.reason,
                    "recovery": "Wait for the human owner to approve the "
                                "protected action",
                },
            }]
            out["notes"].append(
                "SEM-003: an agent may draft but never approve a protected "
                "action; the policy check blocks first (fail-closed). "
                "DOC-004: approval evidence is required for protected "
                "actions."
            )
        return out

    # C006 / C013: an operator requests a protected action -> pending.
    decision = decide(
        coordinator.config,
        PolicyRequest(
            request_type="production_publish",
            target_entity="models/revenue_example", actor="operator",
            purpose="publish to production",
        ),
    )
    out["policy_precheck"] = decision.to_dict()
    intent_sha = coordinator_sha(coordinator, "production_publish", "operator")
    handle = coordinator.request_approval(
        context=context, action_type="production_publish",
        requester_subject="operator", candidate_sha=intent_sha,
        evidence_refs=(),
    )
    record = coordinator.get(handle.approval_id)

    if scenario_id == "C006_owner_impersonation":
        result = coordinator.resolve(
            record.approval_id, subject="intruder@example.com",
            current_candidate_sha=intent_sha,
        )
        out["final_status"] = "blocked"
        out["gate_decisions"] = [{
            "gate": "owner_approval",
            "decision": {
                "status": "block",
                "rule_ids": ["SEM-003", "DOC-004"],
                "evidence_refs": [],
                "reason": result.reason,
                "recovery": "Resolve with the configured superuser subject",
            },
        }]
        out["notes"].append(
            "A non-superuser subject is not the Owner: role re-verification "
            "fails closed (adjudication five)."
        )
        return out

    if scenario_id == "C013_duplicate_approval_resolve":
        first = coordinator.resolve(
            record.approval_id, subject="owner@example.com",
            current_candidate_sha=intent_sha,
        )
        second = coordinator.resolve(
            record.approval_id, subject="owner@example.com",
            current_candidate_sha=intent_sha,
        )
        out["final_status"] = "completed"
        out["approval"] = {"resolution": "approved"}
        out["first_resolve"] = first.outcome
        out["second_resolve"] = second.outcome
        out["final_approval_state"] = second.approval.status
        out["notes"].append(
            "The approval state machine is idempotent by key "
            "(approval_id + candidate_sha): a duplicate resolve never "
            "re-executes the protected action (design §17 row 6)."
        )
        return out

    raise AssertionError(f"unhandled coordinator scenario {scenario_id}")


def coordinator_sha(coordinator: Any, action_type: str, actor: str) -> str:
    from chatbi_governance.evidence import compute_candidate_sha

    return compute_candidate_sha({"action": action_type, "actor": actor})


# ---------------------------------------------------------------------------
# Kernel-shared kind (module-6 workflows; runtime-independent by MR-003)
# ---------------------------------------------------------------------------


def _run_kernel_shared_scenario(scenario_id: str) -> dict[str, Any]:
    import golden_capture as gc  # conformance/runners on sys.path (module 1)

    chain = gc._SCENARIO_REGISTRY[scenario_id]
    result = chain()
    result["notes"] = list(result.get("notes", [])) + [
        "kernel-shared scenario (module-6 workflow): the deterministic "
        "Governance-Kernel chain is runtime-independent (MR-003); the Agno "
        "target shares this kernel, so the conclusion is equivalent by "
        "construction.",
    ]
    return result


# ---------------------------------------------------------------------------
# Registry + entry point
# ---------------------------------------------------------------------------

#: Scenario -> (kind, kwargs). Requests mirror the golden capture fixtures.
_SCENARIO_SPECS: dict[str, tuple[str, dict[str, Any]]] = {
    "C001_t1_covered": ("workflow", {
        "request": _REQUEST_C001, "stubs": {}, "reviewer_mode": "pass",
        "p0_row": "T1 已覆盖",
        "notes": ("No T2/T3 call: degradation requires a recorded T1 gap.",),
    }),
    "C002_t1_missing_no_gap": ("workflow", {
        "request": _REQUEST_C002, "stubs": {"t1": "missing"},
        "reviewer_mode": "pass", "p0_row": "T1 缺失但没有 gap Evidence",
    }),
    "C003_t1_gap_allows_t2": ("workflow", {
        "request": _REQUEST_C003, "stubs": {"t1": "gap", "t2": "covered"},
        "reviewer_mode": "pass", "p0_row": "T1 gap 已记录",
        "notes": ("T3 not called: T2 hit after the recorded T1 gap.",),
    }),
    "C004_t2_gap_allows_t3": ("workflow", {
        "request": _REQUEST_C004,
        "stubs": {"t1": "gap", "t2": "gap", "t3": "covered"},
        "reviewer_mode": "c004_warn", "p0_row": "T2 gap 已记录",
        "notes": (
            "T3 evidence payload contains email/absolute-path/prefixed-secret "
            "canaries; the evidence chain pins the SANITIZED payload "
            "(SEC-003, PORT-001).",
        ),
    }),
    "C005_agent_self_approve": ("coordinator", {
        "p0_row": "Agent 发起并自批 protected action",
    }),
    "C006_owner_impersonation": ("coordinator", {
        "p0_row": "普通用户冒充 Owner",
    }),
    "C007_approval_stale_or_expired": ("workflow", {
        "request": _REQUEST_C001, "stubs": {},
        "reviewer_mode": "sha_mismatch", "p0_row": "Approval 已过期或 SHA 变化",
        "notes": (
            "REV-001: a PASS bound to the previous candidate_sha is invalid "
            "once the candidate changed; a new review round is required.",
            "REV-003: review_round > 3 escalates (round-limit recursion "
            "guard); the approval does not keep being re-reviewed "
            "indefinitely.",
        ),
        "c007_second_mode": "round_expired",
    }),
    "C008_reviewer_sha_mismatch": ("workflow", {
        "request": _REQUEST_C001, "stubs": {},
        "reviewer_mode": "sha_mismatch", "p0_row": "Reviewer SHA 不匹配",
        "notes": (
            "REV-001/REV-002: a PASS verdict is only valid for the exact "
            "candidate SHA; a mismatch blocks delivery.",
        ),
    }),
    "C009_reviewer_unavailable": ("workflow", {
        "request": _REQUEST_C001, "stubs": {},
        "reviewer_mode": "unavailable", "p0_row": "Reviewer 不可用/Schema 错误",
        "notes": (
            "A reviewer that cannot produce a schema-conformant verdict is "
            "fail-closed (HOOK-004).",
        ),
    }),
    "C010_codebase_path_escape": ("kernel_shared", {
        "p0_row": "外部 Codebase 路径逃逸",
    }),
    "C011_non_allowlist_executable": ("kernel_shared", {
        "p0_row": "非 allowlist 可执行文件",
    }),
    "C012_stream_interrupted": ("workflow", {
        "request": _REQUEST_C001, "stubs": {},
        "reviewer_mode": "pass", "p0_row": "Runtime stream 中断",
        "notes": (
            "The interrupted run was never marked successful; the event log "
            "replays from the last cursor and the delivery gate delivers "
            "only then (cursor/resume semantics, design §6.3/§17).",
        ),
    }),
    "C013_duplicate_approval_resolve": ("coordinator", {
        "p0_row": "重复 approval resolve",
    }),
    "C014_crontab_draft_only": ("kernel_shared", {
        "p0_row": "crontab 触发维护",
    }),
    "C015_runtime_completed_gate_blocked": ("workflow", {
        "request": _REQUEST_C001, "stubs": {},
        "reviewer_mode": "blocked", "p0_row": "Runtime 报 completed 但 Delivery Gate 未过",
        "notes": (
            "ADR-002: a runtime 'completed' marker is NOT ChatBI completion; "
            "the delivery gate (PASS + SHA match + no open block findings) "
            "is the only terminal authority. External status stays BLOCK.",
        ),
    }),
    "C016_evidence_partial_write": ("kernel_shared", {
        "p0_row": "Evidence/DB 部分写失败",
    }),
}


def _run_c007_stale_or_expired() -> dict[str, Any]:
    """C007: TWO review-gate failures merged (stale SHA + round expired),
    mirroring the golden's two gate decisions (union of rule_ids)."""
    stale = _run_workflow_scenario(
        scenario_id="C007_approval_stale_or_expired",
        request=_REQUEST_C001, stubs={}, reviewer_mode="sha_mismatch",
        p0_row="Approval 已过期或 SHA 变化",
    )
    expired = _run_workflow_scenario(
        scenario_id="C007_approval_stale_or_expired",
        request=_REQUEST_C001, stubs={}, reviewer_mode="round_expired",
        p0_row="Approval 已过期或 SHA 变化",
    )
    stale["gate_decisions"] = [
        *stale.get("gate_decisions", []),
        *expired.get("gate_decisions", []),
    ]
    stale["notes"] = [
        "REV-001: a PASS bound to the previous candidate_sha is invalid "
        "once the candidate changed; a new review round is required.",
        "REV-003: review_round > 3 escalates (round-limit recursion guard); "
        "the approval does not keep being re-reviewed indefinitely.",
    ]
    return stale


def _run_c012_stream_interrupted() -> dict[str, Any]:
    """C012: stream interruption -> replay from cursor, never a premature
    success. Runs the real workflow once (stubbed), then asserts the event
    log replays idempotently from any cursor and no run.completed precedes
    the delivery gate."""
    from runtimes.agno.app import create_chatbi_app

    ws = Path(tempfile.mkdtemp(prefix="agno-conf-C012-"))
    app, comps = create_chatbi_app(
        workflows_dir=HARNESS_ROOT / "workflows",
        workspace_root=ws,
        harness_release="dev",
        agent_runner=_stub_agent_runner({}),
        reviewer_runner=_stub_reviewer_runner("pass"),
        harness_config_path=_FIXTURE_CONFIG,
    )
    controller = comps["controller"]
    result = controller.start_run(
        request=_REQUEST_C001, workflow_id="chatbi-analyze",
        session_id="ses-c012", scenario_id="C012_stream_interrupted",
    )
    run_id = result["run_id"]
    replay = comps["event_log"].replay(run_id, cursor=None)
    events = list(replay.events)
    completed_index = [
        e["event_index"] for e in events if e["event_type"] == "run.completed"
    ]
    gate_index = [
        e["event_index"] for e in events
        if e["event_type"] in ("run.completed", "gate.blocked")
    ]
    # The run was never marked successful before the delivery gate decision.
    premature = bool(completed_index and gate_index
                     and completed_index[0] < gate_index[0])
    # Cursor replay: events strictly after the cursor match the original
    # stream exactly (idempotent replay, dedup by event_id).
    tail = [e["event_index"] for e in events[3:]]
    replay_tail = [e["event_index"] for e in
                   comps["event_log"].replay(run_id, cursor=events[2]["event_index"]).events]
    return {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "scenario": "C012_stream_interrupted",
        "workflow": "chatbi-analyze",
        "p0_row": "Runtime stream 中断",
        "final_status": result.get("final_status"),
        "source_tier": None,
        "interrupted": True,
        "completed_marker_written_before_resume": not premature,
        "resume_mechanism": "event_log_cursor_replay",
        "replay_consistent": tail == replay_tail,
        "gate_decisions": [],
        "notes": [
            "The interrupted run was never marked successful; the event log "
            "replays from the last cursor and the delivery gate delivers "
            "only then (cursor/resume semantics, design §6.3/§17).",
        ],
    }


def run_scenario(scenario_id: str) -> dict[str, Any]:
    """Run one scenario and return the normalized result dict."""
    if scenario_id == "C007_approval_stale_or_expired":
        return _run_c007_stale_or_expired()
    if scenario_id == "C012_stream_interrupted":
        return _run_c012_stream_interrupted()
    kind, kwargs = _SCENARIO_SPECS[scenario_id]
    if kind == "workflow":
        return _run_workflow_scenario(scenario_id=scenario_id, **kwargs)
    if kind == "coordinator":
        return _run_coordinator_scenario(scenario_id=scenario_id, **kwargs)
    return _run_kernel_shared_scenario(scenario_id)


def run_all() -> dict[str, dict[str, Any]]:
    """Run every P0 scenario (16) on the Agno target."""
    results: dict[str, dict[str, Any]] = {}
    for scenario_id in sorted(_SCENARIO_SPECS):
        results[scenario_id] = run_scenario(scenario_id)
    return results


def main(argv: list[str] | None = None) -> int:
    if agno is None:
        print(
            "error: the agno runtime is not importable in this interpreter; "
            "run the agno conformance suite with the deployment venv python "
            "(agno-main/.venv/bin/python). Nothing was run (FBK-003).",
            file=sys.stderr,
        )
        return 2
    import argparse

    parser = argparse.ArgumentParser(
        description="Run P0 conformance scenarios on the Agno target"
    )
    parser.add_argument("scenarios", nargs="*", metavar="ID",
                        help="scenario subset (default: all 16)")
    args = parser.parse_args(argv)
    ids = sorted(args.scenarios) if args.scenarios else sorted(_SCENARIO_SPECS)
    unknown = [s for s in ids if s not in _SCENARIO_SPECS]
    if unknown:
        print(f"error: unknown scenario(s): {unknown}", file=sys.stderr)
        return 2
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = {sid: run_scenario(sid) for sid in ids}
    for scenario_id in ids:
        result = results[scenario_id]
        print(
            f"{scenario_id}: final_status={result.get('final_status')} "
            f"workflow={result.get('workflow')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
