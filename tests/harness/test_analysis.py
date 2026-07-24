"""Cycle 3 Task 1: evidence primitives and schema contract tests.

Tests cover (per ticket 01-runtime-evidence-schemas.md):
- SHA-256 candidate binding (deterministic, payload-sensitive).
- Sanitization reusing gates sanitization + PII redaction (canary secret /
  absolute path / PII email stripped; no canary leak in any output).
- Missing evidence / empty rule_ids / unsupported payload type -> GateError
  (fail-closed, never degrade to an empty placeholder).
- Degradation evidence chain T1 -> T2 -> T3 records source_tier + rule_ids.
- request / review / provenance schemas: valid input passes, missing field
  fails, missing coverage item fails, blocking finding expressed, invalid
  status rejected.

Applicable rules: EVID (Cycle 3), QLT-001, SEC-003, PORT-001, HOOK-001,
HOOK-004, SEM-001, SEM-002, RAW-001, RAW-003, REV-002, ANS-002.
"""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.evidence import (  # noqa: E402
    EvidenceEntry,
    RunRecord,
    compute_candidate_sha,
    validate_provenance,
    validate_request,
    validate_review,
)
from chatbi_harness.gates import GateError  # noqa: E402


# ---------------------------------------------------------------------------
# Canary values. Chosen to trigger sanitization regexes from gates.py and the
# PII email redaction in evidence.py, but NOT to match the project canary
# sweep grep (which checks for real absolute home paths, private key headers,
# and credential-bearing field names). Rule-ID field names are safe.

CANARY_SECRET = "secret=sk-canary-secret-value-12345"
CANARY_PATH = "/tmp/canary/secret/file"
CANARY_PII = "canary-user@example.com"

ALL_CANARIES = (CANARY_SECRET, CANARY_PATH, CANARY_PII)


def _valid_candidate() -> dict[str, Any]:
    return {
        "answer": "42 active users",
        "method": "semantic_layer_query",
        "footer": {"source_tier": "T1"},
    }


def _valid_request() -> dict[str, Any]:
    return {
        "question": "How many active users last month?",
        "time_range": "2026-06-01/2026-06-30",
        "entity": "active_user",
        "segment": "all_users",
        "actor": "analyst_alpha",
        "purpose": "monthly_review",
        "supported_decision": "growth_strategy",
    }


def _valid_review() -> dict[str, Any]:
    return {
        "run_id": "run-001",
        "round": 1,
        "candidate_sha": "a" * 64,
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
        "reviewer_context_hash": "b" * 64,
        "sanitized_output": True,
    }


def _valid_provenance() -> dict[str, Any]:
    return {
        "question": "How many active users last month?",
        "time_range": "2026-06-01/2026-06-30",
        "entity": "active_user",
        "segment": "all_users",
        "method": "semantic_layer_query",
        "source_tier": "T1",
        "filters": ["is_active=true"],
        "inclusions": ["registered users"],
        "exclusions": ["test accounts"],
        "denominator": "total_registered_users",
        "quality": "freshness_check_passed",
        "limitations": "timezone_offset_may_apply",
        "review_round": 1,
        "freshness": "2026-07-01T00:00:00Z",
        "owner": "metrics_team",
        "confidence": "high",
        "provenance_refs": ["semantic_layer:active_users_metric"],
    }


# ---------------------------------------------------------------------------
# compute_candidate_sha
# ---------------------------------------------------------------------------


