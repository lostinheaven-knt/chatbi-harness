"""diagnostics shim: kernel diagnostics + Claude target capability probe.

The only non-pure-alias module under ``lib/chatbi_harness``: re-exports the
kernel ``chatbi_governance.diagnostics`` namespace verbatim (module object,
so ``chatbi_harness.diagnostics.run_init_diagnostic is
chatbi_governance.diagnostics.run_init_diagnostic``) and adds
``probe_local_capabilities`` back from the Claude runtime probe
(``runtimes/claude_code/probe.py``). The ``shutil``/``os``/... module
attributes stay in this namespace (the kernel module defines no ``__all__``),
so existing patch targets like
``patch("chatbi_harness.diagnostics.shutil.which")`` keep hitting the shared
module objects.
"""

import sys
from pathlib import Path

_LEGACY_PKG = Path(__file__).resolve().parent  # <root>/.claude/lib/chatbi_harness
# Same parents-depth handling as the package shim (root = parents[2] for both
# dev harness/ and installed Workspace; parents[0] = lib/ itself as the
# last-resort mirror fallback for lib-only copies).
_HARNESS_ROOT = _LEGACY_PKG.parents[2]
for _candidate in (_LEGACY_PKG.parents[2], _LEGACY_PKG.parents[0]):
    if (_candidate / "packages").is_dir():
        _HARNESS_ROOT = _candidate
        break
# Same sys.path entries as the package shim: the harness root (for
# ``runtimes.*``) plus the packages container (for ``chatbi_governance``).
for _entry in (_HARNESS_ROOT, _HARNESS_ROOT / "packages"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import chatbi_governance.diagnostics as _kernel_diag  # noqa: E402
from runtimes.claude_code.probe import probe_local_capabilities  # noqa: E402

# Mirror the kernel module's public namespace exactly (the kernel defines no
# __all__; import * semantics = all non-underscore names, including the
# shutil/os/stat/... module objects that existing tests patch).
globals().update(
    {name: getattr(_kernel_diag, name) for name in dir(_kernel_diag) if not name.startswith("_")}
)
__all__ = [
    name for name in dir(_kernel_diag) if not name.startswith("_")
] + ["probe_local_capabilities"]
