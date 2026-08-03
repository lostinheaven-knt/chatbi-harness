"""FR-1: accumulated FM-STALE drift detection.

Three independent drift classes, each a deterministic comparison (HOOK-001):

  1. ``stale_reference`` - the cited ``git_sha`` recorded in a governed
     reference's optional ``## Citation`` section vs the codebase alias's
     current HEAD (``select_codebase_reader`` + ``CodebaseReader.git_metadata``).
  2. ``source_drift``    - the bootstrapped baseline ``source_inventory.json``
     vs a fresh ``SourceInventory`` supplied by the command (pure table/column/
     PK/type diff; the live-DB introspection is a command/runbook concern).
  3. ``model_doc_drift`` - ``knowledge.lint_reference`` field checks on governed
     references. SHA staleness is NOT repeated here - class 1 already scans every
     reference (DRY, design gap 2).

``detect_drift`` returns a ``DriftReport``; the ``/chatbi-audit-drift`` command
persists it via ``harness_state.write_state`` and STOPs. The ``chatbi-governance``
SKILL reads the report and routes candidates (``classify_finding`` +
``DRIFT_ROUTES``) to maintenance commands. Neither this module, the command, nor
the SKILL fixes/approves/publishes (SEM-003/META-008).

Fail-closed semantics (HOOK-004): a live query adapter or trusted-git executable
that is unavailable degrades a class to an ``unavailable`` candidate (the other
classes still run) - it is never a silent "no drift" pass. A missing baseline
``source_inventory.json`` is a hard STOP (prerequisite, like build Step 1.3).
Drift candidates are evidence of accumulated staleness, not a guarantee silent
failure is eliminated (FBK-003). No machine absolute paths leave the report
(PORT-001); ``evidence_ref`` / ``reason`` / ``recovery`` are sanitized via
``gates._sanitize_text`` (sanctioned reuse, mirroring ``impact.py``).

Applicable rules: FM-STALE, DOC-001/002/004, SRC-002, SCOPE-001/002/003,
SEC-001/003, PORT-001, HOOK-001/004, SEM-001/003, META-008, FBK-003.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .gates import (  # noqa: E402 (sanctioned reuse, mirroring impact.py:24)
    GateDecision,
    GateError,
    _sanitize_text,
)
from .evidence import (  # noqa: E402 (sanctioned reuse, mirroring impact.py:25)
    _get_schema,
    _validate_against_schema,
)
from .bootstrap import SourceInventory, read_source_inventory
from .knowledge import _section_block, lint_reference
from .adapters.codebase_reader import CodebaseEvidence, select_codebase_reader


_SCHEMA_VERSION = 1
_VALID_SCOPES = frozenset({"references", "sources", "models", "all"})
_VALID_KINDS = frozenset({"stale_reference", "source_drift", "model_doc_drift"})
_VALID_STATUSES = frozenset({"candidate", "unavailable", "skipped"})

# FBK-003 statement carried verbatim on every report (design module one).
FBK_003_STATEMENT = (
    "Drift detection is evidence of accumulated staleness, not a guarantee "
    "silent failure is eliminated; candidates require human triage before "
    "any fix."
)

# Shared route table (FR-0 re-enabled: build SRC-002 findings route via the
# parallel classify_src002_finding + SRC002_ROUTES; FR-1 drift candidates route
# via classify_finding). Maps a route class to its hand-off target command. Each
# target command carries its own STOP / human-approval gate; DRIFT_ROUTES is a
# hand-off, not an auto-fix.
DRIFT_ROUTES: dict[str, str] = {
    "B": "/chatbi-bootstrap",           # source scope expansion -> human approval + incremental introspect
    "C": "/chatbi-maintain-knowledge",  # stale reference / lint field -> re-author + lint + co-locate
    "D": "/chatbi-correction",          # governed artifact vs source/model contradiction -> dual candidate
    "E": "/chatbi-maintain-model",      # source shape change / metric definition needs change
    "TRIAGE": "STOP human triage",      # unavailable / skipped -> human adjudication
}

# FR-0 SRC-002 dispatch table (build Step 1 findings). B/C/D/E reference
# DRIFT_ROUTES (single source of route targets, DRY); A (STOP ask owner) and
# F (PASS, proceed with build chain) are build-exclusive. TRIAGE stays drift-
# exclusive (drift classify_finding; build uses A's STOP for cross-check
# failures). Each target command carries its own STOP / human-approval gate.
SRC002_ROUTES: dict[str, str] = {
    "A": "STOP: ask domain owner for clarification (REQ-001/002)",
    "B": DRIFT_ROUTES["B"],   # /chatbi-bootstrap
    "C": DRIFT_ROUTES["C"],   # /chatbi-maintain-knowledge
    "D": DRIFT_ROUTES["D"],   # /chatbi-correction
    "E": DRIFT_ROUTES["E"],   # /chatbi-maintain-model
    "F": "PASS: proceed with build chain (no SRC-002 finding)",
}

# Governed-reference marker: the first DOC-002 required header. A markdown file
# whose body has a `## Business context` header line is treated as a governed
# reference (deterministic discovery, HOOK-001). The shipped `_template.md` and
# hidden dirs (`.claude/`, `.chatbi/`, `.git/`) are excluded so the harness
# install and run state are not scanned.
_GOVERNED_REF_MARKER = re.compile(r"(?m)^##\s+Business\s+context\s*$")

# `## Citation` section parsing (design gap 1, OD1 = optional `##` section).
_CITATION_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_CITATION_LINE = re.compile(
    r"(?m)^\s*-\s+\*{0,2}(?P<key>alias|relative_path|git_sha|captured_at)\*{0,2}"
    r"\s*:\s*(?P<val>.+?)\s*$"
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DriftCandidate:
    """One drift finding (candidate / unavailable / skipped)."""

    kind: str
    status: str
    rule_ids: tuple[str, ...]
    evidence_ref: str
    reason: str
    recovery: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(f"Invalid DriftCandidate kind: {self.kind!r}")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"Invalid DriftCandidate status: {self.status!r}")
        # PORT-001 / SEC-003: never let a machine path or secret leave the report.
        object.__setattr__(self, "evidence_ref", _sanitize_text(self.evidence_ref))
        object.__setattr__(self, "reason", _sanitize_text(self.reason))
        object.__setattr__(self, "recovery", _sanitize_text(self.recovery))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "status": self.status,
            "rule_ids": list(self.rule_ids),
            "evidence_ref": self.evidence_ref,
            "reason": self.reason,
            "recovery": self.recovery,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Deterministic routing of one drift candidate to a hand-off target."""

    route_class: str  # "B" | "C" | "D" | "E" | "TRIAGE"
    target_command: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_class": self.route_class,
            "target_command": self.target_command,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Accumulated FM-STALE drift report (schema-validated before return)."""

    schema_version: int
    produced_at: str
    workspace: str
    scope: str
    since: str | None
    head_shas: dict[str, str | None]
    classes: dict[str, tuple[DriftCandidate, ...]]
    fbk_003_statement: str
    path_references: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(
                f"DriftReport schema_version must be {_SCHEMA_VERSION}; "
                f"got {self.schema_version!r}"
            )
        missing = _VALID_KINDS - set(self.classes)
        if missing:
            raise ValueError(f"DriftReport.classes missing keys: {sorted(missing)}")

    @property
    def status(self) -> str:
        """``partial`` if any candidate is unavailable, else ``complete``."""
        for cls in self.classes.values():
            if any(c.status == "unavailable" for c in cls):
                return "partial"
        return "complete"

    @property
    def recovery_actions(self) -> list[str]:
        """De-duplicated, order-preserving recovery strings across candidates."""
        seen: set[str] = set()
        actions: list[str] = []
        for cls in self.classes.values():
            for c in cls:
                if c.recovery and c.recovery not in seen:
                    seen.add(c.recovery)
                    actions.append(c.recovery)
        return actions

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "produced_at": self.produced_at,
            "workspace": self.workspace,
            "scope": self.scope,
            "since": self.since,
            "head_shas": dict(self.head_shas),
            "status": self.status,
            "fbk_003_statement": self.fbk_003_statement,
            "recovery_actions": self.recovery_actions,
            "path_references": [dict(p) for p in self.path_references],
            "classes": {
                "stale_reference": [c.to_dict() for c in self.classes["stale_reference"]],
                "source_drift": [c.to_dict() for c in self.classes["source_drift"]],
                "model_doc_drift": [c.to_dict() for c in self.classes["model_doc_drift"]],
            },
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


# ---------------------------------------------------------------------------
# Gate error helper (mirrors impact._impact_gate_error)
# ---------------------------------------------------------------------------


def _drift_gate_error(
    *,
    rule_ids: tuple[str, ...],
    evidence_ref: str,
    reason: str,
    recovery: str,
) -> GateError:
    return GateError(
        GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=(evidence_ref,),
            reason=reason,
            recovery=recovery,
        )
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _cwd(path: Path):
    """Run a block with ``path`` as the process cwd (restored on exit).

    ``paths._configured_roots`` derives the Workspace alias root from
    ``Path.cwd()`` (paths.py:150), so ``CodebaseReader.git_metadata`` must be
    called with cwd = Workspace root. detect_drift is self-contained: callers
    pass ``workspace_root`` and this helper scopes the chdir.
    """
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


# ---------------------------------------------------------------------------
# Reference discovery + citation parsing
# ---------------------------------------------------------------------------


def _discover_governed_references(
    workspace_root: Path,
) -> list[tuple[Path, str]]:
    """Find governed reference markdown files under ``workspace_root``.

    A governed reference is a ``.md`` file whose body has a
    ``## Business context`` header line (the first DOC-002 required field).
    Hidden directories (``.git``/``.claude``/``.chatbi``/``.venv`` ...) and the
    shipped ``_template.md`` are excluded so the harness install and run state
    are not scanned. Deterministic for identical inputs (HOOK-001).
    """
    refs: list[tuple[Path, str]] = []
    for path in sorted(workspace_root.rglob("*.md")):
        rel = path.relative_to(workspace_root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.name == "_template.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _GOVERNED_REF_MARKER.search(text):
            refs.append((path, text))
    return refs


def _parse_citation(text: str) -> dict[str, str] | None:
    """Parse the optional ``## Citation`` section body into a field dict.

    Returns ``None`` when the section is absent or empty (caller treats both as
    "no usable citation"). A present-but-malformed section returns a partial
    dict; ``_citation_well_formed`` decides if class 1 can compare.
    """
    block = _section_block(text, "Citation")
    if not block.strip():
        return None
    fields: dict[str, str] = {}
    for match in _CITATION_LINE.finditer(block):
        fields[match.group("key")] = match.group("val").strip()
    return fields


def _citation_well_formed(citation: dict[str, str]) -> bool:
    alias = citation.get("alias")
    relative_path = citation.get("relative_path")
    git_sha = citation.get("git_sha")
    if not alias or not relative_path or not git_sha:
        return False
    if not _CITATION_SHA.fullmatch(git_sha):
        return False
    return True


def _check_reference_sha_staleness(
    cited_sha: str, head_sha: str, *, alias: str, relative_path: str,
) -> DriftCandidate | None:
    """Compare a cited sha against the current HEAD sha (design gap 2 primitive).

    Both arguments are assumed valid hex (caller ensures ``head_sha`` is not
    None and ``cited_sha`` passed ``_citation_well_formed``). Equal -> ``None``
    (no candidate); unequal -> a ``stale_reference`` candidate.
    """
    if cited_sha.lower() == head_sha.lower():
        return None
    return DriftCandidate(
        kind="stale_reference",
        status="candidate",
        rule_ids=("SRC-002", "DOC-002"),
        evidence_ref=f"reference:{alias}:{relative_path}",
        reason=(
            f"Reference citation git_sha does not match the current HEAD of "
            f"business codebase alias '{alias}'"
        ),
        recovery=(
            "Re-author the reference via /chatbi-maintain-knowledge and capture "
            "a fresh ## Citation git_sha from the codebase read"
        ),
        details={
            "subtype": "sha_stale",
            "alias": alias,
            "relative_path": relative_path,
            "cited_sha": cited_sha,
            "head_sha": head_sha,
        },
    )


# ---------------------------------------------------------------------------
# Class 1: stale references
# ---------------------------------------------------------------------------


def _detect_stale_references(
    workspace_root: Path,
    config: Any,
    refs: list[tuple[Path, str]],
    since: str | None,
) -> tuple[tuple[DriftCandidate, ...], dict[str, str | None], tuple[dict[str, Any], ...]]:
    """Class 1: cited git_sha vs current HEAD for every governed reference.

    Returns ``(candidates, head_shas_by_alias, portable_path_references)``.
    """
    # `since` is recorded as provenance only in v1 (no open-ended sha-ancestor
    # reasoning, HOOK-001); detection covers all accumulated staleness.
    _ = since
    candidates: list[DriftCandidate] = []
    head_shas: dict[str, str | None] = {}
    path_refs: list[dict[str, Any]] = []

    for ref_path, text in refs:
        rel = ref_path.relative_to(workspace_root).as_posix()
        citation = _parse_citation(text)
        if citation is None:
            candidates.append(DriftCandidate(
                kind="stale_reference",
                status="skipped",
                rule_ids=("DOC-002",),
                evidence_ref=f"governed-reference:{rel}",
                reason=(
                    "Reference has no ## Citation section; cited sha staleness "
                    "cannot be compared"
                ),
                recovery=(
                    "Author or update the reference via /chatbi-maintain-knowledge "
                    "to capture a ## Citation git_sha"
                ),
                details={"subtype": "citation_absent", "reference_path": rel},
            ))
            continue
        if not _citation_well_formed(citation):
            candidates.append(DriftCandidate(
                kind="stale_reference",
                status="skipped",
                rule_ids=("DOC-002", "HOOK-004"),
                evidence_ref=f"governed-reference:{rel}",
                reason=(
                    "Reference ## Citation section is malformed; cited sha "
                    "staleness cannot be compared"
                ),
                recovery=(
                    "Fix the ## Citation shape (alias, relative_path, 40/64-hex "
                    "git_sha) via /chatbi-maintain-knowledge"
                ),
                details={"subtype": "citation_malformed", "reference_path": rel},
            ))
            continue

        alias = citation["alias"]
        relative_path = citation["relative_path"]
        cited_sha = citation["git_sha"]

        selection = select_codebase_reader(config, alias=alias)
        if selection.status != "selected":
            candidates.append(DriftCandidate(
                kind="stale_reference",
                status="unavailable",
                rule_ids=("SCOPE-001", "HOOK-004"),
                evidence_ref=f"reference:{alias}:alias-not-declared",
                reason=(
                    f"Cited business codebase alias '{alias}' is not declared; "
                    "current HEAD cannot be resolved"
                ),
                recovery=(
                    "Declare the cited alias under business_codebases, or correct "
                    "the ## Citation alias via /chatbi-maintain-knowledge"
                ),
                details={
                    "subtype": "alias_not_declared",
                    "alias": alias,
                    "relative_path": relative_path,
                    "reference_path": rel,
                },
            ))
            continue

        reader = selection.reader
        with _cwd(workspace_root):
            evidence = reader.git_metadata(
                alias=alias, target=relative_path, history_mode="metadata_only",
            )
        if evidence.status != "ok":
            candidates.append(DriftCandidate(
                kind="stale_reference",
                status="unavailable",
                rule_ids=("HOOK-004",),
                evidence_ref=f"reference:{alias}:{relative_path}:git-metadata-error",
                reason=(
                    f"git_metadata for cited alias '{alias}' returned "
                    f"{evidence.status}; current HEAD cannot be resolved"
                ),
                recovery=(
                    "Resolve the codebase path binding / git availability for the "
                    "cited alias, then re-run /chatbi-audit-drift"
                ),
                details={
                    "subtype": "git_metadata_error",
                    "alias": alias,
                    "relative_path": relative_path,
                    "error_category": evidence.error_category,
                    "reference_path": rel,
                },
            ))
            continue

        data = evidence.payload.get("data") or {}
        head_sha = data.get("head_sha")
        git_available = bool(data.get("git_available", False))
        portable_ref = data.get("portable_reference")
        if isinstance(portable_ref, dict):
            path_refs.append(dict(portable_ref))
        if alias not in head_shas:
            head_shas[alias] = head_sha

        if head_sha is None:
            if not git_available:
                # Gap 4: no trusted git executable on the system allowlist.
                candidates.append(DriftCandidate(
                    kind="stale_reference",
                    status="unavailable",
                    rule_ids=("SEC-003", "HOOK-004"),
                    evidence_ref="git:trusted-git:unavailable",
                    reason=(
                        "No trusted git executable is available on the system "
                        "allowlist; current HEAD cannot be resolved"
                    ),
                    recovery=(
                        "Restore a trusted git executable on the system allowlist"
                    ),
                    details={
                        "subtype": "trusted_git_unavailable",
                        "alias": alias,
                        "relative_path": relative_path,
                        "reference_path": rel,
                    },
                ))
            else:
                # git present but the cited root has no HEAD (not a git repo /
                # no commits yet). Fail-closed, not a silent pass.
                candidates.append(DriftCandidate(
                    kind="stale_reference",
                    status="unavailable",
                    rule_ids=("HOOK-004",),
                    evidence_ref="git:head-sha:unavailable",
                    reason=(
                        f"Current HEAD could not be resolved for cited alias "
                        f"'{alias}' (git present but no HEAD)"
                    ),
                    recovery=(
                        "Initialize the cited codebase as a git repository with "
                        "at least one commit, then re-run /chatbi-audit-drift"
                    ),
                    details={
                        "subtype": "head_sha_unavailable",
                        "alias": alias,
                        "relative_path": relative_path,
                        "reference_path": rel,
                    },
                ))
            continue

        staleness = _check_reference_sha_staleness(
            cited_sha, head_sha, alias=alias, relative_path=relative_path,
        )
        if staleness is not None:
            candidates.append(staleness)

    return tuple(candidates), head_shas, tuple(path_refs)


# ---------------------------------------------------------------------------
# Class 2: source drift
# ---------------------------------------------------------------------------


def _source_candidate(
    subtype: str, kind: str, db: str, table: str, extra: dict[str, Any],
) -> DriftCandidate:
    if subtype == "scope_expansion":
        rule_ids = ("SCOPE-001", "SEC-001", "SRC-002")
        what = "table" if kind == "table_added" else "column(s)"
        reason = (
            f"Source scope expanded: new {what} in source database '{db}' "
            f"table '{table}'"
        )
        recovery = (
            "Route to /chatbi-bootstrap for human approval of the source "
            "boundary expansion and incremental introspection"
        )
    else:  # shape_change
        rule_ids = ("SRC-002", "DOC-002")
        reason = (
            f"Source shape changed in source database '{db}' table '{table}'"
        )
        recovery = (
            "Route to /chatbi-maintain-model to update the model for the "
            "changed source shape"
        )
    details: dict[str, Any] = {
        "subtype": subtype,
        "kind": kind,
        "source_database": db,
        "table": table,
    }
    details.update(extra)
    return DriftCandidate(
        kind="source_drift",
        status="candidate",
        rule_ids=rule_ids,
        evidence_ref=f"source:{db}:{table}",
        reason=reason,
        recovery=recovery,
        details=details,
    )


def _detect_source_drift(
    baseline: SourceInventory, fresh: SourceInventory | None,
) -> tuple[DriftCandidate, ...]:
    """Class 2: pure diff of baseline vs fresh source inventory.

    ``baseline`` is already read by ``detect_drift`` (prerequisite check).
    ``fresh`` is supplied by the command; ``None`` -> unavailable (gap 3).

    Subtype semantics (reconciling design diff rules with FR-1 §5 AC): a NEW
    table or NEW column is ``scope_expansion`` (route B, human approval); a
    removed table/column, a data_type change, or a PK change is ``shape_change``
    (route E, maintain-model).
    """
    if fresh is None:
        return (DriftCandidate(
            kind="source_drift",
            status="unavailable",
            rule_ids=("SEM-001", "PORT-001", "HOOK-004"),
            evidence_ref="source:adapter:unavailable",
            reason=(
                "No live query adapter produced a fresh source inventory; "
                "source drift cannot be detected"
            ),
            recovery=(
                "Configure at least one managed or approved CLI query adapter "
                "and re-run /chatbi-audit-drift"
            ),
            details={"subtype": "adapter_unavailable"},
        ),)

    candidates: list[DriftCandidate] = []
    baseline_tables = {t.name: t for t in baseline.tables}
    fresh_tables = {t.name: t for t in fresh.tables}
    db = baseline.source_database

    # New tables (fresh only) -> scope_expansion (B).
    for name in sorted(fresh_tables.keys() - baseline_tables.keys()):
        candidates.append(_source_candidate(
            "scope_expansion", "table_added", db, name, {}))
    # Removed tables (baseline only) -> shape_change (E).
    for name in sorted(baseline_tables.keys() - fresh_tables.keys()):
        candidates.append(_source_candidate(
            "shape_change", "table_removed", db, name, {}))

    # Common tables: column-level diff.
    for name in sorted(baseline_tables.keys() & fresh_tables.keys()):
        bcols = {c.name: c for c in baseline_tables[name].columns}
        fcols = {c.name: c for c in fresh_tables[name].columns}
        added = sorted(fcols.keys() - bcols.keys())
        removed = sorted(bcols.keys() - fcols.keys())
        type_changes: list[dict[str, Any]] = []
        pk_changes: list[dict[str, Any]] = []
        for cname in sorted(bcols.keys() & fcols.keys()):
            bc = bcols[cname]
            fc = fcols[cname]
            if bc.data_type != fc.data_type:
                type_changes.append({
                    "column": cname,
                    "baseline": bc.data_type,
                    "fresh": fc.data_type,
                })
            if bc.is_primary_key != fc.is_primary_key:
                pk_changes.append({
                    "column": cname,
                    "baseline": bc.is_primary_key,
                    "fresh": fc.is_primary_key,
                })
        if added:
            candidates.append(_source_candidate(
                "scope_expansion", "column_added", db, name,
                {"added_columns": added}))
        if removed or type_changes or pk_changes:
            candidates.append(_source_candidate(
                "shape_change", "column_shape", db, name,
                {
                    "removed_columns": removed,
                    "type_changes": type_changes,
                    "pk_changes": pk_changes,
                }))

    return tuple(candidates)


# ---------------------------------------------------------------------------
# Class 3: model-doc drift
# ---------------------------------------------------------------------------


def _detect_model_doc_drift(
    workspace_root: Path, refs: list[tuple[Path, str]],
) -> tuple[DriftCandidate, ...]:
    """Class 3: lint_reference field checks on governed references.

    SHA staleness is NOT repeated here (class 1 scans every reference, DRY).
    Each ``LintIssue`` becomes one ``model_doc_drift`` candidate.
    """
    candidates: list[DriftCandidate] = []
    for ref_path, text in refs:
        rel = ref_path.relative_to(workspace_root).as_posix()
        issues = lint_reference(text)
        for issue in issues:
            safe_message = _sanitize_text(issue.message)
            candidates.append(DriftCandidate(
                kind="model_doc_drift",
                status="candidate",
                rule_ids=("DOC-002",),
                evidence_ref=f"governed-reference:{rel}",
                reason=(
                    f"lint issue [{issue.category}] {issue.field}: "
                    f"{safe_message}"
                ),
                recovery=(
                    "Resolve the lint issue via /chatbi-maintain-knowledge "
                    "before publish (route-ready = empty lint tuple)"
                ),
                details={
                    "subtype": "lint_field",
                    "category": issue.category,
                    "field": issue.field,
                    "message": safe_message,
                    "reference_path": rel,
                },
            ))
    return tuple(candidates)


# ---------------------------------------------------------------------------
# classify_finding
# ---------------------------------------------------------------------------


def classify_finding(candidate: DriftCandidate) -> RouteDecision:
    """Deterministically route one drift candidate to a hand-off target (HOOK-001).

    Mapping (design module one classify table):
      stale_reference candidate        -> C  /chatbi-maintain-knowledge
      stale_reference unavailable/skip -> TRIAGE
      source_drift candidate scope_expansion -> B  /chatbi-bootstrap
      source_drift candidate shape_change    -> E  /chatbi-maintain-model
      source_drift unavailable           -> TRIAGE
      model_doc_drift candidate          -> C  /chatbi-maintain-knowledge
      model_doc_drift unavailable/skip   -> TRIAGE
    """
    kind = candidate.kind
    status = candidate.status
    subtype = candidate.details.get("subtype")

    if kind == "stale_reference":
        if status == "candidate":
            return RouteDecision(
                "C", DRIFT_ROUTES["C"],
                "stale cited git_sha -> re-author reference and capture fresh citation",
            )
        return RouteDecision(
            "TRIAGE", DRIFT_ROUTES["TRIAGE"],
            "reference staleness unavailable/skipped -> human triage",
        )

    if kind == "source_drift":
        if status != "candidate":
            return RouteDecision(
                "TRIAGE", DRIFT_ROUTES["TRIAGE"],
                "source drift unavailable -> human triage",
            )
        if subtype == "scope_expansion":
            return RouteDecision(
                "B", DRIFT_ROUTES["B"],
                "source scope expansion -> human approval + incremental introspect",
            )
        if subtype == "shape_change":
            return RouteDecision(
                "E", DRIFT_ROUTES["E"],
                "source shape change -> maintain model",
            )
        return RouteDecision(
            "TRIAGE", DRIFT_ROUTES["TRIAGE"],
            "unknown source drift subtype -> human triage",
        )

    if kind == "model_doc_drift":
        if status == "candidate":
            return RouteDecision(
                "C", DRIFT_ROUTES["C"],
                "model-doc lint field -> re-author + lint + co-locate",
            )
        return RouteDecision(
            "TRIAGE", DRIFT_ROUTES["TRIAGE"],
            "model-doc drift unavailable/skipped -> human triage",
        )

    return RouteDecision(
        "TRIAGE", DRIFT_ROUTES["TRIAGE"],
        "unknown drift kind -> human triage",
    )


def classify_src002_finding(evidence: CodebaseEvidence) -> RouteDecision:
    """Deterministically route one SRC-002 cross-check finding (HOOK-001).

    Build Step 1.6 produces a ``CodebaseEvidence`` per alias/target via
    ``select_codebase_reader`` + ``reader.read/search``. This classifier maps
    the evidence's deterministic discriminators (status + conflicts) to a
    route class (A/D/F), using ``SRC002_ROUTES`` (which references
    ``DRIFT_ROUTES`` for B/C/D/E). It does NOT do entity resolution (class A's
    "ask what" is agent reasoning) and does NOT auto-execute D's approval
    (SEM-003).

    Mapping:
      status == "ok" + conflicts non-empty -> D  /chatbi-correction
        (governed metric definition contradicts external definition = governed
        artifact error; correction produces a dual candidate with
        owner_approved=false)
      status == "ok" + conflicts empty     -> F  PASS (proceed with build chain)
      status in {"blocked", "error"}       -> A  STOP (ask owner: alias/path
        unresolved or cross-check failed)
    """
    if evidence.status == "ok":
        if evidence.conflicts:
            return RouteDecision(
                "D", SRC002_ROUTES["D"],
                "SRC-002 conflict (same-name different-definition) -> "
                "correction dual candidate (owner_approved=false, SEM-003)",
            )
        return RouteDecision(
            "F", SRC002_ROUTES["F"],
            "no SRC-002 conflict; proceed with build chain",
        )
    # blocked (alias/path unresolved) or error (read/search failed)
    return RouteDecision(
        "A", SRC002_ROUTES["A"],
        f"SRC-002 cross-check {evidence.status}: {evidence.reason or 'unresolved'} "
        "-> ask domain owner",
    )


# ---------------------------------------------------------------------------
# detect_drift orchestration
# ---------------------------------------------------------------------------


def detect_drift(
    workspace_root: Path,
    config: Any,
    *,
    scope: str = "all",
    since: str | None = None,
    fresh_source_inventory: SourceInventory | None = None,
    session_id: str | None = None,  # noqa: ARG001 (design signature; v1 does not write state)
) -> DriftReport:
    """Orchestrate the three FM-STALE drift classes and return a schema-validated report.

    ``scope`` gates which classes run (``references``=1, ``sources``=2,
    ``models``=3, ``all``=1+2+3). A missing baseline ``source_inventory.json``
    (class 2 prerequisite) raises :class:`GateError` (hard STOP). An unavailable
    live adapter / trusted git degrades one class to an ``unavailable``
    candidate; the other classes still run. ``fresh_source_inventory`` is
    produced by the command (OD3); ``None`` -> class 2 unavailable.
    """
    if scope not in _VALID_SCOPES:
        raise _drift_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="drift:scope",
            reason=f"Unknown scope: {scope!r}",
            recovery=f"Use one of {sorted(_VALID_SCOPES)}",
        )

    workspace = config["workspace"]["id"]

    # Class 2 prerequisite: baseline source_inventory must exist (hard STOP,
    # not an unavailable candidate). Read once; reused for the diff.
    baseline: SourceInventory | None = None
    if scope in ("sources", "all"):
        baseline = read_source_inventory(
            workspace_root / ".chatbi" / "bootstrap" / "source_inventory.json"
        )

    refs = _discover_governed_references(workspace_root)

    stale_candidates: tuple[DriftCandidate, ...] = ()
    source_candidates: tuple[DriftCandidate, ...] = ()
    model_candidates: tuple[DriftCandidate, ...] = ()
    head_shas: dict[str, str | None] = {}
    path_refs: tuple[dict[str, Any], ...] = ()

    if scope in ("references", "all"):
        stale_candidates, head_shas, path_refs = _detect_stale_references(
            workspace_root, config, refs, since
        )
    if scope in ("sources", "all"):
        # baseline is non-None here (prerequisite check above).
        source_candidates = _detect_source_drift(baseline, fresh_source_inventory)
    if scope in ("models", "all"):
        model_candidates = _detect_model_doc_drift(workspace_root, refs)

    report = DriftReport(
        schema_version=_SCHEMA_VERSION,
        produced_at=_utc_now_iso(),
        workspace=workspace,
        scope=scope,
        since=since,
        head_shas=head_shas,
        classes={
            "stale_reference": stale_candidates,
            "source_drift": source_candidates,
            "model_doc_drift": model_candidates,
        },
        fbk_003_statement=FBK_003_STATEMENT,
        path_references=path_refs,
    )

    _validate_against_schema(
        report.to_dict(),
        _get_schema("drift-report.schema.json"),
        "drift-report.schema.json",
    )
    return report


__all__ = [
    "DRIFT_ROUTES",
    "DriftCandidate",
    "DriftReport",
    "FBK_003_STATEMENT",
    "RouteDecision",
    "SRC002_ROUTES",
    "classify_finding",
    "classify_src002_finding",
    "detect_drift",
]