class ComputeCandidateShaTests(unittest.TestCase):
    def test_is_deterministic_for_identical_input(self) -> None:
        self.assertEqual(
            compute_candidate_sha(_valid_candidate()),
            compute_candidate_sha(_valid_candidate()),
        )

    def test_changes_when_payload_changes(self) -> None:
        left = compute_candidate_sha({"answer": "42"})
        right = compute_candidate_sha({"answer": "43"})
        self.assertNotEqual(left, right)

    def test_produces_64_hex_characters(self) -> None:
        sha = compute_candidate_sha(_valid_candidate())
        self.assertEqual(64, len(sha))
        self.assertTrue(all(c in "0123456789abcdef" for c in sha))

    def test_key_order_independent(self) -> None:
        left = compute_candidate_sha({"a": 1, "b": 2})
        right = compute_candidate_sha({"b": 2, "a": 1})
        self.assertEqual(left, right)

    def test_rejects_non_serializable_payload(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            compute_candidate_sha({"bad": b"raw bytes"})


# ---------------------------------------------------------------------------
# RunRecord
# ---------------------------------------------------------------------------


class RunRecordTests(unittest.TestCase):
    def _make(self, **overrides: Any) -> RunRecord:
        defaults: dict[str, Any] = {
            "run_id": "run-001",
            "round": 1,
            "candidate_sha": compute_candidate_sha(_valid_candidate()),
            "created_rev": "content_sha256:abc123",
            "actor": "analyst_alpha",
            "purpose": "monthly_review",
        }
        defaults.update(overrides)
        return RunRecord(**defaults)

    def test_accepts_valid_input(self) -> None:
        record = self._make()
        self.assertEqual("run-001", record.run_id)
        self.assertEqual(1, record.round)
        self.assertEqual(64, len(record.candidate_sha))

    def test_rejects_invalid_candidate_sha_format(self) -> None:
        with self.assertRaises(ValueError):
            self._make(candidate_sha="not-a-sha")

    def test_rejects_empty_run_id(self) -> None:
        with self.assertRaises(ValueError):
            self._make(run_id="")

    def test_rejects_non_positive_round(self) -> None:
        with self.assertRaises(ValueError):
            self._make(round=0)

    def test_rejects_empty_created_rev(self) -> None:
        with self.assertRaises(ValueError):
            self._make(created_rev="")

    def test_rejects_empty_actor(self) -> None:
        with self.assertRaises(ValueError):
            self._make(actor="")

    def test_rejects_empty_purpose(self) -> None:
        with self.assertRaises(ValueError):
            self._make(purpose="")

    def test_is_frozen(self) -> None:
        record = self._make()
        with self.assertRaises(FrozenInstanceError):
            record.run_id = "mutated"  # type: ignore[misc]

    def test_serializes_deterministically(self) -> None:
        left = self._make().to_json()
        right = self._make().to_json()
        self.assertEqual(left, right)

    def test_to_dict_round_trips_required_fields(self) -> None:
        record = self._make()
        rendered = record.to_dict()
        for field in ("run_id", "round", "candidate_sha", "created_rev",
                      "actor", "purpose"):
            self.assertIn(field, rendered)


# ---------------------------------------------------------------------------
# EvidenceEntry sanitization
# ---------------------------------------------------------------------------


class EvidenceEntrySanitizationTests(unittest.TestCase):
    def test_strips_canary_secret(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={"query_log": CANARY_SECRET},
        )
        rendered = entry.to_json()
        self.assertNotIn(CANARY_SECRET, rendered)
        self.assertIn("[REDACTED_SECRET]", rendered)

    def test_strips_absolute_path(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T2",
            evidence_source="curated_reference",
            rule_ids=("RAW-001",),
            payload={"file_ref": CANARY_PATH},
        )
        rendered = entry.to_json()
        self.assertNotIn(CANARY_PATH, rendered)
        self.assertIn("[REDACTED_PATH]", rendered)

    def test_strips_pii_email(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T3",
            evidence_source="raw_exploration",
            rule_ids=("RAW-003",),
            payload={"user_email": CANARY_PII},
        )
        rendered = entry.to_json()
        self.assertNotIn(CANARY_PII, rendered)
        self.assertIn("[REDACTED_PII]", rendered)

    def test_sanitized_flag_true_when_redaction_occurs(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={"note": CANARY_SECRET},
        )
        self.assertTrue(entry.sanitized)

    def test_sanitized_flag_false_when_clean(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={"note": "no secrets here"},
        )
        self.assertFalse(entry.sanitized)

    def test_no_canary_in_any_output(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={
                "secret_field": CANARY_SECRET,
                "path_field": CANARY_PATH,
                "pii_field": CANARY_PII,
            },
        )
        json_output = entry.to_json()
        dict_output = entry.to_dict()
        for canary in ALL_CANARIES:
            self.assertNotIn(canary, json_output)
            self.assertNotIn(canary, json.dumps(dict_output, sort_keys=True))

    def test_missing_payload_raises_gate_error(self) -> None:
        with self.assertRaises(GateError) as ctx:
            EvidenceEntry.create(
                source_tier="T1",
                evidence_source="managed:semantic_layer",
                rule_ids=("SEM-001",),
                payload=None,
            )
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_unsupported_payload_type_raises_gate_error(self) -> None:
        with self.assertRaises(GateError):
            EvidenceEntry.create(
                source_tier="T1",
                evidence_source="managed:semantic_layer",
                rule_ids=("SEM-001",),
                payload={"bad": b"raw bytes"},
            )

    def test_non_finite_float_raises_gate_error(self) -> None:
        with self.assertRaises(GateError):
            EvidenceEntry.create(
                source_tier="T1",
                evidence_source="managed:semantic_layer",
                rule_ids=("SEM-001",),
                payload={"value": float("nan")},
            )

    def test_rejects_invalid_source_tier(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceEntry.create(
                source_tier="T4",
                evidence_source="managed:semantic_layer",
                rule_ids=("SEM-001",),
                payload={"ok": True},
            )

    def test_rejects_empty_evidence_source(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceEntry.create(
                source_tier="T1",
                evidence_source="",
                rule_ids=("SEM-001",),
                payload={"ok": True},
            )

    def test_rejects_empty_rule_ids(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceEntry.create(
                source_tier="T1",
                evidence_source="managed:semantic_layer",
                rule_ids=(),
                payload={"ok": True},
            )

    def test_is_frozen(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={"ok": True},
        )
        with self.assertRaises(FrozenInstanceError):
            entry.source_tier = "T2"  # type: ignore[misc]

    def test_content_sha256_is_deterministic(self) -> None:
        left = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={"count": 42},
        )
        right = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={"count": 42},
        )
        self.assertEqual(left.content_sha256, right.content_sha256)
        self.assertEqual(64, len(left.content_sha256))

    def test_content_sha256_changes_with_payload(self) -> None:
        left = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={"count": 42},
        )
        right = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={"count": 43},
        )
        self.assertNotEqual(left.content_sha256, right.content_sha256)

    def test_serializes_deterministically(self) -> None:
        left = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001", "SEM-002"),
            payload={"count": 42},
        )
        right = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001", "SEM-002"),
            payload={"count": 42},
        )
        self.assertEqual(left.to_json(), right.to_json())

    def test_deduplicates_rule_ids(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001", "SEM-001", "SEM-002"),
            payload={"ok": True},
        )
        self.assertEqual(("SEM-001", "SEM-002"), entry.rule_ids)

    def test_payload_is_immutable(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={"nested": {"key": "value"}, "items": [1, 2]},
        )
        # The payload dict should be frozen (MappingProxyType); mutation fails.
        with self.assertRaises(TypeError):
            entry.payload["nested"] = "mutated"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Degradation evidence chain T1 -> T2 -> T3
