"""Build <-> target-source reconcile for the Claude Code target (module 4).

``build --target claude-code`` copies the audited target source
(``.claude/**``) into ``dist/claude-code/`` and then proves the copy is
interpretable by the manifest/IR without governance-semantics loss
(modification §6, MR-005 fail-closed):

1. per-file SHA-256 comparison of the build artifact's ``.claude/`` tree
   against the live source tree — differences are only tolerated when the
   relative path is an EXPLICITLY REGISTERED target extension
   (:data:`TARGET_EXTENSIONS`), anything else fails the build;
2. per-hook subprocess contract reconcile — every hook script is executed
   with the documented event payload (the same contract cases exercised by
   test_hooks.py / test_review_gate.py / golden_capture.py) and must
   reproduce its exit-code semantics, proving no hook contract was lost in
   the build.

Registered target extensions (impl §7, adjudication F5): the Claude target
may legitimately carry local/operator state that the audited artifact does
not — ``settings.json`` (SessionStart-only hook registration + hot-reload
warning comment; the other five hooks are registered manually per
``docs/harness/e2e-checklist.md``) and the governance crontab (operator
annotations). e2e-checklist.md / e2e-state.py live OUTSIDE the ``.claude``
reconcile domain and are recorded for documentation only.

This module never rewrites the target tree; it is the fail-closed guard.
Applicable rules: MR-005, HOOK-001, HOOK-004, PORT-001, invariant 5.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

#: Explicitly registered Claude-target extensions: relative paths under
#: ``.claude/`` that MAY legitimately differ between the audited build
#: artifact and the live source tree. Each entry documents the reason;
#: anything not listed here fails the build (MR-005).
TARGET_EXTENSIONS: dict[str, str] = {
    ".claude/settings.json": (
        "SessionStart-only hook registration + hot-reload warning comment "
        "(FF§3.4, adjudication F5): the live tree may register the five "
        "e2e-checklist hooks, the audited artifact stays SessionStart-only."
    ),
    ".claude/schedules/chatbi-governance.crontab": (
        "Operator annotations on the governance crontab (FF§3.8.5) are "
        "workspace-local; the artifact ships the portable template."
    ),
}

#: Registrations OUTSIDE the ``.claude`` reconcile domain (documented for
#: completeness, impl §7): these files can never appear in the diff domain.
OUT_OF_DOMAIN_EXTENSIONS: dict[str, str] = {
    "docs/harness/e2e-checklist.md": "manual hook registration checklist (F5)",
    "e2e-state.py": "dev/workspace state script (FF§3.7)",
}


def file_sha256(path: Path) -> str:
    """Content SHA-256 of one file (deterministic, no path salt)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk(root: Path) -> dict[str, Path]:
    """Relative path -> Path for every regular file under ``root``.

    Directory symlinks ARE followed (the dev tree ships ``.claude/lib/
    {packages,runtimes}`` as relative symlinks to the canonical dirs, module
    2) so the walk sees the same file set on both sides of the reconcile;
    a visited set on resolved realpaths guards against cycles (fail-closed:
    a cyclic tree simply yields what it can). ``__pycache__`` trees and
    editor droppings are excluded — they are derived artifacts, not target
    source (build-product.sh excludes the same).
    """
    found: dict[str, Path] = {}
    visited: set[Path] = set()

    def _descend(base: Path) -> None:
        try:
            real = base.resolve(strict=True)
        except OSError:
            return
        if real in visited:
            return
        visited.add(real)
        try:
            entries = sorted(os.scandir(base), key=lambda entry: entry.name)
        except OSError:
            return
        for entry in entries:
            if entry.name in ("__pycache__", ".DS_Store"):
                continue
            child = Path(entry.path)
            try:
                is_dir = entry.is_dir(follow_symlinks=True)
                is_file = entry.is_file(follow_symlinks=True)
            except OSError:
                continue
            if is_dir:
                _descend(child)
            elif is_file:
                found[str(child.relative_to(root))] = child

    _descend(root)
    return found


def diff_target_sources(
    artifact_claude: Path,
    source_claude: Path,
) -> list[str]:
    """Per-file SHA-256 diff of two ``.claude`` trees.

    Returns one string per difference (added / missing / content-changed).
    Empty list = the artifact is byte-identical to the source.
    """
    artifact_files = _walk(artifact_claude)
    source_files = _walk(source_claude)
    diffs: list[str] = []
    for rel in sorted(set(artifact_files) | set(source_files)):
        artifact_path = artifact_files.get(rel)
        source_path = source_files.get(rel)
        if artifact_path is None:
            diffs.append(f"ADDED-in-artifact: .claude/{rel}")
        elif source_path is None:
            diffs.append(f"MISSING-from-artifact: .claude/{rel}")
        else:
            artifact_sha = file_sha256(artifact_path)
            source_sha = file_sha256(source_path)
            if artifact_sha != source_sha:
                diffs.append(
                    f"CONTENT-DIFF: .claude/{rel} "
                    f"(artifact {artifact_sha[:12]} != source {source_sha[:12]})"
                )
    return diffs


