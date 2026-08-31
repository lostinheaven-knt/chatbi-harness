#!/usr/bin/env python3
"""ChatBI-on-Agno governed service launcher (ships with the product).

Product-tree artifact: build-product.sh ships this file to the product root
and install.sh installs it at the Warehouse Workspace root. It runs the same
governed AgentOS application the product's ``runtimes/agno/app.py`` factory
builds, wired to THIS installed workspace:

- ``sys.path`` = [workspace root, packages/, .claude/lib] — the product
  layout (runtimes/ at the root; the installed ``agno`` package is never
  shadowed by ``runtimes/agno``, module-5 sys.path-hygiene rule);
- IR workflows from ``<ws>/workflows``, prompt assets from ``<ws>/prompts``
  (sha256-pinned) + ``<ws>/.claude/skills`` + ``<ws>/.claude/agents``;
- shared config ``<ws>/.claude/chatbi-harness.json``, local deployment
  config ``<ws>/.claude/chatbi-harness.local.json`` (machine paths only
  here, PORT-001), deployment config ``<ws>/deployment.json``;
- credentials (SEC-003): read from the operator-provided test venv
  ``<agno-main>/.venv/config.json`` into DEEPSEEK_* env vars ONLY — never
  written to deployment.json / evidence / logs;
- service on 127.0.0.1:7778 (product-state port, avoids the dev demo 7777;
  override with CHATBI_AGNO_PORT).

Machine-specific bindings (PORT-001: machine paths only at the deployment
boundary, never in shared product config) resolve via
``runtimes.agno.deployment_bindings`` in this order, fail-closed if both
are absent:

1. env var ``CHATBI_AGNO_MAIN`` = agno installation root (the dir whose
   ``.venv/config.json`` holds the SEC-003 credentials and whose venv python
   runs this launcher);
2. deployment.json field ``agno_main`` (same semantics);
3. neither -> the launcher exits with a FATAL error (no default fallback).

Usage:
    CHATBI_AGNO_MAIN=/path/to/agno-main <agno-venv>/bin/python launch_agno.py

The workspace must already be bootstrapped/configured by the operator;
this launcher does NOT reset or scaffold state (from-zero resets are done
explicitly by the operator per the acceptance manual §4.1 step 0).

Module import (e.g. by acceptance drivers) only resolves bindings and loads
credential env — the application is built under ``__main__`` only.
"""
# ruff: noqa: E402

import os
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent

for entry in (WS, WS / "packages", WS / ".claude" / "lib"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from runtimes.agno.deployment_bindings import (  # noqa: E402
    load_credential_env,
    resolve_agno_main,
)

AGNO_MAIN = resolve_agno_main(WS)

# SEC-003: credentials enter ONLY as environment variables (the config
# loader's _env_or fallback) — never persisted, never printed.
load_credential_env(AGNO_MAIN)

PORT = int(os.environ.get("CHATBI_AGNO_PORT", "7778"))


def build_app():
    from runtimes.agno.app import create_chatbi_app
    from runtimes.agno.config import load_deployment_config
    from runtimes.agno.native import RuntimeNativeRunner

    deployment_path = WS / "deployment.json"
    if not deployment_path.is_file():
        raise SystemExit(
            f"FATAL: deployment config missing: {deployment_path} "
            "(deployment boundary; fail-closed, MR-005)")

    # The reviewer's FileTools is cwd-scoped (base_dir=None): chdir into the
    # workspace so the reviewer's read-only probe resolves (same as the dev
    # launcher; must run before any model call).
    os.chdir(WS)

    deployment = load_deployment_config(deployment_path)
    native = RuntimeNativeRunner(
        deployment=deployment,
        config=None,
        workspace_root=WS,
        harness_release="prod",
    )

    app, components = create_chatbi_app(
        config_path=deployment_path,
        workflows_dir=WS / "workflows",
        workspace_root=WS,
        harness_config_path=WS / ".claude" / "chatbi-harness.json",
        local_config_path=WS / ".claude" / "chatbi-harness.local.json",
        native_runner=native,
        skills_root=WS,
        harness_release="prod",
    )

    # Deployer responsibility (tools.py:43-46): attach the read-only,
    # workspace-scoped FileTools bundle to the governed agent. The allowlist
    # hook maps file_tools -> Read so the bundle passes; save/delete stay
    # disabled (the agent holds NO bare write surface).
    from runtimes.agno.file_scope import build_main_agent_file_tools

    agent = components["agent"]
    agent.tools = list(agent.tools) + [build_main_agent_file_tools(WS)]
    return app, components


if __name__ == "__main__":
    import uvicorn

    app, components = build_app()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
