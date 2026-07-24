from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "session_diagnose.py"
HOOK_LAUNCHER = WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "session_diagnose"
PYTHON_BINDING_LAUNCHER = (
    WORKSPACE_ROOT / "harness" / ".claude" / "hooks" / "python_binding_launcher.py"
)


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
        shutil.copy2(WORKSPACE_ROOT / "harness" / relative, destination)


def install_shared_config(workspace: Path) -> None:
    destination = workspace / ".claude" / "chatbi-harness.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(WORKSPACE_ROOT / "harness" / ".claude" / "chatbi-harness.json", destination)


def install_hook_runtime(workspace: Path) -> None:
    hooks = workspace / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOK_PATH, hooks / HOOK_PATH.name)
    shutil.copy2(HOOK_LAUNCHER, hooks / HOOK_LAUNCHER.name)
    shutil.copy2(
        PYTHON_BINDING_LAUNCHER,
        hooks / PYTHON_BINDING_LAUNCHER.name,
    )
    shutil.copytree(
        WORKSPACE_ROOT / "harness" / ".claude" / "lib",
        workspace / ".claude" / "lib",
    )


def session_start_event(workspace: Path) -> dict[str, object]:
    return {
        "session_id": "session-fixture-001",
        "transcript_path": "/private/tmp/transcript-secret-canary.jsonl",
        "cwd": str(workspace.resolve()),
        "hook_event_name": "SessionStart",
        "source": "startup",
        "model": "claude-fixture-model",
    }


