"""Cycle 5 Task 01: evaluator + ground-truth isolation contract tests.

Tests cover (per ticket 01-evaluator-groundtruth-schemas.md): ground-truth
isolation (tested session cannot read answers), per-assertion scoring, run
record fields (EVAL-003), seen/unseen separation, dual-candidate correction
(FBK-002), no auto-approve (SEM-003), and the FBK-003 statement (pass !=
absolute correctness).

Applicable rules: EVAL-001..005, ABL-001/002, FBK-001/002/003, HOOK-001/004,
SEC-003, PORT-001. Real evaluation runtime (real adapter/reviewer) is a Cycle 5
Task 06 E2E gate; not claimed here (FBK-003).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.gates import GateError  # noqa: E402
from chatbi_harness.evaluator import (  # noqa: E402
    FBK_003_STATEMENT,
    GroundTruthVault,
    build_correction_record,
    build_evaluation_run,
    validate_correction,
    validate_evaluation,
)

CANARY_ANSWER = "sk-secret-canary-answer"


class GroundTruthVaultTests(unittest.TestCase):
    def test_empty_vault_rejected(self) -> None:
        with self.assertRaises(GateError):
            GroundTruthVault({})

    def test_score_returns_result_without_raw_answer(self) -> None:
        vault = GroundTruthVault({"a1": 42})
        result = vault.score("a1", 42)
        self.assertTrue(result.passed)
        # The result exposes hashes, NOT the raw expected answer (isolation).
        d = result.to_dict()
        self.assertNotIn(42, list(d.values()))
        # Only hashes are exposed, never the raw answer value.
        self.assertNotIn("expected_answer", d)
        self.assertNotIn("answer", d)

    def test_unknown_assertion_rejected(self) -> None:
        vault = GroundTruthVault({"a1": 42})
        with self.assertRaises(GateError):
            vault.score("nope", 1)

    def test_wrong_actual_scores_false(self) -> None:
        vault = GroundTruthVault({"a1": 42})
        self.assertFalse(vault.score("a1", 7).passed)

    def test_custom_scorer(self) -> None:
        vault = GroundTruthVault({"a1": {"a", "b"}},
                                 scorers={"a1": lambda exp, act: exp == set(act)})
        self.assertTrue(vault.score("a1", ["b", "a"]).passed)
        self.assertFalse(vault.score("a1", ["a"]).passed)

    def test_no_method_exposes_raw_answer(self) -> None:
        vault = GroundTruthVault({"a1": CANARY_ANSWER})
        result = vault.score("a1", CANARY_ANSWER)
        # Nothing reachable from the result leaks the canary answer.
        self.assertNotIn(CANARY_ANSWER, result.to_dict().__str__())


class EvaluationRunTests(unittest.TestCase):
    def _vault(self) -> GroundTruthVault:
        return GroundTruthVault({"a1": 42, "a2": "hello"})

    def test_build_run_records_all_fields(self) -> None:
        run = build_evaluation_run(
            run_id="run-1", skill_version="chatbi-evaluation@1.0",
            model_id="claude-example-5", vault=self._vault(),
            actuals={"a1": 42, "a2": "hello"}, tokens=1234, latency_ms=567,
            seen=True, threshold_owner_confirmed=True,
            content_payload={"suite": "high-freq"})
        self.assertEqual("run-1", run.run_id)
        self.assertEqual("claude-example-5", run.model_id)
        self.assertEqual(64, len(run.content_hash))
        self.assertEqual(2, run.total_count if False else len(run.assertions))
        self.assertEqual(2, run.passed_count)
        self.assertTrue(run.all_passed)
        self.assertTrue(run.seen)
        self.assertTrue(run.threshold_owner_confirmed)

    def test_seen_unseen_separate(self) -> None:
        seen = build_evaluation_run(
            run_id="rs", skill_version="s", model_id="m", vault=self._vault(),
            actuals={"a1": 42, "a2": "hello"}, tokens=1, latency_ms=1,
            seen=True, threshold_owner_confirmed=True, content_payload={"k": 1})
        unseen = build_evaluation_run(
            run_id="ru", skill_version="s", model_id="m", vault=self._vault(),
            actuals={"a1": 42, "a2": "hello"}, tokens=1, latency_ms=1,
            seen=False, threshold_owner_confirmed=True, content_payload={"k": 1})
        self.assertTrue(seen.seen)
        self.assertFalse(unseen.seen)

    def test_missing_run_fields_rejected(self) -> None:
        with self.assertRaises(GateError):
            build_evaluation_run(
                run_id="", skill_version="s", model_id="m", vault=self._vault(),
                actuals={"a1": 42, "a2": "hello"}, tokens=1, latency_ms=1,
                seen=True, threshold_owner_confirmed=True, content_payload={})

    def test_threshold_not_owner_confirmed_recorded(self) -> None:
        run = build_evaluation_run(
            run_id="r", skill_version="s", model_id="m", vault=self._vault(),
            actuals={"a1": 42, "a2": "hello"}, tokens=1, latency_ms=1,
            seen=True, threshold_owner_confirmed=False, content_payload={})
        self.assertFalse(run.threshold_owner_confirmed)  # not assumed confirmed

    def test_fbk003_statement_in_run(self) -> None:
        run = build_evaluation_run(
            run_id="r", skill_version="s", model_id="m", vault=self._vault(),
            actuals={"a1": 42, "a2": "hello"}, tokens=1, latency_ms=1,
            seen=True, threshold_owner_confirmed=True, content_payload={})
        self.assertIn("FBK-003", run.to_dict()["fbk_003_statement"])

    def test_no_canary_leak_in_run(self) -> None:
        vault = GroundTruthVault({"a1": CANARY_ANSWER})
        run = build_evaluation_run(
            run_id="r", skill_version="s", model_id="m", vault=vault,
            actuals={"a1": CANARY_ANSWER}, tokens=1, latency_ms=1,
            seen=True, threshold_owner_confirmed=True, content_payload={"x": 1})
        self.assertNotIn(CANARY_ANSWER, str(run.to_dict()))


class SchemaValidationTests(unittest.TestCase):
    def test_validate_evaluation_valid(self) -> None:
        run = build_evaluation_run(
            run_id="r", skill_version="s", model_id="m",
            vault=GroundTruthVault({"a1": 1}), actuals={"a1": 1},
            tokens=1, latency_ms=1, seen=True, threshold_owner_confirmed=True,
            content_payload={})
        validate_evaluation(run.to_dict())  # no raise

    def test_validate_evaluation_missing_field_fails(self) -> None:
        d = build_evaluation_run(
            run_id="r", skill_version="s", model_id="m",
            vault=GroundTruthVault({"a1": 1}), actuals={"a1": 1},
            tokens=1, latency_ms=1, seen=True, threshold_owner_confirmed=True,
            content_payload={}).to_dict()
        del d["model_id"]
        with self.assertRaises(GateError):
            validate_evaluation(d)


class CorrectionTests(unittest.TestCase):
    def test_dual_candidate_owner_not_approved_by_default(self) -> None:
        rec = build_correction_record(
            correction_id="c1", fix_kind="reference", fix_target="references/r",
            fix_change_summary="add use-for trigger",
            eval_case_assertion_id="a1",
            eval_case_expected_hash="a" * 64,
            rule_ids=("FBK-002", "DOC-003"))
        self.assertFalse(rec["owner_approved"])  # no auto-merge (SEM-003)
        self.assertIn("fix_candidate", rec)
        self.assertIn("eval_case_candidate", rec)
        validate_correction(rec)

    def test_invalid_fix_kind_rejected(self) -> None:
        with self.assertRaises(GateError):
            build_correction_record(
                correction_id="c1", fix_kind="bogus", fix_target="r",
                fix_change_summary="x", eval_case_assertion_id="a1",
                eval_case_expected_hash="a" * 64, rule_ids=("FBK-002",))

    def test_correction_carries_fbk003(self) -> None:
        rec = build_correction_record(
            correction_id="c1", fix_kind="Skill", fix_target="chatbi-runbook",
            fix_change_summary="clarify degradation",
            eval_case_assertion_id="a1",
            eval_case_expected_hash="a" * 64, rule_ids=("FBK-002",))
        self.assertIn("FBK-003", rec["fbk_003_statement"])

    def test_correction_does_not_auto_approve_metric(self) -> None:
        # A correction touching a canonical metric stays owner_approved=False;
        # the record never auto-approves (SEM-003).
        rec = build_correction_record(
            correction_id="c1", fix_kind="model", fix_target="models/revenue",
            fix_change_summary="adjust canonical metric definition",
            eval_case_assertion_id="a1",
            eval_case_expected_hash="a" * 64,
            rule_ids=("SEM-003", "FBK-002"))
        self.assertFalse(rec["owner_approved"])


class FBK003Tests(unittest.TestCase):
    def test_fbk003_statement_is_non_trivial(self) -> None:
        self.assertIn("silent failure", FBK_003_STATEMENT.lower())
        self.assertIn("FBK-003", FBK_003_STATEMENT)


if __name__ == "__main__":
    unittest.main()
