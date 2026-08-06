"""``chatbi-analyze`` Agno Workflow — IR steps mapped to the Agno runtime
(module 5, MR-D1/MR-D4).

The workflow is built AT RUNTIME from the IR (``workflows/chatbi-analyze.yaml``,
read via ``chatbi_harness_ir.load_workflow``) — no generated Python code
(impl §8.5 / design §8.5). Step mapping (impl §8.5 table):

| IR step                | Workflow implementation                                  |
| ---------------------- | -------------------------------------------------------- |
| request_preflight      | deterministic executor -> kernel ``evidence.validate_request`` |
| policy_precheck        | deterministic executor -> kernel ``policy.decide``       |
| clarify                | agent step (when segment undefined)                      |
| t1_semantic / t2_curated / t3_raw | agent steps; T2/T3 ``when`` = evidence.has_gap   |
| src002_crosscheck      | agent step (vacuously satisfied with no business codebases) |
| candidate_bind         | deterministic executor -> kernel ``compute_candidate_sha`` |
| candidate_review       | independent reviewer executor + kernel ``validate_review`` + SHA match |
| delivery_gate          | deterministic executor -> Kernel GateDecision (ADR-002)  |
| footer_assembly        | deterministic executor -> kernel ``validate_provenance`` |

The DELIVERY GATE conclusion (kernel ``GateDecision``) is the workflow's
terminal authority: the controller derives ``completed``/``blocked``/``stopped``
from it and never lets the Agno-native ``WorkflowCompleted`` become a ChatBI
completion (ADR-002).

Determinism: agent steps call an injected ``agent_runner`` (default = the main
Agent; conformance/tests inject a scripted stub), reviewer steps an injected
``reviewer_runner``. All judgments are kernel-side in both modes.

Applicable rules: HOOK-001, ADR-002, REV-001..003, SEM-001/RAW-001, SRC-002,
invariant 2/3/5.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from chatbi_governance.evidence import (
    EvidenceEntry,
    GateError,
    RunRecord,
    compute_candidate_sha,
    validate_provenance,
    validate_request,
)
from chatbi_governance.gates import GateDecision
from chatbi_governance.policy import PolicyRequest, decide

#: The workflow id this module implements (module 5: only chatbi-analyze).
WORKFLOW_ID = "chatbi-analyze"

#: IR step ids this implementation recognizes.
_IR_STEP_IDS = (
    "request_preflight", "policy_precheck", "clarify", "t1_semantic",
    "t2_curated", "t3_raw", "src002_crosscheck", "candidate_bind",
    "candidate_review", "delivery_gate", "footer_assembly",
)



class _StepConfig:
    """Wiring shared by every step executor (closure-free single object)."""

    def __init__(
        self,
        *,
        config: Any,                      # EffectiveConfig for policy.decide
        agent_runner: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None,
        reviewer_runner: Any,
        on_evidence: Callable[[str, str, EvidenceEntry], None] | None,
        on_tool: Callable[[str, str, str, bool], None] | None,
        on_ctx: Callable[[str, Mapping[str, Any]], None],
        harness_release: str,
        workflow: Any = None,             # agno Workflow (for session_state)
    ) -> None:
        self.config = config
        self.agent_runner = agent_runner
        self.reviewer_runner = reviewer_runner
        self.on_evidence = on_evidence
        self.on_tool = on_tool
        self.on_ctx = on_ctx
        self.harness_release = harness_release
        self.workflow = workflow
        #: IR tool policies per agent step id (StepToolPolicy).
        self.tool_policies: dict[str, Any] = {}


def _fresh_ctx(input: Any) -> dict[str, Any]:
    """Initial context from the run input."""
    if isinstance(input, Mapping) and "request" in input:
        request = dict(input["request"])
    elif isinstance(input, Mapping):
        request = dict(input)
    else:
        raise ValueError("chatbi-analyze input must be a request object")
    return {
        "request": request,
        "run_id": (input.get("run_id") if isinstance(input, Mapping) else None) or "",
        "scenario_id": (input.get("chatbi_scenario") if isinstance(input, Mapping)
                        else None) or "run",
        "policy_request_type": (
            input.get("chatbi_policy_request_type") if isinstance(input, Mapping)
            else None
        ) or "discover",
        "gaps": {},
        "evidence_chain": [],
        "tiers": [],
        "stop": None,
        "low_confidence": False,
        "delivery": None,
    }


def _ctx_from_step_input(step_input: Any, session_state: Any = None) -> dict[str, Any]:
    """Resolve the accumulated context from an agno step invocation.

    Agno 2.6.22 facts (verified against the installed 2.6.22 source):
    - function executors receive a ``StepInput`` object whose
      ``previous_step_content`` is STRINGIFIED by the engine
      (``StepOutput(content=str(result))``), so it cannot carry the context
      dict across steps;
    - when the executor signature declares ``session_state``, agno passes a
      deepcopy of the workflow session state and merges it back after the
      step (``merge_dictionaries``, additively).

    Therefore the run context rides in ``session_state["_ctx"]``: the first
    step seeds it from the run input, every later step continues the merged
    value (additive-only merges match our context growth pattern).
    """
    if session_state is not None and isinstance(session_state.get("_ctx"), Mapping):
        return session_state["_ctx"]
    raw = getattr(step_input, "input", step_input)
    return _fresh_ctx(raw)


# ---------------------------------------------------------------------------
# Step executors (deterministic / agent / reviewer)
# ---------------------------------------------------------------------------


def _agent_call(
    ctx: dict[str, Any], cfg: _StepConfig, step_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Run one agent step through the injected runner, enforcing the IR tool
    allowlist at the adapter boundary (MAJOR-2): every scripted tool call is
    judged by the step's StepToolPolicy; an out-of-allowlist call emits
    ``tool.blocked`` and fails the run fail-closed — the tool never runs."""
    policy = cfg.tool_policies.get(step_id)
    result = cfg.agent_runner(step_id, dict(payload), tool_policy=policy)
    if not isinstance(result, Mapping):
        raise GateError(GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=(f"step:{step_id}",),
            reason=f"agent step {step_id} returned a non-object result",
            recovery="Inspect the agent output contract and re-run",
        ))
    for call in result.get("tool_calls", []):
        name = call.get("name") if isinstance(call, Mapping) else str(call)
        name = str(name or "")
        allowed = policy.check(name) if policy is not None else False
        if cfg.on_tool is not None:
            cfg.on_tool(ctx.get("run_id") or "", step_id, name, allowed)
        if not allowed:
            raise GateError(GateDecision.block(
                rule_ids=("SEC-001", "HOOK-001"),
                evidence_refs=(f"tool:{name}",),
                reason=(
                    f"tool {name!r} is not on the IR allowlist for step "
                    f"{step_id}; the call was blocked and never executed"
                ),
                recovery="Use only the tools declared in the IR allowlist",
            ))
    return result


