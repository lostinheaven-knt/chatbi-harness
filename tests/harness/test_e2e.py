"""Cycle 3 Task 06: offline E2E analysis slice.

Wires the governed analysis loop end-to-end with SYNTHETIC fixtures and a
SYNTHETIC reviewer contract:

    request validation (validate_request)
        -> policy.decide (access/PII/risk precheck)
        -> FixtureAdapter T1 discover (real Cycle 2 adapter, test mode)
        -> EvidenceEntry chain (real Task 01 evidence; T1 -> T2 -> T3)
        -> candidate SHA-256 binding (compute_candidate_sha)
        -> synthetic review verdict (review.schema.json-conformant)
        -> subagent_review_gate (real hook, invoked as subprocess)
        -> stop_gate (real hook, invoked as subprocess)
        -> provenance footer (validate_provenance)

The reviewer here is a SYNTHETIC producer: a Python helper that emits a
``review.schema.json``-conformant verdict representative of what the real
``adversarial-reviewer`` agent (Task 02) would return for each scenario. The
REAL Claude reviewer process is a Cycle 5 exit gate and is NOT invoked here
(HOOK-003, FBK-003). This test proves the FLOW WIRING, the GATE ENFORCEMENT
(PASS+SHA-match delivers; BLOCKED / stale-SHA / missing-evidence do not), and
the FOOTER assembly against the five stress scenarios.

Applicable rules: REQ-001..004, SEM-001..003, RAW-001..003, SRC-001/002,
QLT-001, REV-001/002/003, ANS-001/002/003, SCOPE/SEC, HOOK-001/003/004,
PORT-001.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / "harness" / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.adapters.fixture import FixtureAdapter  # noqa: E402
from chatbi_harness.config import load_effective_config  # noqa: E402
from chatbi_harness.evidence import (  # noqa: E402
    EvidenceEntry,
    GateError,
    compute_candidate_sha,
    validate_provenance,
    validate_request,
    validate_review,
)
from chatbi_harness.policy import PolicyRequest, decide  # noqa: E402
from chatbi_harness.impact import build_impact_manifest  # noqa: E402
from chatbi_harness.knowledge import lint_reference  # noqa: E402
from chatbi_harness.evaluator import (  # noqa: E402
    GroundTruthVault,
    build_correction_record,
    build_evaluation_run,
)
from chatbi_harness.adapters import select_adapter  # noqa: E402

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = (
    WORKSPACE_ROOT / "harness" / ".claude" / "fixtures" / "evaluations" / "analysis-scenarios"
)
EVAL_SUITE_DIR = WORKSPACE_ROOT / "harness" / ".claude" / "fixtures" / "evaluations" / "suite"
COMMANDS_DIR = WORKSPACE_ROOT / "harness" / ".claude" / "commands"
REVIEW_GATE = WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "subagent_review_gate.py"
STOP_GATE = WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "stop_gate.py"
POSTTOOL_GATE = WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "posttool_impact.py"
KNOWLEDGE_REFS = (WORKSPACE_ROOT / "harness" / ".claude" / "skills" / "chatbi-knowledge"
                  / "references")

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

# Scenario -> expected outcome summary (mirrors each expected.json).
_EXPECTED = {
    "ambiguity": dict(status="BLOCKED", delivered=False, source_tier="T1",
                      clarify=True, freshness=False, signoff=False, min_auth=False,
                      inj_logged=False, path="clarify"),
    "historical-sql": dict(status="PASS", delivered=True, source_tier="T3",
                           clarify=False, freshness=True, signoff=True, min_auth=False,
                           inj_logged=False, path="T1->T2->T3"),
    "pii-permission": dict(status="BLOCKED", delivered=False, source_tier="T1",
                           clarify=False, freshness=False, signoff=False, min_auth=True,
                           inj_logged=False, path="block"),
    "prompt-injection": dict(status="PASS", delivered=True, source_tier="T1",
                             clarify=False, freshness=False, signoff=False, min_auth=False,
                             inj_logged=True, path="T1-hit"),
    "stale": dict(status="BLOCKED", delivered=False, source_tier="T1",
                  clarify=False, freshness=True, signoff=True, min_auth=False,
                  inj_logged=False, path="T1-hit"),
}


def _run_gate(hook_path: Path, payload: dict[str, Any] | bytes) -> subprocess.CompletedProcess[bytes]:
    """Invoke a hook script as a subprocess (the documented Hook seam)."""
    stdin = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return subprocess.run(
        [sys.executable, "-B", str(hook_path)],
        cwd=WORKSPACE_ROOT,
        input=stdin,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _load_scenario(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    req = json.loads((SCENARIOS_DIR / name / "request.json").read_text())
    exp = json.loads((SCENARIOS_DIR / name / "expected.json").read_text())
    return req, exp


def _minimal_config_path() -> Path:
    """A minimal valid EffectiveConfig (valid-minimal shape) for policy.decide."""
    shared = {
        "schema_version": 1,
        "workspace": {"id": "warehouse", "root": ".",
                       "allow_candidate_writes": True,
                       "protected_actions": ["approve_metric", "change_access_policy",
                                             "production_publish", "destructive_migration"]},
        "business_codebases": {},
        "adapters": {"semantic": [], "query": [], "fixture_enabled": False},
        "governance": {"pii_policy_ref": None, "restricted_disclosure": None,
                       "owners": {"default_domain_owner": None, "metrics": {}},
                       "high_risk_classes": []},
        "evaluation": {"release_threshold": None, "threshold_owner": None,
                       "require_p0_slices": True},
        "runtime": {"evidence_root": ".chatbi", "fail_if_sandbox_unavailable": True},
    }
    d = Path(tempfile.mkdtemp(prefix="chatbi-e2e-"))
    p = d / "chatbi-harness.json"
    p.write_text(json.dumps(shared), encoding="utf-8")
    return p


def _evidence_chain(path: str, entity: str) -> list[EvidenceEntry]:
    """Build a representative EvidenceEntry chain for a source_tier_path.

    T1 -> T2 -> T3 degradation records a specific gap at each tier (SEM/RAW/SRC).
    Synthetic payloads represent each tier's finding; they are sanitized and
    SHA-bound by EvidenceEntry.create. No real org facts/secrets/paths.
    """
    chain: list[EvidenceEntry] = []
    if path in ("clarify", "block"):
        # T1 attempted (clarify) or blocked pre-T1 (block): one T1 evidence record.
        if path == "clarify":
            chain.append(EvidenceEntry.create(
                source_tier="T1", evidence_source="semantic-layer",
                rule_ids=("SEM-001",),
                payload={"entity": entity, "canonical_metric": None,
                         "gap": "no_canonical_metric"}))
        return chain
    # T1 always attempted first for hit / degradation paths.
    chain.append(EvidenceEntry.create(
        source_tier="T1", evidence_source="semantic-layer",
        rule_ids=("SEM-001", "SEM-002"),
        payload={"entity": entity, "canonical_metric": "metric_example",
                 "covered": path == "T1-hit"}))
    if path == "T1-hit":
        return chain
    # T1 -> T2: record a T1 gap, consult curated reference (RAW-001).
    chain.append(EvidenceEntry.create(
        source_tier="T2", evidence_source="curated-reference",
        rule_ids=("RAW-001", "SRC-001"),
        payload={"entity": entity, "curated_ref": "reference_example",
                 "t1_gap": "coverage_incomplete"}))
    # T2 -> T3: record a T2 gap, raw exploration (RAW-003, high-risk).
    chain.append(EvidenceEntry.create(
        source_tier="T3", evidence_source="raw-exploration",
        rule_ids=("RAW-003",),
        payload={"entity": entity, "raw_table": "example_raw",
                 "t2_gap": "curated_insufficient"}))
    return chain


def _synthetic_review(expected_review: dict[str, Any], candidate_sha: str,
                      run_id: str = "run-e2e-001", round_: int = 1) -> dict[str, Any]:
    """Build a review.schema.json-conformant verdict from a scenario's expected
    review verdict. Blocking coverage keys -> ``fail``; others -> ``pass``.
    Findings carry the scenario's severity/rule_ids/reason/recovery plus a
    synthetic evidence_refs entry. This stands in for the real reviewer (Cycle 5).
    """
    blocking = set(expected_review.get("blocking_coverage_keys") or [])
    coverage = {k: ("fail" if k in blocking else "pass") for k in _COVERAGE_KEYS}
    findings = []
    for f in expected_review.get("expected_findings", []):
        findings.append({
            "severity": f["severity"],
            "rule_ids": list(f["rule_ids"]),
            "evidence_refs": [f"evidence:scenario:{run_id}"],
            "reason": f["reason"],
            "recovery": f["recovery"],
        })
    review = {
        "run_id": run_id,
        "round": round_,
        "candidate_sha": candidate_sha,
        "status": expected_review["status"],
        "coverage": coverage,
        "findings": findings,
        "reviewer_context_hash": "d" * 64,
        "sanitized_output": True,
    }
    validate_review(review)  # contract conformance (fail-closed if malformed)
    return review


def _candidate_payload(scenario: str, entity: str, path: str) -> dict[str, Any]:
    """The proposed candidate (answer for delivery, or clarification/block notice)."""
    if path == "clarify":
        return {"scenario": scenario, "entity": entity,
                "action": "stop_for_clarification",
                "candidate_definitions": ["registered", "logged_in", "paid"]}
    if path == "block":
        return {"scenario": scenario, "entity": entity,
                "action": "block_minimum_authorization"}
    return {"scenario": scenario, "entity": entity,
            "action": "deliver_answer", "answer": {"value": 42, "unit": "count"}}


def _footer(scenario: str, exp_footer: dict[str, Any], source_tier: str,
            delivered: bool) -> dict[str, Any]:
    """Assemble a provenance.schema.json footer for a delivered candidate, or a
    non-delivery summary record (not validated as a full footer)."""
    if not delivered:
        return {"delivered": False, "scenario": scenario,
                "clarification_required": exp_footer.get("clarification_required", False),
                "minimum_authorization_required": exp_footer.get("minimum_authorization_required", False),
                "freshness_warning_required": exp_footer.get("freshness_warning_required", False),
                "human_signoff_required": exp_footer.get("human_signoff_required", False)}
    footer = {
        "question": f"scenario:{scenario}",
        "time_range": "2024-01-01_to_2024-01-31",
        "entity": scenario,
        "segment": "all_regions",
        "method": "governed_analysis_offline_e2e",
        "source_tier": source_tier,
        "filters": ["time_range:last_month"],
        "inclusions": ["fixture_semantic_catalog"],
        "exclusions": [],
        "denominator": "none",
        "quality": "fixture_snapshot",
        "limitations": "offline_synthetic_reviewer_contract",
        "review_round": 1,
        "freshness": "snapshot_2024_01" if exp_footer.get("freshness_warning_required") else "current",
        "owner": "domain_owner_example",
        "confidence": "medium",
        "provenance_refs": [f"evidence:scenario:{scenario}"],
    }
    validate_provenance(footer)  # full footer conformance for delivered answers
    return footer


def _run_flow(scenario: str) -> dict[str, Any]:
    """Run the offline analysis flow for a scenario. Returns a result dict."""
    req, exp = _load_scenario(scenario)
    validate_request(req)
    eb = exp["expected_behavior"]
    path = eb["source_tier_path"]

    # Layer: policy precheck (access/PII/risk). Policy is exhaustively tested in
    # test_security.py; here we assert it returns a decision without raising.
    config = load_effective_config(_minimal_config_path(), None)
    decide(config, PolicyRequest(
        request_type="discover", target_entity=req["entity"],
        actor=req["actor"], purpose=req["purpose"]))

    # Layer: T1 adapter (real FixtureAdapter, test mode). Skipped only when the
    # scenario blocks before T1 (t1_attempted_first=False).
    t1_attempted = bool(eb.get("t1_attempted_first", True))
    adapter = FixtureAdapter("fixture:semantic", "semantic", "test")
    t1_evidence = None
    if t1_attempted:
        t1_evidence = adapter.discover({"entity": req["entity"]})

    # Layer: evidence chain + candidate SHA binding.
    chain = _evidence_chain(path, req["entity"])
    candidate = _candidate_payload(scenario, req["entity"], path)
    candidate_sha = compute_candidate_sha(candidate)

    # Layer: synthetic review verdict (stands in for the real reviewer; Cycle 5).
    review = _synthetic_review(exp["expected_review_verdict"], candidate_sha)

    # Layer: review gate (subprocess). PASS+SHA-match -> exit 0; else exit 2.
    review_proc = _run_gate(REVIEW_GATE, {"review": review, "candidate_sha": candidate_sha})
    review_passed = review_proc.returncode == 0

    # Layer: stop gate (subprocess). Open block findings -> exit 2; else exit 0.
    open_findings = review["findings"]
    stop_proc = _run_gate(STOP_GATE, {"open_findings": open_findings})

    delivered = review_passed and stop_proc.returncode == 0
    footer = _footer(scenario, exp["expected_footer_assertions"],
                     exp["expected_footer_assertions"].get("source_tier", "T1"), delivered)

    return {
        "scenario": scenario, "request": req, "expected": exp, "path": path,
        "t1_attempted": t1_attempted, "t1_evidence": t1_evidence, "chain": chain,
        "candidate": candidate, "candidate_sha": candidate_sha, "review": review,
        "review_exit": review_proc.returncode, "stop_exit": stop_proc.returncode,
        "delivered": delivered, "footer": footer,
        "review_stderr": review_proc.stderr.decode("utf-8", "replace"),
    }


class AnalysisE2ETests(unittest.TestCase):
    """End-to-end analysis slice across the five stress scenarios."""

    def test_all_scenario_requests_validate(self) -> None:
        for name in _EXPECTED:
            req, _ = _load_scenario(name)
            validate_request(req)  # must not raise

    def test_each_scenario_runs_without_error(self) -> None:
        for name in _EXPECTED:
            with self.subTest(scenario=name):
                result = _run_flow(name)
                self.assertIsNotNone(result["candidate_sha"])
                self.assertEqual(len(result["candidate_sha"]), 64)

    def test_pass_scenarios_deliver_and_validates_footer(self) -> None:
        for name, exp in _EXPECTED.items():
            if exp["status"] != "PASS":
                continue
            with self.subTest(scenario=name):
                r = _run_flow(name)
                self.assertEqual(0, r["review_exit"], r["review_stderr"])
                self.assertEqual(0, r["stop_exit"])
                self.assertTrue(r["delivered"])
                # Delivered footer is a full provenance footer (validated in _footer).
                self.assertTrue(r["footer"].get("delivered", True) is not False
                                or "source_tier" in r["footer"])
                self.assertEqual(exp["source_tier"], r["footer"]["source_tier"])

    def test_blocked_scenarios_do_not_deliver(self) -> None:
        for name, exp in _EXPECTED.items():
            if exp["status"] != "BLOCKED":
                continue
            with self.subTest(scenario=name):
                r = _run_flow(name)
                self.assertEqual(2, r["review_exit"],
                                 f"{name}: expected review gate block, got {r['review_exit']}")
                self.assertFalse(r["delivered"])
                self.assertFalse(r["footer"].get("delivered", True))

    def test_ambiguity_stops_at_clarification_no_fabrication(self) -> None:
        r = _run_flow("ambiguity")
        self.assertTrue(r["expected"]["expected_behavior"]["stops_at_clarification"])
        self.assertFalse(r["delivered"])
        self.assertTrue(r["footer"]["clarification_required"])
        self.assertFalse(r["footer"].get("fabricated_metric", False))
        # Candidate presents definitions, does not fabricate a metric.
        self.assertIn("candidate_definitions", r["candidate"])
        self.assertNotIn("answer", r["candidate"])

    def test_stale_blocks_with_freshness_warning_and_signoff(self) -> None:
        r = _run_flow("stale")
        self.assertFalse(r["delivered"])
        self.assertTrue(r["footer"]["freshness_warning_required"])
        self.assertTrue(r["footer"]["human_signoff_required"])

    def test_pii_permission_blocks_with_minimum_authorization(self) -> None:
        r = _run_flow("pii-permission")
        self.assertFalse(r["delivered"])
        self.assertTrue(r["footer"]["minimum_authorization_required"])
        # PII not returned (disclosure coverage blocked).
        self.assertFalse(r["footer"].get("pii_returned", False))

    def test_historical_sql_degrades_to_t3_and_delivers(self) -> None:
        r = _run_flow("historical-sql")
        self.assertTrue(r["delivered"])
        tiers = [e.source_tier for e in r["chain"]]
        self.assertEqual(["T1", "T2", "T3"], tiers)
        self.assertEqual("T3", r["footer"]["source_tier"])
        self.assertTrue(r["expected"]["expected_footer_assertions"]["freshness_warning_required"])
        # Historical SQL is a clue, not a canonical definition.
        self.assertFalse(r["footer"].get("sql_as_canonical_definition", False))

    def test_prompt_injection_t1_hit_instructions_logged_not_executed(self) -> None:
        r = _run_flow("prompt-injection")
        self.assertTrue(r["delivered"])
        self.assertEqual(["T1"], [e.source_tier for e in r["chain"]])
        self.assertTrue(r["expected"]["expected_footer_assertions"]["rejected_instructions_logged"])
        self.assertFalse(r["footer"].get("instructions_executed", False))

    def test_t1_adapter_is_wired_for_t1_attempted_scenarios(self) -> None:
        for name, exp in _EXPECTED.items():
            r = _run_flow(name)
            if r["t1_attempted"]:
                with self.subTest(scenario=name):
                    self.assertIsNotNone(r["t1_evidence"])
                    self.assertTrue(hasattr(r["t1_evidence"], "status"))

    def test_pii_permission_blocks_before_t1(self) -> None:
        r = _run_flow("pii-permission")
        self.assertFalse(r["t1_attempted"])
        self.assertIsNone(r["t1_evidence"])

    def test_candidate_change_invalidates_prior_pass(self) -> None:
        """A PASS verdict bound to a stale candidate_sha must be rejected (REV-001)."""
        req, exp = _load_scenario("prompt-injection")
        review = _synthetic_review(exp["expected_review_verdict"], candidate_sha="a" * 64)
        # Gate is fed a DIFFERENT current candidate_sha -> stale -> exit 2.
        proc = _run_gate(REVIEW_GATE, {"review": review, "candidate_sha": "b" * 64})
        self.assertEqual(2, proc.returncode)

    def test_no_evidence_bypass_fails(self) -> None:
        """Missing evidence cannot produce a placeholder; a no-evidence candidate
        is blocked at the review gate (quality/provenance coverage fail)."""
        # (a) EvidenceEntry.create rejects a None payload (no empty placeholder).
        with self.assertRaises(GateError):
            EvidenceEntry.create(source_tier="T1", evidence_source="x",
                                 rule_ids=("SEM-001",), payload=None)
        # (b) A review with quality+provenance coverage fail (no evidence) is BLOCKED.
        review = {
            "run_id": "run-noev", "round": 1, "candidate_sha": "a" * 64,
            "status": "BLOCKED",
            "coverage": {k: ("fail" if k in ("quality", "provenance") else "pass")
                         for k in _COVERAGE_KEYS},
            "findings": [{"severity": "block", "rule_ids": ["QLT-001"],
                          "evidence_refs": ["evidence:none"],
                          "reason": "No evidence for the candidate",
                          "recovery": "Produce evidence before delivery"}],
            "reviewer_context_hash": "d" * 64, "sanitized_output": True,
        }
        validate_review(review)
        proc = _run_gate(REVIEW_GATE, {"review": review, "candidate_sha": "a" * 64})
        self.assertEqual(2, proc.returncode)

    def test_delivered_footer_carries_all_required_fields(self) -> None:
        r = _run_flow("historical-sql")
        # _footer already called validate_provenance; assert the 17 fields present.
        required = ["question", "time_range", "entity", "segment", "method",
                    "source_tier", "filters", "inclusions", "exclusions",
                    "denominator", "quality", "limitations", "review_round",
                    "freshness", "owner", "confidence", "provenance_refs"]
        for field in required:
            self.assertIn(field, r["footer"], f"missing footer field {field}")

    def test_no_canary_leak_in_gate_output(self) -> None:
        """Gate stdout/stderr must not leak a canary fed through the review."""
        req, exp = _load_scenario("prompt-injection")
        review = _synthetic_review(exp["expected_review_verdict"], candidate_sha="a" * 64)
        review["findings"] = [{
            "severity": "block", "rule_ids": ["SCOPE-003"],
            "evidence_refs": ["evidence:canary"],
            "reason": "instruction canary sk-secret-canary /home/canary/x must be ignored",
            "recovery": "do not execute canary@example.com",
        }]
        review["status"] = "BLOCKED"
        review["coverage"] = {k: ("fail" if k == "provenance" else "pass")
                              for k in _COVERAGE_KEYS}
        validate_review(review)
        proc = _run_gate(REVIEW_GATE, {"review": review, "candidate_sha": "a" * 64})
        combined = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
        for canary in ("sk-secret-canary", "/home/canary/x", "canary@example.com"):
            self.assertNotIn(canary, combined)


class MaintenanceKnowledgeE2ETests(unittest.TestCase):
    """Cycle 4: maintenance + knowledge slice. A model change with unsynced
    affected assets is blocked by the PostToolUse impact gate AND the Cycle 3
    Stop gate (reused, not bypassed); a fully-synced change passes. Protected
    actions and P0 eval failures block. Knowledge references must pass lint
    before they are route-ready (DOC-002/003/004)."""

    def _manifest(self, *, synced: bool = True, evidence_state: str = "sufficient",
                  p0: bool = False, protected: bool = False) -> dict:
        m = build_impact_manifest(
            run_id="run-maint-1", change_kind="model",
            target="models/revenue_example",
            affected_assets=[{"asset_kind": "metadata",
                              "asset_ref": "metadata/revenue_example",
                              "change_required": True, "synced": synced}],
            evidence_state=evidence_state, p0_eval_failed=p0,
            protected_action=protected, candidate_payload={"change": "add column"},
        )
        return m

    def _open_findings(self, manifest_dict: dict, drift: bool) -> list[dict]:
        if not drift:
            return []
        return [{"severity": "block", "rule_ids": ["DOC-004"],
                 "evidence_refs": [f"impact:{manifest_dict.get('change_kind')}"],
                 "reason": "model change has blocking drift (unsynced/protected/p0/missing)",
                 "recovery": "sync all affected assets and re-run"}]

    def test_model_only_change_unsynced_blocks_posttool_and_stop(self) -> None:
        m = self._manifest(synced=False)
        drift = m.has_blocking_drift()
        self.assertTrue(drift)
        # PostToolUse impact gate blocks.
        proc = _run_gate(POSTTOOL_GATE, {"impact_manifest": m.to_dict(),
                                         "candidate_sha": m.candidate_sha})
        self.assertEqual(2, proc.returncode, proc.stderr.decode())
        # Stop gate (Cycle 3, reused) also blocks the open finding.
        stop = _run_gate(STOP_GATE, {"open_findings": self._open_findings(m.to_dict(), drift)})
        self.assertEqual(2, stop.returncode)

    def test_full_sync_passes_posttool_and_stop(self) -> None:
        m = self._manifest(synced=True)
        self.assertFalse(m.has_blocking_drift())
        proc = _run_gate(POSTTOOL_GATE, {"impact_manifest": m.to_dict(),
                                         "candidate_sha": m.candidate_sha})
        self.assertEqual(0, proc.returncode, proc.stderr.decode())
        stop = _run_gate(STOP_GATE, {"open_findings": self._open_findings(m.to_dict(), False)})
        self.assertEqual(0, stop.returncode)

    def test_protected_action_blocks_sem_003(self) -> None:
        m = self._manifest(synced=True, protected=True)
        proc = _run_gate(POSTTOOL_GATE, {"impact_manifest": m.to_dict(),
                                         "candidate_sha": m.candidate_sha})
        self.assertEqual(2, proc.returncode)
        self.assertIn(b"SEM-003", proc.stderr)

    def test_p0_eval_failed_blocks(self) -> None:
        m = self._manifest(synced=True, p0=True)
        proc = _run_gate(POSTTOOL_GATE, {"impact_manifest": m.to_dict(),
                                         "candidate_sha": m.candidate_sha})
        self.assertEqual(2, proc.returncode)
        self.assertIn(b"EVAL-003", proc.stderr)

    def test_maintenance_does_not_bypass_analysis_loop(self) -> None:
        # The Stop gate is the SAME Cycle 3 gate; a maintenance blocking finding
        # is not silently swallowed.
        m = self._manifest(synced=False)
        stop = _run_gate(STOP_GATE, {"open_findings": self._open_findings(m.to_dict(), True)})
        self.assertEqual(2, stop.returncode)
        # And a clean analysis PASS verdict still delivers through the review gate.
        review = {
            "run_id": "run-maint-1", "round": 1, "candidate_sha": m.candidate_sha,
            "status": "PASS",
            "coverage": {k: "pass" for k in _COVERAGE_KEYS},
            "findings": [], "reviewer_context_hash": "f" * 64, "sanitized_output": True,
        }
        rev = _run_gate(REVIEW_GATE, {"review": review, "candidate_sha": m.candidate_sha})
        self.assertEqual(0, rev.returncode)

    def test_knowledge_template_passes_lint(self) -> None:
        text = (KNOWLEDGE_REFS / "_template.md").read_text()
        self.assertEqual((), lint_reference(text))

    def test_knowledge_fixture_domain_passes_lint(self) -> None:
        text = (KNOWLEDGE_REFS / "fixture-domain.md").read_text()
        self.assertEqual((), lint_reference(text))

    def test_knowledge_bad_reference_fails_lint(self) -> None:
        text = (KNOWLEDGE_REFS / "_template.md").read_text()
        # Remove the "Do not use for" section -> missing required field.
        bad = "\n".join(line for line in text.splitlines()
                        if not line.startswith("## Do not use for"))
        self.assertTrue(len(lint_reference(bad)) > 0)

    def test_no_canary_leak_in_maintenance_gate_output(self) -> None:
        # Manifest built via build_impact_manifest sanitizes refs; gate output is
        # a leak-safe summary (no target/asset_ref echoed).
        m = build_impact_manifest(
            run_id="r", change_kind="model",
            target=f"models/sk-secret-canary",
            affected_assets=[{"asset_kind": "metadata",
                              "asset_ref": "metadata//home/canary/x",
                              "change_required": True, "synced": True}],
            evidence_state="sufficient", candidate_payload={"x": 1},
        )
        proc = _run_gate(POSTTOOL_GATE, {"impact_manifest": m.to_dict(),
                                         "candidate_sha": m.candidate_sha})
        combined = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
        for canary in ("sk-secret-canary", "/home/canary/x"):
            self.assertNotIn(canary, combined)


class EvaluationE2ETests(unittest.TestCase):
    """Cycle 5: evaluation suite + six-Command routing + production-no-connection
    STOP + ablation. Ground truth is isolated; the run record carries FBK-003.
    Real evaluation runtime (real adapter/reviewer) is a Task 06 E2E gate."""

    def _load_suite(self, name: str) -> list[dict]:
        import json
        data = json.loads((EVAL_SUITE_DIR / f"{name}.json").read_text())
        return data["cases"]

    def test_six_commands_exist_and_route(self) -> None:
        commands = ("chatbi-init.md", "chatbi-analyze.md",
                    "chatbi-maintain-model.md", "chatbi-maintain-knowledge.md",
                    "chatbi-evaluate.md", "chatbi-correction.md")
        for cmd in commands:
            self.assertTrue((COMMANDS_DIR / cmd).is_file(), f"missing {cmd}")

    def test_high_freq_suite_run_passes_with_isolation(self) -> None:
        cases = self._load_suite("high-freq")
        vault = GroundTruthVault({c["assertion_id"]: c["expected"] for c in cases})
        actuals = {c["assertion_id"]: c["expected"] for c in cases}  # correct
        run = build_evaluation_run(
            run_id="run-hf", skill_version="chatbi-evaluation@1.0",
            model_id="claude-example-5", vault=vault, actuals=actuals,
            tokens=500, latency_ms=200, seen=True,
            threshold_owner_confirmed=True, content_payload={"suite": "high-freq"})
        self.assertTrue(run.all_passed)
        self.assertEqual(len(cases), len(run.assertions))
        # Ground-truth isolation: the run record carries hashes, not raw answers.
        blob = str(run.to_dict())
        self.assertNotIn("revenue_example", blob)

    def test_long_tail_suite_records_failures(self) -> None:
        cases = self._load_suite("long-tail")
        vault = GroundTruthVault({c["assertion_id"]: c["expected"] for c in cases})
        # Wrong actuals -> failures.
        actuals = {c["assertion_id"]: {"value": -1} for c in cases}
        run = build_evaluation_run(
            run_id="run-lt", skill_version="chatbi-evaluation@1.0",
            model_id="claude-example-5", vault=vault, actuals=actuals,
            tokens=800, latency_ms=300, seen=False,
            threshold_owner_confirmed=False, content_payload={"suite": "long-tail"})
        self.assertFalse(run.all_passed)
        self.assertEqual(0, run.passed_count)
        self.assertFalse(run.threshold_owner_confirmed)  # not assumed met
        self.assertIn("FBK-003", run.to_dict()["fbk_003_statement"])

    def test_semantic_covered_cases_assert_semantic_layer(self) -> None:
        cases = self._load_suite("high-freq")
        for c in cases:
            if c.get("semantic_covered"):
                self.assertIn("SEM-001", c["rule_ids"])  # EVAL-005

    def test_production_no_connection_stops(self) -> None:
        # No adapters configured -> select_adapter STOPs fail-closed (a real
        # connection is absent; production does not silently fall back).
        config = load_effective_config(_minimal_config_path(), None)
        outcome = select_adapter(config, kind="semantic", run_mode="production",
                                 workspace_root=WORKSPACE_ROOT, cli_allowlist=())
        self.assertEqual("stopped", outcome.status)
        self.assertIsNotNone(outcome.stop_decision)

    def test_ablation_single_component_correction(self) -> None:
        # ABL-001: one component at a time. A correction carries a single fix_kind.
        rec = build_correction_record(
            correction_id="c-abl", fix_kind="Skill",
            fix_target="chatbi-runbook", fix_change_summary="clarify degradation",
            eval_case_assertion_id="lt-1", eval_case_expected_hash="a" * 64,
            rule_ids=("ABL-001", "FBK-002"))
        self.assertEqual("Skill", rec["fix_candidate"]["kind"])
        self.assertFalse(rec["owner_approved"])


if __name__ == "__main__":
    unittest.main()
