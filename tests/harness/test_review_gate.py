"""Offline contract tests for the SubagentStop review gate and Stop gate.

These tests verify the deterministic gate contracts (HOOK-001) for
``subagent_review_gate.py`` (SubagentStop) and ``stop_gate.py`` (tracked-workflow
Stop), reusing the Task 01 ``evidence``/``gates`` primitives and the Task 02
reviewer contract (``review.schema.json``).

Applicable rules: REV-001, REV-002, REV-003, HOOK-001, HOOK-003, HOOK-004,
HOOK-005, SEC-003, PORT-001.

The tests invoke the real hook scripts as subprocesses (the documented Hook
invocation seam) feeding a JSON event on stdin, mirroring
``tests/harness/test_hooks.py``. They assert exit codes (0 allow / 2 block) and
that the blocking JSON on stderr carries rule_ids + sanitized evidence +
recovery. A live end-to-end run of a real Claude reviewer/SubagentStop process
is a Cycle 5 exit gate and is NOT claimed here (HOOK-003, FBK-003).
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
REVIEW_GATE = WORKSPACE_ROOT / ".claude" / "hooks" / "subagent_review_gate.py"
STOP_GATE = WORKSPACE_ROOT / ".claude" / "hooks" / "stop_gate.py"

# Valid 64-hex SHA-256 values (distinct, so a mismatch is detectable).
SHA_CURRENT = "a" * 64
SHA_STALE = "b" * 64
REVIEWER_CONTEXT_HASH = "c" * 64

# Canary tokens that must never appear in any gate stdout/stderr (SEC-003,
# PORT-001). They are fed INTO the event; the gate must sanitize/ignore them.
# The path canary uses a non-real absolute path (mirrors test_hooks.py's
# /private/tmp/...-canary convention) so it does not reuse a real home prefix.
CANARY_SECRET = "sk-secret-canary"
CANARY_NAMED = "api_key=named-secret-canary"
CANARY_PATH = "/home/canary/secret-canary-path"
CANARY_PII = "canary-leak@example.com"

_COVERAGE_KEYS = (
    "entity",
    "grain",
    "joins",
    "filters_exclusions",
    "date_timezone",
    "denominator",
    "sample_bias",
    "quality",
    "observation_vs_interpretation",
    "disclosure",
    "provenance",
)


def _all_pass_coverage() -> dict[str, str]:
    return {key: "pass" for key in _COVERAGE_KEYS}


def _review_verdict(
    *,
    status: str = "PASS",
    candidate_sha: str = SHA_CURRENT,
    round_: int = 1,
    coverage: dict[str, str] | None = None,
    findings: list[dict[str, object]] | None = None,
    sanitized_output: bool = True,
    run_id: str = "run-001",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "round": round_,
        "candidate_sha": candidate_sha,
        "status": status,
        "coverage": coverage if coverage is not None else _all_pass_coverage(),
        "findings": findings if findings is not None else [],
        "reviewer_context_hash": REVIEWER_CONTEXT_HASH,
        "sanitized_output": sanitized_output,
    }


def run_gate(
    hook_path: Path,
    payload: dict[str, object] | bytes,
) -> subprocess.CompletedProcess[bytes]:
    stdin = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return subprocess.run(
        [sys.executable, "-B", str(hook_path)],
        cwd=WORKSPACE_ROOT,
        input=stdin,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _assert_clean(self: unittest.TestCase, process: subprocess.CompletedProcess[bytes]) -> None:
    combined = process.stdout.decode("utf-8", "replace") + process.stderr.decode(
        "utf-8", "replace"
    )
    self.assertNotIn(CANARY_SECRET, combined)
    self.assertNotIn(CANARY_NAMED, combined)
    self.assertNotIn(CANARY_PATH, combined)
    self.assertNotIn(CANARY_PII, combined)
    self.assertNotIn(str(WORKSPACE_ROOT), combined)


class ReviewGateTests(unittest.TestCase):
    def test_all_eleven_coverage_pass_with_matching_sha_exits_zero(self) -> None:
        event = {
            "review": _review_verdict(candidate_sha=SHA_CURRENT),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(0, process.returncode, process.stderr.decode())
        self.assertEqual(b"", process.stderr)

    def test_one_coverage_fail_blocks_with_rev002(self) -> None:
        coverage = _all_pass_coverage()
        coverage["denominator"] = "fail"
        event = {
            "review": _review_verdict(coverage=coverage),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        self.assertEqual(b"", process.stdout)
        error = json.loads(process.stderr.decode())
        self.assertEqual("block", error["status"])
        self.assertIn("REV-002", error["rule_ids"])
        self.assertTrue(error["evidence_refs"])
        self.assertTrue(error["recovery"])

    def test_one_coverage_missing_key_blocks(self) -> None:
        coverage = _all_pass_coverage()
        del coverage["provenance"]
        event = {
            "review": _review_verdict(coverage=coverage),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)

    def test_block_finding_blocks_with_rev003(self) -> None:
        findings = [
            {
                "severity": "block",
                "rule_ids": ["RAW-003"],
                "evidence_refs": ["candidate:invented-table"],
                "reason": "Candidate invents a table",
                "recovery": "Use a governed table and re-review",
            }
        ]
        event = {
            "review": _review_verdict(findings=findings),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertEqual("block", error["status"])
        self.assertIn("REV-003", error["rule_ids"])

    def test_stale_sha_invalidates_old_pass_and_forces_new_round(self) -> None:
        # The review PASS was for SHA_STALE, but the current candidate is
        # SHA_CURRENT. The prior PASS is invalid; the gate forces a new round.
        event = {
            "review": _review_verdict(candidate_sha=SHA_STALE),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertEqual("block", error["status"])
        self.assertIn("REV-001", error["rule_ids"])
        self.assertTrue(error["recovery"])

    def test_missing_review_blocks_fail_closed(self) -> None:
        event = {"candidate_sha": SHA_CURRENT}
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        self.assertEqual(b"", process.stdout)
        error = json.loads(process.stderr.decode())
        self.assertEqual("block", error["status"])

    def test_missing_candidate_sha_blocks_fail_closed(self) -> None:
        event = {"review": _review_verdict()}
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)

    def test_malformed_review_blocks_fail_closed(self) -> None:
        # Present but missing required schema fields -> schema validation fails.
        event = {"review": {"status": "PASS"}, "candidate_sha": SHA_CURRENT}
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertIn("HOOK-004", error["rule_ids"])

    def test_round_limit_exceeded_stops(self) -> None:
        event = {
            "review": _review_verdict(round_=99),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertIn("REV-003", error["rule_ids"])

    def test_recursion_guard_stops(self) -> None:
        event = {
            "review": _review_verdict(),
            "candidate_sha": SHA_CURRENT,
            "stop_hook_active": True,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertIn("HOOK-001", error["rule_ids"])

    def test_blocked_status_blocks(self) -> None:
        event = {
            "review": _review_verdict(status="BLOCKED"),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertIn("REV-001", error["rule_ids"])

    def test_error_status_blocks(self) -> None:
        event = {
            "review": _review_verdict(status="ERROR"),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)

    def test_sanitized_output_false_blocks(self) -> None:
        event = {
            "review": _review_verdict(sanitized_output=False),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertIn("SEC-003", error["rule_ids"])

    def test_pass_with_justified_not_applicable_coverage_exits_zero(self) -> None:
        coverage = _all_pass_coverage()
        coverage["sample_bias"] = "not_applicable"
        event = {
            "review": _review_verdict(coverage=coverage),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(0, process.returncode, process.stderr.decode())

    def test_unknown_event_fields_tolerated(self) -> None:
        event = {
            "review": _review_verdict(),
            "candidate_sha": SHA_CURRENT,
            "session_id": "session-fixture-001",
            "transcript_path": "/private/tmp/transcript.jsonl",
            "hook_event_name": "SubagentStop",
            "agent_type": "adversarial-reviewer",
            "model": "claude-fixture-model",
            "future_unknown_field": {"nested": 1},
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(0, process.returncode, process.stderr.decode())

    def test_wrong_event_name_blocks(self) -> None:
        event = {
            "review": _review_verdict(),
            "candidate_sha": SHA_CURRENT,
            "hook_event_name": "PreToolUse",
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)

    def test_malformed_stdin_blocks_fail_closed(self) -> None:
        process = run_gate(REVIEW_GATE, b'{"review":')
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertIn("HOOK-004", error["rule_ids"])

    def test_oversized_stdin_blocks_fail_closed(self) -> None:
        process = run_gate(REVIEW_GATE, b"{" + b"x" * (64 * 1024))
        self.assertEqual(2, process.returncode)

    def test_no_canary_leak_when_review_contains_secrets(self) -> None:
        findings = [
            {
                "severity": "block",
                "rule_ids": ["SEC-003"],
                "evidence_refs": [f"{CANARY_NAMED} {CANARY_PATH}"],
                "reason": f"leak {CANARY_PII} {CANARY_SECRET}",
                "recovery": "remove secret",
            }
        ]
        event = {
            "review": _review_verdict(status="BLOCKED", findings=findings),
            "candidate_sha": SHA_CURRENT,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        _assert_clean(self, process)

    def test_no_canary_leak_when_candidate_sha_field_tainted(self) -> None:
        # Even if a tainted value reaches the candidate_sha comparison, the
        # gate's own output must not echo it.
        event = {
            "review": _review_verdict(),
            "candidate_sha": CANARY_SECRET,
        }
        process = run_gate(REVIEW_GATE, event)
        self.assertEqual(2, process.returncode)
        _assert_clean(self, process)


class StopGateTests(unittest.TestCase):
    def test_no_open_findings_exits_zero(self) -> None:
        event = {"open_findings": []}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(0, process.returncode, process.stderr.decode())
        self.assertEqual(b"", process.stderr)

    def test_only_warn_findings_exits_zero(self) -> None:
        findings = [
            {
                "severity": "warn",
                "rule_ids": ["QLT-001"],
                "evidence_refs": ["evidence:freshness"],
                "reason": "freshness unknown",
                "recovery": "record max data date",
            }
        ]
        event = {"open_findings": findings}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(0, process.returncode, process.stderr.decode())

    def test_only_info_findings_exits_zero(self) -> None:
        findings = [
            {
                "severity": "info",
                "rule_ids": ["ANS-001"],
                "evidence_refs": ["candidate:disclosure"],
                "reason": "advisory note",
                "recovery": "none required",
            }
        ]
        event = {"open_findings": findings}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(0, process.returncode, process.stderr.decode())

    def test_open_block_finding_blocks_with_rev003(self) -> None:
        findings = [
            {
                "severity": "block",
                "rule_ids": ["REV-003"],
                "evidence_refs": ["review:block-open"],
                "reason": "blocking finding unresolved at stop time",
                "recovery": "fix the blocking finding and re-review",
            }
        ]
        event = {"open_findings": findings}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(2, process.returncode)
        self.assertEqual(b"", process.stdout)
        error = json.loads(process.stderr.decode())
        self.assertEqual("block", error["status"])
        self.assertIn("REV-003", error["rule_ids"])
        self.assertTrue(error["evidence_refs"])
        self.assertTrue(error["recovery"])

    def test_missing_open_findings_blocks_fail_closed(self) -> None:
        event = {"delivered": True}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertEqual("block", error["status"])

    def test_malformed_finding_blocks_fail_closed(self) -> None:
        event = {"open_findings": [{"severity": "block"}]}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(2, process.returncode)

    def test_open_findings_not_a_list_blocks(self) -> None:
        event = {"open_findings": "none"}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(2, process.returncode)

    def test_recursion_guard_stops(self) -> None:
        event = {"open_findings": [], "stop_hook_active": True}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertIn("HOOK-001", error["rule_ids"])

    def test_unknown_event_fields_tolerated(self) -> None:
        event = {
            "open_findings": [],
            "session_id": "session-fixture-001",
            "transcript_path": "/private/tmp/transcript.jsonl",
            "hook_event_name": "Stop",
            "future_unknown_field": 42,
        }
        process = run_gate(STOP_GATE, event)
        self.assertEqual(0, process.returncode, process.stderr.decode())

    def test_wrong_event_name_blocks(self) -> None:
        event = {"open_findings": [], "hook_event_name": "PreToolUse"}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(2, process.returncode)

    def test_malformed_stdin_blocks_fail_closed(self) -> None:
        process = run_gate(STOP_GATE, b"not-json")
        self.assertEqual(2, process.returncode)
        error = json.loads(process.stderr.decode())
        self.assertIn("HOOK-004", error["rule_ids"])

    def test_no_canary_leak_when_findings_contain_secrets(self) -> None:
        findings = [
            {
                "severity": "block",
                "rule_ids": ["SEC-003"],
                "evidence_refs": [f"{CANARY_NAMED} {CANARY_PATH}"],
                "reason": f"leak {CANARY_PII} {CANARY_SECRET}",
                "recovery": "remove secret",
            }
        ]
        event = {"open_findings": findings}
        process = run_gate(STOP_GATE, event)
        self.assertEqual(2, process.returncode)
        _assert_clean(self, process)


if __name__ == "__main__":
    unittest.main()