def reconcile_build(
    artifact_claude: Path,
    source_claude: Path,
    *,
    whitelist: Mapping[str, str] | None = None,
) -> list[str]:
    """Fail-closed reconcile: violations = diffs outside the registered
    extension whitelist (MR-005). [] = build may pass."""
    extensions = dict(TARGET_EXTENSIONS if whitelist is None else whitelist)
    violations: list[str] = []
    for diff in diff_target_sources(artifact_claude, source_claude):
        # Only CONTENT diffs may hit a registered extension (operator state
        # varies per workspace); structural ADDED/MISSING differences are
        # never tolerated — a broken artifact must not build (MR-005).
        tokens = diff.split()
        if diff.startswith("CONTENT-DIFF") and len(tokens) >= 2:
            rel = tokens[1]
            if rel in extensions:
                continue
        violations.append(
            f"unregistered target-source difference (MR-005): {diff}"
        )
    return violations


# --------------------------------------------------------------------------
# Per-hook subprocess contract reconcile
# --------------------------------------------------------------------------

HOOKS_SUBDIR = ".claude/hooks"

#: Kernel symbol used to build contract payloads (impact manifest).
def _impact_manifest_payload(cwd: Path, *, protected: bool = False) -> dict[str, Any]:
    from chatbi_governance.impact import build_impact_manifest

    manifest = build_impact_manifest(
        run_id="run-reconcile-contract-001",
        change_kind="model",
        target="models/reconcile_contract",
        affected_assets=[
            {
                "asset_kind": "metadata",
                "asset_ref": "metadata/reconcile_contract",
                "change_required": True,
                "synced": True,
            }
        ],
        evidence_state="sufficient",
        protected_action=protected,
        candidate_payload={"change": "reconcile contract"},
    )
    return manifest.to_dict()


def _posttool_payload(cwd: Path, *, stale: bool) -> dict[str, Any]:
    """PostToolUse event: valid impact manifest + matching (or stale) sha."""
    manifest = _impact_manifest_payload(cwd)
    return {
        "impact_manifest": manifest,
        "candidate_sha": "f" * 64 if stale else manifest["candidate_sha"],
    }


def _passing_review(candidate_sha: str) -> dict[str, Any]:
    """A review.schema.json-conformant PASS verdict (synthetic reviewer,
    same shape as golden_capture._synthetic_review — coverage keys mirror
    the review schema's required set)."""
    return {
        "run_id": "run-reconcile-contract-001",
        "round": 1,
        "candidate_sha": candidate_sha,
        "status": "PASS",
        "coverage": {
            "entity": "pass",
            "grain": "pass",
            "joins": "pass",
            "filters_exclusions": "pass",
            "date_timezone": "pass",
            "denominator": "pass",
            "sample_bias": "pass",
            "quality": "pass",
            "observation_vs_interpretation": "pass",
            "disclosure": "pass",
            "provenance": "pass",
        },
        "findings": [],
        "reviewer_context_hash": "d" * 64,
        "sanitized_output": True,
    }


def _blocking_finding() -> list[dict[str, Any]]:
    """One open blocking finding (review.schema.json item shape)."""
    return [
        {
            "severity": "blocking",
            "rule_ids": ["REV-001"],
            "evidence_refs": ["evidence:reconcile:contract"],
            "reason": "contract fixture: unresolved blocking finding",
            "recovery": "resolve and re-review before stopping for delivery",
        }
    ]


def _session_start_payload(cwd: Path) -> dict[str, Any]:
    """SessionStart payload (test_hooks.py session_start_event shape).

    The transcript path is an absolute canary fixture OUTSIDE the macOS
    ``/private`` real-path prefix so the product canary sweep (which now
    scans ``/private/(tmp|var|etc)``, OBS-C) stays hit-free on the shipped
    reconcile module.
    """
    return {
        "session_id": "session-reconcile-contract-001",
        "transcript_path": "/tmp/transcript-secret-canary.jsonl",
        "cwd": str(cwd.resolve()),
        "hook_event_name": "SessionStart",
        "source": "startup",
        "model": "claude-fixture-model",
    }


