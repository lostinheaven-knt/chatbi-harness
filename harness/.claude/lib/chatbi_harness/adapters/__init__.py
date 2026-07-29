"""Adapter selection chain: managed -> approved CLI -> STOP (technical-design §8.2).

The dispatcher walks the configured adapter IDs for a capability kind in
declaration order:

1. ``managed:<name>``: if the managed runtime reports available and the policy
   authorizes the selection, use it. In this environment no real managed
   runtime exists, so :class:`ManagedAdapter` deterministically reports
   official-only / NOT YET EXERCISED and the chain continues to CLI.
2. ``cli:<name>``: if the argv is legal, the executable resolves to an
   allowlist absolute path, and the policy authorizes the selection, use it.
   An illegal argv or a non-allowlisted executable STOPs fail-closed (no shell
   fallback).
3. ``fixture:<name>``: only available when ``fixture_enabled`` is true and the
   run mode is ``test``/``example``. Outside explicit test mode it is rejected
   (PORT-001). The Fixture adapter implementation is Ticket 03; until it is
   registered the chain STOPs with a clear pointer rather than silently
   succeeding.

If no adapter is usable the dispatcher STOPs with the missing capabilities and
the minimum authorization required to proceed (SEM-001, PORT-001).

CLI adapters launch with an argv array directly (``shell=False``); shell
metacharacters, pipes, redirects, command substitution and newlines are
rejected, the executable must resolve to an allowlist absolute path, cwd is
fixed to the Workspace, and the environment is built from a whitelist with no
``--token``/``--api-key`` flags (technical-design §8.2, §13).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..config import EffectiveConfig
from ..gates import GateDecision
from ..policy import PolicyRequest, decide as policy_decide
from .base import (
    Adapter,
    AdapterCapabilities,
    AdapterEvidence,
    validate_adapter_id,
)


# Re-declared to mirror config._SECRET_ARG (Cycle 1). Not imported from a
# private name; the rejection semantics are reused per Ticket 02.
_SECRET_ARGV = re.compile(
    r"^--?(?:api[-_]?key|token|password|secret)(?:[-_]file)?(?:=|$)",
    re.IGNORECASE,
)

# Characters that must never appear in a single CLI argv element when launched
# with shell=False. Their presence indicates an attempt to build a shell string
# rather than an argv array (technical-design §8.2, §13 Shell/命令注入).
_SHELL_METACHARACTERS = frozenset("|;&`$<>\\\n\r")

# A safe minimal PATH used to resolve bare executable names, mirroring
# paths._SAFE_SYSTEM_PATH. Only absolute components of os.defpath are used so
# untrusted PATH entries cannot influence executable resolution.
_SAFE_SYSTEM_PATH = os.pathsep.join(
    component
    for component in os.defpath.split(os.pathsep)
    if component and Path(component).is_absolute()
)

# Minimal environment baseline for CLI subprocesses. Only PATH, locale and
# declared credential environment-variable NAMES (values sourced from the
# current process environment) are passed through; no uncontrolled variable
# leaks to the CLI.
_CLI_ENV_BASE = {
    "LANG": "C",
    "LC_ALL": "C",
}

_RUN_MODES = frozenset({"production", "test", "example"})
_FIXTURE_RUN_MODES = frozenset({"test", "example"})
_ADAPTER_KINDS = frozenset({"semantic", "query"})
_CREDENTIAL_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def validate_cli_argv(argv: tuple[str, ...] | list[str]) -> str | None:
    """Return an error category if ``argv`` is illegal, else None.

    Rejects shell metacharacters, newlines, command substitution and sensitive
    flags (``--token``/``--api-key``/``--secret``) in any element. Each element
    must be a non-empty string. This is the argv-array invariant that makes
    ``shell=False`` safe (technical-design §8.2).
    """
    if not isinstance(argv, (tuple, list)) or len(argv) < 1:
        return "argv_empty"
    for element in argv:
        if not isinstance(element, str) or not element:
            return "argv_empty_element"
        if _SECRET_ARGV.match(element):
            return "sensitive_flag"
        if any(ch in _SHELL_METACHARACTERS for ch in element):
            return "shell_metacharacter"
    return None


def resolve_executable(argv0: str, allowlist: tuple[str, ...]) -> Path | None:
    """Resolve ``argv0`` to an allowlisted absolute executable path.

    Bare names are resolved via a safe system PATH; the resolved realpath must
    be a regular executable file whose realpath is in ``allowlist``. A bare name
    not on the safe system PATH (e.g. a homebrew tool in /opt/homebrew/bin,
    outside os.defpath) may also resolve directly to an allowlist entry whose
    basename matches - the allowlist is the security boundary, so this only
    resolves to an already-approved absolute path. Returns None if the executable
    cannot be found or is not approved.
    """
    if not argv0:
        return None
    try:
        if Path(argv0).is_absolute():
            candidate = argv0
        else:
            candidate = shutil.which(argv0, path=_SAFE_SYSTEM_PATH or None)
            if candidate is None:
                # Bare name not on the safe system PATH: try matching it
                # directly to an allowlist entry by basename. Only already-
                # approved absolute paths are considered (the allowlist is the
                # security boundary); this lets homebrew/non-defpath tools be
                # approved without widening the system PATH.
                return _resolve_bare_from_allowlist(argv0, allowlist)
        resolved = Path(candidate).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    try:
        mode = resolved.stat(follow_symlinks=False).st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        return None
    allowlist_real: set[str] = set()
    for entry in allowlist:
        try:
            allowlist_real.add(str(Path(entry).resolve(strict=True)))
        except (OSError, RuntimeError):
            continue
    if str(resolved) not in allowlist_real:
        return None
    return resolved


def _resolve_bare_from_allowlist(
    argv0: str, allowlist: tuple[str, ...]
) -> Path | None:
    """Resolve a bare name to an allowlisted executable by basename match.

    Only already-approved absolute allowlist entries are considered; the system
    PATH is not consulted. Returns the resolved realpath of the first matching
    regular executable file, or None.
    """
    for entry in allowlist:
        if Path(entry).name != argv0:
            continue
        try:
            resolved = Path(entry).resolve(strict=True)
            mode = resolved.stat(follow_symlinks=False).st_mode
        except (OSError, RuntimeError):
            continue
        if stat.S_ISREG(mode) and os.access(resolved, os.X_OK):
            return resolved
    return None


def build_cli_env(credential_env_names: tuple[str, ...] = ()) -> dict[str, str]:
    """Build a whitelisted environment for a CLI subprocess.

    Only locale, a safe PATH and declared credential environment-variable names
    (with values sourced from the current process environment) are passed
    through. No uncontrolled variable leaks (technical-design §8.2, §13).
    """
    env: dict[str, str] = dict(_CLI_ENV_BASE)
    if _SAFE_SYSTEM_PATH:
        env["PATH"] = _SAFE_SYSTEM_PATH
    for name in credential_env_names:
        if not _CREDENTIAL_NAME.fullmatch(name):
            continue
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


@dataclass(frozen=True, slots=True)
class MissingCapability:
    """One reason an adapter candidate was not usable."""

    adapter_id: str
    reason: str
    error_category: str

    def to_dict(self) -> dict[str, str]:
        return {
            "adapter_id": self.adapter_id,
            "reason": self.reason,
            "error_category": self.error_category,
        }


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    """Result of the adapter selection chain for one capability kind."""

    status: str  # "selected" | "stopped"
    adapter_id: str | None
    adapter: Adapter | None
    selection_evidence: AdapterEvidence | None
    stop_decision: GateDecision | None
    missing_capabilities: tuple[MissingCapability, ...]
    minimal_authorization: str

    @classmethod
    def selected(
        cls,
        *,
        adapter_id: str,
        adapter: Adapter,
        evidence: AdapterEvidence,
    ) -> "SelectionOutcome":
        return cls(
            status="selected",
            adapter_id=adapter_id,
            adapter=adapter,
            selection_evidence=evidence,
            stop_decision=None,
            missing_capabilities=(),
            minimal_authorization="",
        )

    @classmethod
    def stopped(
        cls,
        *,
        missing: tuple[MissingCapability, ...],
        minimal_authorization: str,
        decision: GateDecision,
    ) -> "SelectionOutcome":
        return cls(
            status="stopped",
            adapter_id=None,
            adapter=None,
            selection_evidence=None,
            stop_decision=decision,
            missing_capabilities=missing,
            minimal_authorization=minimal_authorization,
        )

    def to_dict(self) -> dict[str, Any]:
        if self.status == "selected":
            return {
                "status": "selected",
                "adapter_id": self.adapter_id,
                "evidence": self.selection_evidence.to_dict()
                if self.selection_evidence is not None
                else None,
            }
        return {
            "status": "stopped",
            "missing_capabilities": [m.to_dict() for m in self.missing_capabilities],
            "minimal_authorization": self.minimal_authorization,
            "decision": self.stop_decision.to_dict()
            if self.stop_decision is not None
            else None,
        }


class ManagedAdapter:
    """Managed connection adapter (official-only / NOT YET EXERCISED).

    No real managed runtime is available in this environment. ``healthcheck``
    deterministically reports unavailable; the selection chain continues to the
    approved CLI. The adapter ID mirrors Cycle 1 ``_ADAPTER_ID`` (PORT-001).
    Discover/compile/query/quality/lineage all return the unavailable evidence
    because the managed runtime is not exercised; they must never be reached on
    the selected path.
    """

    __slots__ = ("_adapter_id", "_kind")

    def __init__(self, adapter_id: str, kind: str) -> None:
        if not validate_adapter_id(adapter_id) or not adapter_id.startswith("managed:"):
            raise ValueError(f"Invalid managed adapter ID: {adapter_id}")
        if kind not in _ADAPTER_KINDS:
            raise ValueError(f"Unknown adapter kind: {kind}")
        self._adapter_id = adapter_id
        self._kind = kind

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            discover=True, query=True, quality=True, lineage=True, mutate=False
        )

    def _unavailable(self) -> AdapterEvidence:
        return AdapterEvidence.unavailable(
            adapter_id=self._adapter_id,
            evidence_source="managed",
            rule_ids=("PORT-001",),
            error_category="not_yet_exercised",
            reason=(
                "Managed runtime is official-only / NOT YET EXERCISED in this "
                "environment; no real managed connection is available"
            ),
            recovery=(
                "Configure and verify a real managed connection, or rely on "
                "an approved CLI adapter"
            ),
        )

    def healthcheck(self, context: Mapping[str, Any] | None = None) -> AdapterEvidence:
        return self._unavailable()

    def discover(self, request: Mapping[str, Any]) -> AdapterEvidence:
        return self._unavailable()

    def compile(self, query_spec: Mapping[str, Any]) -> AdapterEvidence:
        return self._unavailable()

    def query(
        self,
        compiled: Mapping[str, Any],
        disclosure_policy: Mapping[str, Any] | None = None,
    ) -> AdapterEvidence:
        return self._unavailable()

    def quality(self, source_refs: tuple[str, ...]) -> AdapterEvidence:
        return self._unavailable()

    def lineage(self, source_refs: tuple[str, ...]) -> AdapterEvidence:
        return self._unavailable()


class CliAdapter:
    """Approved CLI adapter launched as an argv array (technical-design §8.2).

    The executable is resolved to an allowlist absolute path, cwd is fixed to
    the Workspace, the environment is built from a whitelist, and stdout is
    captured as a hashed, untrusted data payload that is never spliced into a
    Shell or system prompt. Shell metacharacters, pipes, redirects and command
    substitution are rejected before launch by :func:`validate_cli_argv`.
    """

    __slots__ = (
        "_adapter_id",
        "_kind",
        "_argv",
        "_executable",
        "_cwd",
        "_env",
        "_credential_env_names",
    )

    def __init__(
        self,
        *,
        adapter_id: str,
        kind: str,
        argv: tuple[str, ...],
        executable: Path,
        cwd: Path,
        env: Mapping[str, str],
        credential_env_names: tuple[str, ...] = (),
    ) -> None:
        if not validate_adapter_id(adapter_id) or not adapter_id.startswith("cli:"):
            raise ValueError(f"Invalid CLI adapter ID: {adapter_id}")
        if kind not in _ADAPTER_KINDS:
            raise ValueError(f"Unknown adapter kind: {kind}")
        illegal = validate_cli_argv(argv)
        if illegal is not None:
            raise ValueError(f"Illegal CLI argv: {illegal}")
        self._adapter_id = adapter_id
        self._kind = kind
        self._argv = tuple(argv)
        self._executable = executable
        self._cwd = cwd
        self._env = dict(env)
        self._credential_env_names = tuple(credential_env_names)

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            discover=True, query=True, quality=True, lineage=True, mutate=False
        )

    def _run(self, operation_payload: Mapping[str, Any] | None) -> AdapterEvidence:
        # argv array, shell=False; cwd fixed to Workspace; env whitelisted.
        command = [str(self._executable), *self._argv[1:]]
        stdin_bytes = (
            json.dumps(operation_payload, ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            )
            if operation_payload is not None
            else b""
        )
        try:
            result = subprocess.run(
                command,
                shell=False,
                cwd=str(self._cwd),
                env=self._env,
                input=stdin_bytes,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return AdapterEvidence.error(
                adapter_id=self._adapter_id,
                evidence_source="cli",
                error_category="run_failure",
                reason=f"CLI execution failed: {type(error).__name__}",
                recovery="Verify the approved CLI executable and retry",
                rule_ids=("PORT-001",),
            )
        payload = self._parse_stdout(result.stdout or b"", result.returncode)
        if result.returncode == 0:
            return AdapterEvidence.ok(
                adapter_id=self._adapter_id,
                evidence_source="cli",
                payload=payload,
                rule_ids=("PORT-001",),
                reason="CLI adapter produced structured evidence",
                recovery="No action required",
            )
        return AdapterEvidence.error(
            adapter_id=self._adapter_id,
            evidence_source="cli",
            error_category="nonzero_exit",
            reason=f"CLI exited with status {result.returncode}",
            recovery="Inspect the CLI payload and retry with valid input",
            rule_ids=("PORT-001",),
            payload=payload,
        )

    @staticmethod
    def _parse_stdout(stdout_bytes: bytes, returncode: int) -> dict[str, Any]:
        # stdout is untrusted data: parse as JSON when possible, otherwise wrap
        # the raw text. The payload is always tagged untrusted and never
        # returned as a bare string that could be interpolated into a prompt.
        text = stdout_bytes.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {"untrusted": True, "stdout_raw": text, "returncode": returncode}
        return {"untrusted": True, "stdout": parsed, "returncode": returncode}

    def healthcheck(
        self, context: Mapping[str, Any] | None = None
    ) -> AdapterEvidence:
        return self._run(None)

    def discover(self, request: Mapping[str, Any]) -> AdapterEvidence:
        return self._run({"operation": "discover", "request": dict(request)})

    def compile(self, query_spec: Mapping[str, Any]) -> AdapterEvidence:
        return self._run({"operation": "compile", "query_spec": dict(query_spec)})

    def query(
        self,
        compiled: Mapping[str, Any],
        disclosure_policy: Mapping[str, Any] | None = None,
    ) -> AdapterEvidence:
        payload: dict[str, Any] = {"operation": "query", "compiled": dict(compiled)}
        if disclosure_policy is not None:
            payload["disclosure_policy"] = dict(disclosure_policy)
        return self._run(payload)

    def quality(self, source_refs: tuple[str, ...]) -> AdapterEvidence:
        return self._run(
            {"operation": "quality", "source_refs": list(source_refs)}
        )

    def lineage(self, source_refs: tuple[str, ...]) -> AdapterEvidence:
        return self._run(
            {"operation": "lineage", "source_refs": list(source_refs)}
        )


def _minimal_authorization(kind: str) -> str:
    return (
        f"Provide an authorized managed:{kind} runtime or an approved "
        f"cli:{kind} adapter whose executable resolves to the allowlist; "
        f"fixture is only available in explicit test/example mode"
    )


def _stop_outcome(
    kind: str,
    missing: tuple[MissingCapability, ...],
    *,
    rule_ids: tuple[str, ...],
    evidence_ref: str,
    reason: str,
    recovery: str,
) -> SelectionOutcome:
    return SelectionOutcome.stopped(
        missing=missing,
        minimal_authorization=_minimal_authorization(kind),
        decision=GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=(evidence_ref,),
            reason=reason,
            recovery=recovery,
        ),
    )


def select_adapter(
    config: EffectiveConfig,
    *,
    kind: str,
    run_mode: str,
    workspace_root: Path,
    cli_allowlist: tuple[str, ...] = (),
    selection_request: PolicyRequest | None = None,
) -> SelectionOutcome:
    """Select an adapter for ``kind`` via the managed->CLI->STOP chain.

    ``selection_request`` defaults to a ``discover`` policy request (adapter
    selection is a discover_read operation); callers may pass a more specific
    request to enforce per-operation authorization. The chain is deterministic
    for identical inputs (HOOK-001).
    """
    if kind not in _ADAPTER_KINDS:
        raise ValueError(f"Unknown adapter kind: {kind}")
    if run_mode not in _RUN_MODES:
        raise ValueError(f"Unknown run mode: {run_mode}")

    adapter_ids = tuple(config["adapters"][kind])  # type: ignore[index]
    if not adapter_ids:
        return _stop_outcome(
            kind,
            (
                MissingCapability(
                    "(none)",
                    f"No adapters configured for kind {kind}",
                    "none_configured",
                ),
            ),
            rule_ids=("SEM-001", "PORT-001", "HOOK-004"),
            evidence_ref=f"adapter:{kind}:empty",
            reason=f"No adapters configured for kind {kind}",
            recovery="Configure at least one managed or approved CLI adapter",
        )

    request = selection_request or PolicyRequest(
        request_type="discover",
        actor="agent",
        purpose="adapter_selection",
    )

    missing: list[MissingCapability] = []
    for adapter_id in adapter_ids:
        if not validate_adapter_id(adapter_id):
            # The configuration schema should prevent this; fail closed.
            return _stop_outcome(
                kind,
                tuple(missing)
                + (
                    MissingCapability(
                        adapter_id, "Invalid adapter ID", "invalid_id"
                    ),
                ),
                rule_ids=("PORT-001", "HOOK-004"),
                evidence_ref="adapter:invalid-id",
                reason="Configured adapter ID is invalid",
                recovery="Use an adapter ID of the form managed:|cli:|fixture: name",
            )

        if adapter_id.startswith("managed:"):
            adapter = ManagedAdapter(adapter_id, kind)
            health = adapter.healthcheck()
            if health.status == "ok":
                decision = policy_decide(config, request)
                if decision.status == "pass":
                    return SelectionOutcome.selected(
                        adapter_id=adapter_id, adapter=adapter, evidence=health
                    )
                missing.append(
                    MissingCapability(
                        adapter_id,
                        "managed adapter not authorized by policy",
                        "not_authorized",
                    )
                )
            else:
                missing.append(
                    MissingCapability(
                        adapter_id,
                        health.reason,
                        health.error_category or "unavailable",
                    )
                )
            continue

        if adapter_id.startswith("cli:"):
            cli_key = adapter_id.split(":", 1)[1]
            cli_config = config["cli_adapters"].get(cli_key)  # type: ignore[union-attr]
            if not cli_config:
                missing.append(
                    MissingCapability(
                        adapter_id,
                        "CLI adapter is not configured in local cli_adapters",
                        "cli_not_configured",
                    )
                )
                continue
            argv = tuple(cli_config["argv"])  # type: ignore[index]
            illegal = validate_cli_argv(argv)
            if illegal is not None:
                # Security fail-closed: do not skip, do not fall back to shell.
                return _stop_outcome(
                    kind,
                    tuple(missing)
                    + (
                        MissingCapability(
                            adapter_id,
                            f"CLI argv rejected: {illegal}",
                            illegal,
                        ),
                    ),
                    rule_ids=("SEC-003", "PORT-001", "HOOK-004"),
                    evidence_ref=f"adapter:{adapter_id}:argv",
                    reason=f"CLI argv is illegal: {illegal}",
                    recovery="Use an argv array without shell metacharacters, "
                    "newlines or sensitive flags",
                )
            executable = resolve_executable(argv[0], cli_allowlist)
            if executable is None:
                return _stop_outcome(
                    kind,
                    tuple(missing)
                    + (
                        MissingCapability(
                            adapter_id,
                            "CLI executable does not resolve to an approved "
                            "allowlist path",
                            "not_in_allowlist",
                        ),
                    ),
                    rule_ids=("SEC-003", "PORT-001", "HOOK-004"),
                    evidence_ref=f"adapter:{adapter_id}:executable",
                    reason="CLI executable does not resolve to an approved "
                    "allowlist path",
                    recovery="Approve the executable by its realpath in the "
                    "CLI allowlist",
                )
            decision = policy_decide(config, request)
            if decision.status != "pass":
                missing.append(
                    MissingCapability(
                        adapter_id,
                        "CLI adapter not authorized by policy",
                        "not_authorized",
                    )
                )
                continue
            credential_env_names = tuple(
                cli_config["credential_env_names"]  # type: ignore[index]
            )
            adapter = CliAdapter(
                adapter_id=adapter_id,
                kind=kind,
                argv=argv,
                executable=executable,
                cwd=workspace_root,
                env=build_cli_env(credential_env_names),
                credential_env_names=credential_env_names,
            )
            selection_evidence = AdapterEvidence.ok(
                adapter_id=adapter_id,
                evidence_source="local_probe",
                payload={
                    "selected": True,
                    "kind": kind,
                    "executable_resolved": True,
                    "argv_legal": True,
                },
                rule_ids=("PORT-001",),
                reason="CLI adapter selected; argv legal and executable "
                "resolved to allowlist",
                recovery="No action required",
            )
            return SelectionOutcome.selected(
                adapter_id=adapter_id, adapter=adapter, evidence=selection_evidence
            )

        if adapter_id.startswith("fixture:"):
            fixture_enabled = bool(config["adapters"]["fixture_enabled"])  # type: ignore[index]
            if not fixture_enabled or run_mode not in _FIXTURE_RUN_MODES:
                return _stop_outcome(
                    kind,
                    tuple(missing)
                    + (
                        MissingCapability(
                            adapter_id,
                            "fixture adapter rejected outside explicit "
                            "test/example mode",
                            "fixture_not_test_mode",
                        ),
                    ),
                    rule_ids=("PORT-001", "HOOK-004"),
                    evidence_ref=f"adapter:{adapter_id}:fixture-mode",
                    reason="Fixture adapter is not available outside explicit "
                    "test/example mode",
                    recovery="Enable fixture mode and run with a test/example "
                    "flag, or configure a real adapter",
                )
            # TODO(Ticket-03): construct the Fixture adapter implementation.
            # Until the Fixture adapter is registered, the chain STOPs with a
            # clear pointer rather than silently succeeding (PORT-001).
            missing.append(
                MissingCapability(
                    adapter_id,
                    "fixture adapter not yet implemented (Ticket 03)",
                    "fixture_pending",
                )
            )
            continue

    # Exhausted the candidate list: STOP with missing capabilities and the
    # minimum authorization required to proceed.
    return _stop_outcome(
        kind,
        tuple(missing),
        rule_ids=("SEM-001", "PORT-001", "HOOK-004"),
        evidence_ref=f"adapter:{kind}:none-usable",
        reason=f"No usable adapter for kind {kind}",
        recovery="Configure and authorize a managed or approved CLI adapter, "
        "or run in explicit test mode with fixture enabled",
    )


__all__ = [
    "Adapter",
    "AdapterCapabilities",
    "AdapterEvidence",
    "CliAdapter",
    "ManagedAdapter",
    "MissingCapability",
    "SelectionOutcome",
    "build_cli_env",
    "resolve_executable",
    "select_adapter",
    "validate_adapter_id",
    "validate_cli_argv",
]
