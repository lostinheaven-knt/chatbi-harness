"""Agno runtime adapter for the ChatBI Harness (skill+hooks architecture).

Target-specific components only — every governance judgment lives in
``chatbi_governance`` (invariant 2); this package adds no second business
rules. The agno target is ONE governed native Agent (裁决 1: no workflow
form; 裁决 3: skill+hooks governance). Modules:

- :mod:`runtimes.agno.app` — ``create_chatbi_app``:
  ``AgentOS(agents=[governed_agent])`` built from the IR + prompt manifest
  at startup; NO ``/api/chatbi/v1/*`` second API surface (裁决 2);
- :mod:`runtimes.agno.agent_builder` — ``build_governed_agent``: assembles
  instructions/skills/tools/tool_hooks/guardrails (single agent, 9
  runbooks);
- :mod:`runtimes.agno.governed_tools` — the 19 governance tool functions +
  tool registry + IR condition evaluation;
- :mod:`runtimes.agno.hooks` — six-layer tool_hooks chain + run-level
  guardrails (request/policy/delivery; ADR-002 terminal gate);
- :mod:`runtimes.agno.prompt_loader` — manifest-driven prompt assets
  (sha256 + PORT-001 validation);
- :mod:`runtimes.agno.events` — standard-event emitter + replayable event
  log (cursor, dedup);
- :mod:`runtimes.agno.approvals` — ChatBI ApprovalCoordinator (Kernel
  re-verification; @approval bridge + reverify_before_execute);
- :mod:`runtimes.agno.reviewer` — independent read-only reviewer with
  candidate-SHA binding + review tool wrapper;
- :mod:`runtimes.agno.evidence_index` — ``.chatbi`` ↔ runtime index
  (path + content_sha256, drift detection, rebuild, atomic write);
- :mod:`runtimes.agno.probe` — Agno capability probe → CapabilityManifest;
- :mod:`runtimes.agno.config` — model_ref/provider/key injection point
  (adjudication seven; keys never persist).

This package must stay free of machine paths and secrets (PORT-001/SEC-003,
invariant 5).
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_RUNTIMES_DIR = _THIS_DIR.parent


def _unshadow_installed_agno() -> None:
    """Stop this package from shadowing the INSTALLED ``agno`` package.

    Python resolves the top-level name ``agno`` against every ``sys.path``
    entry. The legacy shim/CLI/tests historically put ``<root>/runtimes`` on
    ``sys.path`` (so ``import runtimes.claude_code…`` resolves) — that entry
    makes ``import agno`` find THIS directory instead of the real agno
    package (installed, e.g. in the deployment venv). Because this
    package's ``__init__`` runs before any submodule imports, removing the
    entry here guarantees that ``from agno.os import AgentOS`` etc. resolve
    to the installed package (the deployment's single source of agno).

    Safe by construction: the entry is removed only when it points at
    exactly this package's parent directory, ``runtimes``/``agno`` stays
    reachable through the already-imported parent package (sys.modules +
    ``__path__``), and when no real agno is installed the honest
    ``ImportError`` is the fail-closed signal (MR-005, FBK-003).
    """
    for entry in list(sys.path):
        try:
            candidate = Path(entry).resolve() / "agno"
        except (OSError, TypeError):
            continue
        if candidate == _THIS_DIR:
            sys.path.remove(entry)


_unshadow_installed_agno()


def ensure_agno_unshadowed() -> None:
    """Re-run the sys.path hygiene before any real-agno import site.

    Test harnesses legitimately re-insert ``<root>/runtimes`` into
    ``sys.path`` after package import (each test module sets up its own
    path), which would re-shadow the installed ``agno`` package. Calling
    this helper immediately before ``from agno... import ...`` keeps the
    installed package authoritative in every process layout.
    """
    _unshadow_installed_agno()
