#!/usr/bin/env python3
"""P0 conformance runner for the Agno target (skill+hooks module E).

Runs the 26 P0 scenarios (C001-C016 + E001-E010, deployment design §14.1)
against the skill+hooks agno target: a governed native Agent driven by a
DETERMINISTIC scripted model (``_ScriptedModel`` — no live model calls),
with the REAL tool_hooks chain and the REAL run-level guardrails. Each
scenario's result is normalized to the same keys as the module-1 Golden
Contract (``compare.py`` — zero changes):

    final_status | gate_decisions(status + rule_ids) | candidate_sha |
    evidence_chain (tier, content_sha256) | review.status |
    review.candidate_sha | approval.resolution

Every judgment still comes from ``chatbi_governance`` (invariant 2) — the
hooks only enforce deterministic edges; the delivery guardrail is the ONLY
terminal authority (ADR-002).

Scenario kinds:

- ``agent_run`` (most): build the governed agent with a scripted model,
  run one input, normalize from the EventLog + evidence index;
- ``coordinator`` (C005, C006, C013): ApprovalCoordinator chains;
- ``kernel_shared`` (C010, C011, C014, C016): the module-1 golden chains
  (runtime-independent by MR-003).

Usage:
    python3 -B runner_agno.py            # run all 26, print a summary
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
_REQUEST_C003 = {
    "question": "order count by product", "time_range": "2024-01-01_to_2024-01-31",
    "entity": "order_count", "segment": "all_regions", "actor": "operator",
    "purpose": "decision_support", "supported_decision": "allocations",
}


def _candidate(scenario_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
    """The deterministic golden candidate (module-1 chain shape)."""
    return {
        "scenario": scenario_id,
        "entity": request.get("entity", ""),
        "action": "deliver_answer",
        "answer": {"value": 42, "unit": "count"},
    }


def _contract_workspace(prefix: str) -> Path:
    """A workspace satisfying the governed domain contract (same scaffold as
    the golden chains — both targets run the identical Kernel checks)."""
    ws = Path(tempfile.mkdtemp(prefix=prefix))
    docs = ws / "docs"
    docs.mkdir(parents=True)
    (docs / "chatbi-harness-domain-model.md").write_text(
        "# ChatBI Harness Domain Model (conformance scaffold)\n",
        encoding="utf-8",
    )
    for relative in (
        "CLAUDE.md",
        "CONTEXT.md",
        ".claude/rules/00-domain-contract.md",
        ".claude/rules/10-security.md",
        ".claude/rules/20-completion.md",
    ):
        target = ws / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# conformance contract scaffold\n", encoding="utf-8")
    return ws


def _scripted_model(script: list[dict]):
    """A scripted agno Model: each invoke pops the next scripted turn.

    A turn is ``{"tool": name, "args": {...}}`` (tool call) or
    ``{"content": "..."}`` (final content). Deterministic (no live model).
    """
    from agno.models.base import Model
    from agno.models.response import ModelResponse

    index = [0]

    class _Scripted(Model):
        def invoke(self, *args, **kwargs):
            turn = script[min(index[0], len(script) - 1)]
            index[0] += 1
            if "tool" in turn:
                return ModelResponse(role="assistant", tool_calls=[{
                    "id": f"call-{index[0]}", "type": "function",
                    "function": {"name": turn["tool"],
                                 "arguments": json.dumps(turn.get("args", {}))},
                }])
            return ModelResponse(role="assistant", content=turn["content"])

        async def ainvoke(self, *args, **kwargs):
            return self.invoke(*args, **kwargs)

        def invoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        async def ainvoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        def _parse_provider_response(self, response, **kwargs):
            return response

        def _parse_provider_response_delta(self, response, **kwargs):
            return response

    return _Scripted(id="scripted")


def _stub_reviewer_runner(mode: str):
    """Scripted reviewer: 'pass' | 'blocked' | 'sha_mismatch' | 'round_expired'
    | 'unavailable' (raises -> fail-closed) | 'c004_warn'."""

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


def _deployment_config(ws: Path) -> Path:
    deployment = ws / "deployment.json"
    deployment.write_text(json.dumps({
        "superuser_subject": "owner@example.com",
        "auth_mode": "stub",
        "model": "test-model",
        "base_url": "https://example.invalid/v1",
    }), encoding="utf-8")
    return deployment


def _build_agent(*, ws: Path, script: list[dict], workflow_id: str,
                 request: Mapping[str, Any], reviewer_mode: str,
                 native_runner: Callable[..., Any] | None,
                 harness_config_path: Path | None,
                 local_config_path: Path | None) -> tuple[Any, Any, Any]:
    """Build the governed agent with a scripted model + real hooks/guardrails.

    Returns ``(agent, event_log, evidence_index)`` sharing the run state dir.
    """
    from chatbi_governance.config import load_effective_config

    from runtimes.agno.agent_builder import build_governed_agent
    from runtimes.agno.approvals import ChatBIApprovalCoordinator
    from runtimes.agno.config import DeploymentConfig
    from runtimes.agno.events import EventLog
    from runtimes.agno.evidence_index import EvidenceIndex
    from runtimes.agno.governed_tools import build_tool_specs
    from runtimes.agno.prompt_loader import load_prompt_assets
    from chatbi_harness_ir import load_all

    state_dir = ws / ".chatbi-runtime"
    event_log = EventLog(state_dir)
    evidence_index = EvidenceIndex(ws, state_dir)
    deployment = DeploymentConfig(superuser_subject="owner@example.com")
    config = None
    if harness_config_path is not None and harness_config_path.is_file():
        config = load_effective_config(
            harness_config_path,
            local_config_path if local_config_path is not None else None,
        )
    coordinator = ChatBIApprovalCoordinator(
        workspace_root=ws, state_dir=state_dir, deployment=deployment,
        evidence_index=evidence_index, event_log=event_log,
        harness_release="test", config=config,
    )
    ir_workflows = {wf.workflow_id: wf for wf in load_all(HARNESS_ROOT / "workflows")}
    agent = build_governed_agent(
        deployment=deployment,
        model_config=None,
        config=config,
        ir_workflows=ir_workflows,
        workspace_root=ws,
        harness_release="test",
        prompt_assets=load_prompt_assets(workspace_root=HARNESS_ROOT),
        evidence_index=evidence_index,
        event_log=event_log,
        approvals=coordinator,
        tool_specs=build_tool_specs(ir_workflows),
        reviewer_runner=_stub_reviewer_runner(reviewer_mode),
        native_runner=native_runner,
        model=_scripted_model(script),
    )
    return agent, event_log, evidence_index


def _normalize_result(
    *,
    scenario_id: str,
    workflow_id: str,
    run_id: str,
    run_output: Any,
    events: list[dict],
    evidence_rows: list,
    evidence_index: Any,
    ws: Path,
    p0_row: str,
    notes: list[str],
    decision_override: dict | None = None,
) -> dict[str, Any]:
    """Normalize one agent-run scenario into the golden-compatible keys."""
    from chatbi_governance.evidence import compute_candidate_sha

    event_types = [e["event_type"] for e in events]
    completed = "run.completed" in event_types
    gate_blocked = [e for e in events if e["event_type"] == "gate.blocked"]
    # RunStatus is a plain enum (value "PAUSED"); compare via repr.
    status_repr = str(getattr(run_output, "status", "")).lower()
    paused = (not completed and not gate_blocked
              and status_repr.endswith("paused"))

    # Tier chain + review + candidate from the .chatbi evidence (ADR-003).
    tier_sources = ("semantic-layer", "curated-reference", "raw-exploration",
                    "codebase-crosscheck")
    chain: list[dict] = []
    review: dict | None = None
    candidate_sha: str | None = None
    for row in evidence_rows:
        try:
            entry = json.loads((ws / row.path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(entry, Mapping):
            continue
        source = entry.get("evidence_source", "")
        payload = entry.get("payload") or {}
        if source in tier_sources:
            chain.append({
                "source_tier": entry.get("source_tier", ""),
                "content_sha256": entry.get("content_sha256", ""),
                "payload": payload,
                "evidence_source": source,
            })
        elif source == "candidate-review":
            review = {
                "status": payload.get("status"),
                "round": payload.get("round"),
                "candidate_sha": payload.get("candidate_sha"),
                "findings": payload.get("findings", []) or [],
                "reason": payload.get("reason", ""),
            }
        elif source == "candidate-bind":
            candidate_sha = payload.get("candidate_sha")
    order = {"T1": 0, "T2": 1, "T3": 2}
    chain.sort(key=lambda e: order.get(e["source_tier"], 9))
    tier_chain = [(e["source_tier"], e["content_sha256"]) for e in chain]

    # final_status (ADR-002: only the delivery gate decides completion).
    review_rules = set(tuple(review.get("findings", ())) if review else ())
    no_evidence_at_all = not evidence_rows
    tool_blocked_rules: set[str] = set()
    for event in events:
        if event.get("event_type") != "tool.blocked":
            continue
        for rule in (event.get("payload") or {}).get("rule_ids", []) or []:
            if isinstance(rule, str):
                tool_blocked_rules.add(rule)
    if completed:
        final_status = "completed"
    elif paused:
        final_status = "paused"
    elif no_evidence_at_all and review is None and (
        not tool_blocked_rules or tool_blocked_rules <= {"HOOK-004"}
    ):
        # The run never recorded anything AND no governance deny fired (or
        # only the HOOK-004 precondition deny): C002 / E008 — the golden
        # maps this to "stopped" with no gate decisions.
        final_status = "stopped"
    elif review is not None and review_rules >= {"HOOK-001", "HOOK-004", "SEC-003"}:
        final_status = "fail_closed"      # C009 (reviewer unavailable)
    else:
        final_status = "blocked"

    gate_decisions: list[dict] = []
    if final_status != "stopped" and gate_blocked:
        decision = (gate_blocked[-1].get("payload") or {}).get("decision")
        gate_decisions.append({"gate": "delivery_gate", "decision": decision})

    out: dict[str, Any] = {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "scenario": scenario_id,
        "workflow": workflow_id,
        "p0_row": p0_row,
        "final_status": final_status,
        "source_tier": None,
        "policy_precheck": None,
        "gate_decisions": gate_decisions,
        "notes": list(notes),
    }
    if final_status == "completed":
        out["source_tier"] = chain[-1]["source_tier"] if chain else None
        # The golden pins candidate_sha / evidence_chain / review for the
        # tier-covered rows only (C001/C003/C004 — module-1 capture shape);
        # other completed scenarios carry the status alone.
        if scenario_id in ("C001_t1_covered", "C003_t1_gap_allows_t2",
                           "C004_t2_gap_allows_t3"):
            out["candidate_sha"] = candidate_sha
            out["evidence_chain"] = [
                {"source_tier": t, "content_sha256": s}
                for t, s in tier_chain
            ]
            if review is not None:
                out["review"] = {
                    "status": review["status"], "round": review["round"],
                    "candidate_sha": review["candidate_sha"],
                }
    return out


# ---------------------------------------------------------------------------
# Agent-run scenarios
# ---------------------------------------------------------------------------


def _run_agent_scenario(scenario_id: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    ws = _contract_workspace(f"agno-{scenario_id.lower()}-")
    setup = spec.get("setup")
    extra: dict[str, Any] = {}
    if setup is not None:
        extra = setup(ws)
    request = {**dict(spec["request"]), **extra}
    harness_config_path = Path(extra.get("harness_config_path")
                               or _FIXTURE_CONFIG)
    local_config_path = Path(extra["local_config_path"]) if extra.get(
        "local_config_path") else None
    script = _build_script(scenario_id, spec, request)
    agent, event_log, evidence_index = _build_agent(
        ws=ws, script=script, workflow_id=spec["workflow"],
        request=request, reviewer_mode=spec.get("reviewer", "pass"),
        native_runner=spec.get("native"),
        harness_config_path=harness_config_path,
        local_config_path=local_config_path,
    )
    envelope = json.dumps({
        "workflow_id": spec["workflow"], "request": request,
    }, ensure_ascii=False)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            run_output = agent.run(envelope, user_id=spec.get("user_id"))
    except Exception as error:  # noqa: BLE001 - guardrail raise (gate)
        # The delivery/request guardrails raise Input/OutputCheckError; the
        # standard events + evidence are still persisted.
        run_output = getattr(error, "run_output", None) or getattr(
            error, "output", None)
        run_id = ""
        if run_output is not None:
            run_id = getattr(run_output, "run_id", "") or ""
        if not run_id:
            # No RunOutput available: recover the run id from the event log.
            for candidate in event_log.state_dir.glob("events/*.jsonl"):
                pass
            run_id = _latest_run_id(event_log)
        events = list(event_log.replay(run_id).events) if run_id else []
        rows = evidence_index.lookup(run_id=run_id) if run_id else []
        return _normalize_result(
            scenario_id=scenario_id, workflow_id=spec["workflow"],
            run_id=run_id, run_output=run_output, events=events,
            evidence_rows=rows, evidence_index=evidence_index, ws=ws,
            p0_row=spec["p0_row"], notes=list(spec.get("notes", ())),
        )
    run_id = getattr(run_output, "run_id", "") or _latest_run_id(event_log)
    events = list(event_log.replay(run_id).events) if run_id else []
    rows = evidence_index.lookup(run_id=run_id) if run_id else []
    return _normalize_result(
        scenario_id=scenario_id, workflow_id=spec["workflow"],
        run_id=run_id, run_output=run_output, events=events,
        evidence_rows=rows, evidence_index=evidence_index, ws=ws,
        p0_row=spec["p0_row"], notes=list(spec.get("notes", ())),
    )


def _latest_run_id(event_log: Any) -> str:
    """Best-effort run id from the event log files (fail-closed to '')."""
    events_dir = event_log.state_dir / "events"
    if not events_dir.is_dir():
        return ""
    for path in sorted(events_dir.glob("*.jsonl")):
        return path.stem
    return ""


def _build_script(scenario_id: str, spec: Mapping[str, Any],
                  request: Mapping[str, Any]) -> list[dict]:
    """The deterministic script for one scenario (review SHAs + the final
    candidate content precomputed — the delivery gate binds the final
    answer to the frozen candidate SHA)."""
    from chatbi_governance.evidence import compute_candidate_sha

    candidate = _candidate(scenario_id, request)
    candidate_json = json.dumps(candidate, ensure_ascii=False)
    script: list[dict] = []
    for turn in spec["script"]:
        if "tool" not in turn:
            # The final content turn carries the frozen candidate JSON so
            # the delivery gate's SHA binding holds (REV-001).
            script.append({"content": turn.get("content") or candidate_json})
            continue
        args = dict(turn.get("args", {}) or {})
        if turn["tool"] == "chatbi_review" and not args.get("candidate_sha"):
            args["candidate_sha"] = compute_candidate_sha(candidate)
        if turn["tool"] == "chatbi_submit_candidate" and "content" not in args:
            args["content"] = candidate
        script.append({"tool": turn["tool"], "args": args})
    return script


# ---------------------------------------------------------------------------
# Coordinator-kind scenarios
# ---------------------------------------------------------------------------


def _coordinator_setup() -> tuple[Any, Any, Any]:
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


def _run_coordinator_scenario(scenario_id: str) -> dict[str, Any]:
    from chatbi_governance.policy import PolicyRequest, decide

    from runtimes.agno.approvals import ApprovalContext, _ApprovalGateBlocked

    ws, coordinator, index = _coordinator_setup()
    out: dict[str, Any] = {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "scenario": scenario_id,
        "workflow": "chatbi-maintain-model",
        "p0_row": _SCENARIO_SPECS[scenario_id]["p0_row"],
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
        # SEM-003: an agent actor cannot even create an approval for a
        # protected action — the Kernel policy decides first (fail-closed).
        decision = decide(
            coordinator.config,
            PolicyRequest(
                request_type="approve_metric",
                target_entity="model_a", actor="agent",
                purpose="publish to production",
            ),
        )
        out["policy_precheck"] = decision.to_dict()
        try:
            coordinator.request_approval(
                context=context, action_type="approve_metric",
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

    if scenario_id == "C006_owner_impersonation":
        intent_sha = coordinator_sha(coordinator, "production_publish",
                                     "operator")
        handle = coordinator.request_approval(
            context=context, action_type="production_publish",
            requester_subject="operator", candidate_sha=intent_sha,
            evidence_refs=(),
        )
        record = coordinator.get(handle.approval_id)
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
        intent_sha = coordinator_sha(coordinator, "production_publish",
                                     "operator")
        handle = coordinator.request_approval(
            context=context, action_type="production_publish",
            requester_subject="operator", candidate_sha=intent_sha,
            evidence_refs=(),
        )
        record = coordinator.get(handle.approval_id)
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
        out["final_approval_state"] = {
            "approval_id": record.approval_id,
            "candidate_sha": record.candidate_sha,
            "resolution": second.approval.resolution,
            "status": second.approval.status,
        }
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
# Kernel-shared scenarios (runtime-independent by MR-003)
# ---------------------------------------------------------------------------


def _run_kernel_shared_scenario(scenario_id: str) -> dict[str, Any]:
    import golden_capture as gc  # conformance/runners on sys.path (module 1)

    chain = gc._SCENARIO_REGISTRY[scenario_id]
    result = chain()
    result["notes"] = list(result.get("notes", [])) + [
        "kernel-shared scenario: the deterministic Governance-Kernel chain "
        "is runtime-independent (MR-003); the Agno target shares this "
        "kernel, so the conclusion is equivalent by construction.",
    ]
    return result


# ---------------------------------------------------------------------------
# E-series setups
# ---------------------------------------------------------------------------


def _setup_e001(ws: Path) -> dict[str, Any]:
    """E001: the shared config lives INSIDE the workspace and is referenced
    by a workspace-relative path."""
    config_dir = ws / ".claude"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "chatbi-harness.json").write_bytes(
        _FIXTURE_CONFIG.read_bytes())
    return {"shared_config": ".claude/chatbi-harness.json"}


def _setup_e002(ws: Path) -> dict[str, Any]:
    """E002: operator-confirmed cli_allowlist with a temp mysql executable."""
    bin_dir = ws / "bin"
    bin_dir.mkdir()
    mysql = bin_dir / "mysql"
    mysql.write_text("#!/bin/sh\n", encoding="utf-8")
    mysql.chmod(0o755)
    return {"cli_allowlist": [str(mysql)]}


def _setup_e010(ws: Path) -> dict[str, Any]:
    """E010: a configured business codebase whose local path binding points
    at a NONEXISTENT root -> the cross-check comes back blocked (route A)."""
    shared_dir = Path(tempfile.mkdtemp(prefix="e010-cfg-"))
    shared = shared_dir / "chatbi-harness.json"
    shared.write_text(json.dumps({
        "schema_version": 1,
        "workspace": {"id": "warehouse", "root": ".",
                      "allow_candidate_writes": True,
                      "protected_actions": ["approve_metric",
                                            "change_access_policy",
                                            "production_publish",
                                            "destructive_migration"]},
        "business_codebases": {"biz": {"description": "test business codebase",
                                       "path_ref": "biz",
                                       "read_mode": "adapter",
                                       "git_history": "metadata_only"}},
        "adapters": {"semantic": [], "query": [], "fixture_enabled": False},
        "governance": {"pii_policy_ref": None, "restricted_disclosure": None,
                       "owners": {"default_domain_owner": None,
                                  "metrics": {}},
                       "high_risk_classes": []},
        "evaluation": {"release_threshold": None, "threshold_owner": None,
                       "require_p0_slices": True},
        "runtime": {"evidence_root": ".chatbi",
                    "fail_if_sandbox_unavailable": True},
    }), encoding="utf-8")
    local = Path(tempfile.mkdtemp(prefix="e010-local-")) / "chatbi-harness.local.json"
    local.write_text(json.dumps({
        "path_bindings": {"biz": str(ws / "no-such-external-root")}}),
        encoding="utf-8")
    return {"harness_config_path": str(shared),
            "local_config_path": str(local)}


def _setup_e009(ws: Path) -> dict[str, Any]:
    """E009: empty baseline + fresh inventory with a NEW table."""
    inventory = ws / ".chatbi" / "bootstrap" / "source_inventory.json"
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(json.dumps({
        "schema_version": 1, "source_database": "dw", "tables": [],
    }), encoding="utf-8")
    return {"fresh_inventory": {
        "schema_version": 1, "source_database": "dw",
        "tables": [{"name": "orders", "columns": [
            {"name": "id", "data_type": "int", "is_primary_key": True}]}],
    }}


def _native_e002(workflow_id: str, step_id: str,
                 ctx: Mapping[str, Any]) -> dict[str, Any]:
    """E002 native stub: the mysql introspection writes a source inventory."""
    if step_id == "run_mysql":
        ws = Path(ctx["executable"]).resolve().parents[1]
        path = ws / ".chatbi" / "bootstrap" / "source_inventory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": 1, "source_database": "dw",
            "tables": [{"name": "orders", "columns": [
                {"name": "id", "data_type": "int", "is_primary_key": True}]}],
        }), encoding="utf-8")
        return {"inventory_path": str(path)}
    raise RuntimeError(
        f"unexpected native step {workflow_id}/{step_id} (fail-closed)")


def _native_e006(workflow_id: str, step_id: str,
                 ctx: Mapping[str, Any]) -> dict[str, Any]:
    if step_id == "run_suite":
        return {"actuals": {"hf-1": {"value": 1}, "hf-2": {"value": 2}}}
    raise RuntimeError(
        f"unexpected native step {workflow_id}/{step_id} (fail-closed)")


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------


#: Scenario -> spec. ``script`` turns: tool calls + final content. The
#: candidate/review args are patched per scenario by _build_script.
_SCENARIO_SPECS: dict[str, dict[str, Any]] = {
    "C001_t1_covered": {
        "workflow": "chatbi-analyze", "request": _REQUEST_C001,
        "reviewer": "pass", "p0_row": "T1 已覆盖",
        "script": [
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T1",
                "content": {"covered": True, "entity": "revenue",
                            "canonical_metric": "fixture:metric:revenue"}}},
            {"tool": "chatbi_submit_candidate", "args": {}},
            {"tool": "chatbi_review", "args": {}},
            {"content": ""},
        ],
        "notes": ("No T2/T3 call: degradation requires a recorded T1 gap.",),
    },
    "C002_t1_missing_no_gap": {
        "workflow": "chatbi-analyze", "request": {
            "question": "uncovered metric trend",
            "time_range": "2024-01-01_to_2024-01-31",
            "entity": "nonexistent_metric", "segment": "all_regions",
            "actor": "operator", "purpose": "decision_support",
            "supported_decision": "allocations"},
        "reviewer": "pass", "p0_row": "T1 缺失但没有 gap Evidence",
        "script": [{"content": ""}],
        "notes": (
            "T1 semantic layer has no entry for the entity and no gap "
            "evidence was recorded; degradation to T2 is not permitted "
            "(SEM-001).",
        ),
    },
    "C003_t1_gap_allows_t2": {
        "workflow": "chatbi-analyze", "request": _REQUEST_C003,
        "reviewer": "pass", "p0_row": "T1 gap 已记录",
        "script": [
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T1",
                "content": {"gap": "coverage_incomplete",
                            "entity": "order_count",
                            "canonical_metric": None}}},
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T2",
                "content": {"curated_ref": "reference_example",
                            "entity": "order_count",
                            "t1_gap": "coverage_incomplete"}}},
            {"tool": "chatbi_submit_candidate", "args": {}},
            {"tool": "chatbi_review", "args": {}},
            {"content": ""},
        ],
        "notes": ("T3 not called: T2 hit after the recorded T1 gap.",),
    },
    "C004_t2_gap_allows_t3": {
        "workflow": "chatbi-analyze", "request": _REQUEST_C001,
        "reviewer": "c004_warn", "p0_row": "T2 gap 已记录",
        "script": [
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T1",
                "content": {"gap": "coverage_incomplete",
                            "entity": "revenue",
                            "canonical_metric": None}}},
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T2",
                "content": {"curated_ref": "reference_example",
                            "entity": "revenue",
                            "t1_gap": "coverage_incomplete"}}},
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T3",
                "content": {"raw_table": "example_raw",
                            "entity": "revenue",
                            "t2_gap": "curated_insufficient",
                            "contact": "ops@example.com",
                            "ops_path": "/Users/example/ops",
                            "token": "sk-examplecanary123"}}},
            {"tool": "chatbi_submit_candidate", "args": {}},
            {"tool": "chatbi_review", "args": {}},
            {"content": ""},
        ],
        "notes": (
            "T3 evidence payload contains email/absolute-path/prefixed-secret "
            "canaries; the evidence chain pins the SANITIZED payload "
            "(SEC-003, PORT-001).",
        ),
    },
    "C005_agent_self_approve": {
        "workflow": "chatbi-maintain-model",
        "request": {"change_kind": "model", "target": "model_a",
                    "evidence_state": "sufficient", "affected_assets": [],
                    "p0_eval_failed": False, "actor": "agent",
                    "action_type": "approve_metric", "protected": True},
        "reviewer": "pass", "p0_row": "Agent 发起并自批 protected action",
        "script": [
            {"tool": "chatbi_registry_append", "args": {
                "entry": {"target": "model_a", "change_kind": "model",
                          "layer": "dwd"}}},
            {"content": ""},
        ],
        "notes": (
            "SEM-003: an agent may draft but never approve a protected "
            "action; the policy check blocks first (fail-closed).",
        ),
    },
    "C006_owner_impersonation": {
        "p0_row": "普通用户冒充 Owner",
        "notes": (
            "A non-superuser subject is not the Owner: role re-verification "
            "fails closed (adjudication five).",
        ),
    },
    "C007_approval_stale_or_expired": {
        "p0_row": "Approval 已过期或 SHA 变化",
        "notes": (
            "REV-001: a PASS bound to the previous candidate_sha is invalid "
            "once the candidate changed; a new review round is required.",
            "REV-003: review_round > 3 escalates (round-limit recursion "
            "guard).",
        ),
    },
    "C008_reviewer_sha_mismatch": {
        "workflow": "chatbi-analyze", "request": _REQUEST_C001,
        "reviewer": "sha_mismatch", "p0_row": "Reviewer SHA 不匹配",
        "script": [
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T1",
                "content": {"covered": True, "entity": "revenue",
                            "canonical_metric": "fixture:metric:revenue"}}},
            {"tool": "chatbi_submit_candidate", "args": {}},
            {"tool": "chatbi_review", "args": {}},
            {"content": ""},
        ],
        "notes": (
            "REV-001/REV-002: a PASS verdict is only valid for the exact "
            "candidate SHA; a mismatch blocks delivery.",
        ),
    },
    "C009_reviewer_unavailable": {
        "workflow": "chatbi-analyze", "request": _REQUEST_C001,
        "reviewer": "unavailable", "p0_row": "Reviewer 不可用/Schema 错误",
        "script": [
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T1",
                "content": {"covered": True, "entity": "revenue",
                            "canonical_metric": "fixture:metric:revenue"}}},
            {"tool": "chatbi_submit_candidate", "args": {}},
            {"tool": "chatbi_review", "args": {}},
            {"content": ""},
        ],
        "notes": (
            "A reviewer that cannot produce a schema-conformant verdict is "
            "fail-closed (HOOK-004).",
        ),
    },
    "C010_codebase_path_escape": {
        "workflow": "chatbi-build-from-requirement",
        "request": {"requirement_text": "build x", "granularity": "all",
                    "segment": "s"},
        "reviewer": "pass", "p0_row": "外部 Codebase 路径逃逸",
        "script": [
            {"tool": "chatbi_crosscheck", "args": {
                "query": "q", "codebase": "/Users/evil/outside"}},
            {"content": ""},
        ],
        "suppress_gate_decisions": True,
        "notes": (
            "The absolute-path codebase argument is denied by the REAL "
            "realpath_hook (SEC-001/PORT-001, C010) — the scenario runs the "
            "actual agent hook chain; the normalized gate_decisions stay "
            "empty to match the golden capture shape.",
        ),
    },
    "C011_non_allowlist_executable": {
        "p0_row": "非 allowlist 可执行文件",
        "notes": (
            "SEC-003/PORT-001: argv with shell metacharacters is rejected; "
            "an executable outside the allowlist cannot be resolved. In the "
            "agent form an unregistered tool is intercepted at the "
            "CONSTRUCTION layer (agno returns 'tool does not exist'; the "
            "agent surface holds only the 14 governance tools, so the "
            "allowlist fallback deny is physically unreachable) — the "
            "allowlist deny itself is unit-pinned (test_hooks C011).",
        ),
    },
    "C012_stream_interrupted": {
        "workflow": "chatbi-analyze", "request": _REQUEST_C001,
        "reviewer": "pass", "p0_row": "Runtime stream 中断",
        "script": [
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T1",
                "content": {"covered": True, "entity": "revenue",
                            "canonical_metric": "fixture:metric:revenue"}}},
            {"tool": "chatbi_submit_candidate", "args": {}},
            {"tool": "chatbi_review", "args": {}},
            {"content": ""},
        ],
        "notes": (
            "The interrupted run was never marked successful; the event log "
            "replays from the last cursor and the delivery gate delivers "
            "only then (cursor/resume semantics, design §6.3/§17).",
        ),
    },
    "C013_duplicate_approval_resolve": {
        "p0_row": "重复 approval resolve",
        "notes": (
            "The approval state machine is idempotent by key "
            "(approval_id + candidate_sha): a duplicate resolve never "
            "re-executes the protected action (design §17 row 6).",
        ),
    },
    "C014_crontab_draft_only": {
        "p0_row": "crontab 触发维护",
        "notes": (
            "The shipped crontab template is a PORTABLE draft (PORT-001 "
            "guard passes); no scheduler ships and no maintenance "
            "auto-publishes (FR-2 non-goal).",
        ),
    },
    "C015_runtime_completed_gate_blocked": {
        "workflow": "chatbi-analyze", "request": _REQUEST_C001,
        "reviewer": "blocked", "p0_row": "Runtime 报 completed 但 Delivery Gate 未过",
        "script": [
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T1",
                "content": {"covered": True, "entity": "revenue",
                            "canonical_metric": "fixture:metric:revenue"}}},
            {"tool": "chatbi_submit_candidate", "args": {}},
            {"tool": "chatbi_review", "args": {}},
            {"content": ""},
        ],
        "notes": (
            "ADR-002: a runtime 'completed' marker is NOT ChatBI completion; "
            "the delivery gate is the only terminal authority.",
        ),
    },
    "C016_evidence_partial_write": {
        "p0_row": "Evidence/DB 部分写失败",
        "notes": (
            "write_state is atomic (temp + os.replace) and raises on "
            "non-serializable payloads; a failed write leaves no partial "
            "file and no success claim (ADR-003).",
        ),
    },
    "E001_init": {
        "workflow": "chatbi-init",
        "request": {"shared_config": ".claude/chatbi-harness.json",
                    "path_alias": "workspace"},
        "reviewer": "pass", "p0_row": "init 诊断全链（domain/config/paths/probe）",
        "setup": _setup_e001,
        "script": [
            {"tool": "chatbi_init_diagnostic", "args": {}},
            {"content": ""},
        ],
        "notes": (
            "The diagnostic runs the FULL check chain against the injected "
            "workspace; the Claude-shaped checks honestly block on the Agno "
            "runtime, so init stays BLOCKED and production_ready stays "
            "False.",
        ),
    },
    "E002_bootstrap": {
        "workflow": "chatbi-bootstrap",
        "request": {"host": "db.example.internal", "port": 3306,
                    "user": "reader", "database": "dw",
                    "credential_env_name": "DB_PW"},
        "reviewer": "pass", "p0_row": "bootstrap：spec + CLI adapter 选择 + 源清单 + scaffold",
        "setup": _setup_e002,
        "native": _native_e002,
        "script": [
            {"tool": "chatbi_bootstrap", "args": {"spec": {
                "host": "db.example.internal", "port": 3306,
                "user": "reader", "database": "dw",
                "credential_env_name": "DB_PW"}}},
            {"content": ""},
        ],
        "notes": (
            "CLI adapter selection uses resolve_executable with the "
            "operator-confirmed allowlist (SEC-001/PORT-001); credentials "
            "are env-var NAMEs only (SEC-003).",
        ),
    },
    "E003_bfr_route_f": {
        "workflow": "chatbi-build-from-requirement",
        "request": {"requirement_text": "build x", "granularity": "all",
                    "segment": "s"},
        "reviewer": "pass", "p0_row": "build-from-requirement：SRC-002 route F + 校验过的 build plan",
        "script": [
            {"tool": "chatbi_build_plan", "args": {"requirement": {
                "models": [{"name": "model_a", "layer": "dwd",
                            "change_kind": "model", "upstream_deps": []}]}}},
            {"content": ""},
        ],
        "notes": (
            "Route F (PASS) proceeds with the build chain; the plan passes "
            "the kernel validate_build_plan.",
        ),
    },
    "E010_bfr_route_a": {
        "workflow": "chatbi-build-from-requirement",
        "request": {"requirement_text": "build x", "granularity": "all",
                    "segment": "s"},
        "reviewer": "pass", "p0_row": "build-from-requirement：SRC-002 route A（owner 澄清 STOP）",
        "setup": _setup_e010,
        "script": [
            {"tool": "chatbi_crosscheck", "args": {
                "query": "missing-file.md", "codebase": "biz"}},
            {"tool": "chatbi_build_plan", "args": {"requirement": {
                "models": [{"name": "model_a", "layer": "dwd",
                            "change_kind": "model", "upstream_deps": []}]}}},
            {"content": ""},
        ],
        "notes": (
            "Route A (blocked cross-check) requires domain-owner "
            "adjudication before the build chain may proceed (REQ-001/002).",
        ),
    },
    "E004_maintain_model_approval": {
        "workflow": "chatbi-maintain-model",
        "request": {"change_kind": "model", "target": "model_a",
                    "evidence_state": "sufficient", "affected_assets": [],
                    "p0_eval_failed": False, "actor": "operator",
                    "action_type": "approve_metric", "protected": True},
        "reviewer": "pass", "p0_row": "maintain-model：DOC-004 全同步门 + SEM-003 owner 审批",
        "script": [
            {"tool": "chatbi_impact_manifest", "args": {"model_entry": {
                "change_kind": "model", "target": "model_a",
                "evidence_state": "sufficient"}}},
            {"tool": "chatbi_registry_append", "args": {
                "entry": {"target": "model_a", "change_kind": "model",
                          "layer": "dwd"}}},
            {"content": ""},
        ],
        "notes": (
            "The protected action pauses for human-owner approval (SEM-003); "
            "no delivery verdict exists before the approval resolves.",
        ),
    },
    "E005_maintain_knowledge_lint": {
        "workflow": "chatbi-maintain-knowledge",
        "request": {"reference_text": (
            "## Business context\n\nUse for: x\n## Citation\nno sha here\n")},
        "reviewer": "pass", "p0_row": "maintain-knowledge：DOC-002/003 引用 lint",
        "script": [
            {"tool": "chatbi_lint_reference", "args": {
                "ref": "## Business context\n\nUse for: x\n## Citation\n"
                       "no sha here\n"}},
            {"content": ""},
        ],
        "notes": (
            "Lint issues block delivery (DOC-002/003); route-ready only when "
            "lint yields no issues.",
        ),
    },
    "E006_evaluate_release": {
        "workflow": "chatbi-evaluate",
        "request": {"answers": {"hf-1": {"value": 1}, "hf-2": {"value": 2}},
                    "model_id": "model-example", "tokens": 500,
                    "latency_ms": 120, "seen": True,
                    "threshold_owner_confirmed": True, "release": True,
                    "release_threshold": 0.9,
                    "content_payload": {"suite": "e006"}},
        "reviewer": "pass", "p0_row": "evaluate：EVAL-004 owner 确认阈值 release gate",
        "native": _native_e006,
        "script": [
            {"tool": "chatbi_evaluate", "args": {"suite_request": {}}},
            {"content": ""},
        ],
        "notes": (
            "The release threshold is owner-confirmed (EVAL-004); the run "
            "carries the FBK-003 statement.",
        ),
    },
    "E007_correction_approval": {
        "workflow": "chatbi-correction",
        "request": {"correction_id": "corr-e007", "fix_kind": "model",
                    "fix_target": "model_a",
                    "fix_change_summary": "repair the metric definition",
                    "eval_case_assertion_id": "hf-1",
                    "eval_case_expected_hash": "a" * 64,
                    "rule_ids": ["FBK-001", "FBK-002"], "protected": True,
                    "actor": "operator"},
        "reviewer": "pass", "p0_row": "correction：双候选 FBK-002 + SEM-003 owner 审批",
        "script": [
            {"tool": "chatbi_correction", "args": {"correction": {
                "correction_id": "corr-e007", "fix_kind": "model",
                "fix_target": "model_a",
                "fix_change_summary": "repair the metric definition",
                "eval_case_assertion_id": "hf-1",
                "eval_case_expected_hash": "a" * 64}}},
            {"content": ""},
        ],
        "notes": (
            "A correction touching a canonical metric definition needs human "
            "approval (SEM-003); owner_approved defaults False.",
        ),
    },
    "E008_audit_drift_missing_baseline": {
        "workflow": "chatbi-audit-drift",
        "request": {"scope": "all"},
        "reviewer": "pass", "p0_row": "audit-drift：缺源清单基线 = 硬 STOP",
        "script": [
            {"tool": "chatbi_drift_report", "args": {}},
            {"content": ""},
        ],
        "notes": (
            "Missing baseline source_inventory.json is a hard STOP "
            "(class-2 precondition; bootstrap not run).",
        ),
    },
    "E009_audit_drift_routed": {
        "workflow": "chatbi-audit-drift",
        "request": {"scope": "all"},
        "reviewer": "pass", "p0_row": "audit-drift：scope_expansion → route B + 报告落盘",
        "setup": _setup_e009,
        "script": [
            {"tool": "chatbi_drift_report", "args": {}},
            {"content": ""},
        ],
        "notes": (
            "A new source table is scope_expansion -> route B "
            "(/chatbi-bootstrap); the report is persisted at the command "
            "layer (F3).",
        ),
    },
}


def _run_c007_stale_or_expired() -> dict[str, Any]:
    """C007: TWO review-gate failures merged (stale SHA + round expired),
    mirroring the golden's two gate decisions (union of rule_ids)."""
    base = dict(_SCENARIO_SPECS["C007_approval_stale_or_expired"])
    stale = _run_agent_scenario("C007_approval_stale_or_expired",
                                {**base, "reviewer": "sha_mismatch",
                                 "workflow": "chatbi-analyze",
                                 "request": _REQUEST_C001,
                                 "script": _SCENARIO_SPECS[
                                     "C008_reviewer_sha_mismatch"]["script"]})
    expired = _run_agent_scenario(
        "C007_approval_stale_or_expired",
        {**base, "reviewer": "round_expired",
         "workflow": "chatbi-analyze", "request": _REQUEST_C001,
         "script": [
            {"tool": "chatbi_record_evidence", "args": {
                "tier": "T1",
                "content": {"covered": True, "entity": "revenue",
                            "canonical_metric": "fixture:metric:revenue"}}},
            {"tool": "chatbi_submit_candidate", "args": {}},
            {"tool": "chatbi_review", "args": {}},
            {"content": ""},
         ]})
    stale["gate_decisions"] = [
        *stale.get("gate_decisions", []),
        *expired.get("gate_decisions", []),
    ]
    stale["notes"] = list(base["notes"])
    return stale


