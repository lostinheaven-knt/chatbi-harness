from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.gates import GateDecision, GateError, fail_closed  # noqa: E402


class GateDecisionTests(unittest.TestCase):
    def test_decision_rejects_an_unknown_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "status"):
            GateDecision(
                status="allow",
                rule_ids=("HOOK-004",),
                evidence_refs=("gate:test",),
                reason="Unsupported outcome",
                recovery="Use pass, warn, or block",
            )

    def test_unexpected_exception_becomes_a_sanitized_block(self) -> None:
        decision = fail_closed(
            ValueError("api_key=sk-should-not-leak at /Users/operator/private/file"),
            evidence_refs=("gate:contract-validation",),
        )

        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("gate:contract-validation",), decision.evidence_refs)
        self.assertIn("ValueError", decision.reason)
        self.assertNotIn("should-not-leak", decision.to_json())
        self.assertNotIn("/Users/operator", decision.to_json())

    def test_gate_error_exposes_only_its_block_decision(self) -> None:
        decision = GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("contract:domain-model",),
            reason="Domain contract is missing",
            recovery="Restore the governed domain model",
        )

        error = GateError(decision)

        self.assertIs(decision, error.decision)
        self.assertEqual(decision.to_json(), str(error))

    def test_public_output_redacts_paths_secrets_and_url_queries(self) -> None:
        decision = GateDecision.block(
            rule_ids=("SEC-003", "PORT-001"),
            evidence_refs=(
                "/Users/operator/private/model.sql",
                "https://example.test/run?token=top-secret&safe=1",
            ),
            reason="api_key=sk-live-secret failed at /private/tmp/customer.csv",
            recovery=r"Retry with password=hunter2 after C:\Users\Admin\secrets.txt",
        )

        rendered = decision.to_json()
        for sensitive in (
            "/Users/operator/private/model.sql",
            "top-secret",
            "sk-live-secret",
            "/private/tmp/customer.csv",
            "hunter2",
            r"C:\Users\Admin\secrets.txt",
        ):
            self.assertNotIn(sensitive, rendered)
        self.assertIn("[REDACTED_PATH]", rendered)
        self.assertIn("[REDACTED_SECRET]", rendered)
        self.assertIn("[REDACTED_QUERY]", rendered)

    def test_pass_and_warn_are_explicit_public_outcomes(self) -> None:
        passed = GateDecision.pass_(
            rule_ids=("HOOK-001",),
            evidence_refs=("contract:loaded",),
            reason="Contract is valid",
            recovery="No action required",
        )
        warned = GateDecision.warn(
            rule_ids=("HOOK-005",),
            evidence_refs=("evaluation:pending",),
            reason="Full evaluation is not a Hook responsibility",
            recovery="Run the affected evaluation command before release",
        )

        self.assertEqual("pass", passed.to_dict()["status"])
        self.assertEqual("warn", warned.to_dict()["status"])

    def test_block_decision_serializes_required_fields_stably(self) -> None:
        decision = GateDecision.block(
            rule_ids=("HOOK-004", "SCOPE-001", "HOOK-004"),
            evidence_refs=("workspace:config", "workspace:config"),
            reason="Configured root is unavailable",
            recovery="Bind one readable Warehouse Workspace",
        )

        expected = {
            "status": "block",
            "rule_ids": ["HOOK-004", "SCOPE-001"],
            "evidence_refs": ["workspace:config"],
            "reason": "Configured root is unavailable",
            "recovery": "Bind one readable Warehouse Workspace",
        }
        self.assertEqual(expected, decision.to_dict())
        self.assertEqual(
            json.dumps(expected, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            decision.to_json(),
        )


if __name__ == "__main__":
    unittest.main()
