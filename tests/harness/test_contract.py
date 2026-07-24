from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.gates import validate_domain_contract  # noqa: E402


CONTRACT_ARTIFACTS = (
    "CLAUDE.md",
    "CONTEXT.md",
    ".claude/rules/00-domain-contract.md",
    ".claude/rules/10-security.md",
    ".claude/rules/20-completion.md",
)
RULE_ID = re.compile(
    r"\b(?:SCOPE|SEC|REQ|SEM|RAW|SRC|DOC|PORT|QLT|REV|ANS|EVAL|ABL|FBK|HOOK)"
    r"-\d{3}\b"
)


def write_minimal_contract(root: Path, artifact_text: str = "Rules: HOOK-004") -> None:
    domain_model = root / "docs" / "chatbi-harness-domain-model.md"
    domain_model.parent.mkdir(parents=True, exist_ok=True)
    domain_model.write_text("Rules: HOOK-004, PORT-001\n", encoding="utf-8")
    for relative_path in CONTRACT_ARTIFACTS:
        artifact = root / relative_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(artifact_text + "\n", encoding="utf-8")


class DomainContractTests(unittest.TestCase):
    def test_checked_in_contract_is_valid(self) -> None:
        decision = validate_domain_contract(WORKSPACE_ROOT)

        self.assertEqual("pass", decision.status, decision.to_json())
        self.assertEqual(("HOOK-004",), decision.rule_ids)

    def test_checked_in_contract_covers_governed_rules_and_root_responsibilities(self) -> None:
        domain_rule_ids = set(
            RULE_ID.findall(
                (WORKSPACE_ROOT / "docs/chatbi-harness-domain-model.md").read_text(
                    encoding="utf-8"
                )
            )
        )
        contract_rule_ids: set[str] = set()
        for relative_path in CONTRACT_ARTIFACTS:
            contract_rule_ids.update(
                RULE_ID.findall(
                    (WORKSPACE_ROOT / relative_path).read_text(encoding="utf-8")
                )
            )

        root_contract = (WORKSPACE_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        required_routes = (
            "/chatbi-init",
            "/chatbi-analyze",
            "/chatbi-maintain-model",
            "/chatbi-maintain-knowledge",
            "/chatbi-evaluate",
            "/chatbi-correction",
        )

        self.assertEqual(46, len(domain_rule_ids))
        self.assertEqual(domain_rule_ids, contract_rule_ids)
        self.assertLessEqual(len(root_contract.splitlines()), 200)
        self.assertIn("docs/chatbi-harness-domain-model.md", root_contract)
        self.assertIn("Humans retain responsibility", root_contract)
        self.assertIn("evidence only", root_contract)
        self.assertIn("four-layer stack", root_contract)
        self.assertIn("T1", root_contract)
        self.assertIn("T2", root_contract)
        self.assertIn("T3", root_contract)
        self.assertIn("independent", root_contract)
        self.assertIn("provenance", root_contract.lower())
        self.assertIn("model or semantic change", root_contract)
        self.assertIn("do not claim this prose enforces it", root_contract)
        for route in required_routes:
            self.assertIn(route, root_contract)

    def test_unknown_rule_reference_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_minimal_contract(root, "Rules: HOOK-004, SCOPE-999")

            decision = validate_domain_contract(root)

        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertIn("SCOPE-999", decision.reason)
        self.assertIn("domain model", decision.recovery.lower())

    def test_missing_governed_rule_coverage_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_minimal_contract(root)
            (root / "docs/chatbi-harness-domain-model.md").write_text(
                "Rules: SEC-003, HOOK-004, PORT-001\n"
                "Unsafe source: api_key=super-secret-canary "
                "at /Users/operator/private/model.sql\n",
                encoding="utf-8",
            )

            decision = validate_domain_contract(root)

        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("contract:rule-coverage",), decision.evidence_refs)
        self.assertIn("PORT-001, SEC-003", decision.reason)
        self.assertIn("contract", decision.recovery.lower())
        self.assertNotIn("super-secret-canary", decision.to_json())
        self.assertNotIn("/Users/operator", decision.to_json())

    def test_root_contract_over_line_budget_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_minimal_contract(root)
            (root / "CLAUDE.md").write_text(
                "Rules: HOOK-004\n" + "contract line\n" * 200,
                encoding="utf-8",
            )

            decision = validate_domain_contract(root)

        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("contract:CLAUDE.md",), decision.evidence_refs)
        self.assertIn("200", decision.reason)
        self.assertIn("rules", decision.recovery.lower())

    def test_contract_with_machine_path_or_secret_fails_closed(self) -> None:
        unsafe_values = (
            "/Users/operator/private/model.sql",
            "api_key=super-secret-canary",
            "https://warehouse.example/query?token=secret-canary",
        )
        for unsafe_value in unsafe_values:
            with self.subTest(unsafe_value=unsafe_value):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    write_minimal_contract(
                        root,
                        f"Rules: HOOK-004\nUnsafe: {unsafe_value}",
                    )

                    decision = validate_domain_contract(root)

                self.assertEqual("block", decision.status)
                self.assertEqual(("HOOK-004",), decision.rule_ids)
                self.assertNotIn(unsafe_value, decision.to_json())
                self.assertIn("sensitive", decision.reason.lower())
                self.assertTrue(decision.recovery)

    def test_missing_domain_model_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision = validate_domain_contract(Path(directory))

        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("contract:domain-model",), decision.evidence_refs)
        self.assertIn("missing", decision.reason.lower())
        self.assertTrue(decision.recovery)


if __name__ == "__main__":
    unittest.main()
