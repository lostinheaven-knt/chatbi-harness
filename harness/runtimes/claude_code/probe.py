"""Claude Code target capability probe (multi-runtime module 2).

Moved out of ``chatbi_governance.diagnostics`` so the Governance Kernel stays
free of Claude-CLI binary probing (impl doc §3.1/§3.3). This module is the
Claude runtime adapter side of the architecture: it executes ``claude
--version`` / ``claude doctor`` in a sanitized environment and normalizes the
result into the kernel's :class:`CapabilitySnapshot` (the data model stays
defined by the kernel; field names are unchanged).

Signature and return type are unchanged from the pre-extraction
``probe_local_capabilities``; ``command_runner`` stays injectable for offline
contract tests (test_diagnostics.py).

Applicable rules: SEC-001, SEC-003, PORT-001, HOOK-002, HOOK-004.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Callable

from chatbi_governance.diagnostics import CapabilitySnapshot


# Verified local Claude Code baseline; the version-comparison decision itself
# lives in the kernel diagnostic (chatbi_governance.diagnostics), this constant
# documents the same baseline on the probing side.
_CLAUDE_VERSION_BASELINE = "2.1.216"

_LOGIN_MISSING = re.compile(
    r"(?:not logged in|log in required|authentication required|unauthenticated)",
    re.IGNORECASE,
)
_LOGIN_AVAILABLE = re.compile(
    r"(?:logged in as|authenticated as|authentication successful)",
    re.IGNORECASE,
)
_SANDBOX_UNAVAILABLE = re.compile(
    r"sandbox.{0,40}(?:unavailable|disabled|not available)",
    re.IGNORECASE | re.DOTALL,
)
_SANDBOX_AVAILABLE = re.compile(
    r"sandbox.{0,40}(?:available|enabled|ready)",
    re.IGNORECASE | re.DOTALL,
)


def _validated_executable(path: Path | None) -> Path | None:
    # Duplicated from the kernel diagnostics helper so this module stays
    # self-contained (impl doc §3.3 "承接 probe_local_capabilities 全文");
    # identical algorithm, identical behavior.
    if path is None or not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=True)
        mode = resolved.stat(follow_symlinks=False).st_mode
    except (OSError, RuntimeError):
        return None
    if not stat.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        return None
    return resolved


def probe_local_capabilities(
    *,
    claude_executable: Path | None,
    safe_path: str | None = None,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CapabilitySnapshot:
    """Probe Claude locally while retaining only normalized capability fields."""

    executable = _validated_executable(claude_executable)
    if executable is None:
        return CapabilitySnapshot(
            claude_available=False,
            claude_version=None,
            doctor_status="unavailable",
            logged_in=None,
            sandbox_available=None,
            available_adapters=(),
            evidence_source="local_probe",
        )

    normalized_safe_path = os.pathsep.join(
        component
        for component in (safe_path or os.defpath).split(os.pathsep)
        if component and Path(component).is_absolute()
    )
    safe_environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": normalized_safe_path,
    }
    for name in ("HOME", "XDG_CONFIG_HOME"):
        value = os.environ.get(name)
        if value and Path(value).is_absolute():
            safe_environment[name] = value

    def invoke(
        *args: str,
        timeout: int,
    ) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
        try:
            return (
                command_runner(
                    [str(executable), *args],
                    cwd=Path.cwd(),
                    env=safe_environment,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    shell=False,
                ),
                None,
            )
        except subprocess.TimeoutExpired:
            return None, "timeout"
        except OSError:
            return None, "unavailable"

    version_process, _version_failure = invoke("--version", timeout=3)
    normalized_version = None
    if version_process is not None and version_process.returncode == 0:
        version_text = f"{version_process.stdout}\n{version_process.stderr}"[:8192]
        version_match = re.search(
            r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?\b",
            version_text,
        )
        normalized_version = version_match.group(0) if version_match else None

    doctor_process, doctor_failure = invoke("doctor", timeout=8)
    if doctor_process is None:
        doctor_status = doctor_failure or "error"
        logged_in = None
        sandbox_available = None
    else:
        doctor_text = f"{doctor_process.stdout}\n{doctor_process.stderr}"[:8192]
        login_missing = bool(_LOGIN_MISSING.search(doctor_text))
        login_available = bool(_LOGIN_AVAILABLE.search(doctor_text))
        if login_missing:
            doctor_status = "not_logged_in"
        elif doctor_process.returncode == 0:
            doctor_status = "pass"
        else:
            doctor_status = "error"
        if login_missing:
            logged_in = False
        elif login_available:
            logged_in = True
        else:
            logged_in = None
        if _SANDBOX_UNAVAILABLE.search(doctor_text):
            sandbox_available = False
        elif _SANDBOX_AVAILABLE.search(doctor_text):
            sandbox_available = True
        else:
            sandbox_available = None

    return CapabilitySnapshot(
        claude_available=True,
        claude_version=normalized_version,
        doctor_status=doctor_status,
        logged_in=logged_in,
        sandbox_available=sandbox_available,
        available_adapters=(),
        evidence_source="local_probe",
    )