def run_hook(
    workspace: Path,
    payload: dict[str, object] | bytes,
    *,
    timeout: int = 20,
    system_path: Path | None = None,
    hook_path: Path = HOOK_PATH,
) -> subprocess.CompletedProcess[bytes]:
    stdin = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    command = [sys.executable, "-B", "-I", str(hook_path)]
    if system_path is not None:
        launcher = (
            "import os,runpy,sys;"
            "os.defpath=sys.argv[1];"
            "hook=sys.argv[2];"
            "sys.argv=[hook];"
            "runpy.run_path(hook,run_name='__main__')"
        )
        command = [
            sys.executable,
            "-B",
            "-I",
            "-c",
            launcher,
            str(system_path),
            str(hook_path),
        ]
    return subprocess.run(
        command,
        cwd=workspace,
        input=stdin,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def run_configured_hook(
    workspace: Path,
    payload: dict[str, object],
    *,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    settings = json.loads(
        (WORKSPACE_ROOT / "harness" / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    return subprocess.run(
        command,
        cwd=workspace,
        env=environment,
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=20,
        check=False,
        shell=True,
        executable="/bin/sh",
    )


class SessionStartHookTests(unittest.TestCase):
    def test_settings_command_never_resolves_python_from_inherited_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            install_shared_config(workspace)
            install_hook_runtime(workspace)
            marker = workspace / "fake-python-marker"
            fake_python = workspace / "python3"
            fake_python.write_text(
                "#!/bin/sh\n"
                "printf invoked > fake-python-marker\n"
                f"exec {shlex.quote(sys.executable)} \"$@\"\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o700)
            environment = dict(os.environ)
            environment["PATH"] = f".{os.pathsep}{environment.get('PATH', '')}"
            environment["CHATBI_PYTHON"] = sys.executable

            process = run_configured_hook(
                workspace,
                session_start_event(workspace),
                environment=environment,
            )
            fake_python_executed = marker.exists()

        self.assertEqual(0, process.returncode, process.stderr.decode())
        self.assertFalse(fake_python_executed)
        self.assertEqual("SessionStart", json.loads(process.stdout)["hook_event_name"])

    def test_invalid_python_bindings_fail_before_any_interpreter_executes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            business_root = Path(directory) / "business-root"
            workspace.mkdir()
            business_root.mkdir()
            install_hook_runtime(workspace)
            local_config = workspace / ".claude" / "chatbi-harness.local.json"
            local_config.write_text(
                json.dumps(
                    {
                        "path_bindings": {"producer_root": str(business_root)},
                        "cli_adapters": {},
                    }
                ),
                encoding="utf-8",
            )
            workspace_marker = workspace / "workspace-python-marker"
            business_marker = workspace / "business-python-marker"
            workspace_python = workspace / "workspace-python"
            business_python = business_root / "business-python"
            for executable, marker in (
                (workspace_python, workspace_marker),
                (business_python, business_marker),
            ):
                executable.write_text(
                    "#!/bin/sh\n"
                    f"printf invoked > {shlex.quote(str(marker))}\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
                executable.chmod(0o700)
            non_executable = Path(directory) / "non-executable-python"
            non_executable.write_text("not executable", encoding="utf-8")

            base_environment = dict(os.environ)
            base_environment["PATH"] = f".{os.pathsep}{base_environment.get('PATH', '')}"
            invalid_bindings: dict[str, str | None] = {
                "missing": None,
                "relative": "python3",
                "non_executable": str(non_executable),
                "workspace": str(workspace_python),
                "business_root": str(business_python),
            }
            observations: list[tuple[str, subprocess.CompletedProcess[bytes]]] = []
            for label, binding in invalid_bindings.items():
                environment = dict(base_environment)
                if binding is None:
                    environment.pop("CHATBI_PYTHON", None)
                else:
                    environment["CHATBI_PYTHON"] = binding
                observations.append(
                    (
                        label,
                        run_configured_hook(
                            workspace,
                            session_start_event(workspace),
                            environment=environment,
                        ),
                    )
                )
            markers_created = workspace_marker.exists() or business_marker.exists()

        self.assertFalse(markers_created)
        for label, process in observations:
            with self.subTest(label=label):
                stderr = process.stderr.decode("utf-8")
                self.assertEqual(2, process.returncode)
                self.assertEqual(b"", process.stdout)
                error = json.loads(stderr)
                self.assertEqual("block", error["status"])
                self.assertEqual(
                    ["hook:session-start:python-binding"],
                    error["evidence_refs"],
                )
                self.assertNotIn("secret", stderr.lower())
                self.assertNotIn(str(workspace), stderr)

    def test_business_root_aliases_are_resolved_before_python_executes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            real_parent = Path(directory) / "real-parent"
            business_root = real_parent / "business"
            workspace.mkdir()
            business_root.mkdir(parents=True)
            install_hook_runtime(workspace)
            marker = workspace / "aliased-business-python-marker"
            business_python = business_root / "python"
            business_python.write_text(
                "#!/bin/sh\n"
                f"printf invoked > {shlex.quote(str(marker))}\n"
                "exit 0\n",
                encoding="utf-8",
            )
            business_python.chmod(0o700)
            parent_link = Path(directory) / "parent-link"
            parent_link.symlink_to(real_parent)
            root_link = Path(directory) / "business-link"
            root_link.symlink_to(business_root)
            configured_roots = {
                "parent_symlink": parent_link / "business",
                "root_symlink": root_link,
                "dot_component": f"{real_parent}/./business",
                "duplicate_separator": f"{real_parent}//business",
                "platform_canonical_alias": str(business_root),
            }
            observations: list[
                tuple[str, subprocess.CompletedProcess[bytes], bool]
            ] = []
            local_config = workspace / ".claude" / "chatbi-harness.local.json"
            for label, configured_root in configured_roots.items():
                marker.unlink(missing_ok=True)
                environment = dict(os.environ)
                environment["PATH"] = (
                    f".{os.pathsep}{environment.get('PATH', '')}"
                )
                environment["CHATBI_PYTHON"] = (
                    str(business_python.resolve())
                    if label == "platform_canonical_alias"
                    else str(business_python)
                )
                local_config.write_text(
                    json.dumps(
                        {
                            "path_bindings": {
                                "producer_root": str(configured_root),
                            },
                            "cli_adapters": {},
                        }
                    ),
                    encoding="utf-8",
                )
                process = run_configured_hook(
                    workspace,
                    session_start_event(workspace),
                    environment=environment,
                )
                observations.append((label, process, marker.exists()))

        for label, process, marker_created in observations:
            with self.subTest(label=label):
                self.assertEqual(2, process.returncode)
                self.assertEqual(b"", process.stdout)
                self.assertFalse(marker_created)
                error = json.loads(process.stderr)
                self.assertEqual("block", error["status"])
                self.assertNotIn("secret", process.stderr.decode().lower())
                self.assertNotIn(str(workspace), process.stderr.decode())

    def test_valid_event_returns_one_diagnostic_without_blocking_the_session(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            install_shared_config(workspace)

            process = run_hook(workspace, session_start_event(workspace))

        self.assertEqual(0, process.returncode, process.stderr.decode())
        self.assertEqual(b"", process.stderr)
        output = json.loads(process.stdout.decode("utf-8"))
        self.assertEqual("SessionStart", output["hook_event_name"])
        self.assertEqual("startup", output["source"])
        self.assertEqual("BLOCKED", output["diagnostic"]["status"])
        self.assertFalse(output["chatbi_commands_available"])
        self.assertFalse(output["diagnostic"]["production_ready"])
        self.assertNotIn("transcript-secret-canary", process.stdout.decode())
        self.assertNotIn(str(workspace), process.stdout.decode())

    def test_invalid_serialization_and_oversized_input_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            invalid_payloads = {
                "invalid_utf8": b"\xff\xfe",
                "malformed_json": b'{"hook_event_name":"SessionStart"',
                "duplicate_key": (
                    b'{"hook_event_name":"SessionStart",'
                    b'"hook_event_name":"secret-canary"}'
                ),
                "oversized": b"{" + (b"x" * (64 * 1024)),
            }

            for label, payload in invalid_payloads.items():
                with self.subTest(label=label):
                    process = run_hook(workspace, payload)
                    stderr = process.stderr.decode("utf-8")

                    self.assertEqual(2, process.returncode)
                    self.assertEqual(b"", process.stdout)
                    error = json.loads(stderr)
                    self.assertEqual("block", error["status"])
                    self.assertIn("HOOK-004", error["rule_ids"])
                    self.assertTrue(error["recovery"])
                    self.assertLessEqual(len(stderr), 512)
                    self.assertNotIn("secret-canary", stderr)
                    self.assertNotIn(str(workspace), stderr)

    def test_doctor_nonzero_and_timeout_never_report_chatbi_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            fake_bin = Path(directory) / "system-bin"
            workspace.mkdir()
            fake_bin.mkdir()
            install_domain_contract(workspace)
            install_shared_config(workspace)
            fake_claude = fake_bin / "claude"

            observations: list[tuple[str, dict[str, object], bytes]] = []
            scripts = {
                "not_logged_in": (
                    "#!/bin/sh\n"
                    "if [ \"$1\" = \"--version\" ]; then printf '2.1.216'; exit 0; fi\n"
                    "printf 'Not logged in api_key=sk-secret-canary' >&2\n"
                    "exit 9\n"
                ),
                "timeout": (
                    "#!/bin/sh\n"
                    "if [ \"$1\" = \"--version\" ]; then printf '2.1.216'; exit 0; fi\n"
                    "exec /bin/sleep 12\n"
                ),
            }
            for label, script in scripts.items():
                fake_claude.write_text(script, encoding="utf-8")
                fake_claude.chmod(0o700)
                process = run_hook(
                    workspace,
                    session_start_event(workspace),
                    system_path=fake_bin,
                )
                observations.append(
                    (label, json.loads(process.stdout.decode("utf-8")), process.stderr)
                )
                self.assertEqual(0, process.returncode)

        for label, output, stderr in observations:
            with self.subTest(label=label):
                self.assertEqual(b"", stderr)
                self.assertEqual("BLOCKED", output["diagnostic"]["status"])
                self.assertFalse(output["chatbi_commands_available"])
                self.assertEqual(
                    label,
                    output["diagnostic"]["capabilities"]["doctor_status"],
                )
                doctor_check = next(
                    check
                    for check in output["diagnostic"]["checks"]
                    if check["id"] == "claude_doctor"
                )
                self.assertEqual("block", doctor_check["status"])
                self.assertNotIn("secret-canary", json.dumps(output))

    def test_library_import_exception_fails_closed_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            install_shared_config(workspace)
            installed_hook = workspace / ".claude" / "hooks" / "session_diagnose.py"
            installed_hook.parent.mkdir(parents=True)
            shutil.copy2(HOOK_PATH, installed_hook)
            broken_library = workspace / ".claude" / "lib" / "chatbi_harness.py"
            broken_library.parent.mkdir(parents=True)
            broken_library.write_text(
                f'raise RuntimeError("api_key=sk-secret-canary {workspace}")\n',
                encoding="utf-8",
            )

            process = run_hook(
                workspace,
                session_start_event(workspace),
                hook_path=installed_hook,
            )

        stderr = process.stderr.decode("utf-8")
        self.assertEqual(2, process.returncode)
        self.assertEqual(b"", process.stdout)
        error = json.loads(stderr)
        self.assertEqual("block", error["status"])
        self.assertEqual(["HOOK-001", "HOOK-004"], error["rule_ids"])
        self.assertIn("library", error["reason"].lower())
        self.assertNotIn("traceback", stderr.lower())
        self.assertNotIn("secret-canary", stderr)
        self.assertNotIn(str(workspace), stderr)

    def test_settings_and_compatibility_document_only_the_verified_contract(
        self,
    ) -> None:
        settings_path = WORKSPACE_ROOT / "harness" / ".claude" / "settings.json"
        compatibility_path = WORKSPACE_ROOT / "harness" / "docs" / "harness" / "compatibility.md"
        installation_path = WORKSPACE_ROOT / "harness" / "docs" / "harness" / "installation.md"

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual({"hooks"}, set(settings))
        self.assertEqual({"SessionStart"}, set(settings["hooks"]))
        handlers = settings["hooks"]["SessionStart"]
        self.assertEqual(1, len(handlers))
        self.assertEqual("startup|resume|clear|compact", handlers[0]["matcher"])
        self.assertEqual(
            [
                {
                    "type": "command",
                    "command": ".claude/hooks/session_diagnose",
                }
            ],
            handlers[0]["hooks"],
        )
        serialized_settings = json.dumps(settings)
        self.assertNotIn("/Users/", serialized_settings)
        self.assertNotIn("secret", serialized_settings.lower())
        self.assertNotIn("..", handlers[0]["hooks"][0]["command"])
        self.assertNotIn("python3", handlers[0]["hooks"][0]["command"])
        self.assertTrue(os.access(HOOK_LAUNCHER, os.X_OK))

        compatibility = compatibility_path.read_text(encoding="utf-8")
        installation = installation_path.read_text(encoding="utf-8")
        self.assertIn("VERIFIED OFFLINE", compatibility)
        self.assertIn("NOT YET EXERCISED", compatibility)
        self.assertIn("PRODUCTION BLOCKER", compatibility)
        self.assertIn("SessionStart", compatibility)
        self.assertIn("cannot block", compatibility)
        self.assertIn("doctor", compatibility)
        self.assertIn("timeout", compatibility)
        self.assertIn("CHATBI_PYTHON", compatibility)
        self.assertIn("explicit confirmation", compatibility.lower())
        self.assertIn("no PATH fallback", compatibility)
        self.assertIn("real paths", compatibility)
        self.assertIn("fixed OS", compatibility)
        self.assertIn("CHATBI_PYTHON", installation)
        self.assertIn("explicit confirmation", installation.lower())
        self.assertNotIn("fixture is production", compatibility.lower())

    def test_event_shape_and_workspace_identity_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            workspace_link = Path(directory) / "workspace-link"
            workspace_link.symlink_to(workspace)
            base = session_start_event(workspace)
            missing_source = dict(base)
            missing_source.pop("source")
            wrong_event = {**base, "hook_event_name": "PreToolUse"}
            unknown_path = {
                **base,
                "shared_config": "../absolute-secret-canary",
            }
            unknown_absolute_executable = {
                **base,
                "claude_executable": str(
                    (Path(directory) / "absolute-secret-canary").resolve()
                ),
            }
            wrong_source_type = {**base, "source": ["startup"]}
            wrong_cwd = {**base, "cwd": str(Path(directory).resolve())}
            traversal_cwd = {**base, "cwd": f"{workspace}/../secret-canary"}
            symlink_cwd = {**base, "cwd": str(workspace_link)}
            invalid_events: dict[str, dict[str, object] | bytes] = {
                "non_object": b"[]",
                "missing_source": missing_source,
                "wrong_event": wrong_event,
                "unknown_path": unknown_path,
                "unknown_absolute_executable": unknown_absolute_executable,
                "wrong_source_type": wrong_source_type,
                "cwd_mismatch": wrong_cwd,
                "cwd_traversal": traversal_cwd,
                "cwd_symlink_spelling": symlink_cwd,
            }

            for label, payload in invalid_events.items():
                with self.subTest(label=label):
                    process = run_hook(workspace, payload)
                    stderr = process.stderr.decode("utf-8")

                    self.assertEqual(2, process.returncode)
                    self.assertEqual(b"", process.stdout)
                    error = json.loads(stderr)
                    self.assertEqual("block", error["status"])
                    self.assertIn("HOOK-004", error["rule_ids"])
                    self.assertLessEqual(len(stderr), 512)
                    self.assertNotIn("secret-canary", stderr)
                    self.assertNotIn(str(workspace), stderr)


if __name__ == "__main__":
    unittest.main()
