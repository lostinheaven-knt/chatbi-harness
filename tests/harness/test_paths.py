from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import traceback
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.config import load_effective_config  # noqa: E402
from chatbi_harness.gates import GateError  # noqa: E402
from chatbi_harness.paths import resolve_path_reference  # noqa: E402


PROTECTED_ACTIONS = [
    "approve_metric",
    "change_access_policy",
    "production_publish",
    "destructive_migration",
]
PATH_RULE_IDS = ("SCOPE-001", "PORT-001", "HOOK-004")


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def load_test_config(
    root: Path,
    *,
    codebases: dict[str, dict[str, str]] | None = None,
    path_bindings: dict[str, str] | None = None,
):
    shared = {
        "schema_version": 1,
        "workspace": {
            "id": "warehouse",
            "root": ".",
            "allow_candidate_writes": True,
            "protected_actions": PROTECTED_ACTIONS,
        },
        "business_codebases": codebases or {},
        "adapters": {"semantic": [], "query": [], "fixture_enabled": False},
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
    shared_path = root / "shared.json"
    local_path = root / "local.json"
    shared_path.write_text(json.dumps(shared), encoding="utf-8")
    local_path.write_text(
        json.dumps({"path_bindings": path_bindings or {}, "cli_adapters": {}}),
        encoding="utf-8",
    )
    return load_effective_config(shared_path, local_path)


class PathIdentityTests(unittest.TestCase):
    def assert_path_error_contract(
        self,
        error: GateError,
        *,
        evidence: str,
        recovery_fragment: str,
        forbidden: tuple[str, ...] = (),
    ) -> None:
        decision = error.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(PATH_RULE_IDS, decision.rule_ids)
        self.assertEqual((evidence,), decision.evidence_refs)
        self.assertIn(recovery_fragment.lower(), decision.recovery.lower())
        rendered = "".join(traceback.format_exception(error))
        for value in forbidden:
            self.assertNotIn(value, rendered)

    def test_absolute_target_is_rejected_without_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            outside = root / "secret-canary.txt"
            workspace.mkdir()
            outside.write_text("secret", encoding="utf-8")
            config = load_test_config(root)

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="warehouse",
                        target=str(outside),
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("path:warehouse:target:absolute",), decision.evidence_refs)
        self.assertIn("absolute", decision.reason.lower())
        self.assertIn("relative", decision.recovery.lower())
        self.assertNotIn(str(outside), decision.to_json())
        self.assertNotIn("secret-canary", decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:warehouse:target:absolute",
            recovery_fragment="relative",
            forbidden=(str(root), str(outside), "secret-canary"),
        )

    def test_parent_traversal_is_rejected_even_when_it_returns_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            (workspace / "nested").mkdir(parents=True)
            (workspace / "model.sql").write_text("select 1", encoding="utf-8")
            config = load_test_config(root)

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="warehouse",
                        target="nested/../model.sql",
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(
            ("path:warehouse:nested/../model.sql:traversal",),
            decision.evidence_refs,
        )
        self.assertIn("parent traversal", decision.reason.lower())
        self.assertIn("without '..'", decision.recovery)
        self.assertNotIn(str(workspace), decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:warehouse:nested/../model.sql:traversal",
            recovery_fragment="without '..'",
            forbidden=(str(root), str(workspace)),
        )

    def test_missing_target_fails_closed_with_relative_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            config = load_test_config(root)

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="warehouse",
                        target="models/missing.sql",
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(
            ("path:warehouse:models/missing.sql:missing",),
            decision.evidence_refs,
        )
        self.assertIn("does not exist", decision.reason.lower())
        self.assertIn("existing target", decision.recovery.lower())
        self.assertNotIn(str(workspace), decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:warehouse:models/missing.sql:missing",
            recovery_fragment="existing target",
            forbidden=(str(root), str(workspace)),
        )

    def test_internal_symlink_cannot_escape_the_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            outside = root / "outside-secret-canary.txt"
            link = workspace / "linked.txt"
            workspace.mkdir()
            outside.write_text("secret", encoding="utf-8")
            link.symlink_to(outside)
            self.assertTrue(link.is_symlink())
            config = load_test_config(root)

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="warehouse",
                        target="linked.txt",
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(
            ("path:warehouse:linked.txt:symlink-escape",), decision.evidence_refs
        )
        self.assertIn("symlink", decision.reason.lower())
        self.assertIn("outside", decision.reason.lower())
        self.assertIn("real target", decision.recovery.lower())
        self.assertNotIn(str(outside), decision.to_json())
        self.assertNotIn("secret-canary", decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:warehouse:linked.txt:symlink-escape",
            recovery_fragment="real target",
            forbidden=(str(root), str(outside), "secret-canary"),
        )

    def test_internal_symlink_is_rejected_even_when_target_stays_inside(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            real_target = workspace / "real.txt"
            link = workspace / "linked.txt"
            workspace.mkdir()
            real_target.write_text("safe", encoding="utf-8")
            link.symlink_to(real_target)
            self.assertTrue(link.is_symlink())
            config = load_test_config(root)

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="warehouse",
                        target="linked.txt",
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("path:warehouse:linked.txt:symlink",), decision.evidence_refs)
        self.assertIn("symlink", decision.reason.lower())
        self.assertIn("real path", decision.recovery.lower())
        self.assertNotIn(str(real_target), decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:warehouse:linked.txt:symlink",
            recovery_fragment="real path",
            forbidden=(str(root), str(real_target)),
        )

    def test_broken_symlink_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            broken = workspace / "broken-link"
            workspace.mkdir()
            broken.symlink_to(workspace / "missing-secret-canary")
            self.assertTrue(broken.is_symlink())
            self.assertFalse(broken.exists())
            config = load_test_config(root)

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="warehouse",
                        target="broken-link",
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(
            ("path:warehouse:broken-link:broken-symlink",),
            decision.evidence_refs,
        )
        self.assertIn("broken symlink", decision.reason.lower())
        self.assertIn("replace", decision.recovery.lower())
        self.assertNotIn(str(broken), decision.to_json())
        self.assertNotIn("secret-canary", decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:warehouse:broken-link:broken-symlink",
            recovery_fragment="replace",
            forbidden=(str(root), str(broken), "secret-canary"),
        )

    def test_directory_reference_rejects_nested_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            models = workspace / "models"
            outside = root / "outside-secret-canary.txt"
            link = models / "linked.txt"
            models.mkdir(parents=True)
            outside.write_text("secret", encoding="utf-8")
            link.symlink_to(outside)
            self.assertTrue(link.is_symlink())
            config = load_test_config(root)

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="warehouse",
                        target="models",
                    )

        decision = caught.exception.decision
        self.assertEqual(
            ("path:warehouse:models/linked.txt:symlink-escape",),
            decision.evidence_refs,
        )
        self.assertIn("symlink", decision.reason.lower())
        self.assertIn("outside", decision.reason.lower())
        self.assertIn("real target", decision.recovery.lower())
        self.assertNotIn(str(outside), decision.to_json())
        self.assertNotIn("secret-canary", decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:warehouse:models/linked.txt:symlink-escape",
            recovery_fragment="real target",
            forbidden=(str(root), str(outside), "secret-canary"),
        )

    def test_unknown_alias_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "model.sql").write_text("select 1", encoding="utf-8")
            config = load_test_config(root)

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="unknown_alias",
                        target="model.sql",
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("path:unknown_alias:alias:unknown",), decision.evidence_refs)
        self.assertIn("unknown", decision.reason.lower())
        self.assertIn("configured alias", decision.recovery.lower())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:unknown_alias:alias:unknown",
            recovery_fragment="configured alias",
            forbidden=(str(root), str(workspace)),
        )

    def test_malformed_unknown_alias_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            config = load_test_config(root)
            malicious_alias = "/private/tmp/secret-canary"

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias=malicious_alias,
                        target="model.sql",
                    )

        decision = caught.exception.decision
        self.assertEqual(
            ("path:invalid-alias:alias:unknown",),
            decision.evidence_refs,
        )
        self.assertNotIn(malicious_alias, decision.to_json())
        self.assertNotIn("secret-canary", decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:invalid-alias:alias:unknown",
            recovery_fragment="configured alias",
            forbidden=(str(root), malicious_alias, "secret-canary"),
        )

    def test_identical_workspace_and_codebase_roots_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "model.sql").write_text("select 1", encoding="utf-8")
            config = load_test_config(
                root,
                codebases={
                    "billing_app": {
                        "description": "Synthetic billing producer",
                        "path_ref": "billing_app_root",
                        "read_mode": "adapter",
                        "git_history": "metadata_only",
                    }
                },
                path_bindings={"billing_app_root": str(workspace)},
            )

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="billing_app",
                        target="model.sql",
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("path:billing_app:root:overlap",), decision.evidence_refs)
        self.assertIn("warehouse", decision.reason)
        self.assertIn("billing_app", decision.reason)
        self.assertIn("separate", decision.recovery.lower())
        self.assertNotIn(str(workspace), decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:billing_app:root:overlap",
            recovery_fragment="separate",
            forbidden=(str(root), str(workspace)),
        )

    def test_ancestor_or_descendant_roots_are_rejected_by_components(self) -> None:
        for direction in ("codebase-inside-workspace", "workspace-inside-codebase"):
            with self.subTest(direction=direction):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    if direction == "codebase-inside-workspace":
                        workspace = root / "workspace"
                        external = workspace / "external"
                    else:
                        external = root / "external"
                        workspace = external / "workspace"
                    workspace.mkdir(parents=True)
                    external.mkdir(parents=True, exist_ok=True)
                    (external / "event.py").write_text("event", encoding="utf-8")
                    config = load_test_config(
                        root,
                        codebases={
                            "billing_app": {
                                "description": "Synthetic billing producer",
                                "path_ref": "billing_app_root",
                                "read_mode": "adapter",
                                "git_history": "metadata_only",
                            }
                        },
                        path_bindings={"billing_app_root": str(external)},
                    )

                    with working_directory(workspace):
                        with self.assertRaises(GateError) as caught:
                            resolve_path_reference(
                                config,
                                alias="billing_app",
                                target="event.py",
                            )

                self.assertEqual(
                    ("path:billing_app:root:overlap",),
                    caught.exception.decision.evidence_refs,
                )
                self.assert_path_error_contract(
                    caught.exception,
                    evidence="path:billing_app:root:overlap",
                    recovery_fragment="separate",
                    forbidden=(str(root), str(workspace), str(external)),
                )

    def test_similar_foo_and_foobar_roots_do_not_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "foo"
            external = root / "foobar"
            workspace.mkdir()
            external.mkdir()
            (external / "event.py").write_bytes(b"abc")
            config = load_test_config(
                root,
                codebases={
                    "billing_app": {
                        "description": "Synthetic billing producer",
                        "path_ref": "billing_app_root",
                        "read_mode": "adapter",
                        "git_history": "metadata_only",
                    }
                },
                path_bindings={"billing_app_root": str(external)},
            )

            with working_directory(workspace):
                reference = resolve_path_reference(
                    config,
                    alias="billing_app",
                    target="event.py",
                )

        self.assertEqual("event.py", reference.relative_path)

    def test_codebase_root_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            external = root / "external"
            external_link = root / "external-link"
            workspace.mkdir()
            external.mkdir()
            (external / "event.py").write_text("event", encoding="utf-8")
            external_link.symlink_to(external, target_is_directory=True)
            self.assertTrue(external_link.is_symlink())
            config = load_test_config(
                root,
                codebases={
                    "billing_app": {
                        "description": "Synthetic billing producer",
                        "path_ref": "billing_app_root",
                        "read_mode": "adapter",
                        "git_history": "metadata_only",
                    }
                },
                path_bindings={"billing_app_root": str(external_link)},
            )

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="billing_app",
                        target="event.py",
                    )

        decision = caught.exception.decision
        self.assertEqual(("path:billing_app:root:symlink",), decision.evidence_refs)
        self.assertIn("symlink", decision.reason.lower())
        self.assertIn("real directory", decision.recovery.lower())
        self.assertNotIn(str(external_link), decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:billing_app:root:symlink",
            recovery_fragment="real directory",
            forbidden=(str(root), str(external_link)),
        )

    def test_codebase_root_must_be_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            root_file = root / "external.txt"
            workspace.mkdir()
            root_file.write_text("not a directory", encoding="utf-8")
            config = load_test_config(
                root,
                codebases={
                    "billing_app": {
                        "description": "Synthetic billing producer",
                        "path_ref": "billing_app_root",
                        "read_mode": "adapter",
                        "git_history": "metadata_only",
                    }
                },
                path_bindings={"billing_app_root": str(root_file)},
            )

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="billing_app",
                        target=".",
                    )

        decision = caught.exception.decision
        self.assertEqual(
            ("path:billing_app:root:not-directory",), decision.evidence_refs
        )
        self.assertIn("not a directory", decision.reason.lower())
        self.assertNotIn(str(root_file), decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:billing_app:root:not-directory",
            recovery_fragment="existing directory",
            forbidden=(str(root), str(root_file)),
        )

    def test_missing_codebase_root_fails_closed_without_leaking_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            missing = root / "missing-secret-canary"
            workspace.mkdir()
            config = load_test_config(
                root,
                codebases={
                    "billing_app": {
                        "description": "Synthetic billing producer",
                        "path_ref": "billing_app_root",
                        "read_mode": "adapter",
                        "git_history": "metadata_only",
                    }
                },
                path_bindings={"billing_app_root": str(missing)},
            )

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="billing_app",
                        target=".",
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(("path:billing_app:root:missing",), decision.evidence_refs)
        self.assertIn("does not exist", decision.reason.lower())
        self.assertIn("existing directory", decision.recovery.lower())
        self.assertNotIn(str(missing), decision.to_json())
        self.assertNotIn("secret-canary", decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:billing_app:root:missing",
            recovery_fragment="existing directory",
            forbidden=(str(root), str(missing), "secret-canary"),
        )

    def test_unconfigured_codebase_root_fails_closed_with_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            config = load_test_config(
                root,
                codebases={
                    "billing_app": {
                        "description": "Synthetic billing producer",
                        "path_ref": "billing_app_root",
                        "read_mode": "adapter",
                        "git_history": "metadata_only",
                    }
                },
            )

            with working_directory(workspace):
                with self.assertRaises(GateError) as caught:
                    resolve_path_reference(
                        config,
                        alias="billing_app",
                        target=".",
                    )

        decision = caught.exception.decision
        self.assertEqual("block", decision.status)
        self.assertEqual(
            ("SCOPE-001", "PORT-001", "HOOK-004"), decision.rule_ids
        )
        self.assertEqual(
            ("path:billing_app:root:unconfigured",), decision.evidence_refs
        )
        self.assertIn("billing_app", decision.reason)
        self.assertIn("unconfigured", decision.reason.lower())
        self.assertIn("path binding", decision.recovery.lower())
        self.assertNotIn(str(root), decision.to_json())
        self.assert_path_error_contract(
            caught.exception,
            evidence="path:billing_app:root:unconfigured",
            recovery_fragment="path binding",
            forbidden=(str(root), str(workspace)),
        )

    def test_external_file_has_portable_stable_content_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            external = root / "external"
            workspace.mkdir()
            (external / "src").mkdir(parents=True)
            (external / "src" / "event.py").write_bytes(b"abc")
            config = load_test_config(
                root,
                codebases={
                    "billing_app": {
                        "description": "Synthetic billing producer",
                        "path_ref": "billing_app_root",
                        "read_mode": "adapter",
                        "git_history": "metadata_only",
                    }
                },
                path_bindings={"billing_app_root": str(external)},
            )

            with working_directory(workspace):
                first = resolve_path_reference(
                    config,
                    alias="billing_app",
                    target="src/event.py",
                )
                second = resolve_path_reference(
                    config,
                    alias=first.alias,
                    target=first.relative_path,
                )

        self.assertEqual(
            {
                "alias": "billing_app",
                "relative_path": "src/event.py",
                "revision": (
                    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
                ),
                "revision_kind": "content_sha256",
            },
            first.to_dict(),
        )
        self.assertEqual(first.to_json(), second.to_json())
        self.assertNotIn(str(external), first.to_json())

    def test_unreadable_target_is_a_sanitized_gate_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            model = workspace / "model.sql"
            secret_canary = root / "secret-canary"
            workspace.mkdir()
            model.write_text("select 1", encoding="utf-8")
            config = load_test_config(root)

            with working_directory(workspace):
                with patch.object(
                    Path,
                    "open",
                    side_effect=PermissionError(str(secret_canary)),
                ):
                    with self.assertRaises(GateError) as caught:
                        resolve_path_reference(
                            config,
                            alias="warehouse",
                            target="model.sql",
                        )

                def remove_during_git_probe(*_args: object) -> None:
                    model.unlink()

                with patch(
                    "chatbi_harness.paths._git_revision",
                    side_effect=remove_during_git_probe,
                ):
                    with self.assertRaises(GateError) as race_caught:
                        resolve_path_reference(
                            config,
                            alias="warehouse",
                            target="model.sql",
                        )

        self.assert_path_error_contract(
            caught.exception,
            evidence="path:warehouse:model.sql:unreadable",
            recovery_fragment="readable target content",
            forbidden=(str(root), str(model), str(secret_canary), "secret-canary"),
        )
        self.assert_path_error_contract(
            race_caught.exception,
            evidence="path:warehouse:model.sql:unreadable",
            recovery_fragment="readable target content",
            forbidden=(str(root), str(model)),
        )

    def test_directory_content_hash_is_stable_and_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            models = workspace / "models"
            models.mkdir(parents=True)
            model = models / "model.sql"
            model.write_text("select 1", encoding="utf-8")
            first_shape = workspace / "first-shape"
            second_shape = workspace / "second-shape"
            first_shape.mkdir()
            second_shape.mkdir()
            (first_shape / "a").write_bytes(b"Xfile\0b\0Y")
            (second_shape / "a").write_bytes(b"X")
            (second_shape / "b").write_bytes(b"Y")
            config = load_test_config(root)

            with working_directory(workspace):
                first = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="models",
                )
                repeated = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="models",
                )
                model.write_text("select 2", encoding="utf-8")
                changed = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="models",
                )
                first_shape_reference = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="first-shape",
                )
                second_shape_reference = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="second-shape",
                )

        self.assertEqual("models", first.relative_path)
        self.assertEqual("content_sha256", first.revision_kind)
        self.assertEqual(64, len(first.revision))
        self.assertEqual(first.revision, repeated.revision)
        self.assertNotEqual(first.revision, changed.revision)
        self.assertNotEqual(
            first_shape_reference.revision,
            second_shape_reference.revision,
        )
        self.assertNotIn(str(workspace), first.to_json())

    def test_clean_git_target_uses_commit_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            model = workspace / "model.sql"
            model.write_text("select 1", encoding="utf-8")
            models = workspace / "models"
            models.mkdir()
            (models / "tracked.sql").write_text("tracked", encoding="utf-8")
            (workspace / ".gitignore").write_text(
                "models/ignored.sql\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "init", "--quiet", str(workspace)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(workspace), "add", "."],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "-c",
                    "user.name=ChatBI Test",
                    "-c",
                    "user.email=chatbi-test@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "fixture",
                ],
                check=True,
                capture_output=True,
            )
            expected_revision = subprocess.run(
                ["git", "-C", str(workspace), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            fsmonitor_marker = root / "fsmonitor-was-invoked"
            fsmonitor_probe = workspace / "fsmonitor-probe.sh"
            fsmonitor_probe.write_text(
                "#!/bin/sh\nprintf invoked > "
                f"{shlex.quote(str(fsmonitor_marker))}\n",
                encoding="utf-8",
            )
            fsmonitor_probe.chmod(0o700)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "config",
                    "core.fsmonitor",
                    str(fsmonitor_probe),
                ],
                check=True,
                capture_output=True,
            )
            config = load_test_config(root)

            with working_directory(workspace):
                reference = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="model.sql",
                )
                clean_directory = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="models",
                )
                (models / "untracked.sql").write_text(
                    "untracked",
                    encoding="utf-8",
                )
                untracked_directory = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="models",
                )
                ignored = models / "ignored.sql"
                ignored.write_text("ignored-v1", encoding="utf-8")
                ignored_directory = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="models",
                )
                ignored.write_text("ignored-v2", encoding="utf-8")
                changed_ignored_directory = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="models",
                )
                model.write_text("select 2", encoding="utf-8")
                dirty_reference = resolve_path_reference(
                    config,
                    alias="warehouse",
                    target="model.sql",
                )

        self.assertEqual("git_sha", reference.revision_kind)
        self.assertEqual(expected_revision, reference.revision)
        self.assertNotIn(str(workspace), reference.to_json())
        self.assertFalse(fsmonitor_marker.exists())
        self.assertFalse((workspace / ".git" / "index.lock").exists())
        self.assertEqual("content_sha256", clean_directory.revision_kind)
        self.assertEqual("content_sha256", untracked_directory.revision_kind)
        self.assertNotEqual(
            clean_directory.revision,
            untracked_directory.revision,
        )
        self.assertNotEqual(
            untracked_directory.revision,
            ignored_directory.revision,
        )
        self.assertNotEqual(
            ignored_directory.revision,
            changed_ignored_directory.revision,
        )
        self.assertEqual("content_sha256", dirty_reference.revision_kind)
        self.assertEqual(
            hashlib.sha256(b"select 2").hexdigest(),
            dirty_reference.revision,
        )

    def test_external_root_cannot_supply_the_git_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            external = root / "external"
            marker = root / "external-git-was-executed"
            workspace.mkdir()
            external.mkdir()
            (external / "event.py").write_text("event", encoding="utf-8")
            fake_git = external / "git"
            fake_git.write_text(
                "#!/bin/sh\nprintf invoked > "
                f"{shlex.quote(str(marker))}\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o700)
            config = load_test_config(
                root,
                codebases={
                    "billing_app": {
                        "description": "Synthetic billing producer",
                        "path_ref": "billing_app_root",
                        "read_mode": "adapter",
                        "git_history": "metadata_only",
                    }
                },
                path_bindings={"billing_app_root": str(external)},
            )
            previous_path = os.environ.get("PATH")
            os.environ["PATH"] = f".{os.pathsep}{previous_path or ''}"
            try:
                with working_directory(workspace):
                    reference = resolve_path_reference(
                        config,
                        alias="billing_app",
                        target="event.py",
                    )
            finally:
                if previous_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = previous_path
            fake_git_executed = marker.exists()

        self.assertFalse(fake_git_executed)
        self.assertEqual("content_sha256", reference.revision_kind)
        self.assertEqual(hashlib.sha256(b"event").hexdigest(), reference.revision)


if __name__ == "__main__":
    unittest.main()
