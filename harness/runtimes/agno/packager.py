"""Portable governance-crontab packager for the Agno target (module 6, MR-E2).

Produces the ``dist/agno/crontab/`` artifact: a portable schedule
specification equivalent to the shipped Claude-side template
(``harness/.claude/schedules/chatbi-governance.crontab``) plus a
``crontab-manifest.json`` sidecar that cross-checks the referenced slash
commands against the IR entry commands (the two targets share the IR, so the
schedule means the same thing on both).

Hard guarantees (modification §8 / adjudication eight):

- **Equivalence**: the generated crontab is byte-identical to the template —
  a line-by-line ``diff`` against ``chatbi-governance.crontab`` is empty by
  construction (tested);
- **PORT-001 guard**: every generation passes
  ``chatbi_governance.schedules.validate_crontab_portability`` (no machine
  paths; every command references ``$CHATBI_WORKSPACE`` + ``$CHATBI_INVOKE``);
- **Draft-only, no secrets**: the artifact only GENERATES a portable spec —
  nothing deploys, nothing schedules, and no key/secret can enter the crontab
  (SEC-003) — the Agno target never enables the AgentOS Scheduler
  (``scheduler=False`` everywhere, adjudication eight);
- **Idempotent**: repeated packaging produces identical bytes.

Applicable rules: PORT-001, SEC-003, HOOK-004, MR-005, invariant 5,
adjudication 8.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from chatbi_governance.schedules import validate_crontab_portability
from chatbi_harness_ir.loader import load_all

#: Name of the portable crontab inside the packager output directory.
CRONTAB_FILENAME = "chatbi-governance.crontab"
CRONTAB_MANIFEST_FILENAME = "crontab-manifest.json"
MANIFEST_SCHEMA_VERSION = "chatbi.crontab-manifest/v1"

#: Slash commands referenced by a crontab line (``/chatbi-audit-drift`` ->
#: ``chatbi-audit-drift`` — the IR entry.command vocabulary).
_COMMAND_REF = re.compile(r"/chatbi-[a-z][a-z0-9-]*")


def crontab_referenced_commands(text: str) -> tuple[str, ...]:
    """The ``/chatbi-*`` commands referenced by a crontab text (deduped,
    order-preserving, matched against the IR entry.command vocabulary).
    Comment lines are included (operators document the optional entries in
    comments) — the sidecar cross-check uses the entry commands from the IR."""
    seen: list[str] = []
    for match in _COMMAND_REF.finditer(text):
        command = match.group(0)[1:]  # strip the leading "/"
        if command not in seen:
            seen.append(command)
    return tuple(seen)


def build_portable_crontab(template_text: str) -> str:
    """Validate + return the portable crontab text.

    The template IS the portable spec (the Claude-side file already carries
    the PORT-001 discipline); the packager re-validates it with the Kernel
    guard and returns it VERBATIM, so the equivalence diff is empty by
    construction. Raises ``GateError`` on any portability violation.
    """
    validate_crontab_portability(template_text)
    return template_text


def build_crontab_manifest(
    *,
    workflow_entries: Mapping[str, str],
    referenced_commands: Iterable[str],
    template_name: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Sidecar manifest cross-checking the crontab commands against the IR.

    ``workflow_entries`` maps workflow_id -> IR entry.command. Every command
    referenced by the crontab must be a declared IR entry command; a mismatch
    fails the packaging (the schedule must mean the same thing on both
    targets — shared IR is the drift mitigation, deployment design §18).
    """
    referenced = tuple(referenced_commands)
    declared = set(workflow_entries.values())
    unknown = [command for command in referenced if command not in declared]
    if unknown:
        raise ValueError(
            f"crontab references commands not declared by any IR entry: "
            f"{unknown}; the schedule and the IR disagree (refusing to "
            "package)"
        )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "template": template_name,
        "generated_at": generated_at
        or "deterministic",  # reproducible bytes (idempotent packaging)
        "referenced_commands": list(referenced),
        "workflow_entries": {
            workflow_id: command
            for workflow_id, command in sorted(workflow_entries.items())
        },
        "draft_only": True,
        "scheduler_enabled": False,
        "portability_guard": "validate_crontab_portability (PORT-001)",
        "secrets": "none",
        "note": (
            "Portable schedule specification only — nothing is deployed or "
            "scheduled by the harness; the operator adapts the entries to "
            "their scheduling infrastructure (FR-2 non-goal)."
        ),
    }


def pack_agno(
    *,
    out_dir: str | Path,
    workflows_dir: str | Path,
    template_path: str | Path,
    harness_release: str = "dev",
) -> dict[str, Any]:
    """Package the portable crontab artifact into ``out_dir/crontab/``.

    Returns the crontab-manifest dict (also persisted). Idempotent: repeated
    calls produce byte-identical outputs.
    """
    template_path = Path(template_path)
    text = template_path.read_text(encoding="utf-8")
    portable = build_portable_crontab(text)

    workflows = load_all(Path(workflows_dir))
    workflow_entries = {
        workflow.workflow_id: workflow.entry.command for workflow in workflows
    }
    manifest = build_crontab_manifest(
        workflow_entries=workflow_entries,
        referenced_commands=crontab_referenced_commands(text),
        template_name=template_path.name,
    )

    crontab_dir = Path(out_dir) / "crontab"
    crontab_dir.mkdir(parents=True, exist_ok=True)
    crontab_path = crontab_dir / CRONTAB_FILENAME
    crontab_path.write_text(portable, encoding="utf-8")
    (crontab_dir / CRONTAB_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return manifest
