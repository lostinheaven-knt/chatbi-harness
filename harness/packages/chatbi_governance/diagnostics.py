"""Explicit, machine-readable initialization diagnostics for ChatBI.

The Governance Kernel owns the diagnostic assembly and the normalized
capability data model (:class:`CapabilitySnapshot`). The Claude-specific
binary probing (``claude --version`` / ``claude doctor``) lives in the
Claude Code runtime adapter (``runtimes/claude_code/probe.py``, module 2 of
the multi-runtime modification); the kernel receives it via the ``probe``
injection parameter or the default-probe registration point
(:func:`set_default_probe`), and fails closed (HOOK-004) when no probe is
available.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .config import load_effective_config
from .gates import GateDecision, GateError, fail_closed, validate_domain_contract
from .paths import PortablePathReference, resolve_path_reference


_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
_ADAPTER_ID = re.compile(r"^(?:managed|cli|fixture):[a-z][a-z0-9_-]{1,62}$")
_DOCTOR_STATUSES = frozenset(
    {"pass", "not_logged_in", "timeout", "unavailable", "error"}
)
# Verified local Claude Code baseline (diagnostics contract, feature-flow
# §3.8.1); the comparison decision stays here in the kernel, the probing
# machinery lives in runtimes/claude_code/probe.py.
_CLAUDE_VERSION_BASELINE = "2.1.216"

# Default capability probe registered by the compatibility shim
# (lib/chatbi_harness) for the legacy no-injection path; None (no registered
# probe) reports the runtime checks unavailable and fails closed (HOOK-004).
_DEFAULT_PROBE: Callable[..., CapabilitySnapshot] | None = None


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    claude_available: bool
    claude_version: str | None
    doctor_status: str
    logged_in: bool | None
    sandbox_available: bool | None
    available_adapters: tuple[str, ...]
    evidence_source: str = "synthetic"
    platform_system: str = platform.system()
    platform_machine: str = platform.machine()
    python_version: str = platform.python_version()

    def __post_init__(self) -> None:
        if self.claude_version is not None and not _VERSION.fullmatch(
            self.claude_version
        ):
            raise ValueError("Claude version must be a normalized semantic version")
        if self.doctor_status not in _DOCTOR_STATUSES:
            raise ValueError("Unknown Claude doctor status")
        if self.evidence_source not in {"local_probe", "synthetic"}:
            raise ValueError("Unknown capability evidence source")
        if any(not _ADAPTER_ID.fullmatch(item) for item in self.available_adapters):
            raise ValueError("Capability snapshot contains an invalid adapter ID")
        for value in (
            self.platform_system,
            self.platform_machine,
            self.python_version,
        ):
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value):
                raise ValueError("Capability snapshot contains invalid runtime metadata")

    def to_dict(self) -> dict[str, object]:
        return {
            "claude_available": self.claude_available,
            "claude_version": self.claude_version,
            "doctor_status": self.doctor_status,
            "logged_in": self.logged_in,
            "sandbox_available": self.sandbox_available,
            "available_adapters": sorted(set(self.available_adapters)),
            "evidence_source": self.evidence_source,
            "platform_system": self.platform_system,
            "platform_machine": self.platform_machine,
            "python_version": self.python_version,
        }


def _validated_executable(path: Path | None) -> Path | None:
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


def set_default_probe(probe: Callable[..., CapabilitySnapshot] | None) -> None:
    """Register the default capability probe for the no-injection path.

    The compatibility shim (``lib/chatbi_harness``) registers the Claude Code
    runtime probe here; a runtime (e.g. Agno) with no registered probe reports
    the runtime checks unavailable and fails closed (HOOK-004).
    """
    global _DEFAULT_PROBE
    _DEFAULT_PROBE = probe


def _discover_claude_executable(
    forbidden_roots: tuple[Path, ...],
    confirmed_executable: Path | None,
) -> tuple[Path | None, str]:
    safe_directories: list[Path] = []
    for component in os.defpath.split(os.pathsep):
        candidate_directory = Path(component)
        if not component or not candidate_directory.is_absolute():
            continue
        try:
            resolved_directory = candidate_directory.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if any(resolved_directory.is_relative_to(root) for root in forbidden_roots):
            continue
        safe_directories.append(resolved_directory)

    confirmed = _validated_executable(confirmed_executable)
    if confirmed is not None and not any(
        confirmed.is_relative_to(root) for root in forbidden_roots
    ):
        confirmed_parent = confirmed.parent.resolve(strict=True)
        child_path = os.pathsep.join(
            str(path) for path in (confirmed_parent, *safe_directories)
        )
        return confirmed, child_path

    for directory in safe_directories:
        candidate = shutil.which("claude", path=str(directory))
        executable = _validated_executable(Path(candidate) if candidate else None)
        if executable is None:
            continue
        if any(executable.is_relative_to(root) for root in forbidden_roots):
            continue
        return executable, os.pathsep.join(str(path) for path in safe_directories)
    return None, os.pathsep.join(str(path) for path in safe_directories)


def _validate_configuration_path(
    workspace_root: Path,
    path: Path,
    layer: str,
) -> tuple[Path | None, GateDecision | None]:
    def blocked(category: str, reason: str, recovery: str) -> GateDecision:
        return GateDecision.block(
            rule_ids=("SCOPE-001", "PORT-001", "HOOK-004"),
            evidence_refs=(f"config-path:{layer}:{category}",),
            reason=reason,
            recovery=recovery,
        )

    if path.is_absolute():
        return None, blocked(
            "absolute",
            "Configuration input must be Workspace-relative",
            "Use a relative configuration path inside the current Workspace",
        )
    if ".." in path.parts:
        return None, blocked(
            "traversal",
            "Configuration input contains parent traversal",
            "Use a normalized relative configuration path without '..'",
        )

    candidate = workspace_root / path
    cursor = workspace_root
    for part in path.parts:
        cursor = cursor / part
        try:
            if cursor.is_symlink():
                return None, blocked(
                    "symlink",
                    "Configuration input contains a symlink",
                    "Use a real configuration file inside the Workspace",
                )
        except (OSError, RuntimeError):
            return None, blocked(
                "unreadable",
                "Configuration input cannot be inspected",
                "Restore an accessible configuration path inside the Workspace",
            )
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return candidate, None
    except (OSError, RuntimeError):
        return None, blocked(
            "unreadable",
            "Configuration input cannot be resolved",
            "Restore an accessible configuration path inside the Workspace",
        )
    if not resolved.is_relative_to(workspace_root):
        return None, blocked(
            "escape",
            "Configuration input resolves outside the Workspace",
            "Use a real configuration file inside the Workspace",
        )
    return resolved, None


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    check_id: str
    decision: GateDecision

    def to_dict(self) -> dict[str, object]:
        return {"id": self.check_id, **self.decision.to_dict()}


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    checks: tuple[DiagnosticCheck, ...]
    path_references: tuple[PortablePathReference, ...] = ()
    capabilities: CapabilitySnapshot | None = None

    @property
    def status(self) -> str:
        statuses = {check.decision.status for check in self.checks}
        if "block" in statuses:
            return "BLOCKED"
        if "warn" in statuses:
            return "WARN"
        return "PASS"

    @property
    def production_ready(self) -> bool:
        # Cycle 1 has no closed-loop proof for governed policy, sandbox, or adapters.
        return False

    @property
    def pending_configuration(self) -> list[str]:
        return sorted(
            check.check_id
            for check in self.checks
            if check.decision.status == "block"
        )

    @property
    def recovery_actions(self) -> list[str]:
        return list(
            dict.fromkeys(
                check.decision.recovery
                for check in self.checks
                if check.decision.status != "pass"
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": self.status,
            "production_ready": self.production_ready,
            "checks": [check.to_dict() for check in self.checks],
            "capabilities": (
                self.capabilities.to_dict() if self.capabilities is not None else None
            ),
            "pending_configuration": self.pending_configuration,
            "recovery_actions": self.recovery_actions,
            "path_references": [
                reference.to_dict() for reference in self.path_references
            ],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def run_init_diagnostic(
    shared_config: Path,
    local_config: Path | None = None,
    *,
    capability_probe: Callable[[], CapabilitySnapshot] | None = None,
    probe: Callable[[], CapabilitySnapshot] | None = None,
    claude_executable: Path | None = None,
    workspace_root: Path | None = None,
) -> DiagnosticResult:
    """Run the explicit initialization diagnostic from the current Workspace.

    ``probe`` is the module-2 injection point for the runtime capability probe
    (e.g. ``runtimes/claude_code.probe.probe_local_capabilities``); injected
    snapshots are normalized to ``evidence_source="synthetic"`` like the
    legacy ``capability_probe`` contract. When neither probe is injected, the
    historical default path runs: executable discovery (Workspace-external
    only) plus the default probe registered via :func:`set_default_probe` —
    with no registered probe the runtime checks report unavailable and the
    diagnostic fails closed (HOOK-004).

    ``workspace_root`` (module-6 additive): the Workspace to diagnose; ``None``
    keeps the historical ``Path.cwd()`` derivation (zero behavior change).
    Server-side runtimes (Agno) pass the run's workspace explicitly so the
    domain-contract / config-path / forbidden-root checks target the run, not
    the process cwd.
    """

    checks: list[DiagnosticCheck] = []
    try:
        # Module-6 additive injection (MAJOR-1 fix): an explicit
        # ``workspace_root`` lets a server-side runtime (Agno) diagnose the
        # RUN's workspace instead of the process cwd; ``None`` keeps the
        # historical ``Path.cwd()`` behavior byte-for-byte (zero change).
        if workspace_root is None:
            workspace_root = Path.cwd().resolve(strict=True)
        else:
            workspace_root = Path(workspace_root).resolve(strict=True)
        domain_decision = validate_domain_contract(workspace_root)
    except Exception as error:
        domain_decision = fail_closed(
            error,
            evidence_refs=("diagnostic:domain-contract",),
            recovery="Restore an accessible Workspace and retry initialization",
        )
    checks.append(DiagnosticCheck("domain_contract", domain_decision))
    if domain_decision.status == "block":
        return DiagnosticResult(tuple(checks))

    validated_shared, path_decision = _validate_configuration_path(
        workspace_root,
        shared_config,
        "shared",
    )
    validated_local = None
    if path_decision is None and local_config is not None:
        validated_local, path_decision = _validate_configuration_path(
            workspace_root,
            local_config,
            "local",
        )
    if path_decision is not None:
        checks.append(DiagnosticCheck("configuration_path", path_decision))
        return DiagnosticResult(tuple(checks))
    if validated_shared is None:
        checks.append(
            DiagnosticCheck(
                "configuration_path",
                fail_closed(
                    RuntimeError("Shared configuration path was not resolved"),
                    rule_ids=("SCOPE-001", "HOOK-004"),
                    evidence_refs=("config-path:shared:invalid",),
                    recovery="Use a valid relative configuration path",
                ),
            )
        )
        return DiagnosticResult(tuple(checks))

    try:
        config = load_effective_config(validated_shared, validated_local)
    except GateError as error:
        checks.append(DiagnosticCheck("configuration", error.decision))
        return DiagnosticResult(tuple(checks))
    except Exception as error:
        checks.append(
            DiagnosticCheck(
                "configuration",
                fail_closed(
                    error,
                    evidence_refs=("diagnostic:configuration",),
                    recovery="Correct the Harness configuration and retry initialization",
                ),
            )
        )
        return DiagnosticResult(tuple(checks))
    checks.append(
        DiagnosticCheck(
            "configuration",
            GateDecision.pass_(
                rule_ids=("HOOK-004", "PORT-001"),
                evidence_refs=("config:effective",),
                reason="Effective configuration is valid",
                recovery="No action required",
            ),
        )
    )
    references: list[PortablePathReference] = []
    aliases = (
        config["workspace"]["id"],
        *sorted(config["business_codebases"].keys()),
    )
    try:
        for alias in aliases:
            references.append(
                resolve_path_reference(
                    config, alias=alias, target=".",
                    workspace_root=workspace_root,
                )
            )
    except GateError as error:
        checks.append(DiagnosticCheck("paths", error.decision))
        return DiagnosticResult(tuple(checks))
    except Exception as error:
        checks.append(
            DiagnosticCheck(
                "paths",
                fail_closed(
                    error,
                    rule_ids=("SCOPE-001", "SEC-003", "HOOK-004"),
                    evidence_refs=("diagnostic:paths",),
                    recovery="Correct the configured roots and retry initialization",
                ),
            )
        )
        return DiagnosticResult(tuple(checks))
    checks.append(
        DiagnosticCheck(
            "paths",
            GateDecision.pass_(
                rule_ids=("SCOPE-001", "SEC-003", "PORT-001", "HOOK-004"),
                evidence_refs=("path:portable-references",),
                reason="Workspace and Business Codebase paths are valid",
                recovery="No action required",
            ),
        )
    )
    try:
        if probe is not None:
            capabilities = probe()
            if not isinstance(capabilities, CapabilitySnapshot):
                raise TypeError("Capability probe returned an invalid snapshot")
            capabilities = replace(capabilities, evidence_source="synthetic")
        elif capability_probe is not None:
            capabilities = capability_probe()
            if not isinstance(capabilities, CapabilitySnapshot):
                raise TypeError("Capability probe returned an invalid snapshot")
            capabilities = replace(capabilities, evidence_source="synthetic")
        else:
            # Historical default path (no injected probe): discover a
            # Workspace-external claude executable, then hand it to the
            # default probe registered by the compatibility shim. With no
            # registered probe (vendor-free kernel) the runtime checks report
            # unavailable and fail closed (HOOK-004) instead of probing.
            forbidden_roots = (
                workspace_root.resolve(strict=True),
                *(
                    Path(path_value).resolve(strict=True)
                    for path_value in config["path_bindings"].values()
                ),
            )
            discovered_claude, safe_path = _discover_claude_executable(
                forbidden_roots,
                claude_executable,
            )
            if _DEFAULT_PROBE is None:
                capabilities = CapabilitySnapshot(
                    claude_available=False,
                    claude_version=None,
                    doctor_status="unavailable",
                    logged_in=None,
                    sandbox_available=None,
                    available_adapters=(),
                    evidence_source="synthetic",
                )
            else:
                capabilities = _DEFAULT_PROBE(
                    claude_executable=discovered_claude,
                    safe_path=safe_path,
                )
    except Exception as error:
        checks.append(
            DiagnosticCheck(
                "capability_probe",
                fail_closed(
                    error,
                    rule_ids=("SEC-001", "HOOK-004"),
                    evidence_refs=("capability:probe",),
                    recovery="Restore the local capability probe and retry initialization",
                ),
            )
        )
        return DiagnosticResult(tuple(checks), tuple(references))

    def add_capability_check(
        check_id: str,
        *,
        passed: bool,
        rule_ids: tuple[str, ...],
        evidence_ref: str,
        pass_reason: str,
        block_reason: str,
        recovery: str,
    ) -> None:
        factory = GateDecision.pass_ if passed else GateDecision.block
        checks.append(
            DiagnosticCheck(
                check_id,
                factory(
                    rule_ids=rule_ids,
                    evidence_refs=(evidence_ref,),
                    reason=pass_reason if passed else block_reason,
                    recovery="No action required" if passed else recovery,
                ),
            )
        )

    if not capabilities.claude_available or capabilities.claude_version is None:
        version_decision = GateDecision.block(
            rule_ids=("PORT-001", "HOOK-002"),
            evidence_refs=("capability:claude-version:unavailable",),
            reason="Claude Code version is unavailable",
            recovery="Install a supported Claude Code version and rerun initialization",
        )
    elif capabilities.claude_version == _CLAUDE_VERSION_BASELINE:
        version_decision = GateDecision.pass_(
            rule_ids=("PORT-001", "HOOK-002"),
            evidence_refs=("capability:claude-version:baseline",),
            reason="Claude Code matches the verified local baseline",
            recovery="No action required",
        )
    else:
        version_decision = GateDecision.warn(
            rule_ids=("PORT-001", "HOOK-002"),
            evidence_refs=("capability:claude-version:update",),
            reason="Claude Code differs from the verified local baseline",
            recovery="Run compatibility checks for this Claude Code version",
        )
    checks.append(DiagnosticCheck("claude_version", version_decision))
    add_capability_check(
        "claude_doctor",
        passed=capabilities.doctor_status == "pass",
        rule_ids=("HOOK-002", "HOOK-004"),
        evidence_ref=f"capability:claude-doctor:{capabilities.doctor_status}",
        pass_reason="Claude doctor completed successfully",
        block_reason="Claude doctor did not complete successfully",
        recovery="Run Claude doctor locally and resolve the reported capability issue",
    )
    add_capability_check(
        "claude_login",
        passed=capabilities.logged_in is True,
        rule_ids=("SEC-001", "HOOK-002"),
        evidence_ref="capability:claude-login",
        pass_reason="Claude login is available",
        block_reason="Claude login is unavailable or unverified",
        recovery="Authenticate Claude Code before using ChatBI commands",
    )
    add_capability_check(
        "sandbox",
        passed=capabilities.sandbox_available is True,
        rule_ids=("SEC-001", "HOOK-004"),
        evidence_ref="capability:sandbox",
        pass_reason="Sandbox capability is available",
        block_reason="Sandbox capability is unavailable or unverified",
        recovery="Enable and verify the required Claude Code sandbox",
    )

    configured_adapters = tuple(
        config["adapters"][kind][index]
        for kind in ("semantic", "query")
        for index in range(len(config["adapters"][kind]))
    )
    available_adapters = set(capabilities.available_adapters)
    add_capability_check(
        "adapters",
        passed=(
            bool(configured_adapters)
            and set(configured_adapters).issubset(available_adapters)
        ),
        rule_ids=("PORT-001", "HOOK-004"),
        evidence_ref="capability:adapters",
        pass_reason="Configured adapters report available capabilities",
        block_reason="Configured adapters are missing or unavailable",
        recovery="Configure and verify at least one declared adapter capability",
    )

    owners = config["governance"]["owners"]
    has_owner = bool(owners["default_domain_owner"] or owners["metrics"])
    add_capability_check(
        "governance_owner",
        passed=has_owner,
        rule_ids=("SEM-003", "HOOK-004"),
        evidence_ref="config:governance-owner",
        pass_reason="A governed domain owner is configured",
        block_reason="No governed domain owner is configured",
        recovery="Configure a real domain owner before production use",
    )
    governance = config["governance"]
    add_capability_check(
        "pii_policy",
        passed=bool(
            governance["pii_policy_ref"] and governance["restricted_disclosure"]
        ),
        rule_ids=("SEC-002", "SEC-003", "HOOK-004"),
        evidence_ref="config:pii-policy",
        pass_reason="PII policy and disclosure mode are configured",
        block_reason="PII policy or disclosure mode is unconfigured",
        recovery="Configure the governed PII policy reference and disclosure mode",
    )
    evaluation = config["evaluation"]
    add_capability_check(
        "release_threshold",
        passed=(
            evaluation["release_threshold"] is not None
            and bool(evaluation["threshold_owner"])
        ),
        rule_ids=("EVAL-004", "HOOK-004"),
        evidence_ref="config:release-threshold",
        pass_reason="Release threshold and owner are configured",
        block_reason="Release threshold or threshold owner is unconfigured",
        recovery="Have the accountable owner configure a release threshold",
    )

    uses_content_revision = any(
        reference.revision_kind == "content_sha256" for reference in references
    )
    revision_decision = GateDecision.pass_(
        rule_ids=("PORT-001",),
        evidence_refs=(
            "path:content-revision" if uses_content_revision else "path:git-revision",
        ),
        reason=(
            "Portable content hash revision evidence is available"
            if uses_content_revision
            else "Git revision evidence is available"
        ),
        recovery="No action required",
    )
    checks.append(DiagnosticCheck("revision_evidence", revision_decision))
    return DiagnosticResult(tuple(checks), tuple(references), capabilities)
