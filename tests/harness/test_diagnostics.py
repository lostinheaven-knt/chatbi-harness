from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.diagnostics import (  # noqa: E402
    CapabilitySnapshot,
    probe_local_capabilities,
    run_init_diagnostic,
)


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def install_domain_contract(workspace: Path) -> None:
    for relative in (
        "CLAUDE.md",
        "CONTEXT.md",
        "docs/chatbi-harness-domain-model.md",
        ".claude/rules/00-domain-contract.md",
        ".claude/rules/10-security.md",
        ".claude/rules/20-completion.md",
    ):
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WORKSPACE_ROOT / relative, destination)


def write_ready_config(workspace: Path) -> Path:
    config = json.loads(
        (WORKSPACE_ROOT / ".claude" / "chatbi-harness.json").read_text(
            encoding="utf-8"
        )
    )
    config["adapters"] = {
        "semantic": ["managed:semantic"],
        "query": ["managed:query"],
        "fixture_enabled": False,
    }
    config["governance"].update(
        {
            "pii_policy_ref": "synthetic-pii-policy",
            "restricted_disclosure": "sql_only",
            "owners": {
                "default_domain_owner": "role:synthetic-domain-owner",
                "metrics": {},
            },
        }
    )
    config["evaluation"].update(
        {
            "release_threshold": 0.9,
            "threshold_owner": "role:synthetic-evaluation-owner",
        }
    )
    shared_config = workspace / ".claude" / "chatbi-harness.json"
    shared_config.write_text(json.dumps(config), encoding="utf-8")
    return shared_config


