"""Governed agent assembly for the ChatBI skill+hooks target (module C/D).

:func:`build_governed_agent` assembles the SINGLE governance agent (裁决 Q1
— one agent carries all nine workflows; per-workflow factories are NOT
implemented) from the module A-D components:

- instructions: the prompt assets' governance + runbook bodies PLUS the
  nine-workflow routing table (request type -> runbook name, derived from
  the IR ``prompts[]`` declarations — the model picks the runbook per
  request type; soft constraint, honest registration);
- skills: ``Skills([LocalSkills(skills_root)], raise_on_loader_error=True)``
  mounting the 7 manifest-registered CC skills (agno 2.6.22, spike R5
  pinned);
- tools: the 19 governance tools (dumb bodies, module A) + read-only file
  tools surface (agent holds NO bare Write/Edit/Bash — design R2);
- tool_hooks: the six-layer chain (module B);
- pre_hooks: ``ChatbiRequestGuardrail`` + ``ChatbiPolicyGuardrail``
  (normalize_pre_hooks — guardrails run synchronously in AgentOS server
  mode, ``agent/_hooks.py:60-95``);
- post_hooks: ``ChatbiDeliveryGuardrail`` (the ONLY terminal authority,
  ADR-002);
- model: the deployment model_ref resolution (config.py, adjudication
  seven); the reviewer agent (independent read-only actor) is built by the
  caller and injected via ``reviewer_agent`` / ``reviewer_runner`` (stub
  seam for conformance).

The run-level trusted subject (SEC-003) is recorded by the PolicyGuardrail
from the run context only — never from the input body.

Applicable rules: MR-005, ADR-002/003, SEC-003, PORT-001, HOOK-001,
SEM-003, invariant 2/5.
"""

from __future__ import annotations

