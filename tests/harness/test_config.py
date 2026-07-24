from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / "harness" / ".claude" / "lib"
CONFIG_FIXTURES = WORKSPACE_ROOT / "harness" / ".claude" / "fixtures" / "config"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.config import load_effective_config  # noqa: E402
from chatbi_harness.gates import GateError  # noqa: E402


PROTECTED_ACTIONS = [
    "approve_metric",
    "change_access_policy",
    "production_publish",
    "destructive_migration",
]


def minimal_shared_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "workspace": {
            "id": "warehouse",
            "root": ".",
            "allow_candidate_writes": True,
            "protected_actions": PROTECTED_ACTIONS,
        },
        "business_codebases": {},
        "adapters": {
            "semantic": [],
            "query": [],
            "fixture_enabled": False,
        },
        "governance": {
            "pii_policy_ref": None,
            "restricted_disclosure": None,
            "owners": {"default_domain_owner": None, "metrics": {}},
            "high_risk_classes": [],
        },
        "evaluation": {
            "release_threshold": None,
            "threshold_owner": None,
            "require_p0_slices": True,
        },
        "runtime": {
            "evidence_root": ".chatbi",
            "fail_if_sandbox_unavailable": True,
        },
    }


class EffectiveConfigTests(unittest.TestCase):
    def test_checked_in_examples_and_config_fixtures_are_executable_contracts(self) -> None:
        shared = load_effective_config(
            WORKSPACE_ROOT / "harness" / ".claude" / "chatbi-harness.json"
        )
        example = load_effective_config(
            WORKSPACE_ROOT / "harness" / ".claude" / "chatbi-harness.example.json",
            WORKSPACE_ROOT / "harness" / ".claude" / "chatbi-harness.local.example.json",
        )

        self.assertEqual(1, shared["schema_version"])
        self.assertEqual("warehouse", example["workspace"]["id"])
        self.assertEqual(None, example["evaluation"]["release_threshold"])
        self.assertEqual(None, example["evaluation"]["threshold_owner"])

        valid_fixture = load_effective_config(CONFIG_FIXTURES / "valid-minimal.json")
        self.assertEqual(1, valid_fixture["schema_version"])
        invalid_fixtures = (
            "missing-field.json",
            "duplicate-key.json",
            "unknown-field.json",
            "invalid-alias.json",
            "absolute-shared-path.json",
            "embedded-secret.json",
            "owner-threshold-conflict.json",
            "production-fixture-fallback.json",
        )
        for fixture_name in invalid_fixtures:
            with self.subTest(fixture=fixture_name):
                with self.assertRaises(GateError):
                    load_effective_config(CONFIG_FIXTURES / fixture_name)

    def test_missing_shared_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "missing.json"

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("config:shared",), decision.evidence_refs)
        self.assertIn("unavailable", decision.reason.lower())
        self.assertNotIn(str(shared_path), decision.to_json())
        self.assertIn("readable", decision.recovery.lower())

    def test_top_level_config_must_be_a_json_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text("[]", encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:shared",), decision.evidence_refs)
        self.assertIn("object", decision.reason.lower())
        self.assertTrue(decision.recovery)

    def test_non_finite_json_numbers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            for non_finite in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(non_finite=repr(non_finite)):
                    shared = minimal_shared_config()
                    shared["evaluation"]["release_threshold"] = non_finite
                    shared["evaluation"]["threshold_owner"] = (
                        "role:synthetic-owner"
                    )
                    shared_path.write_text(json.dumps(shared), encoding="utf-8")

                    with self.assertRaises(GateError) as caught:
                        load_effective_config(shared_path)

                    decision = caught.exception.decision
                    self.assertEqual("block", decision.status)
                    self.assertEqual(("HOOK-004",), decision.rule_ids)
                    self.assertEqual(("config:shared",), decision.evidence_refs)
                    self.assertIn("finite", decision.reason.lower())
                    self.assertNotIn("NaN", decision.to_json())
                    self.assertNotIn("Infinity", decision.to_json())
                    self.assertTrue(decision.recovery)

    def test_invalid_codebase_alias_fails_closed(self) -> None:
        shared = minimal_shared_config()
        shared["business_codebases"] = {
            "Billing App": {
                "description": "Synthetic source",
                "path_ref": "shared_root",
                "read_mode": "adapter",
                "git_history": "metadata_only",
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("config:schema",), decision.evidence_refs)
        self.assertIn("alias", decision.reason.lower())
        self.assertIn("lowercase", decision.recovery.lower())

    def test_codebase_path_reference_must_be_unique(self) -> None:
        shared = minimal_shared_config()
        shared["business_codebases"] = {
            alias: {
                "description": "Synthetic source",
                "path_ref": "shared_root",
                "read_mode": "adapter",
                "git_history": "metadata_only",
            }
            for alias in ("billing_app", "orders_app")
        }
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("SCOPE-001", "PORT-001", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:business-codebases",), decision.evidence_refs)
        self.assertIn("path_ref", decision.reason)
        self.assertIn("unique", decision.recovery.lower())

    def test_codebase_shape_and_read_mode_are_schema_validated(self) -> None:
        shared = minimal_shared_config()
        shared["business_codebases"] = {
            "billing_app": {
                "description": "Synthetic source",
                "path_ref": "billing_app_root",
                "read_mode": "execute",
                "git_history": "metadata_only",
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:schema",), decision.evidence_refs)
        self.assertIn("read_mode", decision.reason)
        self.assertIn("adapter", decision.reason)
        self.assertTrue(decision.recovery)

    def test_shared_config_rejects_machine_paths_and_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsafe_values = (
                str(root / "machine-bound" / "billing-app"),
                "root=/Users/example/private",
                "root=C:/Users/example/private",
                r"root=C:\Users\example\private",
                r"root=\\server\share\private",
                "api_key=sk-super-secret-canary",
            )
            for unsafe_value in unsafe_values:
                with self.subTest(kind=unsafe_value.split("=")[0]):
                    shared = minimal_shared_config()
                    shared["business_codebases"] = {
                        "billing_app": {
                            "description": unsafe_value,
                            "path_ref": "billing_app_root",
                            "read_mode": "adapter",
                            "git_history": "metadata_only",
                        }
                    }
                    shared_path = root / "chatbi-harness.json"
                    shared_path.write_text(json.dumps(shared), encoding="utf-8")

                    with self.assertRaises(GateError) as caught:
                        load_effective_config(shared_path)

                    decision = caught.exception.decision
                    self.assertEqual("block", decision.status)
                    self.assertEqual(
                        ("SEC-003", "PORT-001", "HOOK-004"), decision.rule_ids
                    )
                    self.assertEqual(("config:shared",), decision.evidence_refs)
                    self.assertIn("unsafe", decision.reason.lower())
                    self.assertNotIn(unsafe_value, decision.to_json())
                    self.assertTrue(decision.recovery)

    def test_shared_config_rejects_forward_slash_unc_paths(self) -> None:
        unsafe_value = "root=//server/share/repo"
        shared = minimal_shared_config()
        shared["business_codebases"] = {
            "source_app": {
                "description": unsafe_value,
                "path_ref": "source_root",
                "read_mode": "adapter",
                "git_history": "metadata_only",
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:shared",), decision.evidence_refs)
        self.assertNotIn(unsafe_value, decision.to_json())
        self.assertIn("machine path", decision.reason.lower())

    def test_shared_config_rejects_file_uri_without_rejecting_https(self) -> None:
        unsafe_value = "file:///Users/example/private/repo"
        shared = minimal_shared_config()
        shared["business_codebases"] = {
            "source_app": {
                "description": unsafe_value,
                "path_ref": "source_root",
                "read_mode": "adapter",
                "git_history": "metadata_only",
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_path = root / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

            shared["business_codebases"]["source_app"]["description"] = (
                "https://example.test/docs"
            )
            shared_path.write_text(json.dumps(shared), encoding="utf-8")
            effective = load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:shared",), decision.evidence_refs)
        self.assertNotIn(unsafe_value, decision.to_json())
        self.assertEqual(
            "https://example.test/docs",
            effective["business_codebases"]["source_app"]["description"],
        )

    def test_missing_required_nested_field_fails_closed(self) -> None:
        shared = minimal_shared_config()
        del shared["workspace"]["id"]
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:schema",), decision.evidence_refs)
        self.assertIn("workspace", decision.reason)
        self.assertIn("id", decision.reason)
        self.assertTrue(decision.recovery)

    def test_schema_version_other_than_one_fails_closed(self) -> None:
        shared = minimal_shared_config()
        shared["schema_version"] = 2
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:schema",), decision.evidence_refs)
        self.assertIn("schema_version", decision.reason)
        self.assertIn("1", decision.reason)
        self.assertTrue(decision.recovery)

    def test_unknown_shared_field_fails_closed_against_declared_schema(self) -> None:
        shared = minimal_shared_config()
        shared["surprise"] = True
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("config:schema",), decision.evidence_refs)
        self.assertIn("surprise", decision.reason)
        self.assertIn("remove", decision.recovery.lower())

    def test_config_larger_than_256_kib_fails_closed_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_bytes(b" " * 262_145)

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("config:shared",), decision.evidence_refs)
        self.assertIn("256", decision.reason)
        self.assertIn("smaller", decision.recovery.lower())

    def test_malformed_json_fails_closed_without_parser_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text('{"schema_version":', encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("config:shared",), decision.evidence_refs)
        self.assertIn("json", decision.reason.lower())
        self.assertNotIn(str(shared_path), decision.to_json())
        self.assertTrue(decision.recovery)

    def test_non_utf8_config_fails_closed_without_exposing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_bytes(b'{"schema_version":1,"value":"\xff"}')

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("config:shared",), decision.evidence_refs)
        self.assertIn("utf-8", decision.reason.lower())
        self.assertNotIn("\\xff", decision.to_json())
        self.assertTrue(decision.recovery)

    def test_duplicate_json_key_fails_closed_with_gate_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(
                '{"schema_version":1,"schema_version":1}',
                encoding="utf-8",
            )

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("HOOK-004",), decision.rule_ids)
        self.assertEqual(("config:shared",), decision.evidence_refs)
        self.assertIn("duplicate", decision.reason.lower())
        self.assertIn("unique", decision.recovery.lower())

    def test_valid_shared_config_loads_as_deterministic_read_only_effective_config(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(
                json.dumps(minimal_shared_config()),
                encoding="utf-8",
            )

            first = load_effective_config(shared_path)
            second = load_effective_config(shared_path)

        self.assertEqual("warehouse", first["workspace"]["id"])
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual({}, first.to_dict()["path_bindings"])
        with self.assertRaises(TypeError):
            first["workspace"]["id"] = "changed"

    def test_optional_local_bindings_merge_without_mutating_shared_policy(self) -> None:
        shared = minimal_shared_config()
        shared["business_codebases"] = {
            "billing_app": {
                "description": "Synthetic billing event producer",
                "path_ref": "billing_app_root",
                "read_mode": "adapter",
                "git_history": "metadata_only",
            }
        }
        local = {
            "path_bindings": {"billing_app_root": ""},
            "cli_adapters": {
                "semantic": {
                    "argv": ["approved-semantic-cli", "query", "--json"],
                    "credential_env_names": ["SEMANTIC_TOKEN"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local["path_bindings"]["billing_app_root"] = str(root / "billing-app")
            shared_path = root / "chatbi-harness.json"
            local_path = root / "chatbi-harness.local.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")
            local_path.write_text(json.dumps(local), encoding="utf-8")

            effective = load_effective_config(shared_path, local_path)

        self.assertEqual(
            local["path_bindings"],
            effective.to_dict()["path_bindings"],
        )
        self.assertEqual(local["cli_adapters"], effective.to_dict()["cli_adapters"])
        self.assertEqual(PROTECTED_ACTIONS, effective.to_dict()["workspace"]["protected_actions"])

    def test_local_config_cannot_override_shared_or_protected_policy(self) -> None:
        local = {
            "path_bindings": {},
            "cli_adapters": {},
            "workspace": {"protected_actions": []},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_path = root / "chatbi-harness.json"
            local_path = root / "chatbi-harness.local.json"
            shared_path.write_text(
                json.dumps(minimal_shared_config()), encoding="utf-8"
            )
            local_path.write_text(json.dumps(local), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path, local_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("SEM-003", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:local",), decision.evidence_refs)
        self.assertIn("local", decision.reason.lower())
        self.assertIn("path_bindings", decision.recovery)
        self.assertIn("cli_adapters", decision.recovery)

    def test_shared_config_rejects_local_only_fields_instead_of_dropping_them(self) -> None:
        shared = minimal_shared_config()
        shared["path_bindings"] = {}
        shared["cli_adapters"] = {}
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("PORT-001", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:shared-layer",), decision.evidence_refs)
        self.assertIn("cli_adapters, path_bindings", decision.reason)
        self.assertIn("local", decision.recovery.lower())

    def test_local_config_accepts_env_names_but_rejects_secret_values(self) -> None:
        local = {
            "path_bindings": {},
            "cli_adapters": {
                "semantic": {
                    "argv": ["approved-semantic-cli", "--token=super-secret-canary"],
                    "credential_env_names": ["SEMANTIC_TOKEN"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_path = root / "chatbi-harness.json"
            local_path = root / "chatbi-harness.local.json"
            shared_path.write_text(
                json.dumps(minimal_shared_config()), encoding="utf-8"
            )
            local_path.write_text(json.dumps(local), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path, local_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("SEC-003", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:local",), decision.evidence_refs)
        self.assertIn("secret", decision.reason.lower())
        self.assertNotIn("super-secret-canary", decision.to_json())
        self.assertIn("environment variable", decision.recovery.lower())

    def test_local_argv_cannot_split_secret_flag_from_its_value(self) -> None:
        local = {
            "path_bindings": {},
            "cli_adapters": {
                "semantic": {
                    "argv": [
                        "approved-semantic-cli",
                        "--token",
                        "literal-secret-canary",
                    ],
                    "credential_env_names": ["SEMANTIC_TOKEN"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_path = root / "chatbi-harness.json"
            local_path = root / "chatbi-harness.local.json"
            shared_path.write_text(
                json.dumps(minimal_shared_config()), encoding="utf-8"
            )
            local_path.write_text(json.dumps(local), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path, local_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("SEC-003", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:local",), decision.evidence_refs)
        self.assertNotIn("literal-secret-canary", decision.to_json())
        self.assertIn("environment variable", decision.recovery.lower())

    def test_local_argv_rejects_credential_file_flags(self) -> None:
        unsafe_argvs = (
            ["approved", "--token-file", "/private/credential"],
            ["approved", "--api-key_file=/private/credential"],
            ["approved", "--password-file", "literal-secret-canary"],
            ["approved", "--secret_file=/private/credential"],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_path = root / "chatbi-harness.json"
            local_path = root / "chatbi-harness.local.json"
            shared_path.write_text(
                json.dumps(minimal_shared_config()), encoding="utf-8"
            )
            for argv in unsafe_argvs:
                with self.subTest(flag=argv[1]):
                    local = {
                        "path_bindings": {},
                        "cli_adapters": {
                            "semantic": {
                                "argv": argv,
                                "credential_env_names": ["SEMANTIC_TOKEN"],
                            }
                        },
                    }
                    local_path.write_text(json.dumps(local), encoding="utf-8")

                    with self.assertRaises(GateError) as caught:
                        load_effective_config(shared_path, local_path)

                    decision = caught.exception.decision
                    self.assertEqual("block", decision.status)
                    self.assertEqual(("config:local",), decision.evidence_refs)
                    for sensitive_part in argv[1:]:
                        self.assertNotIn(sensitive_part, decision.to_json())
                    self.assertIn("environment variable", decision.recovery.lower())

    def test_local_credential_environment_names_follow_declared_pattern(self) -> None:
        local = {
            "path_bindings": {},
            "cli_adapters": {
                "semantic": {
                    "argv": ["approved-semantic-cli"],
                    "credential_env_names": ["semantic-token"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_path = root / "chatbi-harness.json"
            local_path = root / "chatbi-harness.local.json"
            shared_path.write_text(
                json.dumps(minimal_shared_config()), encoding="utf-8"
            )
            local_path.write_text(json.dumps(local), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path, local_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:schema",), decision.evidence_refs)
        self.assertIn("credential_env_names", decision.reason)
        self.assertIn("pattern", decision.reason.lower())
        self.assertTrue(decision.recovery)

    def test_local_path_binding_must_be_absolute_and_declared(self) -> None:
        shared = minimal_shared_config()
        shared["business_codebases"] = {
            "billing_app": {
                "description": "Synthetic source",
                "path_ref": "billing_app_root",
                "read_mode": "adapter",
                "git_history": "metadata_only",
            }
        }
        local = {
            "path_bindings": {"unknown_root": "relative/path"},
            "cli_adapters": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_path = root / "chatbi-harness.json"
            local_path = root / "chatbi-harness.local.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")
            local_path.write_text(json.dumps(local), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path, local_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("SCOPE-001", "PORT-001", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:path-bindings",), decision.evidence_refs)
        self.assertIn("unknown_root", decision.reason)
        self.assertIn("absolute", decision.recovery.lower())
        self.assertIn("declared", decision.recovery.lower())

    def test_declared_local_path_binding_cannot_be_relative(self) -> None:
        shared = minimal_shared_config()
        shared["business_codebases"] = {
            "billing_app": {
                "description": "Synthetic source",
                "path_ref": "billing_app_root",
                "read_mode": "adapter",
                "git_history": "metadata_only",
            }
        }
        local = {
            "path_bindings": {"billing_app_root": "relative/path"},
            "cli_adapters": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared_path = root / "chatbi-harness.json"
            local_path = root / "chatbi-harness.local.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")
            local_path.write_text(json.dumps(local), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path, local_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:path-bindings",), decision.evidence_refs)
        self.assertNotIn("relative/path", decision.to_json())
        self.assertIn("absolute", decision.recovery.lower())

    def test_all_protected_actions_are_mandatory(self) -> None:
        shared = minimal_shared_config()
        shared["workspace"]["protected_actions"] = PROTECTED_ACTIONS[:-1]
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("SEM-003", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:protected-actions",), decision.evidence_refs)
        self.assertIn("destructive_migration", decision.reason)
        self.assertIn("restore", decision.recovery.lower())

    def test_sandbox_failure_policy_cannot_be_disabled(self) -> None:
        shared = minimal_shared_config()
        shared["runtime"]["fail_if_sandbox_unavailable"] = False
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("SEC-001", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:sandbox-policy",), decision.evidence_refs)
        self.assertIn("sandbox", decision.reason.lower())
        self.assertIn("true", decision.recovery.lower())

    def test_release_threshold_requires_an_explicit_owner(self) -> None:
        shared = minimal_shared_config()
        shared["evaluation"]["release_threshold"] = 0.9
        shared["evaluation"]["threshold_owner"] = None
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("EVAL-004", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:evaluation",), decision.evidence_refs)
        self.assertIn("threshold", decision.reason.lower())
        self.assertIn("owner", decision.reason.lower())
        self.assertIn("owner", decision.recovery.lower())

    def test_release_threshold_rejects_a_blank_owner(self) -> None:
        shared = minimal_shared_config()
        shared["evaluation"]["release_threshold"] = 0.9
        shared["evaluation"]["threshold_owner"] = "   "
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("EVAL-004", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:evaluation",), decision.evidence_refs)
        self.assertIn("owner", decision.reason.lower())
        self.assertIn("owner", decision.recovery.lower())

    def test_release_threshold_honors_declared_numeric_minimum(self) -> None:
        shared = minimal_shared_config()
        shared["evaluation"]["release_threshold"] = -0.01
        shared["evaluation"]["threshold_owner"] = "role:synthetic-owner"
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:schema",), decision.evidence_refs)
        self.assertIn("release_threshold", decision.reason)
        self.assertIn("minimum", decision.reason.lower())
        self.assertTrue(decision.recovery)

    def test_fixture_enabled_flag_is_required_by_schema(self) -> None:
        shared = minimal_shared_config()
        del shared["adapters"]["fixture_enabled"]
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("config:schema",), decision.evidence_refs)
        self.assertIn("fixture_enabled", decision.reason)
        self.assertTrue(decision.recovery)

    def test_fixture_mode_cannot_be_a_production_adapter_fallback(self) -> None:
        shared = minimal_shared_config()
        shared["adapters"] = {
            "semantic": ["managed:semantic", "fixture:semantic"],
            "query": ["cli:query"],
            "fixture_enabled": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            shared_path = Path(directory) / "chatbi-harness.json"
            shared_path.write_text(json.dumps(shared), encoding="utf-8")

            with self.assertRaises(GateError) as caught:
                load_effective_config(shared_path)

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("PORT-001", "HOOK-004"), decision.rule_ids)
        self.assertEqual(("config:fixture-mode",), decision.evidence_refs)
        self.assertIn("fixture", decision.reason.lower())
        self.assertIn("fallback", decision.reason.lower())
        self.assertIn("isolate", decision.recovery.lower())


if __name__ == "__main__":
    unittest.main()
