"""Asset root resolution for schemas/fixtures (replaces parents[N] derivations).

Module 2 of the multi-runtime modification: the Governance Kernel no longer
derives asset paths from ``Path(__file__).parents[N]`` depth arithmetic
(feature-flow §3.1.2); all schema/fixture/rule asset roots resolve through
this module so the directory shape is decoupled from the kernel location.

Resolution precedence (impl doc §3.2):

1. ``CHATBI_HARNESS_ASSETS`` env var (explicit override, e.g. an Agno
   deployment pointing at its asset directory);
2. canonical asset root: ``<root>/schemas`` + ``<root>/fixtures`` at the
   harness root (dev: ``harness/``; install: the Workspace root), where
   ``<root>`` is ``_package_root().parents[1]`` (the kernel is fixed under
   ``packages/`` — the impl-doc draft's ``parents[2]`` is an off-by-one
   erratum);
3. transition fallback: ``<root>/.claude`` when the assets still live only
   under the Claude Code target directory;
4. ``<root>`` itself — missing assets are reported fail-closed by callers.

The ``.claude/{schemas,fixtures}`` copies remain the Claude target install
copies; byte-consistency between the two sides is pinned by the module-4
reconcile tooling.
"""

from __future__ import annotations

import os
from pathlib import Path


def _package_root() -> Path:
    # <root>/packages/chatbi_governance
    return Path(__file__).resolve().parent


def get_assets_root() -> Path:
    """Resolve the asset root (env override -> canonical -> .claude fallback)."""
    override_value = os.environ.get("CHATBI_HARNESS_ASSETS")
    if override_value:
        return Path(override_value)
    harness_root = _package_root().parents[1]
    if (harness_root / "schemas").is_dir():
        return harness_root  # canonical: <root>/schemas + <root>/fixtures
    legacy = harness_root / ".claude"
    if (legacy / "schemas").is_dir():
        return legacy  # transition fallback: assets still only under .claude/
    return harness_root  # missing assets are reported fail-closed by callers


def get_schema_dir() -> Path:
    return get_assets_root() / "schemas"


def get_schema_path(name: str) -> Path:
    """Resolve one schema file under the asset root."""
    return get_schema_dir() / name


def get_fixtures_root() -> Path:
    return get_assets_root() / "fixtures"


def get_rules_dir() -> Path:
    # For future extensions; current rule validation walks workspace files.
    return get_assets_root() / "rules"
