"""Agno target application factory (module 5, MR-D1).

Implements the impl-doc §8.1 skeleton against the real Agno 2.6.22
``AgentOS`` API:

- ChatBI endpoints live under ``/api/chatbi/v1/*`` on the ``base_app``
  (adjudication three); ``on_route_conflict="error"`` makes ANY path+method
  overlap between the ChatBI router and the AgentOS built-in routes a hard
  failure (never a silent override of either side);
- the ``chatbi-analyze`` workflow is read from the IR and registered with
  AgentOS at startup ("runtime reads the IR and registers the Workflow", no
  generated Python — design §8.5);
- ``db`` = the Agno database (product state: sessions/runs); workflows
  inherit it via ``AgentOS(db=...)`` + ``checkpoint="runs"``;
- ``tracing=False`` and ``telemetry=False`` by default (explicitly enabled by
  the deployer with a sanitization filter), ``scheduler=False``
  (adjudication eight: AgentOS Scheduler stays OFF in this module).

Returns the fully built FastAPI application (``AgentOS.get_app()``).

Applicable rules: MR-005, adjudication 3/7/8/10, invariant 2/5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import FastAPI

APP_TITLE = "chatbi-agno"
CHATBI_API_PREFIX = "/api/chatbi/v1"


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


def create_chatbi_app(
    *,
    config_path: str | Path | None = None,
    workflows_dir: str | Path,
    db: Any = None,
    workspace_root: str | Path | None = None,
    harness_release: str = "dev",
    auth_resolver: Callable[..., Any] | None = None,
    agent_runner: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_runner: Any = None,
    native_runner: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    approval_action_type: str | None = None,
    main_agent: Any = None,
    harness_config_path: str | Path | None = None,
    local_config_path: str | Path | None = None,
    scheduler: bool = False,
) -> tuple[FastAPI, dict[str, Any]]:
    """Build the ChatBI-on-AgnoOS application.

    Returns ``(app, components)`` — ``app`` is the fully provisioned FastAPI
    application (AgentOS ``base_app`` + ChatBI router + built-in routes) and
    ``components`` exposes the controller/coordinator/event-log/evidence-index
    for tests and tooling.

    Module 6: ALL NINE IR workflows are registered (analyze + the eight
    generic workflows), the governance Kernel version is checked at startup
    (fail-closed on mismatch), and ``auth_mode == "jwt"`` wires the verified
    JWT boundary inside the router factory.
    """
    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.os import AgentOS

    # Module 6 version separation (design §13 rule 2): the adapter is only
    # certified for the pinned governance Kernel version; an unknown or
    # mismatched Kernel refuses startup (fail-closed, MR-005).
    from .probe import check_kernel_compat  # noqa: PLC0415

    check_kernel_compat(_kernel_version())

    from .router_chatbi import get_chatbi_router

    base = FastAPI(title=APP_TITLE, docs_url=f"{CHATBI_API_PREFIX}/docs")

    router, components = get_chatbi_router(
        config_path=config_path,
        prefix=CHATBI_API_PREFIX,
        workflows_dir=workflows_dir,
        db=db,
        workspace_root=workspace_root,
        harness_release=harness_release,
        auth_resolver=auth_resolver,
        agent_runner=agent_runner,
        reviewer_runner=reviewer_runner,
        native_runner=native_runner,
        approval_action_type=approval_action_type,
        main_agent=main_agent,
        harness_config_path=harness_config_path,
        local_config_path=local_config_path,
    )
    # Direct registration (NOT include_router): FastAPI 0.139 wraps included
    # routers in path-less objects, which would hide the ChatBI routes from
    # AgentOS's route-conflict detection (agno 2.6.22 get_existing_route_paths
    # does not flatten). Registering the APIRoute objects directly keeps the
    # on_route_conflict="error" guarantee real (adjudication three).
    from fastapi.routing import APIRoute as _APIRoute

    for route in router.routes:
        if isinstance(route, _APIRoute):
            base.router.routes.append(route)

    os_app = AgentOS(
        id="chatbi-agno",
        name=APP_TITLE,
        description=(
            "ChatBI governed agent runtime on Agno AgentOS (module 6: all "
            "nine IR workflows registered; AgentOS Scheduler stays OFF, "
            "adjudication eight)."
        ),
        base_app=base,
        on_route_conflict="error",      # adjudication three: conflicts fail
        workflows=list(components["workflows"].values()),
        db=db,
        checkpoint="runs",
        tracing=False,                  # deployer opts in with a sanitizer
        telemetry=False,
        scheduler=scheduler,            # adjudication eight: OFF (module 5/6)
    )
    app = os_app.get_app()
    return app, components