def _pretool_read_payload(cwd: Path, file_path: str) -> dict[str, Any]:
    return {
        "cwd": str(cwd.resolve()),
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "tool_use_id": "tool-use-reconcile-001",
        "hook_event_name": "PreToolUse",
        "session_id": "session-reconcile-contract-001",
    }


#: (hook file, payload factory, expected exit, description)
HookContract = tuple[
    str,
    Callable[[Path], dict[str, Any] | bytes],
    int,
    str,
]

HOOK_CONTRACTS: tuple[HookContract, ...] = (
    (
        "session_diagnose.py",
        _session_start_payload,
        0,
        "SessionStart diagnostic runs and exits 0 (diagnostics may report "
        "unavailable, never blocks the session start itself)",
    ),
    (
        "subagent_review_gate.py",
        lambda cwd: {
            "review": _passing_review("a" * 64),
            "candidate_sha": "a" * 64,
        },
        0,
        "review PASS + exact candidate SHA match allows delivery (REV-001)",
    ),
    (
        "subagent_review_gate.py",
        lambda cwd: {
            "review": _passing_review("a" * 64),
            "candidate_sha": "b" * 64,
        },
        2,
        "review PASS with STALE candidate SHA blocks delivery (REV-001)",
    ),
    (
        "stop_gate.py",
        lambda cwd: {"open_findings": []},
        0,
        "no open blocking finding allows the stop (REV-003)",
    ),
    (
        "stop_gate.py",
        lambda cwd: {"open_findings": _blocking_finding()},
        2,
        "open blocking finding forces exit 2 with recovery (REV-003)",
    ),
    (
        "posttool_impact.py",
        lambda cwd: _posttool_payload(cwd, stale=False),
        0,
        "valid impact manifest + matching candidate SHA allows (DOC-004)",
    ),
    (
        "posttool_impact.py",
        lambda cwd: _posttool_payload(cwd, stale=True),
        2,
        "stale candidate SHA blocks the impact record (DOC-004, HOOK-004)",
    ),
    (
        "pretool_guard.py",
        lambda cwd: _pretool_read_payload(cwd, "README.md"),
        0,
        "Read inside the workspace is allowed (SCOPE-001)",
    ),
    (
        "pretool_guard.py",
        lambda cwd: _pretool_read_payload(cwd, "/tmp/outside.txt"),
        2,
        "Read outside the workspace is blocked (SCOPE-001)",
    ),
    (
        "config_change_gate.py",
        lambda cwd: {"source": "managed", "file_path": "settings.json"},
        0,
        "managed ConfigChange is not assumed blockable (design §11.1)",
    ),
)


def _run_hook(
    hook_path: Path,
    payload: dict[str, Any] | bytes,
    *,
    cwd: Path,
    timeout: int = 20,
) -> subprocess.CompletedProcess[bytes]:
    stdin = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return subprocess.run(
        [sys.executable, "-B", str(hook_path)],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def check_hook_contracts(
    hooks_dir: Path,
    *,
    cwd: Path | None = None,
    run_hook: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> list[str]:
    """Run every hook contract case as a subprocess; [] = all contracts hold.

    ``cwd`` defaults to the harness root (the tree being built): it carries
    the domain contract, shared config and the shim-backed lib the hooks
    resolve via ``Path(__file__).parents[1]/"lib"`` (same technique as
    golden_capture's gate runner).
    """
    cwd = cwd or hooks_dir.parents[1]
    run = run_hook or _run_hook
    violations: list[str] = []
    for index, (filename, factory, expected_exit, description) in enumerate(
        HOOK_CONTRACTS
    ):
        hook_path = hooks_dir / filename
        if not hook_path.is_file():
            violations.append(
                f"hook contract #{index}: {filename} missing from {hooks_dir}"
            )
            continue
        payload = factory(cwd)
        try:
            proc = run(hook_path, payload, cwd=cwd)
        except subprocess.TimeoutExpired:
            violations.append(
                f"hook contract #{index}: {filename} timed out "
                f"({description})"
            )
            continue
        except OSError as error:
            violations.append(
                f"hook contract #{index}: {filename} could not run: {error}"
            )
            continue
        if proc.returncode != expected_exit:
            stderr = proc.stderr.decode("utf-8", "replace")[:500]
            violations.append(
                f"hook contract #{index}: {filename} exit {proc.returncode} "
                f"!= expected {expected_exit} ({description}); stderr={stderr!r}"
            )
    return violations
