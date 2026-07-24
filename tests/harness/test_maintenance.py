"""Cycle 4 Task 01: impact manifest + PostToolUse gate contract tests.

Tests cover (per ticket 01-impact-manifest-posttool.md):
- ImpactManifest build/validate: valid, invalid enums, missing required,
  sanitization; has_blocking_drift for each condition.
- Impact matrix branches: model/column/semantic/reference/Skill/downstream/eval.
- PostToolUse gate (invoked as subprocess, the documented Hook seam): sufficient
  + synced -> exit 0; missing/uncertain evidence, P0 eval failed, protected
  action, unsynced asset, stale SHA, missing manifest, malformed stdin -> exit 2;
  unknown fields tolerated; PostToolUse only records (undo=false,
  modified_change=false); no canary leak.

Applicable rules: DOC-004, EVAL-001/003, SEM-003, HOOK-001/003/004/005,
SEC-003, PORT-001, ABL-001/002.

A live end-to-end run of a real Claude PostToolUse process is a Cycle 5 exit
gate and is NOT claimed here (HOOK-003, FBK-003).
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.gates import GateError  # noqa: E402
from chatbi_harness.impact import (  # noqa: E402
    AffectedAsset,
    ImpactManifest,
    build_impact_manifest,
    validate_impact_manifest,
)

POSTTOOL_GATE = WORKSPACE_ROOT / ".claude" / "hooks" / "posttool_impact.py"

CANARY_SECRET = "sk-secret-canary"
CANARY_PATH = "/home/canary/secret-canary-path"

_CHANGE_KINDS = ("model", "column", "semantic", "reference", "Skill",
                 "downstream", "eval")


def _run_gate(payload: dict | bytes) -> subprocess.CompletedProcess[bytes]:
    stdin = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return subprocess.run(
        [sys.executable, "-B", str(POSTTOOL_GATE)],
        cwd=WORKSPACE_ROOT,
        input=stdin,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _manifest_dict(**overrides) -> dict:
    """A clean, non-blocking manifest dict (sufficient, all synced)."""
    base = {
        "run_id": "run-m-1",
        "change_kind": "model",
        "target": "models/revenue_example",
        "affected_assets": [
            {"asset_kind": "metadata", "asset_ref": "metadata/revenue_example",
             "change_required": True, "synced": True},
            {"asset_kind": "tests", "asset_ref": "tests/test_revenue",
             "change_required": True, "synced": True},
        ],
        "evidence_state": "sufficient",
        "p0_eval_failed": False,
        "protected_action": False,
        "candidate_sha": "a" * 64,
        "created_rev": "candidate_sha:aaaaaaaaaaaa",
    }
    base.update(overrides)
    return base


class ImpactManifestBuildTests(unittest.TestCase):
    def test_build_valid_manifest(self) -> None:
        m = build_impact_manifest(
            run_id="run-1", change_kind="model", target="models/revenue_example",
            affected_assets=[{"asset_kind": "metadata", "asset_ref": "metadata/r",
                              "change_required": True, "synced": True}],
            evidence_state="sufficient", candidate_payload={"change": "add column"},
        )
        self.assertIsInstance(m, ImpactManifest)
        self.assertEqual("sufficient", m.evidence_state)
        self.assertEqual(64, len(m.candidate_sha))
        validate_impact_manifest(m.to_dict())  # conforms

    def test_invalid_change_kind_rejected(self) -> None:
        with self.assertRaises(GateError):
            build_impact_manifest(
                run_id="r", change_kind="unknown", target="t",
                affected_assets=[], evidence_state="sufficient",
                candidate_payload={})

    def test_invalid_evidence_state_rejected(self) -> None:
        with self.assertRaises(GateError):
            build_impact_manifest(
                run_id="r", change_kind="model", target="t",
                affected_assets=[], evidence_state="bogus",
                candidate_payload={})

    def test_empty_target_rejected(self) -> None:
        with self.assertRaises(GateError):
            build_impact_manifest(
                run_id="r", change_kind="model", target="",
                affected_assets=[], evidence_state="sufficient",
                candidate_payload={})

    def test_unknown_asset_kind_rejected(self) -> None:
        with self.assertRaises(GateError):
            build_impact_manifest(
                run_id="r", change_kind="model", target="t",
                affected_assets=[{"asset_kind": "bogus", "asset_ref": "r",
                                  "change_required": False, "synced": False}],
                evidence_state="sufficient", candidate_payload={})

    def test_validate_missing_field_fails(self) -> None:
        d = _manifest_dict()
        del d["evidence_state"]
        with self.assertRaises(GateError):
            validate_impact_manifest(d)

    def test_validate_bad_enum_fails(self) -> None:
        d = _manifest_dict(evidence_state="bogus")
        with self.assertRaises(GateError):
            validate_impact_manifest(d)

    def test_validate_bad_candidate_sha_pattern_fails(self) -> None:
        d = _manifest_dict(candidate_sha="not-a-sha")
        with self.assertRaises(GateError):
            validate_impact_manifest(d)

    def test_impact_matrix_all_change_kinds_build(self) -> None:
        for kind in _CHANGE_KINDS:
            with self.subTest(change_kind=kind):
                m = build_impact_manifest(
                    run_id="r", change_kind=kind, target=f"target/{kind}",
                    affected_assets=[{"asset_kind": "metadata", "asset_ref": "r",
                                      "change_required": True, "synced": True}],
                    evidence_state="sufficient", candidate_payload={"k": kind},
                )
                self.assertEqual(kind, m.change_kind)


class BlockingDriftTests(unittest.TestCase):
    def _build(self, **overrides) -> ImpactManifest:
        assets = overrides.pop("affected_assets",
                               [{"asset_kind": "metadata", "asset_ref": "r",
                                 "change_required": True, "synced": True}])
        kwargs = dict(
            run_id="r", change_kind="model", target="t",
            affected_assets=assets, evidence_state="sufficient",
            candidate_payload={"x": 1})
        kwargs.update(overrides)
        return build_impact_manifest(**kwargs)

    def test_sufficient_all_synced_no_drift(self) -> None:
        self.assertFalse(self._build().has_blocking_drift())

    def test_missing_evidence_blocks(self) -> None:
        self.assertTrue(self._build(evidence_state="missing").has_blocking_drift())

    def test_uncertain_evidence_blocks(self) -> None:
        self.assertTrue(self._build(evidence_state="uncertain").has_blocking_drift())

    def test_p0_eval_failed_blocks(self) -> None:
        self.assertTrue(self._build(p0_eval_failed=True).has_blocking_drift())

    def test_protected_action_blocks(self) -> None:
        self.assertTrue(self._build(protected_action=True).has_blocking_drift())

    def test_unsynced_asset_blocks(self) -> None:
        m = self._build(affected_assets=[
            {"asset_kind": "metadata", "asset_ref": "r",
             "change_required": True, "synced": False}])
        self.assertTrue(m.has_blocking_drift())

    def test_no_change_required_not_blocking(self) -> None:
        m = self._build(affected_assets=[
            {"asset_kind": "metadata", "asset_ref": "r",
             "change_required": False, "synced": False}])
        self.assertFalse(m.has_blocking_drift())


class PostToolGateTests(unittest.TestCase):
    def test_sufficient_synced_exits_zero_and_records(self) -> None:
        proc = _run_gate({"impact_manifest": _manifest_dict(),
                          "candidate_sha": "a" * 64, "tool_name": "Edit"})
        self.assertEqual(0, proc.returncode, proc.stderr.decode())
        record = json.loads(proc.stdout.decode())
        self.assertTrue(record["recorded"])
        self.assertFalse(record["undo"])            # only records, never undoes
        self.assertFalse(record["modified_change"])  # never modifies the change

    def test_missing_evidence_blocks(self) -> None:
        proc = _run_gate({"impact_manifest": _manifest_dict(evidence_state="missing")})
        self.assertEqual(2, proc.returncode)

    def test_uncertain_evidence_blocks(self) -> None:
        proc = _run_gate({"impact_manifest": _manifest_dict(evidence_state="uncertain")})
        self.assertEqual(2, proc.returncode)

    def test_p0_eval_failed_blocks(self) -> None:
        proc = _run_gate({"impact_manifest": _manifest_dict(p0_eval_failed=True)})
        self.assertEqual(2, proc.returncode)
        self.assertIn(b"EVAL-003", proc.stderr)

    def test_protected_action_blocks_sem_003(self) -> None:
        proc = _run_gate({"impact_manifest": _manifest_dict(protected_action=True)})
        self.assertEqual(2, proc.returncode)
        self.assertIn(b"SEM-003", proc.stderr)

    def test_unsynced_asset_blocks_doc_004(self) -> None:
        manifest = _manifest_dict(affected_assets=[
            {"asset_kind": "metadata", "asset_ref": "metadata/r",
             "change_required": True, "synced": False}])
        proc = _run_gate({"impact_manifest": manifest})
        self.assertEqual(2, proc.returncode)
        self.assertIn(b"DOC-004", proc.stderr)

    def test_stale_candidate_sha_blocks(self) -> None:
        proc = _run_gate({"impact_manifest": _manifest_dict(candidate_sha="a" * 64),
                          "candidate_sha": "b" * 64})
        self.assertEqual(2, proc.returncode)

    def test_missing_impact_manifest_blocks(self) -> None:
        proc = _run_gate({"tool_name": "Edit"})
        self.assertEqual(2, proc.returncode)

    def test_malformed_stdin_blocks(self) -> None:
        proc = _run_gate(b"{not json")
        self.assertEqual(2, proc.returncode)

    def test_oversized_stdin_blocks(self) -> None:
        proc = _run_gate(b"x" * (64 * 1024 + 10))
        self.assertEqual(2, proc.returncode)

    def test_unknown_fields_tolerated(self) -> None:
        proc = _run_gate({"impact_manifest": _manifest_dict(),
                          "candidate_sha": "a" * 64,
                          "session_id": "s", "transcript_path": "/tmp/x",
                          "future_field": {"nested": 1}})
        self.assertEqual(0, proc.returncode, proc.stderr.decode())

    def test_wrong_hook_event_name_blocks(self) -> None:
        proc = _run_gate({"impact_manifest": _manifest_dict(),
                          "hook_event_name": "NotPostToolUse"})
        self.assertEqual(2, proc.returncode)

    def test_recursion_guard_blocks(self) -> None:
        proc = _run_gate({"impact_manifest": _manifest_dict(),
                          "stop_hook_active": True})
        self.assertEqual(2, proc.returncode)

    def test_posttool_does_not_revert_on_block(self) -> None:
        # Blocking exit 2 must not claim an undo/revert; it only flags.
        proc = _run_gate({"impact_manifest": _manifest_dict(evidence_state="missing")})
        self.assertEqual(2, proc.returncode)
        out = proc.stdout.decode()
        self.assertEqual("", out)  # no record emitted on block; no revert action

    def test_no_canary_leak(self) -> None:
        manifest = _manifest_dict(target=f"models/{CANARY_SECRET}",
                                  affected_assets=[
                                      {"asset_kind": "metadata",
                                       "asset_ref": f"metadata/{CANARY_PATH}",
                                       "change_required": True, "synced": True}])
        # build_impact_manifest sanitizes the refs; the gate output must not leak.
        proc = _run_gate({"impact_manifest": manifest, "candidate_sha": "a" * 64})
        combined = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode(
            "utf-8", "replace")
        for canary in (CANARY_SECRET, CANARY_PATH):
            self.assertNotIn(canary, combined)


if __name__ == "__main__":
    unittest.main()
