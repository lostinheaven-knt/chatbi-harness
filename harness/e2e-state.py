#!/usr/bin/env python3
"""E2E state-fixture helper for the live Claude Code E2E (Cycle 5 Task 06).

Writes schema-conformant run-state fixtures to ``.chatbi/runs/current/`` so the
deterministic gates (PostToolUse / SubagentStop / Stop) can read them on a real
CC event WITHOUT you having to discover the CC session_id (the gates fall back
to ``current`` when the session-keyed state is absent).

Run from the E2E workspace root (e.g. /tmp/chatbi-e2e) in a SEPARATE terminal
from the claude session. Usage:

    python3 e2e-state.py impact-pass     # clean manifest -> PostToolUse exit 0
    python3 e2e-state.py impact-block    # unsynced manifest -> PostToolUse exit 2
    python3 e2e-state.py review-pass     # PASS verdict -> SubagentStop exit 0
    python3 e2e-state.py review-block    # BLOCKED verdict -> SubagentStop exit 2
    python3 e2e-state.py findings-block  # open block finding -> Stop exit 2
    python3 e2e-state.py findings-clean  # empty findings -> Stop exit 0
    python3 e2e-state.py clear           # remove .chatbi/runs/current/

Then in the claude session trigger the event (see docs/harness/e2e-checklist.md):
  PostToolUse: ask the model to run a benign Bash like `ls`.
  SubagentStop: ask the model to invoke the adversarial-reviewer subagent.
  Stop: end the turn / `/exit`.

The fixtures are synthetic (no org facts/secrets/paths). Validated against the
governed schemas before writing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE / ".claude" / "lib"))

from chatbi_harness.harness_state import write_state  # noqa: E402
from chatbi_harness.impact import build_impact_manifest  # noqa: E402
from chatbi_harness.evidence import validate_review  # noqa: E402

_COVERAGE = ("entity", "grain", "joins", "filters_exclusions", "date_timezone",
             "denominator", "sample_bias", "quality",
             "observation_vs_interpretation", "disclosure", "provenance")
SHA = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _impact(synced: bool, evidence_state: str = "sufficient") -> dict:
    m = build_impact_manifest(
        run_id="e2e-run", change_kind="model", target="models/example_revenue",
        affected_assets=[{"asset_kind": "metadata", "asset_ref": "metadata/example",
                          "change_required": True, "synced": synced}],
        evidence_state=evidence_state, candidate_payload={"change": "e2e fixture"},
    )
    return m.to_dict()


def _review(status: str, fail_key: str | None = None) -> dict:
    coverage = {k: "pass" for k in _COVERAGE}
    if fail_key:
        coverage[fail_key] = "fail"
    rev = {
        "run_id": "e2e-run", "round": 1, "candidate_sha": SHA, "status": status,
        "coverage": coverage, "findings": [], "reviewer_context_hash": "c" * 64,
        "sanitized_output": True,
    }
    validate_review(rev)
    return rev


def _findings(block: bool) -> list:
    if not block:
        return []
    return [{"severity": "block", "rule_ids": ["REV-003"],
             "evidence_refs": ["evidence:e2e"], "reason": "e2e open block finding",
             "recovery": "resolve the e2e finding"}]


_MODES = {
    "impact-pass": ("impact_manifest.json", lambda: _impact(True)),
    "impact-block": ("impact_manifest.json", lambda: _impact(False)),
    "review-pass": ("review.json", lambda: _review("PASS")),
    "review-block": ("review.json", lambda: _review("BLOCKED", "denominator")),
    "findings-block": ("open_findings.json", lambda: _findings(True)),
    "findings-clean": ("open_findings.json", lambda: _findings(False)),
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in _MODES and sys.argv[1] != "clear":
        print(__doc__)
        return 2
    if sys.argv[1] == "clear":
        import shutil
        cur = WORKSPACE / ".chatbi" / "runs" / "current"
        if cur.exists():
            shutil.rmtree(cur)
        print(f"cleared {cur}")
        return 0
    name, factory = _MODES[sys.argv[1]]
    data = factory()
    path = write_state(WORKSPACE, "current", name, data)
    print(f"wrote {name} -> {path}")
    print(f"  content: {json.dumps(data)[:120]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
