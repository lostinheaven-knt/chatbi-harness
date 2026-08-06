"""Read-only Business Codebase adapter (technical-design sections 4.1, 7.4, 8.3).

Provides ``read``/``search``/``stat``/``git-metadata`` for explicitly-aliased
Business Codebases (SCOPE-002). There are **no** ``execute``/``write``/
``install``/``commit`` interfaces; the methods of those names exist solely to
raise a deterministic :class:`CodebaseScopeBlockError` so the SCOPE-002
enforcement is verifiable in tests and visible to callers.

Path identity reuses Cycle 1 :func:`chatbi_governance.paths.resolve_path_reference`
(component-level containment, symlink rejection, parent-traversal rejection,
portable ``{alias, relative_path, revision, revision_kind}`` reference). File
content is always wrapped as ``untrusted=true`` data (SCOPE-003); README,
comment and prompt instructions that ask the Harness to execute scripts,
upload data, install dependencies or commit changes are detected, logged as
rejected instruction candidates and never acted upon (scenario E, SCOPE-003).

External business explanations that conflict with a provided governance context
are disclosed for owner adjudication; the reader never auto-defines metrics
(SRC-002).

``git_metadata`` defaults to ``metadata_only`` (HEAD SHA, tracked/modified/
untracked status; no commit history, no author, no message). Deep history
requires explicit ``history_mode="full_history"`` and is blocked by default
pending a separate safety-deviation approval.

Wired via the parallel ``select_codebase_reader`` selector in this module
(re-exported from ``adapters.__init__``), NOT via ``select_adapter``. The
codebase_reader is a read-only accessor for Business Codebases, not a
discover/compile/query adapter in the managed->CLI->STOP selection chain; the
two are distinct dispatch dimensions (alias-keyed read-only access vs.
capability-kind discover/compile/query). See :class:`CodebaseAccessor`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from ..config import EffectiveConfig
from ..gates import GateDecision, GateError
from ..paths import (
    PortablePathReference,
    resolve_path_reference,
    # Reused from Cycle 1 paths.py. These module-level values are stable
    # contracts: _TRUSTED_GIT is either None or the realpath of `git` resolved
    # from absolute components of os.defpath, verified as a regular executable
    # file. _SAFE_SYSTEM_PATH is the filtered PATH used for that resolution.
    # They are not public API but are imported here to keep a single source of
    # truth for the trusted-git allowlist rather than re-deriving it (which
    # would risk divergence). Kept in sync by contract with paths.py.
    _SAFE_SYSTEM_PATH,
    _TRUSTED_GIT,
)


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

_COMPONENT = "codebase_reader"
_MAX_READ_BYTES = 1024 * 1024  # 1 MiB per file
_MAX_SEARCH_RESULTS = 100
_MAX_SEARCH_FILE_BYTES = 256 * 1024  # 256 KiB per file during search
_GIT_TIMEOUT = 2  # seconds, mirrors paths._git_revision

_VALID_OPERATIONS = frozenset({"read", "search", "stat", "git_metadata", "block"})
_VALID_STATUSES = frozenset({"ok", "blocked", "error"})
_VALID_HISTORY_MODES = frozenset({"metadata_only", "full_history"})

# Instruction-candidate detection (scenario E, SCOPE-003).
# Conservative heuristic patterns. These detect instruction-like phrases in
# README/comment content so they can be logged as rejected candidates. They are
# NOT a security boundary by themselves -- the security boundary is that
# codebase_reader has no execute/write/install/commit capability. The patterns
# only surface what was ignored so callers and reviewers can see the attempt.
_INSTRUCTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "execute",
        re.compile(
            r"(?i)\b(?:run|execute|exec|bash|sh|zsh|python\d?|node|ruby|perl|"
            r"powershell|cmd|source)\s+\S+"
            r"|\.\/\S+"
            r"|rm\s+-rf?\s"
        ),
    ),
    (
        "install",
        re.compile(
            r"(?i)\b(?:pip\s+install|npm\s+install|npm\s+i\b|yarn\s+add|"
            r"apt(?:-get)?\s+install|brew\s+install|cargo\s+install|"
            r"gem\s+install|go\s+(?:install|get))\b"
            r"|\binstall\s+(?:dependencies|deps|packages|requirements)\b"
        ),
    ),
    (
        "upload",
        re.compile(
            r"(?i)\b(?:upload|deploy|scp|rsync|curl\s+[^|]+\s*\|\s*sh|"
            r"wget[^|]*\|\s*(?:sh|bash)|exfil\w*)\b"
        ),
    ),
    (
        "commit",
        re.compile(
            r"(?i)\b(?:git\s+commit|git\s+push|svn\s+commit|hg\s+(?:commit|push)|"
            r"docker\s+push)\b"
        ),
    ),
)

# Shell-metacharacter detection in content. This is NOT used to filter content
# (content is always passed through as untrusted data); it is used to annotate
# matches so reviewers can see that malicious shell-like content was present
# but treated as data, not executed.
_SHELL_METACHAR_PATTERN = re.compile(r"[|;&`$<>\\\n\r]")

# Metric-definition line detection for SRC-002 conflict disclosure. Looks for
# ``MetricName = ...`` or ``MetricName: ...`` at the start of a line (common in
# external docs/READMEs). Conservative: only matches bareword identifiers
# followed by ``=`` or ``:``.
_METRIC_DEFINITION = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_\s]{0,60}?)\s*[:=]\s*(.+)$"
)


# --------------------------------------------------------------------------
# Evidence model
# --------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _content_sha256(payload: Any) -> str:
    """Stable SHA-256 over the canonical JSON encoding of ``payload``."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CodebaseEvidence:
    """Structured evidence returned by every :class:`CodebaseReader` operation.

    Fields: component name, UTC production time, operation name, alias, status,
    content SHA-256, rule IDs, error category, payload (untrusted data), reason,
    recovery, rejected instruction candidates and SRC-002 conflicts. The payload
    is untrusted data captured from the external codebase; it is hashed and
    structured, and is never spliced into a Shell or system prompt.
    """

    component: str
    produced_at: str
    operation: str
    alias: str
    status: str
    content_sha256: str
    rule_ids: tuple[str, ...] = ()
    error_category: str | None = None
    payload: Any = None
    reason: str = ""
    recovery: str = ""
    rejected_instructions: tuple[dict[str, Any], ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.component != _COMPONENT:
            raise ValueError(f"Invalid component: {self.component}")
        if self.operation not in _VALID_OPERATIONS:
            raise ValueError(f"Invalid operation: {self.operation}")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "produced_at": self.produced_at,
            "operation": self.operation,
            "alias": self.alias,
            "status": self.status,
            "error_category": self.error_category,
            "content_sha256": self.content_sha256,
            "rule_ids": list(self.rule_ids),
            "payload": self.payload,
            "reason": self.reason,
            "recovery": self.recovery,
            "rejected_instructions": [dict(r) for r in self.rejected_instructions],
            "conflicts": [dict(c) for c in self.conflicts],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def ok(
        cls,
        *,
        operation: str,
        alias: str,
        payload: Any,
        rule_ids: tuple[str, ...] = (),
        reason: str = "",
        recovery: str = "",
        rejected_instructions: tuple[dict[str, Any], ...] = (),
        conflicts: tuple[dict[str, Any], ...] = (),
    ) -> "CodebaseEvidence":
        full_payload = {
            "untrusted": True,
            "data": payload,
            "rejected_instructions": [dict(r) for r in rejected_instructions],
            "conflicts": [dict(c) for c in conflicts],
        }
        return cls(
            component=_COMPONENT,
            produced_at=_utc_now_iso(),
            operation=operation,
            alias=alias,
            status="ok",
            content_sha256=_content_sha256(full_payload),
            rule_ids=rule_ids,
            payload=full_payload,
            reason=reason,
            recovery=recovery,
            rejected_instructions=rejected_instructions,
            conflicts=conflicts,
        )

    @classmethod
    def blocked(
        cls,
        *,
        operation: str,
        alias: str,
        reason: str,
        recovery: str,
        rule_ids: tuple[str, ...] = (),
        error_category: str = "blocked",
        payload: Any = None,
    ) -> "CodebaseEvidence":
        full_payload = {"untrusted": True, "data": payload}
        return cls(
            component=_COMPONENT,
            produced_at=_utc_now_iso(),
            operation=operation,
            alias=alias,
            status="blocked",
            content_sha256=_content_sha256(full_payload),
            rule_ids=rule_ids,
            error_category=error_category,
            payload=full_payload,
            reason=reason,
            recovery=recovery,
        )

    @classmethod
    def error(
        cls,
        *,
        operation: str,
        alias: str,
        reason: str,
        recovery: str = "",
        rule_ids: tuple[str, ...] = (),
        error_category: str = "error",
        payload: Any = None,
    ) -> "CodebaseEvidence":
        full_payload = {"untrusted": True, "data": payload}
        return cls(
            component=_COMPONENT,
            produced_at=_utc_now_iso(),
            operation=operation,
            alias=alias,
            status="error",
            content_sha256=_content_sha256(full_payload),
            rule_ids=rule_ids,
            error_category=error_category,
            payload=full_payload,
            reason=reason,
            recovery=recovery,
        )


# --------------------------------------------------------------------------
# SCOPE-002 block exception
# --------------------------------------------------------------------------


class CodebaseScopeBlockError(GateError):
    """Raised when a SCOPE-002 blocked operation is invoked.

    ``execute``/``write``/``install``/``commit`` are not provided by the
    codebase_reader. The methods exist solely to raise this exception so the
    block is deterministic, verifiable and carries the standard GateDecision
    contract (rule_ids, evidence_refs, reason, recovery).
    """


def _scope_block_error(alias: str, operation: str) -> CodebaseScopeBlockError:
    safe_alias = alias if re.fullmatch(r"[a-z][a-z0-9_-]{1,62}", alias) else "invalid"
    return CodebaseScopeBlockError(
        GateDecision.block(
            rule_ids=("SCOPE-002", "SCOPE-003", "HOOK-004"),
            evidence_refs=(
                f"codebase:{safe_alias}:{operation}:scope-blocked",
            ),
            reason=(
                f"Business Codebase adapter does not provide '{operation}'; "
                f"only read/search/stat/git-metadata are available"
            ),
            recovery=(
                "Use the Workspace for candidate writes; external Codebases "
                "are read-only"
            ),
        )
    )


# --------------------------------------------------------------------------
# Instruction candidate detection (scenario E, SCOPE-003)
# --------------------------------------------------------------------------


def _detect_rejected_instructions(
    text: str, relative_path: str
) -> tuple[dict[str, Any], ...]:
    """Scan ``text`` for instruction candidates and return them as rejected.

    Each candidate dict contains: ``category`` (execute/install/upload/commit),
    ``relative_path``, ``line_number`` (1-based), ``snippet`` (the matched line,
    truncated). The candidates are logged so reviewers can see what was
    ignored; they are never acted upon.
    """
    candidates: list[dict[str, Any]] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        for category, pattern in _INSTRUCTION_PATTERNS:
            if pattern.search(line):
                snippet = line.strip()[:200]
                candidates.append(
                    {
                        "category": category,
                        "relative_path": relative_path,
                        "line_number": line_number,
                        "snippet": snippet,
                    }
                )
                # Do NOT break: a single line can contain multiple instruction
                # categories (e.g., "Execute X and Upload Y"). Each match is an
                # independent rejected candidate that should be logged.
    return tuple(candidates)


# --------------------------------------------------------------------------
# SRC-002 conflict disclosure
# --------------------------------------------------------------------------


def _detect_conflicts(
    text: str,
    relative_path: str,
    governance_context: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], ...]:
    """Disclose conflicts between external content and the governance context.

    If ``governance_context`` is None or has no ``metrics``, returns an empty
    tuple. Otherwise, scans ``text`` for metric-definition-like lines and
    compares them against the governance definitions. When the same metric name
    appears with a different definition, a conflict dict is returned containing:
    ``metric_name``, ``external_definition``, ``governance_definition``,
    ``relative_path``, ``line_number``.

    The reader never auto-defines or overrides metrics; it only discloses the
    conflict for owner adjudication (SRC-002).
    """
    if not governance_context:
        return ()
    governance_metrics = governance_context.get("metrics")
    if not isinstance(governance_metrics, Mapping):
        return ()

    conflicts: list[dict[str, Any]] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        match = _METRIC_DEFINITION.match(line)
        if match is None:
            continue
        external_name = match.group(1).strip().lower()
        external_definition = match.group(2).strip()[:300]
        for gov_name, gov_meta in governance_metrics.items():
            if not isinstance(gov_meta, Mapping):
                continue
            if external_name == gov_name.lower():
                gov_definition = str(gov_meta.get("definition", ""))[:300]
                if gov_definition and external_definition != gov_definition:
                    conflicts.append(
                        {
                            "metric_name": external_name,
                            "external_definition": external_definition,
                            "governance_definition": gov_definition,
                            "relative_path": relative_path,
                            "line_number": line_number,
                        }
                    )
    return tuple(conflicts)


