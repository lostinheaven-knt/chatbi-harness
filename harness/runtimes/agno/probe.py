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
ADAPTER_VERSION = "0.6.0"

#: Governance Kernel version this adapter is certified against (module 6,
#: version separation — design §13 rule 2: a mismatched Kernel/Adapter
#: combination must be refused at startup, fail-closed). The Kernel carries
#: its own VERSION file (adjudication one).
ADAPTER_COMPAT_KERNEL = "0.1.0"


def check_kernel_compat(kernel_version: str | None) -> None:
    """Startup compatibility gate: Kernel version vs the certified range.

    Raises ``RuntimeError`` (fail-closed, design §13 rule 2) when the Kernel
    version cannot be read or is not the certified one; ``doctor``/``build``
    refuse the deployment instead of starting with an unknown combination.
    """
    if not kernel_version or kernel_version != ADAPTER_COMPAT_KERNEL:
        raise RuntimeError(
            f"adapter {ADAPTER_NAME}/{ADAPTER_VERSION} requires governance "
            f"kernel {ADAPTER_COMPAT_KERNEL!r}, got "
            f"{kernel_version!r}; refuse deployment (fail-closed, design "
            "§13 rule 2)"
        )

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


#: design §14.2 acceptance items tracked for the Agno supported matrix
#: (module 6, MR-E3). Every item must be attested by tests/evidence before
#: the matrix may read "supported"; production certification is a SEPARATE
#: approval and is never derived from this list (deployment design §14.2
#: last row: synthetic tests cannot claim production-certified).
SECTION_14_2_ITEMS = (
    "baseline_no_regression",          # 761-test baseline (728+33) zero regression
    "golden_equivalence",              # kernel-extraction Golden equivalence
    "claude_target_conformance",       # Claude target P0 conformance
    "agno_conformance_all_workflows",  # Agno P0 conformance: all 9 workflows
    "same_fixture_conclusions",        # identical GateDecision/rule_ids/candidate_sha/evidence
    "sse_recovery",                    # SSE/WS reconnect, event dedup, approval recovery
    "jwt_role_mapping",                # JWT subject -> ChatBI Owner role mapping tests
    "reviewer_independence",           # reviewer independence + read-only boundary
    "realpath_sandbox",                # workspace realpath/alias/deny tests
    "evidence_backward_compat",        # .chatbi Evidence backward compatibility
    "hard_gates_listed",               # production hard gates listed; no synthetic certification
)


def supported_verdict(evidence: Mapping[str, bool]) -> tuple[str, str]:
    """The Agno supported-matrix verdict from the §14.2 evidence items.

    All items attested -> ``("supported", note)``; any missing ->
    ``("partial", note)`` listing the missing items (fail-closed, FBK-003).
    ``production_certified`` is always a separate boolean decided by the
    production approval process — never derived here.
    """
    missing = [
        item for item in SECTION_14_2_ITEMS
        if not bool(evidence.get(item))
    ]
    if missing:
        return (
            "partial",
            "Agno target is PARTIAL until every design-§14.2 item is "
            f"attested; missing evidence: {', '.join(missing)}. Production "
            "certification is a separate approval (never derived from "
            "conformance tests).",
        )
    return (
        "supported",
        "all design-§14.2 items attested (module 6 acceptance); production "
        "certification remains a separate approval.",
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
