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
- tools: the 14 governance tools (dumb bodies, module A) + read-only file
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

from pathlib import Path
from typing import Any, Callable, Mapping

from .governed_tools import RunScope, ToolSpec, build_governed_tools
from .hooks import (
    ChatbiDeliveryGuardrail,
    ChatbiPolicyGuardrail,
    ChatbiRequestGuardrail,
    build_tool_hooks,
)
from .prompt_loader import PromptAssets


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
) -> Any:
    """Build the governed Agent (agno 2.6.22) with all module A-D wiring.

    Every stub seam (``reviewer_runner``/``native_runner``) is injected here
    so conformance and unit tests drive the agent without a live model.
    """
    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.agent import Agent
    from agno.models.openai import OpenAIResponses
    from agno.skills import LocalSkills, Skills
    from agno.utils.hooks import normalize_post_hooks, normalize_pre_hooks

    if model_config.api_key:
        import os

        os.environ.setdefault("OPENAI_API_KEY", model_config.api_key)
        os.environ.setdefault("OPENAI_BASE_URL", model_config.base_url)

    scope = run_scope if run_scope is not None else RunScope()
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
    instructions = [
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
        model=OpenAIResponses(
            id=model_config.model,
            base_url=model_config.base_url or None,
        ),
        instructions=instructions,
        skills=Skills([LocalSkills(str(prompt_assets.skills_root))],
                      raise_on_loader_error=True),
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