# --------------------------------------------------------------------------
# CodebaseAccessor (read-only access contract, parallel to Adapter Protocol)
# --------------------------------------------------------------------------


class CodebaseAccessor(Protocol):
    """Read-only Business Codebase access contract (SCOPE-002/003, SRC-002).

    Deliberately distinct from the Adapter Protocol (adapters/base.py): codebase
    reading is a read-only access dimension keyed by alias, NOT a
    discover/compile/query capability dimension. This Protocol declares ONLY
    read/search/stat/git_metadata (plus component + capabilities) -- there are
    no discover/compile/query/quality/lineage methods to misreport (FBK-003:
    honest capability reporting). execute/write/install/commit are NOT part of
    the contract; CodebaseReader raises CodebaseScopeBlockError on them to make
    the SCOPE-002 block verifiable.

    CodebaseReader satisfies this Protocol structurally; no changes to
    CodebaseReader are required to wire it through select_codebase_reader.
    """

    @property
    def component(self) -> str: ...

    def capabilities(self) -> Mapping[str, bool]: ...

    def read(
        self,
        *,
        alias: str,
        target: str,
        governance_context: Mapping[str, Any] | None = None,
    ) -> CodebaseEvidence: ...

    def search(
        self, *, alias: str, pattern: str, max_results: int = 100
    ) -> CodebaseEvidence: ...

    def stat(self, *, alias: str, target: str) -> CodebaseEvidence: ...

    def git_metadata(
        self, *, alias: str, target: str, history_mode: str = "metadata_only"
    ) -> CodebaseEvidence: ...


