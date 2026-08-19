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

#: High-salience protocol preamble = instructions[0] of the ChatBI agent.
#:
#: How the FIRST model message (role=system) is built when serve.py starts
#: chatbi-agno (live dump session 5a65c991, ~31 KiB). ChatBI does NOT set
#: Agent.system_message; agno-main get_system_message therefore concatenates:
#:
#:   [1] agent.description          (no tag; first paragraph)
#:   [2] "- " + instructions[0]     this preamble (_GOVERNANCE_PROTOCOL)
#:   [3] "- " + instructions[1]     skills/chatbi-governance/SKILL.md
#:   [4] "- " + instructions[2]     skills/chatbi-runbook/SKILL.md
#:   [5] "- " + instructions[3]     _routing_table(ir_workflows)
#:
#: use_instruction_tags defaults False, so there is NO <instructions>
#: wrapper — each list item is a markdown "- " bullet. markdown=False
#: and add_datetime_to_context unset, so no <additional_information>.
#: Skills() is not mounted, so no skill-index snippet. Reviewer prompt
#: (adversarial-reviewer.md) is a SEPARATE agent and is not in this
#: system message.
#:
#: Real-model integration (agno 验收 3.1): the model read the full runbook
#: body (~5-6K tokens) and still answered a data question WITHOUT calling any
#: governance tool, so the delivery gate C002-blocked the output. The long
#: prose buried the ordering requirement; this short preamble makes the
#: mandatory flow and the ask-the-user rule impossible to miss. Adapter-side
#: only — the shared runbook/manifest assets are untouched.
_GOVERNANCE_PROTOCOL = (
    "GOVERNANCE PROTOCOL (mandatory for every data-analysis question):\n"
    "0. ROLE AND VOICE (two phases — do not mix them in what the user sees):\n"
    "You are a business-literate warehouse developer, analyst, and "
    "architect: you understand the product domain AND you design "
    "governed ODS/DWD/DWS/ADS models. "
    "Phase A — understand the ask and discuss the plan: be flexible. "
    "Talk like a consultant. Offer options, tradeoffs, and a "
    "recommendation. Ask only what changes the answer. STOP with a "
    "question (step 3 / step 10). Do NOT dump an evidence pipeline or "
    "rule catalog. "
    "Phase B — execute and deliver: stay strict. Call the governed "
    "tools in order; never invent numbers, enums, or joins; the JSON "
    "delivery contract (step 8) is unchanged. "
    "INTERNAL vs EXTERNAL language: tool arguments, evidence payloads, "
    "and reviewer text MAY use T1/T2/T3 and rule ids (SEM-001, ANS-003, "
    "SRC-002, …). Every message the operator reads MUST translate those "
    "terms. Do not lead with rule ids. Preferred wording:\n"
    "- T1 -> 已发布的指标 / 语义层口径\n"
    "- T2 -> 已治理的数仓模型（可贴源/明细/汇总/应用层）\n"
    "- T3 -> 生产库原始表（尚未入仓，把握程度低）\n"
    "- ODS/DWD/DWS/ADS -> 贴源层 / 明细层 / 汇总层 / 应用层 "
    "(give the English abbreviation once in parentheses)\n"
    "- C2 / Business Codebase -> 业务文档里的定义\n"
    "- ANS-003 / confidence=low -> 口径限制与把握程度（需写明缺了什么）\n"
    "- SEM-003 / protected action -> 需要业务负责人签字的口径或发布\n"
    "- SRC-002 / locatable citation -> 文档里能点开的出处\n"
    "- candidate / freeze / review PASS -> 提交结论 / 独立复核通过\n"
    "When proposing a build chain, say: 先把生产表纳入贴源层，再整理成"
    "分析用的明细和汇总，批准后才写模型并构建 — then the tool names "
    "in one short line if useful. Never answer a business user with a "
    "bullet list of rule ids.\n"
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
    "5. LOAD THEN ACT: after the request is final, call "
    "chatbi_load_runbook with the workflow from the routing table "
    "(chatbi-analyze for a data question; chatbi-bootstrap / "
    "chatbi-build-from-requirement / chatbi-maintain-model for warehouse "
    "build). Do NOT query production tables, record evidence, or submit "
    "a candidate until that load succeeds — the hook will deny those "
    "tools otherwise. Follow the loaded procedure. Record evidence via "
    "chatbi_record_evidence (published metrics first; warehouse models "
    "then production-table exploration only after a recorded gap).\n"
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
    "{timezone_line}\n"
    "7. MODEL SUFFICIENCY (HARD): if models/ods|dwd|dws|ads contain NO "
    "dbt SQL models, you MUST NOT answer via T3 raw source queries. "
    "Cross-check C2 + the source inventory, then PROPOSE the governed "
    "build chain (chatbi_bootstrap source extension -> "
    "chatbi_build_plan layers ods/dwd/dws/ads -> chatbi_dbt_draft / "
    "chatbi_dbt_execute), state the affected assets and the expected "
    "end-to-end flow, and STOP for operator approval. After the "
    "operator approves, execute the approved chain and only then "
    "analyze from the new governed models. T3 raw is allowed ONLY "
    "after the operator explicitly declines the build (SCOPE-001, "
    "SEM-003). When models exist but still cannot support the "
    "requested grain/dimension, propose the missing layer — do not "
    "silently fall back to raw tables.\n"
    "7.5. EVIDENCE GATHERING (proactive citation before freeze): "
    "before freezing a candidate, proactively search the C2 business "
    "docs for every business claim you make (enum values, table "
    "mappings, field meanings). Search with TABLE NAMES and FIELD "
    "NAMES (e.g. t_plg_creator_projects), NOT phrases. Use "
    "chatbi_crosscheck(query=<table_name>, codebase=<alias>, "
    "search=True) to locate descriptions; if search returns 0, try "
    "reading the known doc (chatbi_crosscheck(query=<doc_path>, "
    "search=False)) or list available docs (chatbi_crosscheck("
    ", list_files=True)). Cite evidence as <alias>:<path>@<rev>#<line>. "
    "Claims you cannot evidence from C2 MUST be labelled Agent "
    "interpretation in the candidate. When the reviewer blocks on "
    "evidence, AUTO-RETRY: search with different table/field names, "
    "read the full doc, then re-submit. Only ask the user after "
    "exhausting search options.\n"
    "8. Freeze and deliver in ONE shape (REV-001 SHA binding): call "
    "chatbi_submit_candidate with the content ALREADY in the delivery "
    "contract shape - {{\"answer_body\": \"<the full markdown answer>\", "
    "\"provenance_footer\": \"<one line: source tier | review round | "
    "freshness | owner | confidence>\"}}. Then call chatbi_review. After "
    "PASS, output the SAME JSON object UNCHANGED as the final line of your "
    "message - do NOT reorganize, rephrase, or re-wrap it; the delivery "
    "gate hashes what you deliver and compares to the reviewed SHA, so any "
    "structural change re-blocks (gate-msg: final candidate changed after "
    "review PASS). Escape newlines inside the JSON string values; write no "
    "prose after the object.\n"
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
    "12. All nine workflows load the same way: chatbi_load_runbook then "
    "the workflow's chatbi_* tool (e.g. chatbi_bootstrap). Analyze is "
    "not pre-loaded in this prompt. The CC /chatbi-* commands and native "
    "skill tools (get_skill_*) do NOT exist in this runtime — never "
    "call them."
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
    # workspace_timezone (2026-08-17): inject the deployment's owner-confirmed
    # timezone caliber (EVAL-004 pattern) into the preamble. The caliber is the
    # reference for data timestamps / calendar-day / freshness assertions; when
    # unconfigured the model must disclose the raw convention it observed.
    timezone_line = (
        "Workspace timezone caliber: not configured - state the raw "
        "convention you observed (e.g. naive datetimes) and disclose the "
        "assumption when a calendar-day or freshness judgment depends on it.")
    if config is not None:
        try:
            tz = (config.get("governance") or {}).get("workspace_timezone") or {}
        except Exception:  # noqa: BLE001 - fail-open to the unconfigured wording
            tz = {}
        tz_zone = tz.get("zone")
        tz_owner = tz.get("owner")
        tz_owner = tz_owner.strip() if isinstance(tz_owner, str) else ""
        if isinstance(tz_zone, str) and tz_zone.strip() and tz_owner:
            timezone_line = (
                "Workspace timezone caliber (owner-confirmed by {owner}): "
                "{zone}. State data timestamps and \"as of\" references in "
                "this timezone, and cite it in the provenance footer when a "
                "calendar-day or freshness judgment depends on it.").format(
                    owner=tz_owner, zone=tz_zone)
    # System-message parts (prompt-slim): description + 2 instruction
    # strings. Analyze/governance skill bodies are NOT inlined — load via
    # chatbi_load_runbook. agno-main joins each item as "- {item}\n".
    instructions = [
        _GOVERNANCE_PROTOCOL.format(
            coverage_policy_line=coverage_line,
            timezone_line=timezone_line),
        _routing_table(ir_workflows),
    ]

    agent = Agent(
        id="chatbi-agno",
        name="ChatBI Governed Agent (skill+hooks)",
        # Multi-turn continuity (2026-08-15): AgentOS defaults to
        # add_history_to_context=False — every new run saw ONLY the current
        # user message, so handoff continuations ("选1" / "开新运行重评")
        # had no context and the model answered garbled (session 68813a62).
        # Bounded to the last 3 runs to cap context growth on long sessions.
        add_history_to_context=True,
        num_history_runs=3,
        # [1] FIRST paragraph of role=system (see get_system_message 3.3.1).
        # Not wrapped; appears before the "- GOVERNANCE PROTOCOL" bullet.
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
        # (sha256-pinned); the native skill tools must not exist
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
