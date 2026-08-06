"""Build the auditable Claude target manifest (multi-runtime module 4, MR-C3).

Produces ``dist/claude-code/harness-manifest.json`` with the FULL field set
of design §13:

- ``harness_release``        short git SHA of the workspace HEAD (same
                             technique as golden_capture)
- ``ir_schema_version``      ``chatbi.harness/v1`` (IR package constant)
- ``kernel_version``         read from ``packages/chatbi_governance/VERSION``
- ``adapter_name``           ``claude_code``
- ``adapter_version``        adapter version constant
- ``runtime_version``        Claude CLI version from the capability probe
- ``event_schema_version``   ``chatbi.event/v1``
- ``evidence_schema_versions``  union of the IR ``evidence.schema_versions``
                             across the nine workflows
- ``supported_matrix``       per-target capability statuses (design §13
                             rule 6: the matrix must ship in the build
                             artifact, not just in docs)

The manifest also embeds the fail-closed judgment (MR-005): the union of IR
``required``-level requirements — across BOTH the ``requirements`` layer
and the ``capabilities`` layer (evaluation OBS-A) — is checked against the
Claude capability manifest with ``CapabilityManifest.missing_required``;
the verdict is recorded so a downstream deployer can refuse without
re-deriving it.

Build artifacts are auditable, never a source of truth (design §12);
``deploy`` stays a separate authorized action (design §8.5).

Applicable rules: MR-005, PORT-001, SEC-003, invariant 5.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from chatbi_harness_ir import SCHEMA_VERSION, load_all
from chatbi_runtime_contract.capabilities import CapabilityManifest

from .adapter import (
    ADAPTER_NAME,
    ADAPTER_VERSION,
    EVENT_SCHEMA_VERSION,
    ClaudeCodeAdapter,
)

MANIFEST_NAME = "harness-manifest.json"
MANIFEST_SCHEMA_VERSION = "chatbi.harness-manifest/v1"


def _harness_release(harness_root: Path) -> str:
    """Short git SHA of HEAD, else 'unknown' (mirrors golden_capture)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(harness_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            release = proc.stdout.decode("utf-8", "replace").strip()
            if release:
                return release
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _kernel_version(harness_root: Path) -> str:
    version_file = harness_root / "packages" / "chatbi_governance" / "VERSION"
    try:
        text = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return text or "unknown"


def _evidence_schema_versions(workflows: list[Any]) -> dict[str, str]:
    """Union of IR ``evidence.schema_versions``; duplicates must agree."""
    merged: dict[str, str] = {}
    for wf in workflows:
        if wf.evidence is None:
            continue
        for name, schema_file in wf.evidence.schema_versions.items():
            previous = merged.get(name)
            if previous is not None and previous != schema_file:
                raise ValueError(
                    f"evidence schema {name!r} referenced as {schema_file!r} "
                    f"and {previous!r} (must agree across workflows)"
                )
            merged[name] = schema_file
    return dict(sorted(merged.items()))


def required_union(workflows: list[Any]) -> dict[str, str]:
    """Union of IR requirement levels across workflows (name -> level).

    Merges BOTH the ``requirements`` layer and the ``capabilities`` layer
    (evaluation OBS-A): 8/9 workflows declare e.g. ``realpath_sandbox:
    required`` only under ``capabilities``, and a fail-closed judgment that
    ignored that layer would silently under-report. Per impl doc §4.1 the
    ``capabilities`` table refines/overrides ``requirements`` entry-wise,
    so ``{**requirements, **capabilities}`` is the precedence order.
    """
    union: dict[str, str] = {}
    for wf in workflows:
        levels: dict[str, Any] = {}
        levels.update(wf.requirements)
        levels.update(wf.capabilities)  # capabilities refine requirements (§4.1)
        for name, level in levels.items():
            union[name] = level.value if hasattr(level, "value") else str(level)
    return dict(sorted(union.items()))


def build_claude_manifest(
    harness_root: Path,
    *,
    workflows: list[Any] | None = None,
    capability_manifest: CapabilityManifest | None = None,
    harness_release: str | None = None,
    runtime_version: str | None = None,
    probe_snapshot: Any | None = None,
    issued_by: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the full design-§13 manifest document (pure-ish, no I/O)."""
    if workflows is None:
        workflows = load_all(harness_root / "workflows")
    adapter = ClaudeCodeAdapter(harness_root)
    if capability_manifest is None:
        capability_manifest = adapter.probe(
            probe_snapshot=probe_snapshot,
            runtime_version=runtime_version or "unknown",
            issued_by=issued_by or ADAPTER_NAME,
            generated_at=generated_at,
        )
    else:
        violations = capability_manifest.validate()
        if violations:
            raise ValueError(
                "capability manifest invalid: " + "; ".join(violations)
            )

    required = required_union(workflows)
    missing = capability_manifest.missing_required(required)

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "harness_release": harness_release or _harness_release(harness_root),
        "ir_schema_version": SCHEMA_VERSION,
        "kernel_version": _kernel_version(harness_root),
        "adapter_name": ADAPTER_NAME,
        "adapter_version": ADAPTER_VERSION,
        "runtime_version": capability_manifest.runtime_version,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "evidence_schema_versions": _evidence_schema_versions(workflows),
        "supported_matrix": {
            "target": "claude-code",
            "runtime": capability_manifest.runtime,
            "issued_by": capability_manifest.issued_by,
            "generated_at": capability_manifest.generated_at,
            "capabilities": {
                name: {"status": entry.status.value, "modes": list(entry.modes)}
                for name, entry in sorted(capability_manifest.capabilities.items())
            },
            "fail_closed": {
                "verdict": "deployable" if not missing else "deployment_refused",
                "missing_required": missing,
            },
        },
    }
    return manifest


def write_manifest(dist_root: Path, manifest: Mapping[str, Any]) -> Path:
    """Write ``dist_root/harness-manifest.json`` (parent dirs created)."""
    path = dist_root / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