# --------------------------------------------------------------------------
# CodebaseReader
# --------------------------------------------------------------------------


class CodebaseReader:
    """Read-only Business Codebase adapter (SCOPE-002, SCOPE-003, SRC-002).

    Operates on aliases declared in ``config["business_codebases"]``. Each
    operation reuses :func:`resolve_path_reference` for path identity, symlink
    rejection and portable references. File content is always wrapped as
    ``untrusted=true``.

    Wired into the selection surface via the parallel ``select_codebase_reader``
    selector in this module (re-exported from ``adapters.__init__``), NOT via
    ``select_adapter``: the codebase_reader is a read-only accessor, not a
    discover/compile/query adapter in the selection chain. See
    :class:`CodebaseAccessor`.
    """

    __slots__ = ("_config",)

    def __init__(self, config: EffectiveConfig) -> None:
        self._config = config

    @property
    def component(self) -> str:
        return _COMPONENT

    def capabilities(self) -> dict[str, bool]:
        """Declare read-only capabilities (SCOPE-002).

        ``execute``/``write``/``install``/``commit`` are always False; calling
        the corresponding methods raises :class:`CodebaseScopeBlockError`.
        """
        return {
            "read": True,
            "search": True,
            "stat": True,
            "git_metadata": True,
            "execute": False,
            "write": False,
            "install": False,
            "commit": False,
        }

    # -- read-only operations -----------------------------------------------

    def read(
        self,
        *,
        alias: str,
        target: str,
        governance_context: Mapping[str, Any] | None = None,
    ) -> CodebaseEvidence:
        """Read one file from the aliased codebase and return untrusted content.

        Returns a portable reference (alias, relative_path, revision,
        revision_kind) plus the file content wrapped as ``untrusted=true``.
        Instruction candidates in the content are detected and logged as
        rejected (scenario E, SCOPE-003). Conflicts with the governance context
        are disclosed (SRC-002).
        """
        try:
            ref = resolve_path_reference(self._config, alias=alias, target=target)
        except GateError as error:
            return self._path_error_evidence("read", alias, error)

        root = self._root_for_alias(alias)
        if root is None:
            return CodebaseEvidence.error(
                operation="read",
                alias=alias,
                reason=f"Could not resolve root for alias {alias}",
                recovery="Verify the codebase path binding in local configuration",
                rule_ids=("SCOPE-001", "PORT-001", "HOOK-004"),
                error_category="root_unresolved",
            )

        file_path = root / ref.relative_path
        try:
            raw_bytes = file_path.read_bytes()
        except OSError as error:
            return CodebaseEvidence.error(
                operation="read",
                alias=alias,
                reason=f"Cannot read file: {type(error).__name__}",
                recovery="Verify the file exists and is readable within the root",
                rule_ids=("SCOPE-002", "HOOK-004"),
                error_category="read_failure",
            )

        truncated = len(raw_bytes) > _MAX_READ_BYTES
        if truncated:
            raw_bytes = raw_bytes[:_MAX_READ_BYTES]
        text = raw_bytes.decode("utf-8", errors="replace")

        rejected = _detect_rejected_instructions(text, ref.relative_path)
        conflicts = _detect_conflicts(text, ref.relative_path, governance_context)

        data: dict[str, Any] = {
            "portable_reference": ref.to_dict(),
            "content": {
                "untrusted": True,
                "text": text,
                "byte_length": len(raw_bytes),
                "truncated": truncated,
                "encoding": "utf-8",
            },
        }
        reason = "File content read as untrusted data"
        recovery = "No action required"
        rule_ids: tuple[str, ...] = ("SCOPE-002", "SCOPE-003")
        if conflicts:
            reason = (
                "File content read as untrusted data; "
                f"{len(conflicts)} conflict(s) disclosed for owner adjudication"
            )
            recovery = (
                "Request the domain owner to adjudicate the conflicting "
                "metric definitions; do not auto-define or override metrics"
            )
            rule_ids = rule_ids + ("SRC-002",)

        return CodebaseEvidence.ok(
            operation="read",
            alias=alias,
            payload=data,
            rule_ids=rule_ids,
            reason=reason,
            recovery=recovery,
            rejected_instructions=rejected,
            conflicts=conflicts,
        )

    def search(
        self,
        *,
        alias: str,
        pattern: str,
        max_results: int = _MAX_SEARCH_RESULTS,
    ) -> CodebaseEvidence:
        """Search for ``pattern`` (literal substring) within the aliased root.

        Stays within the root via :func:`resolve_path_reference` per file;
        symlinks and parent-traversal components are rejected. Each match
        includes a portable reference and the matched line as untrusted data.
        """
        try:
            compiled = re.compile(re.escape(pattern), re.IGNORECASE)
        except re.error as error:
            return CodebaseEvidence.error(
                operation="search",
                alias=alias,
                reason=f"Invalid search pattern: {error}",
                recovery="Use a literal search pattern",
                rule_ids=("HOOK-004",),
                error_category="invalid_pattern",
            )

        root = self._root_for_alias(alias)
        if root is None:
            return CodebaseEvidence.error(
                operation="search",
                alias=alias,
                reason=f"Could not resolve root for alias {alias}",
                recovery="Verify the codebase path binding in local configuration",
                rule_ids=("SCOPE-001", "PORT-001", "HOOK-004"),
                error_category="root_unresolved",
            )

        matches: list[dict[str, Any]] = []
        rejected_paths: list[dict[str, Any]] = []
        truncated = False

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Sort for deterministic iteration order.
            dirnames.sort()
            filenames.sort()
            for filename in filenames:
                if len(matches) >= max_results:
                    truncated = True
                    break
                abs_path = Path(dirpath) / filename
                try:
                    relative = abs_path.relative_to(root).as_posix()
                except ValueError:
                    continue
                try:
                    ref = resolve_path_reference(
                        self._config, alias=alias, target=relative
                    )
                except GateError as error:
                    # Symlink escape, traversal, etc. -- log and skip.
                    rejected_paths.append(
                        {
                            "relative_path": relative,
                            "error_category": error.decision.evidence_refs[0]
                            if error.decision.evidence_refs
                            else "path_rejected",
                            "reason": error.decision.reason,
                        }
                    )
                    continue

                try:
                    raw_bytes = (root / ref.relative_path).read_bytes()
                except OSError:
                    continue
                if len(raw_bytes) > _MAX_SEARCH_FILE_BYTES:
                    raw_bytes = raw_bytes[:_MAX_SEARCH_FILE_BYTES]
                text = raw_bytes.decode("utf-8", errors="replace")

                for line_number, line in enumerate(text.splitlines(), start=1):
                    if compiled.search(line):
                        matches.append(
                            {
                                "portable_reference": ref.to_dict(),
                                "line_number": line_number,
                                "line_content": {
                                    "untrusted": True,
                                    "text": line[:500],
                                },
                            }
                        )
                        if len(matches) >= max_results:
                            truncated = True
                            break
            if truncated:
                break

        data: dict[str, Any] = {
            "pattern": pattern,
            "matches": matches,
            "match_count": len(matches),
            "truncated": truncated,
            "rejected_paths": rejected_paths,
            "search_scope": "alias_root",
        }
        return CodebaseEvidence.ok(
            operation="search",
            alias=alias,
            payload=data,
            rule_ids=("SCOPE-002", "SCOPE-003"),
            reason=(
                f"Search returned {len(matches)} match(es) within the alias root"
            ),
            recovery="No action required",
        )

    def stat(self, *, alias: str, target: str) -> CodebaseEvidence:
        """Return file/directory stat metadata for ``target`` within ``alias``."""
        try:
            ref = resolve_path_reference(self._config, alias=alias, target=target)
        except GateError as error:
            return self._path_error_evidence("stat", alias, error)

        root = self._root_for_alias(alias)
        if root is None:
            return CodebaseEvidence.error(
                operation="stat",
                alias=alias,
                reason=f"Could not resolve root for alias {alias}",
                recovery="Verify the codebase path binding in local configuration",
                rule_ids=("SCOPE-001", "PORT-001", "HOOK-004"),
                error_category="root_unresolved",
            )

        file_path = root / ref.relative_path
        try:
            file_stat = file_path.stat(follow_symlinks=False)
        except OSError as error:
            return CodebaseEvidence.error(
                operation="stat",
                alias=alias,
                reason=f"Cannot stat file: {type(error).__name__}",
                recovery="Verify the file exists and is accessible within the root",
                rule_ids=("SCOPE-002", "HOOK-004"),
                error_category="stat_failure",
            )

        mode = file_stat.st_mode
        data: dict[str, Any] = {
            "portable_reference": ref.to_dict(),
            "size": file_stat.st_size,
            "mtime": file_stat.st_mtime,
            "mode": stat.S_IMODE(mode),
            "is_file": stat.S_ISREG(mode),
            "is_dir": stat.S_ISDIR(mode),
        }
        return CodebaseEvidence.ok(
            operation="stat",
            alias=alias,
            payload=data,
            rule_ids=("SCOPE-002",),
            reason="File stat metadata returned",
            recovery="No action required",
        )

    def git_metadata(
        self,
        *,
        alias: str,
        target: str,
        history_mode: str = "metadata_only",
    ) -> CodebaseEvidence:
        """Return git metadata for ``target`` (SCOPE-002, Cycle 1 trusted git).

        ``metadata_only`` (default): returns HEAD SHA, tracked/modified/
        untracked status. No commit history (no author, no message, no
        timestamps beyond HEAD). Reuses the Cycle 1 trusted-git allowlist.

        ``full_history``: blocked by default; requires a separate safety-
        deviation approval before deep history is enabled.
        """
        if history_mode not in _VALID_HISTORY_MODES:
            return CodebaseEvidence.error(
                operation="git_metadata",
                alias=alias,
                reason=f"Invalid history_mode: {history_mode}",
                recovery="Use 'metadata_only' or 'full_history'",
                rule_ids=("HOOK-004",),
                error_category="invalid_history_mode",
            )

        if history_mode == "full_history":
            return CodebaseEvidence.blocked(
                operation="git_metadata",
                alias=alias,
                reason=(
                    "Full git history is not enabled by default; deep history "
                    "requires a separate safety-deviation approval"
                ),
                recovery=(
                    "Obtain a safety-deviation approval for full_history mode "
                    "before enabling deep commit history access"
                ),
                rule_ids=("SCOPE-002", "SEC-003", "HOOK-004"),
                error_category="full_history_blocked",
            )

        try:
            ref = resolve_path_reference(self._config, alias=alias, target=target)
        except GateError as error:
            return self._path_error_evidence("git_metadata", alias, error)

        root = self._root_for_alias(alias)
        if root is None:
            return CodebaseEvidence.error(
                operation="git_metadata",
                alias=alias,
                reason=f"Could not resolve root for alias {alias}",
                recovery="Verify the codebase path binding in local configuration",
                rule_ids=("SCOPE-001", "PORT-001", "HOOK-004"),
                error_category="root_unresolved",
            )

        git_info = self._git_metadata_only(root, ref.relative_path)
        data: dict[str, Any] = {
            "portable_reference": ref.to_dict(),
            "history_mode": "metadata_only",
            "head_sha": git_info["head_sha"],
            "tracked": git_info["tracked"],
            "modified": git_info["modified"],
            "untracked": git_info["untracked"],
            "git_available": _TRUSTED_GIT is not None,
            "commit_history": None,  # metadata_only: no commit history
        }
        return CodebaseEvidence.ok(
            operation="git_metadata",
            alias=alias,
            payload=data,
            rule_ids=("SCOPE-002",),
            reason="Git metadata (metadata_only) returned; no commit history",
            recovery="No action required",
        )

    # -- SCOPE-002 blocked operations ---------------------------------------

    def execute(self, *args: Any, **kwargs: Any) -> None:
        """Not provided; raises to make the SCOPE-002 block explicit."""
        alias = str(kwargs.get("alias", args[0] if args else "unknown"))
        raise _scope_block_error(alias, "execute")

    def write(self, *args: Any, **kwargs: Any) -> None:
        """Not provided; raises to make the SCOPE-002 block explicit."""
        alias = str(kwargs.get("alias", args[0] if args else "unknown"))
        raise _scope_block_error(alias, "write")

    def install(self, *args: Any, **kwargs: Any) -> None:
        """Not provided; raises to make the SCOPE-002 block explicit."""
        alias = str(kwargs.get("alias", args[0] if args else "unknown"))
        raise _scope_block_error(alias, "install")

    def commit(self, *args: Any, **kwargs: Any) -> None:
        """Not provided; raises to make the SCOPE-002 block explicit."""
        alias = str(kwargs.get("alias", args[0] if args else "unknown"))
        raise _scope_block_error(alias, "commit")

    # -- internal helpers ---------------------------------------------------

    def _root_for_alias(self, alias: str) -> Path | None:
        """Resolve the root Path for ``alias`` from the effective config.

        Returns None if the alias is not a configured business codebase or the
        path binding is missing. The root is NOT validated here (symlink,
        directory, overlap) -- that validation happens inside
        :func:`resolve_path_reference` via ``_configured_roots``. This helper
        only extracts the declared path so we can walk the tree for ``search``.
        """
        codebases = self._config.get("business_codebases")
        if not isinstance(codebases, Mapping):
            return None
        codebase = codebases.get(alias)
        if not isinstance(codebase, Mapping):
            return None
        path_ref = codebase.get("path_ref")
        if not isinstance(path_ref, str):
            return None
        path_bindings = self._config.get("path_bindings")
        if not isinstance(path_bindings, Mapping):
            return None
        root_str = path_bindings.get(path_ref)
        if not isinstance(root_str, str):
            return None
        try:
            return Path(root_str).resolve(strict=True)
        except (OSError, RuntimeError):
            return None

    def _path_error_evidence(
        self, operation: str, alias: str, error: GateError
    ) -> CodebaseEvidence:
        """Convert a Cycle 1 path GateError into a blocked CodebaseEvidence."""
        decision = error.decision
        return CodebaseEvidence.blocked(
            operation=operation,
            alias=alias,
            reason=decision.reason,
            recovery=decision.recovery,
            rule_ids=tuple(decision.rule_ids),
            error_category="path_rejected",
            payload={"decision": decision.to_dict()},
        )

    @staticmethod
    def _git_metadata_only(root: Path, relative_path: str) -> dict[str, Any]:
        """Return metadata-only git info for ``relative_path`` in ``root``.

        Reuses the Cycle 1 trusted-git allowlist (``_TRUSTED_GIT``). If git is
        not available or the root is not a git repo, all fields are None/False.
        No commit history is returned (metadata_only).
        """
        result: dict[str, Any] = {
            "head_sha": None,
            "tracked": False,
            "modified": False,
            "untracked": False,
        }
        if _TRUSTED_GIT is None:
            return result

        safe_env = {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": _SAFE_SYSTEM_PATH,
        }

        def run_git(*args: str) -> subprocess.CompletedProcess[str] | None:
            try:
                return subprocess.run(
                    [
                        _TRUSTED_GIT,
                        "-c",
                        "core.fsmonitor=false",
                        "-c",
                        f"core.hooksPath={os.devnull}",
                        *args,
                    ],
                    cwd=root,
                    env=safe_env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=_GIT_TIMEOUT,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return None

        # HEAD SHA
        head = run_git("rev-parse", "--verify", "HEAD")
        if head is None or head.returncode != 0:
            return result
        sha = head.stdout.strip()
        if len(sha) not in (40, 64) or any(
            c not in "0123456789abcdefABCDEF" for c in sha
        ):
            return result
        result["head_sha"] = sha.lower()

        # Tracked?
        tracked = run_git("ls-files", "--", relative_path)
        if tracked is not None and tracked.returncode == 0:
            result["tracked"] = bool(tracked.stdout.strip())

        # Modified (working tree or staged)?
        if result["tracked"]:
            for comparison in (
                ("diff-files", "--quiet", "--", relative_path),
                ("diff-index", "--cached", "--quiet", "HEAD", "--", relative_path),
            ):
                diff = run_git(*comparison)
                if diff is not None and diff.returncode != 0:
                    result["modified"] = True
                    break

        # Untracked?
        untracked = run_git(
            "ls-files", "--others", "--exclude-standard", "--", relative_path
        )
        if untracked is not None and untracked.returncode == 0:
            result["untracked"] = bool(untracked.stdout.strip())

        return result


# --------------------------------------------------------------------------
# CodebaseSelection + select_codebase_reader (parallel to select_adapter)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CodebaseSelection:
    """Result of selecting a read-only CodebaseReader for one alias.

    Parallel in shape to SelectionOutcome (adapters/__init__.py): selected |
    stopped + stop_decision. But for the codebase-alias dimension: holds a
    CodebaseReader (a CodebaseAccessor, NOT an Adapter) and never an
    AdapterEvidence. Selection produces no synthetic evidence -- the first
    read/search/stat/git_metadata call produces the real CodebaseEvidence
    (with portable_reference / rejected_instructions / conflicts).

    select_codebase_reader validates only that the alias is declared in
    business_codebases (config-level, no I/O). Root resolution is deferred to
    the per-operation call: a declared-but-unbound alias surfaces as
    CodebaseEvidence.error(root_unresolved) at read time (clean, recoverable),
    and pretool_guard already fail-closes on unconfigured roots before any tool
    runs. Early STOP at selection would duplicate both.
    """

    status: str  # "selected" | "stopped"
    alias: str
    reader: CodebaseReader | None
    stop_decision: GateDecision | None

    @classmethod
    def selected(cls, *, alias: str, reader: CodebaseReader) -> CodebaseSelection:
        return cls(
            status="selected", alias=alias, reader=reader, stop_decision=None
        )

    @classmethod
    def stopped(cls, *, alias: str, decision: GateDecision) -> CodebaseSelection:
        return cls(
            status="stopped", alias=alias, reader=None, stop_decision=decision
        )

    def to_dict(self) -> dict[str, Any]:
        if self.status == "selected":
            return {"status": "selected", "alias": self.alias}
        return {
            "status": "stopped",
            "alias": self.alias,
            "decision": self.stop_decision.to_dict() if self.stop_decision else None,
        }


def select_codebase_reader(
    config: EffectiveConfig, *, alias: str
) -> CodebaseSelection:
    """Select the read-only CodebaseReader for ``alias`` (parallel to select_adapter).

    Selection is by codebase alias (the natural key for read-only external
    access), NOT by capability kind. This does NOT touch the select_adapter
    managed->cli->fixture->STOP chain (adapters/__init__.py); the two are
    distinct dispatch dimensions. The returned reader is a CodebaseAccessor
    (read-only); it never reports discover/compile/query/quality/lineage
    (FBK-003) and execute/write/install/commit raise CodebaseScopeBlockError
    (SCOPE-002).

    Fail-closed STOP when ``alias`` is not a declared business_codebase
    (SCOPE-001/HOOK-004). Deterministic for identical inputs (HOOK-001).

    Root resolution is deferred to the per-operation call (see CodebaseSelection
    docstring); selection is pure config validation with no I/O.
    """
    codebases = config.get("business_codebases")
    if not isinstance(codebases, Mapping) or alias not in codebases:
        return CodebaseSelection.stopped(
            alias=alias,
            decision=GateDecision.block(
                rule_ids=("SCOPE-001", "HOOK-004"),
                evidence_refs=(f"codebase:{alias}:not-declared",),
                reason=f"Business Codebase alias '{alias}' is not declared",
                recovery=(
                    "Declare the alias under business_codebases in shared "
                    "configuration"
                ),
            ),
        )
    reader = CodebaseReader(config)
    return CodebaseSelection.selected(alias=alias, reader=reader)


__all__ = [
    "CodebaseAccessor",
    "CodebaseEvidence",
    "CodebaseReader",
    "CodebaseScopeBlockError",
    "CodebaseSelection",
    "select_codebase_reader",
]