#: High-salience protocol preamble, prepended to the agent's instructions.
#: Real-model integration (agno 验收 3.1): the model read the full runbook
#: body (~5-6K tokens) and still answered a data question WITHOUT calling any
#: governance tool, so the delivery gate C002-blocked the output. The long
#: prose buried the ordering requirement; this short preamble makes the
#: mandatory flow and the ask-the-user rule impossible to miss. Adapter-side
#: only — the shared runbook/manifest assets are untouched.
_GOVERNANCE_PROTOCOL = (
    "GOVERNANCE PROTOCOL (mandatory for every data-analysis question):\n"
    "1. You MUST begin by calling chatbi_record_request — never produce a "
    "data answer without the governed flow. The request needs exactly 7 "
    "fields: question, time_range (format YYYY-MM-DD_to_YYYY-MM-DD), "
    "entity, segment, actor, purpose, supported_decision.\n"
    "2. Fill the standard request defaults when the user's question does "
    "not state them: actor=operator, purpose=decision_support, "
    "supported_decision=analysis. Ask the user ONLY for what genuinely "
    "changes the answer — the analysis window (time_range) when the "
    "question has no temporal scope, the entity/segment when ambiguous "
    "(REQ-001 clarify). Never guess the window; never send empty values.\n"
    "3. If the request is NOT final — you still need to clarify the window, "
    "entity, segment, or caliber with the user, or the user asked you for "
    "suggestions — STOP after chatbi_record_request. Answer with your "
    "clarification/suggestions ending in a question. Do NOT call "
    "chatbi_semantic_discover / chatbi_record_evidence yet: recorded "
    "evidence commits the run to delivery, and a clarification ending with "
    "evidence recorded is BLOCKED by the delivery gate (REV-001). Only "
    "when the request is final and you are proceeding to deliver, go to "
    "step 4.\n"
    "4. BUSINESS SEMANTICS (C2 authority): when the user asks about "
    "business meanings — e.g. which source tables map to which functions, "
    "or what a business term like '核心功能/主功能/辅助功能' means — the "
    "configured Business Codebase (chatbi_crosscheck with the declared "
    "alias) is the C2 context authority. READ the matched documents FULLY, "
    "extract the mapping/taxonomy yourself, and PROCEED with the resolved "
    "entities. Do NOT ask the user to choose between business documents "
    "you can read yourself; ask only when the documents genuinely conflict "
    "or are silent (REQ-004, SRC-002). Static mappings (table-to-function "
    "taxonomies) are time-independent — do not ask for a time window just "
    "to read them.\n"
    "5. Record evidence via chatbi_record_evidence (T1 semantic layer "
    "first; T2/T3 only after a recorded T1 gap).\n"
    "6. COVERAGE & CONFIDENCE (QLT-001/ANS-003 — calibrated guidance, not "
    "hard requirements): establish the data's actual time coverage "
    "(MIN/MAX of the authoritative time column via chatbi_query_source) "
    "and deliver the caliber the data supports: single-point data -> "
    "point-in-time answer; partial coverage -> disclose exactly which days "
    "are covered or missing; an empty window -> deliver the verifiable "
    "no-data finding. When coverage makes the confidence low, DISCLOSE it "
    "(freshness + confidence=low + ANS-003 high-risk note) rather than "
    "blocking. ASK the user to confirm the caliber only when the choice "
    "materially changes the answer and the user did not already specify "
    "it. Quantitative thresholds (e.g. which missing-day ratio counts as "
    "low confidence, when data counts as stale) are deployment policy "
    "configured and confirmed by the domain owner — never invent them "
    "yourself (EVAL-004).\n"
    "{coverage_policy_line}\n"
    "7. MODEL SUFFICIENCY: when the governed dw models (ods/dwd/dws/ads) "
    "cannot support the requested analysis — missing tables, dimensions, "
    "or grain — do NOT keep digging raw tables or re-asking the user for "
    "calibers. Cross-check the business docs (C2) and the source "
    "inventory, then PROPOSE the governed build chain for what is "
    "missing (source extension via chatbi_bootstrap -> "
    "chatbi_build_plan layers ods/dwd/dws/ads -> chatbi_dbt_draft/"
    "chatbi_dbt_execute), state the affected assets and the expected "
    "end-to-end flow, and STOP for operator approval BEFORE executing "
    "any protected action (SCOPE-001, SEM-003). The operator may "
    "approve, adjust, or decline the proposal.\n"
    "8. Freeze the final answer with chatbi_submit_candidate, then call "
    "chatbi_review — the answer is delivered only after the independent "
    "reviewer passes (REV-001).\n"
    "9. When a tool call is DENIED, read the recovery message and follow "
    "it; if it says ASK the user, ask the user. If a denial says the "
    "warehouse is NOT initialized (no source inventory / no dw_agno "
    "models), STOP querying immediately and PROPOSE initialization to the "
    "operator (chatbi-bootstrap, only after their confirmation) — never "
    "substitute raw file exploration, and do not wait for the user to say "
    "\"build a warehouse\".\n"
    "10. When you need input from the user, END your message with a "
    "question — the delivery gate treats question-ending messages as "
    "conversational handoffs, not deliveries.\n"
    "11. If you believe a governance rule, checkpoint, or denial is "
    "unreasonable or conflicts with the user's intent in the current "
    "situation, do NOT bypass it silently and do NOT just stop with a "
    "bare failure — explain your objection, quote the conflicting rule, "
    "propose alternatives, and ASK the user to adjudicate (end with a "
    "question). The user's answer governs; deterministic gates still "
    "apply until then (HOOK-001).\n"
    "12. Non-analyze workflows (init, bootstrap, maintain-model, "
    "build-from-requirement, evaluate, correction, audit-drift, "
    "maintain-knowledge): load the runbook with chatbi_load_runbook and "
    "execute with the workflow's chatbi_* tool (e.g. chatbi_bootstrap). "
    "The CC /chatbi-* commands and native skill tools (get_skill_*) do NOT "
    "exist in this runtime — never call them."
)

from pathlib import Path
from typing import Any, Callable, Mapping

from .governed_tools import RunScope, ToolSpec, build_governed_tools
from .hooks import (
    ChatbiDeliveryGuardrail,
    ChatbiPolicyGuardrail,
    ChatbiRequestGuardrail,
    build_tool_hooks,
)
from .prompt_loader import PromptAssets, build_runbook_registry