# ---------------------------------------------------------------------------


class DegradationChainTests(unittest.TestCase):
    def test_t1_t2_t3_records_source_tier_and_rule_ids(self) -> None:
        t1 = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001", "SEM-002"),
            payload={"metric": "active_users", "compiled": True},
        )
        t2 = EvidenceEntry.create(
            source_tier="T2",
            evidence_source="curated_reference",
            rule_ids=("RAW-001",),
            payload={"gap_reason": "semantic layer has no metric",
                     "fallback": "curated reference"},
        )
        t3 = EvidenceEntry.create(
            source_tier="T3",
            evidence_source="raw_exploration",
            rule_ids=("RAW-003",),
            payload={"explored_table": "fct_events",
                     "high_risk": True},
        )

        chain = [t1, t2, t3]
        self.assertEqual(["T1", "T2", "T3"],
                         [e.source_tier for e in chain])
        self.assertEqual(("SEM-001", "SEM-002"), chain[0].rule_ids)
        self.assertEqual(("RAW-001",), chain[1].rule_ids)
        self.assertEqual(("RAW-003",), chain[2].rule_ids)

        # Every entry has a distinct content SHA (different payloads).
        shas = {e.content_sha256 for e in chain}
        self.assertEqual(3, len(shas))

        # All entries serialize without leaking canaries.
        for entry in chain:
            for canary in ALL_CANARIES:
                self.assertNotIn(canary, entry.to_json())


# ---------------------------------------------------------------------------
# request.schema.json
# ---------------------------------------------------------------------------


class RequestSchemaTests(unittest.TestCase):
    def test_valid_request_passes(self) -> None:
        validate_request(_valid_request())

    def test_missing_question_fails(self) -> None:
        payload = _valid_request()
        del payload["question"]
        with self.assertRaises(GateError):
            validate_request(payload)

    def test_missing_actor_fails(self) -> None:
        payload = _valid_request()
        del payload["actor"]
        with self.assertRaises(GateError):
            validate_request(payload)

    def test_missing_supported_decision_fails(self) -> None:
        payload = _valid_request()
        del payload["supported_decision"]
        with self.assertRaises(GateError):
            validate_request(payload)


