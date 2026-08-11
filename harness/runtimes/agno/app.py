"""Agno target application factory (skill+hooks architecture, module F).

Creates the ChatBI-on-AgentOS application in its AGENT form (裁决 1/2/3):
NO ``/api/chatbi/v1/*`` second API surface, NO workflow registrations — a
single governed native Agent carries all nine IR workflows (runbook
routing + governance tools + tool_hooks + guardrails):

    1. ``ensure_agno_unshadowed()`` + ``check_kernel_compat`` (MR-005,
       fail-closed version gate, design §13 rule 2);
    2. IR load (all nine workflows, ``chatbi_harness_ir.load_all``) +
       prompt assets (``prompt_loader.load_prompt_assets`` — manifest
       sha256 + PORT-001 validation; failure refuses startup);
    3. deployment config / effective config / model resolution
       (``config.py`` — adjudication 7: keys only from the deployment
       config / env, never persisted);
    4. ``agent_builder.build_governed_agent``: instructions (governance +
       runbook bodies + routing table), 7 skills, 14 governance tools,
       six-layer tool_hooks, 2 pre + 1 post guardrails (ADR-002 terminal
       gate), stub seams (``reviewer_runner``/``native_runner``/``model``)
       injected for conformance;
    5. ``AgentOS(id="chatbi-agno", agents=[governed_agent], ...)`` —
       agent-ui connects via the AgentOS native routes only (``/agents``,
       ``/agents/{id}/runs``, ``/sessions``, ``/health``);
    6. returns ``(os_app.get_app(), components)``.

Signature differences vs the module-5 factory (M7 registration): removed
``main_agent`` (the builder constructs the agent) and
``approval_action_type`` (@approval is IR-``human_approval``-driven, module
A); added ``model_refs`` (adjudication 7 model injection), ``skills_root``
(prompt asset root override) and ``model`` (stub seam for conformance —
default builds from the deployment model config).

Applicable rules: MR-005, ADR-002/003, adjudication 1/3/7/8, invariant 2/5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import FastAPI

from chatbi_governance.config import load_effective_config

from .approvals import ChatBIApprovalCoordinator
from .config import load_deployment_config
from .events import EventLog
from .evidence_index import EvidenceIndex
from .governed_tools import build_tool_specs
from .prompt_loader import load_prompt_assets
from .reviewer import build_reviewer_agent

APP_TITLE = "chatbi-agno"


def _kernel_version() -> str | None:
    """The governance Kernel VERSION (chatbi_governance/VERSION, adjudication
    one: the Kernel version is recorded separately from the adapter)."""
    try:
        from pathlib import Path as _Path

        version_path = _Path(
            _Path(__file__).resolve().parents[1]
        ) / "packages" / "chatbi_governance" / "VERSION"
        if not version_path.is_file():
            import chatbi_governance as _kernel

            version_path = _Path(_kernel.__file__).resolve().parent / "VERSION"
        return version_path.read_text(encoding="utf-8").strip() or None
    except Exception:  # noqa: BLE001 - fail-closed: unknown version refuses
        return None


def _resolve_config(
    *,
    ws: Path,
    harness_config_path: str | Path | None,
    local_config_path: str | Path | None,
) -> Any:
    """Effective governance config (fail-closed when explicitly requested
    but unreadable; None when absent — callers treat None as LOW-2
    fail-closed)."""
    shared_path = harness_config_path
    if shared_path is None:
        candidate = ws / ".claude" / "chatbi-harness.json"
        if candidate.is_file():
            shared_path = candidate
    if shared_path is None or not Path(shared_path).is_file():
        return None
    return load_effective_config(
        Path(shared_path),
        Path(local_config_path) if local_config_path else None,
    )


def create_chatbi_app(
    *,
    config_path: str | Path | None = None,
    workflows_dir: str | Path,
    db: Any = None,
    workspace_root: str | Path | None = None,
    harness_release: str = "dev",
    auth_resolver: Callable[..., Any] | None = None,
    agent_runner: Callable[..., Any] | None = None,     # deprecated seam (M7)
    reviewer_runner: Any = None,                        # stub seam（conformance）
    native_runner: Callable[..., Any] | None = None,    # runtime_native seam
    harness_config_path: str | Path | None = None,
    local_config_path: str | Path | None = None,
    skills_root: str | Path | None = None,              # prompt 资产根（缺省 resolve）
    scheduler: bool = False,
    model: Any = None,                                  # stub seam（conformance）
) -> tuple[FastAPI, dict[str, Any]]:
    """Build the ChatBI-on-AgentOS application (agent form, skill+hooks).

    Returns ``(app, components)`` — ``app`` is the AgentOS FastAPI
    application (native routes only; no ``/api/chatbi/v1/*``) and
    ``components`` exposes the agent/tool specs/tool hooks/guardrails/
    event log/evidence index/approvals/prompt assets for tests and tooling.

    ``auth_resolver`` and ``agent_runner`` are kept for call-compatibility
    with the module-5 factory but are UNUSED in the agent form (the run
    subject comes from the AgentOS run context via the PolicyGuardrail; the
    agent's behavior is driven by the model, scripted in conformance).
    Model resolution (adjudication 7) is carried by the deployment config
    (``config.py`` ``model_refs`` section / env) — the historical
    ``model_refs`` parameter was removed (LOW-3, eval round 1).
    """
    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.os import AgentOS

    # MR-005: certified Kernel version gate (design §13 rule 2).
    from .probe import check_kernel_compat  # noqa: PLC0415

    check_kernel_compat(_kernel_version())

    deployment = load_deployment_config(config_path, env=None)
    if workspace_root is None:
        workspace_root = Path.cwd()
    ws = Path(workspace_root).resolve()
    state_dir = ws / deployment.state_dir_name
    state_dir.mkdir(parents=True, exist_ok=True)

    event_log = EventLog(state_dir)
    evidence_index = EvidenceIndex(ws, state_dir)

    if db is None:
        from agno.db.sqlite import SqliteDb  # noqa: PLC0415

        db = SqliteDb(db_file=str(state_dir / "agno.db"))

    model_config = None
    if model is None:
        model_config = deployment.model_config("default")

    config = _resolve_config(ws=ws, harness_config_path=harness_config_path,
                             local_config_path=local_config_path)

    coordinator = ChatBIApprovalCoordinator(
        workspace_root=ws,
        state_dir=state_dir,
        deployment=deployment,
        evidence_index=evidence_index,
        event_log=event_log,
        harness_release=harness_release,
        config=config,
    )

    # IR (all nine) + prompt assets — fail-closed on any validation failure.
    from chatbi_harness_ir import load_all  # noqa: PLC0415

    workflows = load_all(Path(workflows_dir))
    ir_workflows = {wf.workflow_id: wf for wf in workflows}
    prompt_assets = load_prompt_assets(
        workspace_root=Path(skills_root) if skills_root is not None else ws,
    )

    reviewer_agent = None
    if model_config is not None:
        reviewer_agent = build_reviewer_agent(
            deployment, model_config,
            instructions=prompt_assets.reviewer_instructions,
        )

    from .agent_builder import build_governed_agent  # noqa: PLC0415

    governed_agent = build_governed_agent(
        deployment=deployment,
        model_config=model_config,
        config=config,
        ir_workflows=ir_workflows,
        workspace_root=ws,
        harness_release=harness_release,
        prompt_assets=prompt_assets,
        evidence_index=evidence_index,
        event_log=event_log,
        approvals=coordinator,
        tool_specs=build_tool_specs(ir_workflows),
        reviewer_agent=reviewer_agent,
        reviewer_runner=reviewer_runner,
        native_runner=native_runner,
        model=model,
    )

    os_app = AgentOS(
        id="chatbi-agno",
        name=APP_TITLE,
        description=(
            "ChatBI governed agent runtime on Agno AgentOS (skill+hooks "
            "form: single native agent; AgentOS Scheduler stays OFF, "
            "adjudication eight)."
        ),
        agents=[governed_agent],
        db=db,
        checkpoint="runs",
        tracing=False,                  # deployer opts in with a sanitizer
        telemetry=False,
        scheduler=scheduler,            # adjudication eight: OFF
    )
    app = os_app.get_app()
    components: dict[str, Any] = {
        "agent": governed_agent,
        "tool_specs": build_tool_specs(ir_workflows),
        "tool_hooks": governed_agent.tool_hooks,
        "guardrails": {
            "pre": governed_agent.pre_hooks,
            "post": governed_agent.post_hooks,
        },
        "event_log": event_log,
        "evidence_index": evidence_index,
        "approvals": coordinator,
        "prompt_assets": prompt_assets,
    }
    return app, components
