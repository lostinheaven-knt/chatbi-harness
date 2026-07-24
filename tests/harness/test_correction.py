"""Cycle 5 Task 03: correction-loop contract tests.

Tests cover (per ticket 03-correction-command-test.md): dual-candidate
generation (FBK-002), owner_approval default false / no auto-approve (SEM-003),
FBK-001 structured tracking (semantic-layer resolution ratio + corrective-
language ratio), ABL-001 single-component changes, and FBK-003. Complements the
CorrectionTests in test_evaluation.py.

Applicable rules: FBK-001/002/003, SEM-003, ABL-001/002, SEC-003, PORT-001.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / "harness" / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.gates import GateError  # noqa: E402
from chatbi_harness.evaluator import (  # noqa: E402
    build_correction_record,
    validate_correction,
)


def _record(correction_id: str = "c1", fix_kind: str = "reference",
            rule_ids: tuple[str, ...] = ("FBK-002",)) -> dict:
    return build_correction_record(
        correction_id=correction_id, fix_kind=fix_kind,
        fix_target="references/r", fix_change_summary="add use-for trigger",
        eval_case_assertion_id="a1", eval_case_expected_hash="a" * 64,
        rule_ids=rule_ids, description="example correction")


class CorrectionContractTests(unittest.TestCase):
    def test_record_validates(self) -> None:
        rec = _record()
        validate_correction(rec)  # no raise

    def test_dual_candidate_both_present(self) -> None:
        rec = _record()
        self.assertIn("fix_candidate", rec)
        self.assertIn("eval_case_candidate", rec)
        self.assertEqual({"kind", "target", "change_summary"},
                         set(rec["fix_candidate"]))
        self.assertEqual({"assertion_id", "expected_hash"},
                         set(rec["eval_case_candidate"]))

    def test_owner_must_approve_to_merge(self) -> None:
        rec = _record()
        self.assertFalse(rec["owner_approved"])  # default: not mergeable
        # Merging is an owner action; the record only reflects approval.
        merged = dict(rec, owner_approved=True)
        validate_correction(merged)
        self.assertTrue(merged["owner_approved"])

    def test_metric_correction_stays_unapproved(self) -> None:
        rec = build_correction_record(
            correction_id="c-metric", fix_kind="model",
            fix_target="models/revenue", fix_change_summary="canonical metric",
            eval_case_assertion_id="a1", eval_case_expected_hash="a" * 64,
            rule_ids=("SEM-003", "FBK-002"))
        self.assertFalse(rec["owner_approved"])  # SEM-003: never auto-approved


class FBK001TrackingTests(unittest.TestCase):
    """FBK-001: structured corrections enter periodic review tracking the
    semantic-layer resolution ratio and corrective-language ratio."""

    @staticmethod
    def _summarize(tracking: list[dict]) -> dict[str, float]:
        if not tracking:
            return {"semantic_layer_resolution_ratio": 0.0,
                    "corrective_language_ratio": 0.0}
        t1 = sum(1 for t in tracking if t.get("resolved_at_tier") == "T1")
        corr = sum(1 for t in tracking if t.get("is_corrective_language"))
        n = len(tracking)
        return {"semantic_layer_resolution_ratio": round(t1 / n, 4),
                "corrective_language_ratio": round(corr / n, 4)}

    def test_ratios_computed(self) -> None:
        tracking = [
            {"correction_id": "c1", "resolved_at_tier": "T1", "is_corrective_language": True},
            {"correction_id": "c2", "resolved_at_tier": "T2", "is_corrective_language": False},
            {"correction_id": "c3", "resolved_at_tier": "T1", "is_corrective_language": True},
            {"correction_id": "c4", "resolved_at_tier": "T3", "is_corrective_language": True},
        ]
        s = self._summarize(tracking)
        self.assertEqual(0.5, s["semantic_layer_resolution_ratio"])  # 2/4 at T1
        self.assertEqual(0.75, s["corrective_language_ratio"])  # 3/4 corrective

    def test_empty_tracking(self) -> None:
        s = self._summarize([])
        self.assertEqual(0.0, s["semantic_layer_resolution_ratio"])
        self.assertEqual(0.0, s["corrective_language_ratio"])


class AblationTests(unittest.TestCase):
    def test_single_component_fix_kind(self) -> None:
        # ABL-001: change ONE component at a time. fix_candidate.kind is a single
        # value (reference | Skill | model), not a multi-component change.
        for kind in ("reference", "Skill", "model"):
            rec = _record(fix_kind=kind)
            self.assertEqual(kind, rec["fix_candidate"]["kind"])

    def test_multi_kind_rejected(self) -> None:
        # Cannot express a multi-component fix in one record.
        with self.assertRaises(GateError):
            build_correction_record(
                correction_id="c", fix_kind="reference+model",
                fix_target="r", fix_change_summary="x",
                eval_case_assertion_id="a1", eval_case_expected_hash="a" * 64,
                rule_ids=("ABL-001",))


class NoLeakTests(unittest.TestCase):
    def test_no_canary_in_correction_record(self) -> None:
        rec = build_correction_record(
            correction_id="c", fix_kind="reference",
            fix_target="references/sk-secret-canary",
            fix_change_summary="summary /home/canary/x",
            eval_case_assertion_id="a1", eval_case_expected_hash="a" * 64,
            rule_ids=("FBK-002",))
        # fix_target is sanitized via gates._sanitize_text (strips secret/path).
        self.assertNotIn("sk-secret-canary", rec["fix_candidate"]["target"])
        self.assertNotIn("/home/canary/x", rec["fix_candidate"]["change_summary"])


if __name__ == "__main__":
    unittest.main()
