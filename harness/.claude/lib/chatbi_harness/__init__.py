"""Compatibility shim: re-exports chatbi_governance under the legacy chatbi_harness name.

Module 2 of the multi-runtime modification: the Governance Kernel moved to
``packages/chatbi_governance`` and this package is now a pure import shim, so
every existing import surface keeps working unchanged — hooks' sys.path
insert of ``parents[1]/"lib"`` (feature-flow §3.1.3), tests, golden capture,
``e2e-state.py``.

Mechanism (impl doc §3.4):

1. resolve the harness root and add ``packages/`` + ``runtimes/`` to
   ``sys.path``. NOTE the depth erratum vs. the impl-doc draft: the dev
   layout needs ``parents[2]`` (``<repo>/harness``), the installed layout
   needs ``parents[3]`` (``<workspace>``) — probe both candidates.
2. import the kernel and register its Claude runtime probe as the default
   capability probe of ``run_init_diagnostic`` (vendor-free kernel: the shim,
   being the Claude Code compat layer, owns that wiring).
3. eagerly alias every kernel submodule (``adapters.*`` included, except
   ``diagnostics`` — a real file shim covers it) under ``chatbi_harness.*``
   so each file is loaded exactly once and class objects are identical under
   both names (no isinstance split).
4. re-export the kernel public surface, restoring ``probe_local_capabilities``
   from the Claude runtime probe module.
"""

import importlib
import sys
from pathlib import Path

_LEGACY_PKG = Path(__file__).resolve().parent  # <root>/.claude/lib/chatbi_harness
# Root candidates, in preference order (parents-depth erratum note: the
# impl-doc draft used parents[3] for both dev and install; the correct depth
# is parents[2] in both layouts — <root>/.claude/lib/chatbi_harness ->
# parents[0]=lib, parents[1]=.claude, parents[2]=<root>):
#   parents[2] == dev harness/ AND installed Workspace root (kernel at
#   <root>/packages);
#   parents[0] == lib/ itself (lib/packages + lib/runtimes mirror symlinks,
#   last-resort for lib-only copies such as test_hooks' install_hook_runtime —
#   the mirror is a symlink here and a real copy in lib-only workspaces).
_HARNESS_ROOT = _LEGACY_PKG.parents[2]
for _candidate in (_LEGACY_PKG.parents[2], _LEGACY_PKG.parents[0]):
    if (_candidate / "packages").is_dir():
        _HARNESS_ROOT = _candidate
        break
# sys.path entries: the harness root itself (so ``import runtimes.*`` resolves
# — a ``harness/runtimes`` entry would look for runtimes/runtimes/ and fail)
# plus the packages container (so ``import chatbi_governance`` resolves).
for _entry in (_HARNESS_ROOT, _HARNESS_ROOT / "packages"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import chatbi_governance as _kernel  # noqa: E402

# Register the Claude Code target probe as the kernel's default capability
# probe (legacy no-injection path). None (runtime probe unavailable) is
# fail-closed: run_init_diagnostic reports the runtime checks unavailable
# instead of probing (HOOK-004).
import chatbi_governance.diagnostics as _kernel_diagnostics  # noqa: E402

try:
    from runtimes.claude_code.probe import (  # noqa: E402
        probe_local_capabilities as _probe_local_capabilities,
    )
except Exception:
    _probe_local_capabilities = None
_kernel_diagnostics.set_default_probe(_probe_local_capabilities)

# Full eager alias for every submodule (adapters.* included): guarantees a
# single load per file, so class objects are identical under both names
# (avoids the isinstance split trap). ``diagnostics`` is excluded — a real
# file shim covers it (re-exports the kernel module plus the probe).
_SUBMODULES = (
    "gates", "config", "paths", "policy", "evidence", "impact", "knowledge",
    "evaluator", "schedules", "build_plan", "bootstrap", "drift",
    "harness_state",
    "adapters",
    "adapters.base", "adapters.codebase_reader", "adapters.fixture",
)
for _name in _SUBMODULES:
    _mod = importlib.import_module(f"chatbi_governance.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _mod
    if "." not in _name:
        # Mirror the attribute the original package gained when its submodule
        # was imported (e.g. ``chatbi_harness.evidence``).
        setattr(sys.modules[__name__], _name, _mod)

# Eagerly import the diagnostics file shim so ``chatbi_harness.diagnostics``
# is reachable as an attribute right after ``import chatbi_harness``.
importlib.import_module(f"{__name__}.diagnostics")  # noqa: E402

globals().update({n: getattr(_kernel, n) for n in _kernel.__all__})
if _probe_local_capabilities is not None:
    globals()["probe_local_capabilities"] = _probe_local_capabilities
    __all__ = [*_kernel.__all__, "probe_local_capabilities"]
else:
    __all__ = list(_kernel.__all__)