# ---------------------------------------------------------------------------
# review.schema.json
# ---------------------------------------------------------------------------


class ReviewSchemaTests(unittest.TestCase):
    def test_valid_review_passes(self) -> None:
        validate_review(_valid_review())

    def test_missing_run_id_fails(self) -> None:
        payload = _valid_review()
        del payload["run_id"]
        with self.assertRaises(GateError):
            validate_review(payload)

    def test_missing_candidate_sha_fails(self) -> None:
        payload = _valid_review()
        del payload["candidate_sha"]
        with self.assertRaises(GateError):
            validate_review(payload)

    def test_missing_coverage_item_fails(self) -> None:
        payload = _valid_review()
        del payload["coverage"]["denominator"]
        with self.assertRaises(GateError):
            validate_review(payload)

    def test_invalid_status_fails(self) -> None:
        payload = _valid_review()
        payload["status"] = "MAYBE"
        with self.assertRaises(GateError):
            validate_review(payload)

    def test_blocking_finding_is_expressed(self) -> None:
        payload = _valid_review()
        payload["status"] = "BLOCKED"
        payload["findings"] = [
            {
                "severity": "block",
                "rule_ids": ["REV-003"],
                "evidence_refs": ["review:denominator-mismatch"],
                "reason": "Denominator does not match semantic layer definition",
                "recovery": "Align the denominator with the governed metric",
            }
        ]
        validate_review(payload)

    def test_pass_status_with_empty_findings_passes(self) -> None:
        payload = _valid_review()
        payload["status"] = "PASS"
        payload["findings"] = []
        validate_review(payload)

    def test_finding_missing_severity_fails(self) -> None:
        payload = _valid_review()
        payload["findings"] = [
            {
                "rule_ids": ["REV-003"],
                "evidence_refs": ["review:x"],
                "reason": "x",
                "recovery": "y",
            }
        ]
        with self.assertRaises(GateError):
            validate_review(payload)


# ---------------------------------------------------------------------------
# provenance.schema.json
# ---------------------------------------------------------------------------


class ProvenanceSchemaTests(unittest.TestCase):
    def test_valid_provenance_passes(self) -> None:
        validate_provenance(_valid_provenance())

    def test_missing_owner_fails(self) -> None:
        payload = _valid_provenance()
        del payload["owner"]
        with self.assertRaises(GateError):
            validate_provenance(payload)

    def test_missing_source_tier_fails(self) -> None:
        payload = _valid_provenance()
        del payload["source_tier"]
        with self.assertRaises(GateError):
            validate_provenance(payload)

    def test_missing_denominator_fails(self) -> None:
        payload = _valid_provenance()
        del payload["denominator"]
        with self.assertRaises(GateError):
            validate_provenance(payload)

    def test_invalid_source_tier_fails(self) -> None:
        payload = _valid_provenance()
        payload["source_tier"] = "T4"
        with self.assertRaises(GateError):
            validate_provenance(payload)


# ---------------------------------------------------------------------------
# Canary sweep: no canary leak in any evidence or error output
# ---------------------------------------------------------------------------


class CanarySweepTests(unittest.TestCase):
    def test_no_canary_in_evidence_json(self) -> None:
        entry = EvidenceEntry.create(
            source_tier="T1",
            evidence_source="managed:semantic_layer",
            rule_ids=("SEM-001",),
            payload={
                "secret": CANARY_SECRET,
                "path": CANARY_PATH,
                "email": CANARY_PII,
                "nested": {"deep_secret": CANARY_SECRET},
            },
        )
        rendered = entry.to_json()
        for canary in ALL_CANARIES:
            self.assertNotIn(canary, rendered)

    def test_no_canary_in_gate_error_message(self) -> None:
        try:
            EvidenceEntry.create(
                source_tier="T1",
                evidence_source="managed:semantic_layer",
                rule_ids=("SEM-001",),
                payload=None,
            )
            self.fail("Expected GateError for missing payload")
        except GateError as error:
            message = str(error)
            decision_json = error.decision.to_json()
            for canary in ALL_CANARIES:
                self.assertNotIn(canary, message)
                self.assertNotIn(canary, decision_json)

    def test_no_canary_in_run_record_json(self) -> None:
        record = RunRecord(
            run_id="run-001",
            round=1,
            candidate_sha=compute_candidate_sha({"canary": CANARY_SECRET}),
            created_rev="content_sha256:abc",
            actor="analyst_alpha",
            purpose="monthly_review",
        )
        rendered = record.to_json()
        for canary in ALL_CANARIES:
            self.assertNotIn(canary, rendered)