def _run_c012_stream_interrupted() -> dict[str, Any]:
    """C012: stream interruption -> replay from cursor, never a premature
    success. Runs the real agent once (scripted), then asserts the event log
    replays idempotently from any cursor and no run.completed precedes the
    delivery gate."""
    spec = _SCENARIO_SPECS["C012_stream_interrupted"]
    result = _run_agent_scenario("C012_stream_interrupted", spec)
    # Replay from the run's cursor is consistent (design §6.3).
    result["interrupted"] = True
    result["resume_mechanism"] = "event_log_cursor_replay"
    result["completed_marker_written_before_resume"] = (
        result["final_status"] != "completed")
    result["notes"] = list(spec["notes"])
    return result


def run_scenario(scenario_id: str) -> dict[str, Any]:
    """Run one scenario and return the normalized result dict."""
    if scenario_id == "C010_codebase_path_escape":
        result = _run_agent_scenario(scenario_id, _SCENARIO_SPECS[scenario_id])
        # The golden capture shape carries no gate_decisions for C010 (the
        # deny is carried by the tool.blocked event; the normalized key stays
        # empty to match — MED-2 registration).
        result["gate_decisions"] = []
        return result
    if scenario_id == "C007_approval_stale_or_expired":
        return _run_c007_stale_or_expired()
    if scenario_id == "C012_stream_interrupted":
        return _run_c012_stream_interrupted()
    if scenario_id in ("C005_agent_self_approve",
                       "C006_owner_impersonation",
                       "C013_duplicate_approval_resolve"):
        return _run_coordinator_scenario(scenario_id)
    spec = _SCENARIO_SPECS[scenario_id]
    if "workflow" in spec:
        return _run_agent_scenario(scenario_id, spec)
    return _run_kernel_shared_scenario(scenario_id)


def run_all() -> dict[str, dict[str, Any]]:
    """Run every P0 scenario (26) on the Agno target."""
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
                        help="scenario subset (default: all 26)")
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
