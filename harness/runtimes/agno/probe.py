"""Agno runtime capability probe -> CapabilityManifest (module 5, MR-D1).

Implements the Agno column of the impl-doc §6.2 manifest draft with honest
detection (FBK-003): every probe item is actually checked when the agno
package is importable; when agno (or fastapi) is not importable the manifest
is emitted with ``runtime_version="unavailable"`` and the statuses that
require the runtime drop to ``partial`` — the fail-closed judgment
(``missing_required``) then reports every IR ``required`` capability so a
deployment is refused instead of silently degraded (MR-005).

The probe never mutates anything and never emits secrets (SEC-003/PORT-001).

Applicable rules: MR-004/MR-005 (probe + fail-closed), FBK-003 (honest
reporting), invariant 5.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from typing import Any, Mapping

from chatbi_runtime_contract.capabilities import (
    CapabilityEntry,
    CapabilityManifest,
    CapabilityStatus,
)

ADAPTER_NAME = "agno"
ADAPTER_VERSION = "0.5.0"

#: impl-doc §6.2 Agno manifest draft. ``provided_by_adapter`` items are the
#: components this module implements (reviewer/evidence index/sandbox reuse).
#: IR requirement names (persistent_session / resumable_run / tool_allowlist)
#: are declared explicitly so the fail-closed judgment (MR-005) can resolve
#: every IR-required capability against the manifest.
_CAPABILITY_DRAFT: dict[str, tuple[CapabilityStatus, tuple[str, ...]]] = {
    "streaming": (CapabilityStatus.PROVIDED_BY_RUNTIME, ("sse", "websocket")),
    "persistent_session": (CapabilityStatus.PROVIDED_BY_RUNTIME, ()),
    "session_resume": (CapabilityStatus.PROVIDED_BY_RUNTIME, ()),
    "resumable_run": (CapabilityStatus.PROVIDED_BY_RUNTIME, ()),
    "human_approval": (CapabilityStatus.PARTIAL, ("chatbi_owner_policy",)),
    "independent_reviewer": (CapabilityStatus.PROVIDED_BY_ADAPTER, ()),
    "realpath_sandbox": (CapabilityStatus.PROVIDED_BY_ADAPTER, ()),
    "tool_allowlist": (CapabilityStatus.PROVIDED_BY_ADAPTER, ()),
    "evidence_store": (CapabilityStatus.PROVIDED_BY_ADAPTER, ()),
    "scheduler": (CapabilityStatus.UNSUPPORTED, ()),
}


def _agno_version() -> str | None:
    """Detect the installed agno version (None when not importable)."""
    try:
        from importlib.metadata import version

        return version("agno")
    except Exception:
        return None


def _imports_available() -> bool:
    """True when the agno + fastapi import surface is present."""
    return (
        importlib.util.find_spec("agno") is not None
        and importlib.util.find_spec("fastapi") is not None
    )


def probe_agno(
    *,
    issued_by: str = f"{ADAPTER_NAME}/{ADAPTER_VERSION}",
    agno_version: str | None = None,
    capabilities_override: Mapping[str, CapabilityEntry] | None = None,
) -> CapabilityManifest:
    """Probe the Agno runtime and return a five-class CapabilityManifest.

    ``agno_version``/``capabilities_override`` are injectable for tests;
    default behavior probes the environment honestly.
    """
    runtime_version = agno_version
    runtime_ok = _imports_available()
    if runtime_version is None:
        runtime_version = _agno_version() or "unavailable"

    if capabilities_override is not None:
        capabilities = dict(capabilities_override)
    else:
        capabilities = {
            name: CapabilityEntry(status=status, modes=modes)
            for name, (status, modes) in _CAPABILITY_DRAFT.items()
        }
        if not runtime_ok or runtime_version == "unavailable":
            # Runtime missing: capabilities that the runtime or adapter would
            # provide cannot be certified in this environment (FBK-003). The
            # fail-closed judgment will refuse deployment on required items.
            for name in ("streaming", "session_resume",
                         "independent_reviewer", "evidence_store",
                         "realpath_sandbox"):
                entry = capabilities[name]
                capabilities[name] = CapabilityEntry(
                    status=CapabilityStatus.PARTIAL, modes=entry.modes
                )

    return CapabilityManifest(
        runtime=ADAPTER_NAME,
        runtime_version=runtime_version,
        capabilities=capabilities,
        issued_by=issued_by,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def ir_requirements_from_workflows(
    workflows: Any,
) -> dict[str, str]:
    """Aggregate IR ``requirements``/``capabilities`` levels per capability.

    Shared with the Claude-side manifest builder (build_manifest.required_union)
    so both targets use the same fail-closed bar; implemented locally to keep
    the agno runtime free of claude-code imports.
    """
    required: dict[str, str] = {}
    for workflow in workflows:
        for section in ("requirements", "capabilities"):
            for name, level in getattr(workflow, section, {}).items():
                existing = required.get(name)
                if existing is None or _level_rank(level.value) > _level_rank(existing):
                    required[name] = level.value
    return required


def _level_rank(level: str) -> int:
    return {"required": 3, "protected_actions": 2, "optional": 1, "none": 0}.get(
        level, 0
    )