def _routing_table(ir_workflows: Mapping[str, Any]) -> str:
    """The nine-workflow routing table (request type -> runbook name).

    Derived from the IR ``prompts[]`` declarations (soft guidance: the model
    selects the runbook per request type; workflows with no registered
    runbook carry built-in instructions).
    """
    lines = [
        "Governed request routing — select the runbook by request type:"
    ]
    for workflow_id in sorted(ir_workflows):
        workflow = ir_workflows[workflow_id]
        prompts = getattr(workflow, "prompts", ()) or ()
        names = [getattr(p, "name", "") for p in prompts if getattr(p, "name", "")]
        if names:
            lines.append(f"- {workflow_id}: runbook(s) {', '.join(names)}")
        else:
            lines.append(f"- {workflow_id}: no runbook (built-in instructions)")
    lines.append(
        "Step order (T1->T2->T3, clarify, routing) follows the runbook; the "
        "deterministic edges (evidence preconditions, candidate SHA, review, "
        "approval, allowlist, realpath, delivery gate) are enforced by the "
        "tool hooks and guardrails."
    )
    # A2 (design-runbook-completion): the runbook-loading instruction follows
    # the routing table — the table is the selector, chatbi_load_runbook is
    # the loader (native get_skill_* tools are NOT allowlisted, C011).
    lines.append(
        "Runbook loading: before executing a workflow, load its runbook with "
        "chatbi_load_runbook(<workflow_id>) (once per run). The routing "
        "table above is the selector; deterministic edges are enforced by "
        "tool hooks."
    )
    return "\n".join(lines)