class EvidenceIntegrationTests(unittest.TestCase):
    """Offline integration of the evidence -> review contract -> footer loop.

    Complements ``test_e2e.py`` (which exercises the full flow through the
    subprocess review/stop gates) by asserting the evidence-layer wiring:
    a degradation evidence chain binds to a candidate SHA, a schema-conformant
    review verdict references that SHA, and a provenance footer assembles from
    the same evidence. Applicable rules: SEM-001/002, RAW-001/003, REV-002,
    ANS-002, HOOK-001.
    """

    def _chain(self) -> list[EvidenceEntry]:
        return [
            EvidenceEntry.create(source_tier="T1", evidence_source="semantic-layer",
                                 rule_ids=("SEM-001", "SEM-002"),
                                 payload={"entity": "active_users", "covered": False}),
            EvidenceEntry.create(source_tier="T2", evidence_source="curated-reference",
                                 rule_ids=("RAW-001",),
                                 payload={"entity": "active_users", "ref": "example"}),
            EvidenceEntry.create(source_tier="T3", evidence_source="raw-exploration",
                                 rule_ids=("RAW-003",),
                                 payload={"entity": "active_users", "raw": "example_raw"}),
        ]

    def test_chain_records_degradation_tiers_and_rule_ids(self) -> None:
        chain = self._chain()
        self.assertEqual(["T1", "T2", "T3"], [e.source_tier for e in chain])
        self.assertEqual(("RAW-003",), chain[-1].rule_ids)

    def test_review_verdict_binds_to_candidate_sha_and_validates(self) -> None:
        candidate = {"answer": 42, "unit": "count"}
        sha = compute_candidate_sha(candidate)
        review = {
            "run_id": "run-int-1", "round": 1, "candidate_sha": sha,
            "status": "PASS",
            "coverage": {k: "pass" for k in (
                "entity", "grain", "joins", "filters_exclusions", "date_timezone",
                "denominator", "sample_bias", "quality",
                "observation_vs_interpretation", "disclosure", "provenance")},
            "findings": [],
            "reviewer_context_hash": "e" * 64, "sanitized_output": True,
        }
        validate_review(review)  # conforms
        self.assertEqual(sha, review["candidate_sha"])

    def test_blocking_finding_review_validates(self) -> None:
        sha = compute_candidate_sha({"answer": 1})
        review = {
            "run_id": "run-int-2", "round": 1, "candidate_sha": sha,
            "status": "BLOCKED",
            "coverage": {k: ("fail" if k == "denominator" else "pass") for k in (
                "entity", "grain", "joins", "filters_exclusions", "date_timezone",
                "denominator", "sample_bias", "quality",
                "observation_vs_interpretation", "disclosure", "provenance")},
            "findings": [{"severity": "block", "rule_ids": ["SEM-001"],
                          "evidence_refs": ["evidence:int"],
                          "reason": "denominator ambiguous", "recovery": "clarify"}],
            "reviewer_context_hash": "e" * 64, "sanitized_output": True,
        }
        validate_review(review)
        self.assertEqual("BLOCKED", review["status"])

    def test_provenance_footer_assembles_and_validates(self) -> None:
        footer = {
            "question": "q", "time_range": "2024-01", "entity": "active_users",
            "segment": "all", "method": "governed", "source_tier": "T3",
            "filters": ["last_month"], "inclusions": ["fixture"], "exclusions": [],
            "denominator": "none", "quality": "fixture_snapshot",
            "limitations": "offline_synthetic_reviewer", "review_round": 1,
            "freshness": "snapshot_2024_01", "owner": "owner_example",
            "confidence": "medium", "provenance_refs": ["evidence:int"],
        }
        validate_provenance(footer)  # all 17 fields conform

    def test_candidate_change_invalidates_sha(self) -> None:
        self.assertNotEqual(
            compute_candidate_sha({"answer": 42}),
            compute_candidate_sha({"answer": 43}),
        )


if __name__ == "__main__":
    unittest.main()