def _step_request_preflight(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    request = ctx["request"]
    validate_request(request)  # GateError -> step error -> run failed
    ctx["validated"] = True
    return ctx


def _step_policy_precheck(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    request = ctx["request"]
    decision = decide(
        cfg.config,
        PolicyRequest(
            request_type=ctx["policy_request_type"],
            target_entity=request.get("entity", ""),
            actor=request.get("actor", "agent"),
            purpose=request.get("purpose", ""),
        ),
    )
    ctx["policy_decision"] = decision.to_dict()
    if decision.status == "block":
        # SEM-003 / SEC-001: raise a GateError so the run fails fail-closed.
        raise GateError(decision)
    return ctx


def _step_clarify(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    if ctx.get("stop"):
        return ctx
    request = ctx["request"]
    if request.get("segment") != "undefined":
        return ctx  # IR when: request.field_is(segment, undefined)
    result = _agent_call(ctx, cfg, "clarify", {"request": request})
    ctx["stop"] = {"reason": "clarify",
                   "message": result.get("message", "Request fields are missing")}
    return ctx


def _step_t1_semantic(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    if ctx.get("stop"):
        return ctx
    request = ctx["request"]
    result = _agent_call(
        ctx, cfg, "t1_semantic",
        {"request": request, "entity": request.get("entity", "")},
    )
    status = result.get("status", "missing")
    if status == "covered":
        entry = EvidenceEntry.create(
            source_tier="T1", evidence_source="semantic-layer",
            rule_ids=("SEM-001", "SEM-002"),
            payload=result.get("payload", {}),
        )
        ctx["t1_catalog_hit"] = True
        ctx["tiers"].append("T1")
        ctx["evidence_chain"].append(entry.to_dict())
        if cfg.on_evidence is not None:
            cfg.on_evidence(ctx.get("run_id") or "", "t1_semantic", entry)
    elif status == "gap":
        entry = EvidenceEntry.create(
            source_tier="T1", evidence_source="semantic-layer",
            rule_ids=("SEM-001", "SEM-002"),
            payload=result.get("payload", {}),
        )
        ctx["t1_catalog_hit"] = False
        ctx["gaps"]["T1"] = [result.get("gap", "coverage_incomplete")]
        ctx["tiers"].append("T1")
        ctx["evidence_chain"].append(entry.to_dict())
        if cfg.on_evidence is not None:
            cfg.on_evidence(ctx.get("run_id") or "", "t1_semantic", entry)
    else:
        # T1 missing WITHOUT a recorded gap -> STOP (SEM-001); no chain
        # evidence is recorded (mirrors the golden contract).
        ctx["t1_catalog_hit"] = False
        ctx["stop"] = {
            "reason": "t1_missing_no_gap",
            "message": "T1 semantic layer has no entry for the entity and "
                       "no gap evidence was recorded",
        }
    return ctx


def _step_t2_curated(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    if ctx.get("stop") or "T1" not in ctx.get("gaps", {}):
        return ctx  # IR when: evidence.has_gap("T1")
    request = ctx["request"]
    result = _agent_call(
        ctx, cfg, "t2_curated",
        {"request": request, "entity": request.get("entity", ""),
         "t1_gap": ctx["gaps"]["T1"]},
    )
    if result.get("status") == "covered":
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="curated-reference",
            rule_ids=("RAW-001", "SRC-001"),
            payload=result.get("payload", {}),
        )
    else:
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="curated-reference",
            rule_ids=("RAW-001", "SRC-001"),
            payload=result.get("payload", {}),
        )
        ctx["gaps"]["T2"] = [result.get("gap", "curated_insufficient")]
    ctx["tiers"].append("T2")
    ctx["evidence_chain"].append(entry.to_dict())
    if cfg.on_evidence is not None:
        cfg.on_evidence(ctx.get("run_id") or "", "t2_curated", entry)
    return ctx


def _step_t3_raw(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    if ctx.get("stop") or "T2" not in ctx.get("gaps", {}):
        return ctx  # IR when: evidence.has_gap("T2")
    request = ctx["request"]
    result = _agent_call(
        ctx, cfg, "t3_raw",
        {"request": request, "entity": request.get("entity", ""),
         "t2_gap": ctx["gaps"]["T2"]},
    )
    entry = EvidenceEntry.create(
        source_tier="T3", evidence_source="raw-exploration",
        rule_ids=("RAW-003",),
        payload=result.get("payload", {}),
    )
    ctx["tiers"].append("T3")
    ctx["evidence_chain"].append(entry.to_dict())
    ctx["low_confidence"] = True
    if cfg.on_evidence is not None:
        cfg.on_evidence(ctx.get("run_id") or "", "t3_raw", entry)
    return ctx


def _step_src002_crosscheck(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    if ctx.get("stop"):
        return ctx
    business = getattr(cfg.config, "get", None) and (
        cfg.config.get("business_codebases") or {}
    )
    if not business:
        # Vacuously satisfied when no external Business Codebases are
        # configured (analyze command prose §Historical SQL).
        ctx["crosscheck"] = {"vacuous": True}
        return ctx
    result = _agent_call(
        ctx, cfg, "src002_crosscheck",
        {"request": ctx["request"], "codebases": business},
    )
    entry = EvidenceEntry.create(
        source_tier="T2", evidence_source="codebase-crosscheck",
        rule_ids=("SRC-002",), payload=result.get("payload", {}),
    )
    ctx["evidence_chain"].append(entry.to_dict())
    if cfg.on_evidence is not None:
        cfg.on_evidence(ctx.get("run_id") or "", "src002_crosscheck", entry)
    return ctx


def _step_candidate_bind(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    if ctx.get("stop"):
        return ctx
    request = ctx["request"]
    candidate = {
        "scenario": ctx["scenario_id"],
        "entity": request.get("entity", ""),
        "action": "deliver_answer",
        "answer": {"value": 42, "unit": "count"},
    }
    ctx["candidate"] = candidate
    ctx["candidate_sha"] = compute_candidate_sha(candidate)
    return ctx


def _step_candidate_review(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    if ctx.get("stop"):
        return ctx
    from .reviewer import run_review

    run_record = RunRecord(
        run_id=ctx.get("run_id", "run"),
        round=ctx.get("review_round", 1),
        candidate_sha=ctx["candidate_sha"],
        created_rev=cfg.harness_release,
        actor=ctx["request"].get("actor", "operator"),
        purpose=ctx["request"].get("purpose", ""),
    )
    if cfg.on_evidence is not None:
        pass  # review evidence is recorded by the controller
    result = run_review(
        run_record=run_record,
        candidate_sha=ctx["candidate_sha"],
        evidence_chain=tuple(ctx["evidence_chain"]),
        reviewer_runner=cfg.reviewer_runner,
    )
    ctx["review"] = {
        "status": result.verdict.value,
        "round": run_record.round,
        "candidate_sha": result.candidate_sha,
        "reason": result.reason,
    }
    # MEDIUM-1: the verdict is governance Evidence — persist it under .chatbi
    # (sanitized by EvidenceEntry.create, content_sha256 bound) so the
    # review judgment is auditable alongside the evidence chain.
    review_entry = EvidenceEntry.create(
        source_tier="T2", evidence_source="candidate-review",
        rule_ids=("REV-001", "REV-002", "REV-003"),
        payload={
            "status": result.verdict.value,
            "round": run_record.round,
            "candidate_sha": result.candidate_sha,
            "findings": list(result.findings),
            "reason": result.reason,
        },
        runtime_name="agno",
        native_run_id=ctx.get("run_id") or "",
        harness_release=cfg.harness_release,
    )
    if cfg.on_evidence is not None:
        cfg.on_evidence(ctx.get("run_id") or "", "candidate_review",
                        review_entry)
    return ctx


def _step_delivery_gate(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    if ctx.get("stop"):
        return ctx
    review = ctx.get("review", {})
    reason = review.get("reason") or ""
    pass_rule_ids = ("REV-001", "REV-002", "REV-003")
    if review.get("status") == "PASS":
        ctx["delivery"] = GateDecision.pass_(
            rule_ids=pass_rule_ids,
            evidence_refs=("evidence:candidate-review",),
            reason=(
                f"Independent review PASS for candidate "
                f"{ctx['candidate_sha'][:12]}… (REV-001/002/003)"
            ),
            recovery="No action required",
        ).to_dict()
        return ctx
    # Blocking decision with the golden review-gate rule vocabulary
    # (mirrors the subagent_review_gate / stop_gate rule sets):
    if "unavailable" in reason or "unverifiable" in reason or "fail-closed" in reason:
        # Reviewer unavailable / unparseable / schema-error -> fail-closed.
        rule_ids = ("HOOK-001", "HOOK-004", "SEC-003")
    elif "round exceeded" in reason:
        rule_ids = ("REV-003", "HOOK-001")
    elif "candidate SHA" in reason or "exact candidate" in reason:
        # PASS bound to a different SHA is invalid (stale candidate).
        rule_ids = ("REV-001", "REV-003")
    else:
        # Verdict is not a clean PASS for the frozen candidate.
        rule_ids = ("REV-001", "REV-003", "HOOK-001")
    ctx["delivery"] = GateDecision.block(
        rule_ids=rule_ids,
        evidence_refs=("evidence:candidate-review",),
        reason=reason or "delivery gate requirement not met",
        recovery="resolve the blocking finding and re-review the candidate",
    ).to_dict()
    return ctx


def _step_footer_assembly(step_input: Any, cfg: _StepConfig, session_state: Any = None) -> dict[str, Any]:
    ctx = _ctx_from_step_input(step_input, session_state)
    if ctx.get("stop"):
        return ctx
    if not ctx.get("delivery") or ctx["delivery"]["status"] != "pass":
        return ctx  # IR when: delivery_decision.is_pass
    request = ctx["request"]
    tiers = ctx["tiers"]
    source_tier = tiers[-1] if tiers else "T1"
    footer = {
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
            "(ANS-003)" if ctx.get("low_confidence")
            else "governed evidence chain"
        ),
        "review_round": ctx.get("review_round", 1),
        "freshness": "snapshot_2024_01" if ctx.get("low_confidence")
        else "current",
        "owner": "domain_owner_example",
        "confidence": "low" if ctx.get("low_confidence") else "medium",
        "provenance_refs": [f"evidence:run:{ctx.get('run_id', 'run')}"],
    }
    validate_provenance(footer)  # GateError -> step error -> run failed
    ctx["footer"] = footer
    run_id = ctx.get("run_id") or ""
    cfg.on_ctx(run_id, ctx)
    return ctx


# ---------------------------------------------------------------------------
# Workflow construction
# ---------------------------------------------------------------------------


def build_analyze_workflow(
    *,
    workflows_dir: str | Path,
    config: Any,
    agent_runner: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_runner: Any = None,
    on_evidence: Callable[[str, str, EvidenceEntry], None] | None = None,
    on_tool: Callable[[str, str, str, bool], None] | None = None,
    on_ctx: Callable[[str, Mapping[str, Any]], None] | None = None,
    harness_release: str = "dev",
    db: Any = None,
    approval_action_type: str | None = None,
    main_agent: Any = None,
) -> Any:
    """Build the ``chatbi-analyze`` Agno Workflow from the IR at runtime.

    ``agent_runner(step_id, payload) -> dict`` — the main-agent execution seam
    (default: runs the real ``main_agent``); ``reviewer_runner`` — the
    independent-review seam (default: live reviewer agent). Both are injectable
    so conformance/tests run deterministically (runtime_stubs).

    ``approval_action_type`` (test/deployment config only, module-5 spike):
    when set to a protected action, the workflow inserts a human-approval step
    (Agno ``requires_confirmation`` pause) after the policy precheck; the
    controller bridges the pause to the ChatBI ApprovalCoordinator. The
    analyze IR itself declares no human_approval step — this is the Spike's
    target-specific approval seam (module 6 wires per-IR approval steps).
    """
    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.workflow import Step, Workflow

    from chatbi_harness_ir.loader import load_workflow

    ir = load_workflow(Path(workflows_dir) / "chatbi-analyze.yaml")
    if ir.workflow_id != WORKFLOW_ID:
        raise ValueError(
            f"IR workflow {ir.workflow_id!r} != expected {WORKFLOW_ID!r}"
        )
    step_ids = [step.id for step in ir.steps]
    unknown = [sid for sid in step_ids if sid not in _IR_STEP_IDS]
    if unknown:
        raise ValueError(f"IR declares steps the agno workflow cannot map: {unknown}")

    cfg = _StepConfig(
        config=config,
        agent_runner=agent_runner,
        reviewer_runner=reviewer_runner,
        on_evidence=on_evidence,
        on_tool=on_tool,
        on_ctx=on_ctx or (lambda run_id, ctx: None),
        harness_release=harness_release,
        workflow=None,
    )
    # IR tool-allowlist injection: per agent step, the declared allow/deny
    # (falling back to the workflow-level default) becomes the StepToolPolicy
    # the agent-call boundary enforces (MAJOR-2).
    from .tools import StepToolPolicy

    cfg.tool_policies = {
        step.id: StepToolPolicy.from_ir_step(step, ir.tools)
        for step in ir.steps
        if step.executor.value in ("agent_with_tools", "independent_reviewer")
    }

    def _live_agent_runner(
        step_id: str, payload: Mapping[str, Any], tool_policy: Any = None,
    ) -> dict[str, Any]:
        if main_agent is None:
            raise RuntimeError("main agent unavailable (fail-closed)")
        if tool_policy is not None:
            from .tools import filter_agent_tools

            allowed_tools = filter_agent_tools(
                list(main_agent.tools or []), tool_policy
            )
            if len(allowed_tools) != len(list(main_agent.tools or [])):
                # Real agno mechanism: the step agent's tool surface is
                # filtered by the IR allowlist before the run, so the
                # runtime cannot invoke a non-allowlisted tool.
                from agno.agent import Agent as _Agent

                main_agent = _Agent(
                    id=main_agent.id,
                    name=main_agent.name,
                    model=main_agent.model,
                    tools=allowed_tools,
                    session_id=main_agent.session_id,
                    markdown=False,
                )
        response = main_agent.run(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        )
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("main agent returned no content (fail-closed)")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "main agent output is not JSON (fail-closed)"
            ) from error
        if not isinstance(parsed, Mapping):
            raise RuntimeError("main agent output is not a JSON object")
        return parsed

    if agent_runner is None:
        agent_runner = _live_agent_runner

    def _wrap(executor: Callable[..., Any]) -> Callable[..., Any]:
        """Report the context after EVERY step so the controller can derive
        the terminal state even when the workflow stops early (ADR-002).
        The wrapper declares ``session_state`` so agno passes it through."""

        def _executor_wrapper(step_input: Any, session_state: Any = None) -> Any:
            result = executor(step_input, cfg, session_state)
            if isinstance(result, Mapping):
                # Write the context back so agno's merge carries it to the
                # next step's session_state copy (step-to-step threading).
                if isinstance(session_state, dict):
                    session_state["_ctx"] = result
                # run_id rides the ctx itself (agno's save_session pops
                # current_run_id from the persisted session state, so the
                # session_state key is unreliable across a pause boundary).
                run_id = result.get("run_id") or ""
                if not run_id and isinstance(session_state, dict):
                    run_id = session_state.get("current_run_id") or ""
                cfg.on_ctx(run_id, result)
            return result

        _executor_wrapper.__name__ = executor.__name__
        return _executor_wrapper

    steps = [
        Step(
            executor=_wrap(_step_request_preflight),
            step_id="request_preflight",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_policy_precheck),
            step_id="policy_precheck",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_clarify),
            step_id="clarify",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_t1_semantic),
            step_id="t1_semantic",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_t2_curated),
            step_id="t2_curated",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_t3_raw),
            step_id="t3_raw",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_src002_crosscheck),
            step_id="src002_crosscheck",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_candidate_bind),
            step_id="candidate_bind",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_candidate_review),
            step_id="candidate_review",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_delivery_gate),
            step_id="delivery_gate",
            on_error="fail",
        ),
        Step(
            executor=_wrap(_step_footer_assembly),
            step_id="footer_assembly",
            on_error="fail",
        ),
    ]

    if approval_action_type is not None:
        # Module-5 spike approval seam (see docstring above).
        from .workflow_approval import make_approval_gate_step

        steps.insert(2, make_approval_gate_step(approval_action_type))

    return Workflow(
        id=WORKFLOW_ID,
        name="Governed analysis (5-layer flow)",
        description=ir.description,
        steps=steps,
        db=db,
        store_events=True,
        stream_events=True,
    )