def build_governed_agent(
    *,
    deployment: Any,
    model_config: Any,
    config: Any,                         # EffectiveConfig (policy.decide etc.)
    ir_workflows: Mapping[str, Any],
    workspace_root: Path,
    harness_release: str,
    prompt_assets: PromptAssets,
    evidence_index: Any,
    event_log: Any,
    approvals: Any,
    tool_specs: list[ToolSpec],
    run_scope: RunScope | None = None,
    reviewer_agent: Any = None,
    reviewer_runner: Any = None,         # stub 注入 seam（conformance）
    native_runner: Callable[..., Any] | None = None,
    clock: Any = None,
    model: Any = None,                   # stub seam（conformance _ScriptedModel）
) -> Any:
    """Build the governed Agent (agno 2.6.22) with all module A-D wiring.

    Every stub seam (``reviewer_runner``/``native_runner``/``model``) is
    injected here so conformance and unit tests drive the agent without a
    live model; ``model=None`` builds the deployment model config
    (OpenAIResponses, adjudication 7).
    """
    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.agent import Agent
    from agno.models.openai import OpenAIResponses
    from agno.utils.hooks import normalize_post_hooks, normalize_pre_hooks

    if model is None and model_config is None:
        raise RuntimeError(
            "a model or a model_config is required to build the governed "
            "agent (fail-closed, MR-005)"
        )
    if model is None and model_config.api_key:
        import os

        os.environ.setdefault("OPENAI_API_KEY", model_config.api_key)
        os.environ.setdefault("OPENAI_BASE_URL", model_config.base_url)

    scope = run_scope if run_scope is not None else RunScope()
    # A1: the runbook registry is derived from the IR prompts[] + the prompt
    # manifest (single source of truth, A2) once, then shared by the tool
    # surface and the domain hook (fail-closed: any drift -> PromptLoadError
    # at startup; None is never passed here).
    runbook_registry = build_runbook_registry(ir_workflows, prompt_assets)
    # M1 (review-binding): the reviewer's governing-context hash is the
    # manifest-pinned sha256 of the adversarial-reviewer instructions.
    # prompt_loader already verified content sha == manifest registration at
    # startup, so the entries carry the authoritative value; resolve here
    # fail-closed (never a silent candidate_sha fallback).
    reviewer_context_hash = next(
        (entry.sha256 for entry in prompt_assets.entries
         if entry.name == "agents/adversarial-reviewer.md"),
        "",
    )
    if not reviewer_context_hash:
        raise ValueError(
            "prompt assets carry no pinned sha256 for the "
            "agents/adversarial-reviewer.md entry (fail-closed)")
    tools, spec_by_name = build_governed_tools(
        specs=tool_specs,
        deployment=deployment,
        config=config,
        evidence_index=evidence_index,
        event_log=event_log,
        approvals=approvals,
        reviewer_agent=reviewer_agent,
        workspace_root=workspace_root,
        harness_release=harness_release,
        native_runner=native_runner,
        reviewer_runner=reviewer_runner,
        clock=clock,
        run_scope=scope,
        runbook_registry=runbook_registry,
        reviewer_context_hash=reviewer_context_hash,
    )
    tool_hooks = build_tool_hooks(
        specs_by_name=spec_by_name,
        ir_workflows=ir_workflows,
        config=config,
        approvals=approvals,
        evidence_index=evidence_index,
        event_log=event_log,
        workspace_root=workspace_root,
        harness_release=harness_release,
        run_scope=scope,
        reviewer_runner=reviewer_runner,
        native_runner=native_runner,
        deployment=deployment,
        clock=clock,
        runbook_registry=runbook_registry,
    )
    pre_hooks = normalize_pre_hooks([
        ChatbiRequestGuardrail(config=config, event_log=event_log,
                               run_scope=scope, deployment=deployment),
        ChatbiPolicyGuardrail(config=config, event_log=event_log,
                              deployment=deployment),
    ])
    post_hooks = normalize_post_hooks([
        ChatbiDeliveryGuardrail(
            config=config, event_log=event_log,
            evidence_index=evidence_index, workspace_root=workspace_root,
            harness_release=harness_release, run_scope=scope,
            ir_workflows=ir_workflows),
    ])
    # coverage_policy (2026-08-14): inject the deployment's owner-confirmed
    # quantitative coverage thresholds (EVAL-004) into the preamble. Config
    # explicit values win; when no threshold is configured, the unconfigured
    # wording applies and the model must not invent thresholds. ``config``
    # is None on the conformance stub path -> unconfigured wording.
    coverage_line = (
        "No deployment coverage policy is configured: apply coverage "
        "disclosure without inventing thresholds (EVAL-004).")
    if config is not None:
        try:
            policy = (config.get("governance") or {}).get("coverage_policy") or {}
        except Exception:  # noqa: BLE001 - fail-open to the unconfigured wording
            policy = {}
        ratio = policy.get("low_confidence_missing_ratio")
        days = policy.get("stale_after_days")
        owner = policy.get("owner")
        owner = owner.strip() if isinstance(owner, str) else ""
        if (ratio is not None or days is not None) and owner:
            coverage_line = (
                "Deployment coverage policy (owner-confirmed by {owner}): "
                "missing-days ratio >= {ratio} -> confidence=low; data "
                "max-date older than {days} days -> stale.").format(
                    owner=owner,
                    ratio=ratio if ratio is not None else 0.2,
                    days=days if days is not None else 7)
    instructions = [
        _GOVERNANCE_PROTOCOL.format(coverage_policy_line=coverage_line),
        *prompt_assets.instructions,
        _routing_table(ir_workflows),
    ]

    agent = Agent(
        id="chatbi-agno",
        name="ChatBI Governed Agent (skill+hooks)",
        description=(
            "Single governance agent for the nine IR workflows: runbook "
            "routing + governance tools + tool hooks + guardrails "
            "(allowlist, evidence preconditions, candidate SHA, review, "
            "approval, delivery gate)."
        ),
        model=model if model is not None else OpenAIResponses(
            id=model_config.model,
            base_url=model_config.base_url or None,
        ),
        instructions=instructions,
        # No agno-native Skills mounting (agno 验收 2026-08-12): the
        # Skills([LocalSkills]) wrapper exposes get_skill_instructions /
        # get_skill_reference / get_skill_script as agent tools, which the
        # real model picks for "load skill" intent and gets C011-blocked —
        # tool-surface noise that distracts from the governed loader. The
        # runbook content is fully reachable via chatbi_load_runbook
        # (sha256-pinned) and the governance+runbook bodies are already
        # statically injected above; the native skill tools must not exist
        # on the agent's surface (C011 interception stays for genuine
        # violations only).
        tools=tools,
        tool_hooks=tool_hooks,
        pre_hooks=pre_hooks,
        post_hooks=post_hooks,
        markdown=False,
        # Independent session; no shared memory wiring (reviewer pattern).
        memory_manager=None,
        enable_agentic_memory=False,
        enable_user_memories=False,
        add_memories_to_context=False,
        store_events=False,
    )
    return agent
