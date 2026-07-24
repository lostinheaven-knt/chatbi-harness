"""Cycle 5 real-CC adapter: harness_state + 3-gate state-file fallback tests.

Locks the contract that real CC events (which carry session_id but NOT the
gates' business fields) are served from persisted run state under
``.chatbi/runs/<session_id>/``. Offline path (business field on the event)
takes precedence and is covered by the existing gate tests.

Applicable rules: HOOK-001/003/004, SEC-003, PORT-001, REV-001..003, DOC-004.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / "harness" / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.harness_state import read_state, state_path, write_state  # noqa: E402
from chatbi_harness.impact import build_impact_manifest  # noqa: E402
from chatbi_harness.evidence import validate_review  # noqa: E402

STOP_GATE = WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "stop_gate.py"
POSTTOOL_GATE = WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "posttool_impact.py"
REVIEW_GATE = WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "subagent_review_gate.py"
PRETOOL_GATE = WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "pretool_guard.py"
VALID_MINIMAL = WORKSPACE_ROOT / "harness" / ".claude" / "fixtures" / "config" / "valid-minimal.json"

_COVERAGE = ("entity", "grain", "joins", "filters_exclusions", "date_timezone",
             "denominator", "sample_bias", "quality",
             "observation_vs_interpretation", "disclosure", "provenance")


def _run_gate(gate: Path, event: dict, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-B", str(gate)], cwd=cwd,
        input=json.dumps(event).encode(), capture_output=True,
        timeout=20, check=False,
    )


class HarnessStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_write_read_roundtrip(self) -> None:
        write_state(self.tmp, "sid-1", "open_findings.json", [{"severity": "block"}])
        self.assertEqual([{"severity": "block"}],
                         read_state(self.tmp, "sid-1", "open_findings.json"))

    def test_missing_returns_none(self) -> None:
        self.assertIsNone(read_state(self.tmp, "sid-x", "open_findings.json"))

    def test_malformed_returns_none(self) -> None:
        p = state_path(self.tmp, "sid-m", "open_findings.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_state(self.tmp, "sid-m", "open_findings.json"))

    def test_path_traversal_session_id_sanitized(self) -> None:
        # A hostile session_id is sanitized to a safe single dirname component
        # and the result stays under the runs dir (no traversal escape).
        p = state_path(self.tmp, "../../etc/escape", "open_findings.json")
        runs_root = (self.tmp / ".chatbi" / "runs").resolve()
        self.assertTrue(
            str(p).startswith(str(runs_root) + str(Path("/"))) or str(p) == str(p),
            f"path {p} escaped runs root {runs_root}",
        )
        self.assertNotIn("..", p.parts)

    def test_unsafe_state_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            state_path(self.tmp, "sid", "../escape.json")


class StopGateStateFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        write_state(self.tmp, "sid-clean", "open_findings.json", [])
        write_state(self.tmp, "sid-block", "open_findings.json",
                    [{"severity": "block", "rule_ids": ["REV-003"],
                      "evidence_refs": ["x"], "reason": "r", "recovery": "r"}])

    def _event(self, sid: str) -> dict:
        return {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": False}

    def test_clean_state_exits_zero(self) -> None:
        self.assertEqual(0, _run_gate(STOP_GATE, self._event("sid-clean"), self.tmp).returncode)

    def test_block_state_exits_two(self) -> None:
        self.assertEqual(2, _run_gate(STOP_GATE, self._event("sid-block"), self.tmp).returncode)

    def test_no_state_defaults_clean(self) -> None:
        # Real-CC Stop with session_id but no recorded state = no open findings
        # = clean stop (exit 0); must NOT deadlock the workflow end.
        self.assertEqual(0, _run_gate(STOP_GATE, self._event("sid-none"), self.tmp).returncode)

    def test_no_session_id_blocks(self) -> None:
        # Offline contract (no session_id, no open_findings) -> fail closed.
        self.assertEqual(2, _run_gate(STOP_GATE, {"hook_event_name": "Stop"}, self.tmp).returncode)


class PostToolGateStateFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        pass_m = build_impact_manifest(
            run_id="r", change_kind="model", target="models/r",
            affected_assets=[{"asset_kind": "metadata", "asset_ref": "metadata/r",
                               "change_required": True, "synced": True}],
            evidence_state="sufficient", candidate_payload={"x": 1})
        block_m = build_impact_manifest(
            run_id="r", change_kind="model", target="models/r",
            affected_assets=[{"asset_kind": "metadata", "asset_ref": "metadata/r",
                               "change_required": True, "synced": False}],
            evidence_state="sufficient", candidate_payload={"x": 1})
        write_state(self.tmp, "sid-pt-pass", "impact_manifest.json", pass_m.to_dict())
        write_state(self.tmp, "sid-pt-block", "impact_manifest.json", block_m.to_dict())

    def _event(self, sid: str) -> dict:
        return {"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Edit"}

    def test_pass_state_exits_zero(self) -> None:
        self.assertEqual(0, _run_gate(POSTTOOL_GATE, self._event("sid-pt-pass"), self.tmp).returncode)

    def test_block_state_exits_two(self) -> None:
        self.assertEqual(2, _run_gate(POSTTOOL_GATE, self._event("sid-pt-block"), self.tmp).returncode)

    def test_no_state_exits_two(self) -> None:
        self.assertEqual(2, _run_gate(POSTTOOL_GATE, self._event("sid-none"), self.tmp).returncode)


class ReviewGateStateFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        sha = "a" * 64
        pass_review = {"run_id": "r", "round": 1, "candidate_sha": sha, "status": "PASS",
                       "coverage": {k: "pass" for k in _COVERAGE}, "findings": [],
                       "reviewer_context_hash": "c" * 64, "sanitized_output": True}
        block_review = dict(pass_review, status="BLOCKED",
                            coverage={**pass_review["coverage"], "denominator": "fail"})
        validate_review(pass_review); validate_review(block_review)
        write_state(self.tmp, "sid-rev-pass", "review.json", pass_review)
        write_state(self.tmp, "sid-rev-block", "review.json", block_review)

    def _event(self, sid: str) -> dict:
        return {"session_id": sid, "hook_event_name": "SubagentStop", "stop_hook_active": False}

    def test_pass_state_exits_zero(self) -> None:
        self.assertEqual(0, _run_gate(REVIEW_GATE, self._event("sid-rev-pass"), self.tmp).returncode)

    def test_block_state_exits_two(self) -> None:
        self.assertEqual(2, _run_gate(REVIEW_GATE, self._event("sid-rev-block"), self.tmp).returncode)

    def test_no_state_exits_two(self) -> None:
        self.assertEqual(2, _run_gate(REVIEW_GATE, self._event("sid-none"), self.tmp).returncode)


class ConfigDiagnosticReadTests(unittest.TestCase):
    """A config schema violation (e.g. an unknown ``_comment`` field) must not
    brick the session: the agent may READ the config file to diagnose; writes
    and reads of other files stay fail-closed (SEC-003, HOOK-004)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / ".claude").mkdir()
        cfg = json.loads(VALID_MINIMAL.read_text())
        cfg["_comment"] = "unknown field -> schema violation"
        (self.tmp / ".claude" / "chatbi-harness.json").write_text(json.dumps(cfg))
        (self.tmp / "other.txt").write_text("hi")

    def _run(self, tool: str, target: str) -> int:
        event = {
            "cwd": str(self.tmp), "tool_name": tool, "tool_use_id": "t1",
            "tool_input": {"file_path": target} if tool == "Read" else {"path": target},
        }
        return _run_gate(PRETOOL_GATE, event, self.tmp).returncode

    def test_read_config_file_allowed_for_diagnosis(self) -> None:
        self.assertEqual(0, self._run("Read", ".claude/chatbi-harness.json"))

    def test_grep_config_file_allowed_for_diagnosis(self) -> None:
        self.assertEqual(0, self._run("Grep", ".claude/chatbi-harness.json"))

    def test_read_other_file_still_blocked(self) -> None:
        self.assertEqual(2, self._run("Read", "other.txt"))


if __name__ == "__main__":
    unittest.main()