class InitDiagnosticTests(unittest.TestCase):
    def test_missing_domain_contract_blocks_before_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            missing_config = Path(".claude/missing.json")

            with working_directory(workspace):
                result = run_init_diagnostic(missing_config)

        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.production_ready)
        self.assertEqual(1, len(result.checks))
        self.assertEqual("domain_contract", result.checks[0].check_id)
        self.assertEqual(
            ["contract:domain-model"],
            result.to_dict()["checks"][0]["evidence_refs"],
        )
        self.assertIn("Restore", result.checks[0].decision.recovery)
        self.assertNotIn(str(workspace), result.to_json())

    def test_missing_configuration_is_a_structured_blocked_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            missing_config = Path(".claude/missing.json")
            outside_config = Path(directory) / "outside-secret-canary.json"
            shutil.copy2(
                WORKSPACE_ROOT / ".claude" / "chatbi-harness.json",
                outside_config,
            )
            linked_config = workspace / ".claude" / "linked-config.json"
            linked_config.symlink_to(outside_config)

            with working_directory(workspace):
                result = run_init_diagnostic(missing_config)
                unsafe_results = [
                    run_init_diagnostic(candidate)
                    for candidate in (
                        outside_config,
                        Path("../outside-secret-canary.json"),
                        Path(".claude/linked-config.json"),
                    )
                ]

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual(
            ["domain_contract", "configuration"],
            [check.check_id for check in result.checks],
        )
        config_check = result.checks[-1]
        self.assertEqual("block", config_check.decision.status)
        self.assertEqual(("config:shared",), config_check.decision.evidence_refs)
        self.assertIn("readable", config_check.decision.recovery.lower())
        self.assertNotIn(str(workspace), result.to_json())
        self.assertNotIn("secret-canary", result.to_json())
        for unsafe_result in unsafe_results:
            self.assertEqual("BLOCKED", unsafe_result.status)
            self.assertEqual(
                "configuration_path",
                unsafe_result.checks[-1].check_id,
            )
            self.assertNotIn(str(outside_config), unsafe_result.to_json())
            self.assertNotIn("secret-canary", unsafe_result.to_json())

    def test_valid_install_reaches_portable_path_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            alpha_root = Path(directory) / "alpha-root"
            zeta_root = Path(directory) / "zeta-root"
            workspace.mkdir()
            alpha_root.mkdir()
            zeta_root.mkdir()
            (alpha_root / "event.py").write_text("alpha", encoding="utf-8")
            (zeta_root / "event.py").write_text("zeta", encoding="utf-8")
            install_domain_contract(workspace)
            shared_config = workspace / ".claude" / "chatbi-harness.json"
            shared = json.loads(
                (
                    WORKSPACE_ROOT / ".claude" / "chatbi-harness.json"
                ).read_text(encoding="utf-8")
            )
            shared["business_codebases"] = {
                "zeta_app": {
                    "description": "Synthetic zeta producer",
                    "path_ref": "zeta_root",
                    "read_mode": "adapter",
                    "git_history": "metadata_only",
                },
                "alpha_app": {
                    "description": "Synthetic alpha producer",
                    "path_ref": "alpha_root",
                    "read_mode": "adapter",
                    "git_history": "metadata_only",
                },
            }
            shared_config.write_text(json.dumps(shared), encoding="utf-8")
            reordered_shared_config = (
                workspace / ".claude" / "chatbi-harness-reordered.json"
            )
            reordered_shared = dict(shared)
            reordered_shared["business_codebases"] = {
                alias: shared["business_codebases"][alias]
                for alias in ("alpha_app", "zeta_app")
            }
            reordered_shared_config.write_text(
                json.dumps(reordered_shared),
                encoding="utf-8",
            )
            local_config = workspace / ".claude" / "local.json"
            local_config.write_text(
                json.dumps(
                    {
                        "path_bindings": {
                            "zeta_root": str(zeta_root),
                            "alpha_root": str(alpha_root),
                        },
                        "cli_adapters": {},
                    }
                ),
                encoding="utf-8",
            )

            with working_directory(workspace):
                result = run_init_diagnostic(
                    shared_config.relative_to(workspace),
                    local_config.relative_to(workspace),
                    capability_probe=lambda: CapabilitySnapshot(
                        claude_available=False,
                        claude_version=None,
                        doctor_status="unavailable",
                        logged_in=None,
                        sandbox_available=None,
                        available_adapters=(),
                    ),
                )
                reordered_result = run_init_diagnostic(
                    reordered_shared_config.relative_to(workspace),
                    local_config.relative_to(workspace),
                    capability_probe=lambda: CapabilitySnapshot(
                        claude_available=False,
                        claude_version=None,
                        doctor_status="unavailable",
                        logged_in=None,
                        sandbox_available=None,
                        available_adapters=(),
                    ),
                )

        self.assertEqual(
            ["domain_contract", "configuration", "paths"],
            [check.check_id for check in result.checks[:3]],
        )
        self.assertEqual("pass", result.checks[2].decision.status)
        self.assertEqual(
            ["warehouse", "alpha_app", "zeta_app"],
            [reference.alias for reference in result.path_references],
        )
        reference = result.path_references[0]
        self.assertEqual("warehouse", reference.alias)
        self.assertEqual(".", reference.relative_path)
        self.assertEqual("content_sha256", reference.revision_kind)
        self.assertEqual(64, len(reference.revision))
        self.assertNotIn(str(workspace), result.to_json())
        self.assertEqual(result.to_json(), reordered_result.to_json())

    def test_missing_production_capabilities_are_explicitly_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            shared_config = workspace / ".claude" / "chatbi-harness.json"
            shutil.copy2(
                WORKSPACE_ROOT / ".claude" / "chatbi-harness.json",
                shared_config,
            )
            unavailable = CapabilitySnapshot(
                claude_available=False,
                claude_version=None,
                doctor_status="unavailable",
                logged_in=None,
                sandbox_available=None,
                available_adapters=(),
            )

            with working_directory(workspace):
                result = run_init_diagnostic(
                    shared_config.relative_to(workspace),
                    capability_probe=lambda: unavailable,
                )

        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.production_ready)
        blocked_ids = {
            check.check_id
            for check in result.checks
            if check.decision.status == "block"
        }
        self.assertTrue(
            {
                "claude_version",
                "claude_doctor",
                "claude_login",
                "sandbox",
                "adapters",
                "governance_owner",
                "pii_policy",
                "release_threshold",
            }.issubset(blocked_ids)
        )
        self.assertEqual(sorted(blocked_ids), result.pending_configuration)
        self.assertTrue(result.recovery_actions)
        self.assertNotIn(str(workspace), result.to_json())

    def test_ready_install_can_report_pass_or_compatible_version_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            shared_config = write_ready_config(workspace)
            ready = CapabilitySnapshot(
                claude_available=True,
                claude_version="2.1.216",
                doctor_status="pass",
                logged_in=True,
                sandbox_available=True,
                available_adapters=("managed:semantic", "managed:query"),
            )
            newer = CapabilitySnapshot(
                claude_available=True,
                claude_version="2.2.0",
                doctor_status="pass",
                logged_in=True,
                sandbox_available=True,
                available_adapters=("managed:semantic", "managed:query"),
            )

            with working_directory(workspace):
                passing = run_init_diagnostic(
                    shared_config.relative_to(workspace),
                    capability_probe=lambda: ready,
                )
                warning = run_init_diagnostic(
                    shared_config.relative_to(workspace),
                    capability_probe=lambda: newer,
                )

        self.assertEqual("PASS", passing.status)
        self.assertFalse(passing.production_ready)
        self.assertEqual("synthetic", passing.capabilities.evidence_source)
        self.assertEqual([], passing.pending_configuration)
        self.assertEqual("WARN", warning.status)
        self.assertFalse(warning.production_ready)
        version_check = next(
            check for check in warning.checks if check.check_id == "claude_version"
        )
        self.assertEqual("warn", version_check.decision.status)
        self.assertIn("compatibility", version_check.decision.recovery.lower())

    def test_local_probe_times_out_and_never_returns_raw_command_output(self) -> None:
        secret_canary = "api_key=sk-secret-canary-value"
        absolute_canary = "/private/tmp/secret-canary"
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_runner(
            argv: list[str],
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            calls.append((argv, kwargs))
            if "--version" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=f"2.1.216\n{secret_canary}\n{absolute_canary}",
                    stderr="",
                )
            raise subprocess.TimeoutExpired(
                cmd=argv,
                timeout=8,
                output=secret_canary,
                stderr=absolute_canary,
            )

        snapshot = probe_local_capabilities(
            claude_executable=Path(sys.executable),
            command_runner=fake_runner,
        )

        self.assertTrue(snapshot.claude_available)
        self.assertEqual("2.1.216", snapshot.claude_version)
        self.assertEqual("timeout", snapshot.doctor_status)
        self.assertIsNone(snapshot.logged_in)
        self.assertIsNone(snapshot.sandbox_available)
        self.assertEqual(2, len(calls))
        self.assertTrue(all(Path(call[0][0]).is_absolute() for call in calls))
        self.assertTrue(all(call[1]["capture_output"] is True for call in calls))
        self.assertTrue(all(call[1]["shell"] is False for call in calls))
        serialized = json.dumps(snapshot.to_dict())
        self.assertNotIn("secret-canary", serialized)
        self.assertNotIn(absolute_canary, serialized)

        def nonzero_runner(
            argv: list[str],
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            if "--version" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="2.1.216", stderr=""
                )
            return subprocess.CompletedProcess(
                argv,
                1,
                stdout=f"Not logged in\nSandbox disabled\n{secret_canary}",
                stderr=absolute_canary,
            )

        nonzero = probe_local_capabilities(
            claude_executable=Path(sys.executable),
            command_runner=nonzero_runner,
        )
        self.assertEqual("not_logged_in", nonzero.doctor_status)
        self.assertFalse(nonzero.logged_in)
        self.assertFalse(nonzero.sandbox_available)
        self.assertNotIn("secret-canary", json.dumps(nonzero.to_dict()))

        def doctor_without_login_evidence(
            argv: list[str],
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            output = "2.1.216" if "--version" in argv else "Sandbox enabled"
            return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

        unverified_login = probe_local_capabilities(
            claude_executable=Path(sys.executable),
            command_runner=doctor_without_login_evidence,
        )
        self.assertEqual("pass", unverified_login.doctor_status)
        self.assertIsNone(unverified_login.logged_in)
        self.assertTrue(unverified_login.sandbox_available)

    def test_unconfirmed_path_cannot_supply_the_claude_probe_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            untrusted_bin = Path(directory) / "untrusted-bin"
            workspace.mkdir()
            untrusted_bin.mkdir()
            install_domain_contract(workspace)
            shared_config = write_ready_config(workspace)
            fake_claude = untrusted_bin / "claude"
            marker = workspace / "fake-claude-marker"
            fake_claude.write_text(
                "#!/bin/sh\nprintf invoked > fake-claude-marker\n",
                encoding="utf-8",
            )
            fake_claude.chmod(0o700)
            previous_path = os.environ.get("PATH")
            os.environ["PATH"] = str(untrusted_bin)
            try:
                with working_directory(workspace):
                    result = run_init_diagnostic(
                        shared_config.relative_to(workspace)
                    )
                    with patch(
                        "chatbi_harness.diagnostics.shutil.which",
                        side_effect=RuntimeError(
                            f"{workspace}/private-secret-canary"
                        ),
                    ):
                        failed_probe = run_init_diagnostic(
                            shared_config.relative_to(workspace)
                        )
                fake_claude_executed = marker.exists()
            finally:
                if previous_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = previous_path

        self.assertFalse(fake_claude_executed)
        self.assertEqual("BLOCKED", result.status)
        self.assertIsNotNone(result.capabilities)
        self.assertFalse(result.capabilities.claude_available)
        self.assertEqual("BLOCKED", failed_probe.status)
        self.assertEqual("capability_probe", failed_probe.checks[-1].check_id)
        self.assertNotIn(str(workspace), failed_probe.to_json())
        self.assertNotIn("secret-canary", failed_probe.to_json())

    def test_init_command_and_minimal_docs_state_the_current_contract(self) -> None:
        command = WORKSPACE_ROOT / ".claude" / "commands" / "chatbi-init.md"
        installation = WORKSPACE_ROOT / "docs" / "harness" / "installation.md"
        configuration = WORKSPACE_ROOT / "docs" / "harness" / "configuration.md"

        command_text = command.read_text(encoding="utf-8")
        for heading in (
            "## Input",
            "## Preconditions",
            "## Allowed changes",
            "## Stop conditions",
            "## Output evidence",
            "## Rules",
        ):
            self.assertIn(heading, command_text)
        self.assertIn("explicit confirmation", command_text.lower())
        self.assertIn("[confirmed-claude-executable]", command_text)
        self.assertIn("claude_executable=confirmed_claude_path", command_text)
        self.assertIn("Otherwise omit that keyword", command_text)
        self.assertIn("Workspace-relative", command_text)
        self.assertIn("contain `..`", command_text)
        self.assertIn("use symlinks", command_text)
        self.assertIn("PASS", command_text)
        self.assertIn("WARN", command_text)
        self.assertIn("BLOCKED", command_text)

        installation_text = installation.read_text(encoding="utf-8")
        configuration_text = configuration.read_text(encoding="utf-8")
        self.assertIn("VERIFIED OFFLINE", installation_text)
        self.assertIn("NOT YET EXERCISED", installation_text)
        self.assertIn("PRODUCTION BLOCKER", installation_text)
        self.assertIn("explicit confirmation", configuration_text.lower())
        self.assertIn("content_sha256", configuration_text)
        self.assertNotIn("production certified", installation_text.lower())


if __name__ == "__main__":
    unittest.main()
