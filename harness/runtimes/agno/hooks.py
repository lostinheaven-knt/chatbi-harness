"""Tool-level hooks + run-level guardrails for the ChatBI agent (module B).

The single governance agent carries its deterministic enforcement on two
edges (design §2, modification §6.1):

- **tool_hooks** (tool boundary, synchronous — equivalent to CC
  PreToolUse/PostToolUse): a six-layer chain per tool call. The agno 2.6.22
  nested-chain mechanism (``tools/function.py:1021-1090``) reverses the list
  and reduces it, so **the list HEAD is the outermost hook (executes
  first)** — empirically verified on 2.6.22 (the design doc's "列表尾=最外层"
  is inverted; M7 registration). Semantic order outer->inner:

    1. ``realpath_hook``    — path-typed args: absolute-path escape and
                             undeclared codebase alias -> deny (C010, SEC-001);
                             runs BEFORE sanitize so escape detection sees the
                             RAW argument values (M7 note);
    2. ``sanitize_hook``    — SEC-003/PORT-001 arg sanitization (+ run scope
                             refresh from ``run_context``);
    3. ``allowlist_hook``   — IR tool-surface allowlist (C011): a tool that
                             is not a governance tool or a read-only file
                             tool is denied;
    4. ``approval_verify_hook`` — ``@approval`` tools: AgentOS confirmation
                             has passed (the call is about to run) -> Kernel
                             re-verification via the ApprovalCoordinator
                             (module D); failure -> deny + re-apply (C005/6/7);
    5. ``domain_hook``      — per-tool kernel judgments (tier-gap
                             preconditions, candidate SHA binding, review
                             verdict validation, EVAL-004/DOC-004 gates,
                             lint/drift/bootstrap/init chains);
    6. ``event_hook``       — ``tool.requested`` before ``next_func``,
                             ``tool.completed`` after; denies are emitted by
                             the denying hook itself (the inner event hook
                             cannot run once the chain is cut) with the same
                             ``tool.blocked`` shape.

  Axioms (test-pinned): any deny never executes the tool; any hook exception
  fails closed (never an implicit pass, HOOK-004).

- **run-level guardrails** (run boundary, synchronous — equivalent to CC
  SessionStart/Stop hooks). AgentOS server mode runs non-guardrail hooks in
  the background; guardrails always run synchronously and may raise
  ``InputCheckError``/``OutputCheckError`` (``agent/_hooks.py:60-95``), so
  ALL run-level blocking edges are BaseGuardrail subclasses:

  - :class:`ChatbiRequestGuardrail` (pre[0]) — structured run input:
    ``evidence.validate_request`` (analyze) with the minimal clarifying
    question on failure; free-text inputs pass (entry is lenient, the
    terminal gate is authoritative);
  - :class:`ChatbiPolicyGuardrail` (pre[1]) — records the run-level trusted
    subject (``run_subject`` contextvar — subject only ever comes from the
    run context, never the input body, SEC-003) + SEM-003 protected-intent
    precheck for structured requests;
  - :class:`ChatbiDeliveryGuardrail` (post[0]) — the ONLY terminal authority
    (ADR-002): reads the run's evidence chain from the evidence index,
    applies the delivery gate (REV-001/002/003 + candidate SHA binding +
    provenance footer F1), emits ``run.completed`` ONLY on PASS, otherwise
    emits ``gate.blocked`` and raises ``OutputCheckError``.

Agent-mode honest registration (M7): step order (T1->T2->T3, clarify,
routing) is runbook soft guidance; the hooks only enforce deterministically
computable edges (evidence preconditions, SHA, approval, allowlist,
realpath, terminal gate) — no claim of runtime step-order enforcement.

Applicable rules: HOOK-001, HOOK-004, MR-005, ADR-002/003, SEC-001/003,
SEM-003, PORT-001, REV-001/002/003, C010/C011 semantics, invariant 2/5.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from chatbi_governance.adapters import resolve_executable
from chatbi_governance.adapters.codebase_reader import select_codebase_reader
from chatbi_governance.bootstrap import (
    SourceColumn,
    SourceInventory,
    SourceTable,
    build_mysql_adapter_spec,
    merge_local_config,
    read_source_inventory,
)
from chatbi_governance.build_plan import (
    append_model_registry,
    build_model_entry,
    validate_build_plan,
    validate_layer_dependency,
)
from chatbi_governance.diagnostics import run_init_diagnostic
from chatbi_governance.drift import (
    DriftCandidate,
    classify_finding,
    classify_src002_finding,
    detect_drift,
)
from chatbi_governance.evidence import (
    EvidenceEntry,
    GateError,
    compute_candidate_sha,
    validate_provenance,
    validate_request,
    validate_review,
)
from chatbi_governance.gates import GateDecision, _sanitize_text
from chatbi_governance.harness_state import _safe_session_id, write_state
from chatbi_governance.impact import AffectedAsset, build_impact_manifest
from chatbi_governance.knowledge import lint_reference
from chatbi_governance.policy import PolicyRequest, decide

from .governed_tools import RunScope, evaluate_step_condition
from .tools import StepToolPolicy, TOOL_NAME_MAP

#: agno import: the package __init__ runs the unshadow guard, but a later
#: test module may re-insert ``<root>/runtimes`` into sys.path (re-shadowing
#: the installed agno) while this module is imported fresh in a batch run —
#: re-run the guard explicitly (reviewer.py pattern).
from . import ensure_agno_unshadowed  # noqa: E402

ensure_agno_unshadowed()
from agno.guardrails.base import BaseGuardrail  # noqa: E402

#: Guardrail exceptions are imported lazily below in the guardrail classes to
#: keep the module importable without the agno runtime (module-level import
#: stays agno-free so conformance/unit tests can import the hook builders on
#: any interpreter).

#: Run-level trusted subject (SEC-003): set by ChatbiPolicyGuardrail from the
#: run context ONLY (never from the input body); read by the approval hook.
run_subject: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chatbi_run_subject", default=""
)

#: Review failure-mode rule sets (delivery-gate vocabulary, M5-S6 semantics —
#: mirrors the module-5 delivery-gate vocabulary).
_RULES_UNAVAILABLE = ("HOOK-001", "HOOK-004", "SEC-003")
_RULES_STALE_SHA = ("REV-001", "REV-003")
_RULES_ROUND = ("REV-003", "HOOK-001")
_RULES_NOT_PASS = ("REV-001", "REV-003", "HOOK-001")


def _clarify_request_decision(decision: GateDecision) -> GateDecision:
    """Translate a request-schema GateDecision into a clarify-oriented denial.

    Real-model integration (agno 验收 3.1): the model fills the request
    contract from the tool description and the denial recovery. The generic
    schema recovery ("Correct the payload...") gives no signal to ASK the
    user for a missing field, so the agent guesses an empty time_range and
    the delivery gate later blocks with C002. Map missing/empty fields to an
    explicit ask-the-user recovery (REQ-001 clarify) without weakening the
    fail-closed decision (rule_ids/status unchanged).
    """
    reason = decision.reason or ""
    recovery = decision.recovery or ""
    if "missing required field" in reason and "'" in reason:
        field = reason.split("'")[1]
        recovery = (
            f"Required request field '{field}' is missing. If the user's "
            f"question does not provide '{field}', ASK the user for it "
            f"before proceeding (REQ-001 clarify); never guess or send an "
            f"empty value."
        )
    elif "time_range" in reason:
        recovery = (
            "time_range is required, format 'YYYY-MM-DD_to_YYYY-MM-DD' "
            "(e.g. '2024-01-01_to_2024-01-31'). If the user did not specify "
            "an analysis window, ASK the user for it (REQ-001 clarify); "
            "never guess or send an empty value."
        )
    if recovery == (decision.recovery or ""):
        return decision
    return GateDecision(
        status=decision.status,
        rule_ids=decision.rule_ids,
        evidence_refs=decision.evidence_refs,
        reason=reason,
        recovery=recovery,
    )


#: agno 2.6.22 native skill tools (F1): bundled by
#: ``Skills([LocalSkills(skills_root)])``, never allowlisted (C011). The
#: allowlist-hook deny for these three carries a recovery pointing at the
#: governed alternative (design-runbook-completion A1 — educate the model
#: instead of letting the deny destabilize the flow).
_SKILL_TOOLS = frozenset({
    "get_skill_instructions", "get_skill_reference", "get_skill_script",
})
_SKILL_TOOL_RECOVERY = (
    "Use chatbi_load_runbook(<workflow_id>) to load the governed runbook "
    "for the current workflow (native skill tools are not allowlisted, C011)"
)

#: Per-run BLOCKED-review ceiling (design-runbook-completion B1): the same
#: run may accumulate at most 3 ``review.completed``(BLOCKED) events; the
#: 4th review attempt is denied at the tool edge WITHOUT invoking the
#: reviewer (terminal deny, payload.terminal=true — zero model cost, the
#: d5f38994 7-round runaway becomes bounded). Normal fix loops need 1-2
#: rounds; the 3rd leaves room for genuine reviewer variance (ec09881b
#: passed then blocked); >3 is pathological. Counting is per-run (event-log
#: derived, candidate-SHA independent), PASS does not reset the budget, and
#: budgets never carry across runs (multi-turn dialogue semantics).
REVIEW_BLOCK_LIMIT = 3

#: dbt-run evidence log tail cap (2 KiB, design §6.2 step 5).
_LOG_TAIL_CAP = 2048

#: Semantic doc content cap returned to the model (16 KiB per doc; the
#: evidence payload carries metadata only, design §7.1 step 3).
_SEMANTIC_DOC_CAP = 16 * 1024


def _reviewed_candidate_shas(event_log: Any, run_id: str) -> frozenset[str]:
    """candidate_sha values of ``review.completed``(PASS) events in the run.

    Phase 2 module B (Q6, technical-design-agno-phase2 §6.2 step 3): the
    deterministic dbt-execution review prerequisite. Same event-log replay
    technique as :func:`_review_block_count` (per-run, auditable).
    """
    try:
        events = event_log.replay(run_id).events
    except Exception:  # noqa: BLE001 - no log -> no reviewed candidate (deny)
        return frozenset()
    shas: set[str] = set()
    for event in events:
        if event.get("event_type") != "review.completed":
            continue
        payload = event.get("payload") or {}
        if payload.get("status") != "PASS":
            continue
        sha = payload.get("candidate_sha")
        if isinstance(sha, str) and sha:
            shas.add(sha)
    return frozenset(shas)


def _review_block_count(event_log: Any, run_id: str) -> int:
    """BLOCKED ``review.completed`` events in the run (event-log derived).

    The event log is the single authoritative per-run counter (HOOK-001:
    auditable, replayable; the RunScope is a process-shared object reused
    across runs/sessions — a scope field would need run-boundary resets and
    is error-prone). Replay cost is bounded (per-run JSONL, 64MB guard).
    """
    try:
        events = event_log.replay(run_id).events
    except Exception:  # noqa: BLE001 - no log -> count 0 (the delivery
        return 0        # gate still fails closed; never a silent pass)
    return sum(
        1 for e in events
        if e.get("event_type") == "review.completed"
        and (e.get("payload") or {}).get("status") == "BLOCKED"
    )

#: Tier -> IR when precondition for chatbi_record_evidence.
_TIER_WHEN = {"T2": 'evidence.has_gap("T1")', "T3": 'evidence.has_gap("T2")'}
_TIER_SOURCE = {"T1": "semantic-layer", "T2": "curated-reference",
                "T3": "raw-exploration"}
_TIER_RULE_IDS = {"T1": ("SEM-001", "SEM-002"), "T2": ("RAW-001", "SRC-001"),
                  "T3": ("RAW-003",)}

#: Path-typed argument keys inspected by the realpath hook. Phase 2 (Q5):
#: ``relative_path`` (chatbi_dbt_draft) joins the set — absolute paths are
#: rejected (PORT-001); the draft handler double-checks containment.
_PATH_KEYS = ("codebase", "ref", "path", "target", "codebase_path",
              "relative_path")


# ---------------------------------------------------------------------------
# Phase 2 (module A, technical-design-agno-phase2 §5.2): read-only SQL
# validation + table allowlist (pure functions, no SQL parser dependency)
# ---------------------------------------------------------------------------

#: Forbidden statement keywords (word-boundary, case-insensitive). ``set`` /
#: ``use`` / ``show`` / ``describe`` / ``explain`` are statement starters in
#: MySQL — a SELECT statement must never contain them at word boundaries
#: (regex whitelist discipline: false positives deny, never a silent pass).
_FORBIDDEN_SQL_WORD_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|rename|grant|"
    r"revoke|call|set|use|show|describe|explain|load\s+data|"
    r"into\s+(outfile|dumpfile))\b",
    re.IGNORECASE,
)
_SQL_MAX_STATEMENT_BYTES = 16 * 1024


def validate_readonly_select(statement: str) -> str | None:
    """None = legal; otherwise the error category (deterministic, HOOK-001).

    Rules (all regex whitelist, no SQL parser dependency —
    technical-design-agno-phase2 §5.2):

    - must start with ``select`` (``^select\b``, case-insensitive) after
      stripping whitespace;
    - ``;`` anywhere -> ``multi_statement`` (trailing included);
    - ``--`` or ``/*`` -> ``comment``;
    - a forbidden keyword at a word boundary -> ``forbidden_keyword``;
    - length > 16 KiB -> ``too_long``.
    """
    if not isinstance(statement, str):
        return "not_select"
    stripped = statement.strip()
    if not re.match(r"^select\b", stripped, re.IGNORECASE):
        return "not_select"
    if ";" in statement:
        return "multi_statement"
    if "--" in statement or "/*" in statement:
        return "comment"
    if _FORBIDDEN_SQL_WORD_RE.search(statement):
        return "forbidden_keyword"
    if len(statement.encode("utf-8")) > _SQL_MAX_STATEMENT_BYTES:
        return "too_long"
    return None


#: from/join table reference (``db.table`` or ``table``, backticks allowed).
_TABLE_REF_RE = re.compile(
    r"(?:from|join)\s+"
    r"((?:`[^`]+`|[a-zA-Z_][a-zA-Z0-9_]*)"
    r"(?:\s*\.\s*(?:`[^`]+`|[a-zA-Z_][a-zA-Z0-9_]*))?)",
    re.IGNORECASE | re.DOTALL,
)


def resolve_table_refs(statement: str) -> list[str]:
    """Extract from/join table references (schema.table and backticks
    included), regex-collected and deduplicated (design §5.2).

    The allowlist check is the security boundary: a reference this regex
    misses is simply NOT in the allowlist -> the query is denied
    (fail-closed direction).
    """
    refs: list[str] = []
    seen: set[str] = set()
    for match in _TABLE_REF_RE.finditer(statement or ""):
        raw = match.group(1).strip().lower()
        raw = raw.replace("`", "").replace(" ", "")
        if raw and raw not in seen:
            seen.add(raw)
            refs.append(raw)
    return refs


def _load_query_allowlists(
    workspace_root: Path,
) -> tuple[frozenset[str], frozenset[str], str]:
    """(dw_agno 模型集, public 表集, source_database) — fail-closed.

    dw_agno models: ``.chatbi/model_registry.json`` entry.name union
    ``ws/models/**/*.sql`` filenames (minus .sql); public tables:
    ``.chatbi/bootstrap/source_inventory.json`` tables[].name. A missing or
    corrupt source contributes the EMPTY set for its domain (queries there
    are denied, never silently widened) — design §5.2.
    """
    models: set[str] = set()
    try:
        from chatbi_governance.build_plan import read_model_registry

        for entry in read_model_registry(
                workspace_root / ".chatbi" / "model_registry.json"):
            models.add(str(entry.name).lower())
    except Exception:  # noqa: BLE001 - corrupt/absent registry contributes nothing
        pass
    models_dir = workspace_root / "models"
    if models_dir.is_dir():
        for path in models_dir.rglob("*.sql"):
            models.add(path.stem.lower())
    public: set[str] = set()
    source_db = ""
    try:
        inventory = read_source_inventory(
            workspace_root / ".chatbi" / "bootstrap"
            / "source_inventory.json")
        source_db = str(inventory.source_database).lower()
        public = {str(t.name).lower() for t in inventory.tables}
    except Exception:  # noqa: BLE001 - absent/corrupt inventory -> denied
        pass
    return frozenset(models), frozenset(public), source_db


def query_table_allowlist(
    workspace_root: Path,
) -> tuple[frozenset[str], frozenset[str]]:
    """(dw_agno 模型集, public 表集) — fail-closed allowlist source
    (design §5.2). See :func:`_load_query_allowlists`."""
    models, public, _source_db = _load_query_allowlists(workspace_root)
    return models, public


# ---------------------------------------------------------------------------
# Phase 2 (module D, technical-design-agno-phase2 §7): semantic discovery
# ---------------------------------------------------------------------------


def _fixture_catalog_path() -> Path | None:
    """Runtime-relative semantic fixture catalog (PORT-001: no machine path).

    Resolved relative to THIS package (``harness/.claude/fixtures/
    semantic-catalog.json`` in the dev tree; the same relative layout in the
    built product). None = unresolvable (fail-closed when fixture mode is
    active)."""
    candidate = Path(__file__).resolve().parents[2] / ".claude" / "fixtures" \
        / "semantic-catalog.json"
    return candidate if candidate.is_file() else None


def _semantic_metric_of(doc_path: Path) -> str | None:
    """The first ``# Metric: <name>`` line of a semantic doc, or None."""
    try:
        for line in doc_path.read_text(encoding="utf-8").splitlines()[:20]:
            stripped = line.strip()
            if stripped.lower().startswith("# metric:"):
                return stripped[len("# metric:"):].strip()
    except (OSError, UnicodeDecodeError):
        return None
    return None


@dataclass(frozen=True)
class HookOutcome:
    """One hook decision (deny payload carrier)."""

    allowed: bool
    event_type: str | None = None        # tool.blocked etc.
    payload: Mapping[str, Any] = field(default_factory=dict)


def _sanitize_args(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {key: _sanitize_args(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_sanitize_args(item) for item in value]
    return value


def _is_absolute_path(value: str) -> bool:
    if value.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", value)):
        return True
    return Path(value).is_absolute()


def _deny_payload(
    name: str,
    *,
    rule_ids: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    reason: str,
    recovery: str,
) -> dict[str, Any]:
    """Standard deny payload (tool.blocked shape, CC-同构)."""
    return {
        "status": "blocked",
        "tool": name,
        "rule_ids": list(rule_ids),
        "evidence_refs": list(evidence_refs),
        "reason": _sanitize_text(reason),
        "recovery": _sanitize_text(recovery),
    }


def _emit_tool_blocked(
    event_log: Any, scope: RunScope, name: str, payload: Mapping[str, Any],
) -> None:
    from .events import emit_standard_event

    emit_standard_event(
        event_log,
        run_id=scope.run_id or "run",
        session_id=scope.session_id or "session",
        workflow_id=scope.workflow_id or "chatbi-analyze",
        step_id=name,
        event_type="tool.blocked",
        payload=dict(payload),
        evidence_refs=tuple(payload.get("evidence_refs", []) or ()),
    )


def _record_evidence_file(
    *,
    scope: RunScope,
    evidence_index: Any,
    workspace_root: Path,
    harness_release: str,
    entry: EvidenceEntry,
    step_id: str,
) -> Path:
    """Persist one EvidenceEntry under .chatbi and index it (ADR-003)."""
    safe_sid = _safe_session_id(scope.session_id or "session")
    written = write_state(
        workspace_root, safe_sid,
        f"evidence-{step_id}-{(scope.run_id or 'run')[:8]}.json",
        entry.to_dict(),
    )
    evidence_index.add(written)
    return written


def _emit_evidence_recorded(
    event_log: Any, scope: RunScope, entry: EvidenceEntry,
) -> None:
    from .events import emit_standard_event

    emit_standard_event(
        event_log,
        run_id=scope.run_id or "run",
        session_id=scope.session_id or "session",
        workflow_id=scope.workflow_id or "chatbi-analyze",
        step_id=None,
        event_type="evidence.recorded",
        payload={
            "source_tier": entry.source_tier,
            "evidence_source": entry.evidence_source,
            "content_sha256": entry.content_sha256,
            "rule_ids": list(entry.rule_ids),
        },
        evidence_refs=(entry.evidence_source,),
    )


# ---------------------------------------------------------------------------
# build_tool_hooks
# ---------------------------------------------------------------------------


def build_tool_hooks(
    *,
    specs_by_name: Mapping[str, Any],
    ir_workflows: Mapping[str, Any],
    config: Any,
    approvals: Any,
    evidence_index: Any,
    event_log: Any,
    workspace_root: Path,
    harness_release: str,
    run_scope: RunScope | None = None,
    reviewer_runner: Any = None,
    native_runner: Callable[..., Any] | None = None,
    deployment: Any = None,
    clock: Any = None,
    runbook_registry: Mapping[str, Any] | None = None,   # A1（IR+manifest 派生）
) -> list[Callable[..., Any]]:
    """Build the agent.tool_hooks chain (design §2.1, six layers).

    Returns the list in OUTERMOST-first order: the agno mechanism
    (``tools/function.py:1047-1049`` — reverse + reduce) makes the list HEAD
    the outermost hook that executes first (empirically verified on 2.6.22:
    ``tool_hooks=[A, B]`` runs A-before -> B-before -> tool -> B-after ->
    A-after; the design doc's "列表尾=最外层" is inverted, M7 registration).
    Hook signature: ``(name, func, args)`` — ``func`` is the next_func
    continuation (call ``func(**args)`` to run the chain; NOT calling it
    denies the tool).
    """
    from .approvals import (
        ApprovalContext,
        bridge_request_approval,
        reverify_before_execute,
    )

    scope = run_scope if run_scope is not None else RunScope()

    # -- layer 2: allowlist policy -----------------------------------------
    # The agent's ACTUAL surface is governance tools + read-only file tools
    # (design R2: the agent holds no bare Write/Edit/Bash). The IR deny
    # lists (Task/WebFetch/WebSearch/…) and the step-level write denies are a
    # second layer — anything on any IR deny list is blocked (C011).
    deny: set[str] = set()
    for workflow in ir_workflows.values():
        tools_spec = getattr(workflow, "tools", None)
        if tools_spec is None:
            continue
        deny.update(getattr(tools_spec, "deny", ()) or ())
    allow = set(specs_by_name.keys()) | {"Read", "Grep", "Glob"}
    allow = {name for name in allow if name not in deny}
    allowlist_policy = StepToolPolicy(allow=allow, deny=deny)

    def _check_allowlist(name: str) -> bool:
        # tool_name normalization: agno composite names -> IR vocabulary.
        return allowlist_policy.check(TOOL_NAME_MAP.get(name, name))

    # -- layer 1: sanitize + scope refresh ---------------------------------
    def sanitize_hook(name: str, func: Callable[..., Any],
                      args: Mapping[str, Any],
                      run_context: Any = None) -> Any:
        if run_context is not None:
            run_id = getattr(run_context, "run_id", "") or ""
            if run_id and run_id != scope.run_id:
                # Run boundary (multi-turn isolation, design-runbook C2-1):
                # RunScope is a single-agent process-shared object reused
                # across runs/sessions (F9) — per-run state must never leak
                # into a later run. A previous run's T1 gap could otherwise
                # satisfy THIS run's T2/T3 tier-gap precondition at the tool
                # edge (SEM-001 bypass), and the reviewer context would carry
                # stale evidence refs / review round (REV-003 misdirection).
                # The review BLOCK ceiling is already event-log derived
                # (_review_block_count, per run_id) — the remaining scope
                # fields are reset here at the run boundary (live-verified:
                # run N's gap leaked into run N+1's T2 acceptance before this
                # fix; deterministic probe reproduced both directions).
                scope.evidence_chain.clear()
                scope.review_round = 1
                scope.candidate_sha = ""
                scope.impact = None
            scope.run_id = run_id or scope.run_id
            scope.session_id = (
                getattr(run_context, "session_id", "") or scope.session_id
            )
            scope.workflow_id = (
                getattr(run_context, "workflow_id", "") or scope.workflow_id
            )
        clean = _sanitize_args(dict(args))
        return func(**clean)

    # -- layer 2: allowlist -------------------------------------------------
    def allowlist_hook(name: str, func: Callable[..., Any],
                       args: Mapping[str, Any]) -> Any:
        if not _check_allowlist(name):
            decision = GateDecision.block(
                rule_ids=("HOOK-001", "SEC-001"),
                evidence_refs=(f"tool:not-allowlisted:{name}",),
                reason=(
                    f"tool {name!r} is not on the IR tool surface; "
                    "unregistered tools are blocked (C011)"
                ),
                recovery="Use a governance tool or a read-only file tool",
            )
            payload = _deny_payload(name, rule_ids=decision.rule_ids,
                                    evidence_refs=decision.evidence_refs,
                                    reason=decision.reason,
                                    recovery=decision.recovery)
            # A-1: the three native skill tools keep their deny but their
            # recovery points at the governed alternative (方案② — the
            # get_skill_* surface stays blocked, C011).
            if name in _SKILL_TOOLS:
                payload["recovery"] = _SKILL_TOOL_RECOVERY
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        return func(**args)

    # -- layer 3: realpath --------------------------------------------------
    def realpath_hook(name: str, func: Callable[..., Any],
                      args: Mapping[str, Any]) -> Any:
        for key in _PATH_KEYS:
            value = args.get(key)
            if not isinstance(value, str) or not value:
                continue
            if _is_absolute_path(value):
                decision = GateDecision.block(
                    rule_ids=("SEC-001", "PORT-001"),
                    evidence_refs=(f"path:absolute:{key}",),
                    reason=(
                        f"argument {key!r} contains an absolute path; "
                        "workspace-relative paths only (PORT-001)"
                    ),
                    recovery="Pass a workspace-relative path or a configured "
                             "codebase alias",
                )
                payload = _deny_payload(name, rule_ids=decision.rule_ids,
                                        evidence_refs=decision.evidence_refs,
                                        reason=decision.reason,
                                        recovery=decision.recovery)
                _emit_tool_blocked(event_log, scope, name, payload)
                return payload
            if key == "codebase" and config is not None:
                selection = select_codebase_reader(config, alias=value)
                if selection.status == "stopped":
                    decision = selection.stop_decision
                    payload = _deny_payload(
                        name, rule_ids=decision.rule_ids,
                        evidence_refs=decision.evidence_refs,
                        reason=decision.reason, recovery=decision.recovery)
                    _emit_tool_blocked(event_log, scope, name, payload)
                    return payload
        return func(**args)

    # -- layer 4: approval re-verification (module D) -----------------------
    def _approval_action_type(name: str) -> str:
        request = scope.request or {}
        action = request.get("action_type")
        if isinstance(action, str) and action:
            return action
        # IR human_approval step when condition: owner.pending(<action>).
        for workflow in ir_workflows.values():
            for step in getattr(workflow, "steps", ()) or ():
                when = getattr(step, "when", None)
                if isinstance(when, str) and "owner.pending" in when:
                    match = re.search(r"owner\.pending\(([A-Za-z0-9_-]+)\)", when)
                    if match:
                        return match.group(1)
        return "approve_metric"

    def approval_verify_hook(name: str, func: Callable[..., Any],
                             args: Mapping[str, Any]) -> Any:
        from .events import emit_standard_event

        spec = specs_by_name.get(name)
        if spec is None or spec.approval != "required":
            return func(**args)
        requester = run_subject.get()
        if not requester:
            # MED-3 (eval round 1): the requester subject must come from the
            # run context (SEC-003). An empty subject is fail-closed — never
            # a fabricated literal ("operator") in the audit chain.
            payload = _deny_payload(
                name, rule_ids=("SEC-003",),
                reason="approval requester subject is missing; the run has "
                       "no authenticated user (fail-closed, SEC-003)",
                recovery="Authenticate the run user and re-request the "
                         "protected action")
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        action_type = _approval_action_type(name)
        candidate_sha = scope.candidate_sha or compute_candidate_sha(
            {"action": action_type, "actor": requester})
        context = ApprovalContext(
            workflow_id=scope.workflow_id or "chatbi-maintain-model",
            run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            step_id=name,
        )
        try:
            handle = bridge_request_approval(
                coordinator=approvals,
                context=context,
                action_type=action_type,
                requester_subject=requester,
                candidate_sha=candidate_sha,
                evidence_refs=tuple(
                    e.get("evidence_source", "")
                    for e in scope.evidence_chain if isinstance(e, Mapping)
                ),
            )
        except Exception as error:  # policy block (SEM-003) etc.
            rule_ids = ("SEM-003", "DOC-004")
            reason = (
                f"approval request blocked: {type(error).__name__}: {error}"
            )
            if isinstance(error, GateError):
                rule_ids = error.decision.rule_ids
                reason = error.decision.reason
            payload = _deny_payload(name, rule_ids=rule_ids,
                                    reason=reason,
                                    recovery="Wait for the human owner to "
                                             "approve the protected action")
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        record = approvals.get(handle.approval_id)
        if record is None:
            payload = _deny_payload(name, rule_ids=("HOOK-004",),
                                    reason="approval record missing (fail-closed)",
                                    recovery="Re-request the approval")
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        # Kernel re-verification BEFORE execution (先验后续). The AgentOS
        # confirmation is the transport; the governance judgment treats the
        # confirmation as the configured superuser's action (ADR-002: the
        # Kernel is authoritative) — the run user stays the requester, so
        # requester != resolver is enforced (a superuser-run requesting its
        # own protected action is rejected).
        superuser = getattr(deployment, "superuser_subject", None)
        violations = reverify_before_execute(
            record,
            subject=superuser or "",
            current_candidate_sha=candidate_sha,
            config=config,
            superuser_subject=superuser,
            evidence_index=evidence_index,
            workspace_root=Path(workspace_root),
            clock=clock,
        )
        if violations:
            emit_standard_event(
                event_log,
                run_id=scope.run_id or "run",
                session_id=scope.session_id or "session",
                workflow_id=scope.workflow_id or "chatbi-maintain-model",
                step_id=name,
                event_type="approval.resolved",
                payload={"approval_id": record.approval_id,
                         "resolution": "rejected",
                         "candidate_sha": record.candidate_sha,
                         "reason": "; ".join(violations)},
                evidence_refs=record.evidence_refs,
            )
            payload = _deny_payload(
                name, rule_ids=("SEM-003", "DOC-004"),
                reason="; ".join(violations),
                recovery="Re-apply: the protected action requires a fresh "
                         "human-owner approval")
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload
        # PASS: the confirmation is valid — record the resolution event
        # (the record stays coordinator-authoritative; the event is the
        # audit trail) then execute the tool.
        emit_standard_event(
            event_log,
            run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-maintain-model",
            step_id=name,
            event_type="approval.resolved",
            payload={"approval_id": record.approval_id,
                     "resolution": "approved",
                     "candidate_sha": record.candidate_sha},
            evidence_refs=record.evidence_refs,
        )
        return func(**args)

    # -- layer 5: per-tool kernel judgments ---------------------------------
    domain_hook = _build_domain_hook(
        scope=scope,
        config=config,
        approvals=approvals,
        evidence_index=evidence_index,
        event_log=event_log,
        workspace_root=Path(workspace_root),
        harness_release=harness_release,
        reviewer_runner=reviewer_runner,
        native_runner=native_runner,
        ir_workflows=ir_workflows,
        runbook_registry=runbook_registry,
        #: Phase 2 (Q4): the deployment boundary (cli_allowlist / dbt_bin /
        #: run_mode / warehouse_db) is the authority for the mysql/dbt
        #: execution surface (technical-design-agno-phase2 §3.3, §5.1).
        deployment=deployment,
    )

    # -- layer 6: event envelope (requested / completed) --------------------
    def event_hook(name: str, func: Callable[..., Any],
                   args: Mapping[str, Any]) -> Any:
        from .events import emit_standard_event

        emit_standard_event(
            event_log,
            run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-analyze",
            step_id=name,
            event_type="tool.requested",
            payload={"tool": name},
        )
        result = func(**args)
        emit_standard_event(
            event_log,
            run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-analyze",
            step_id=name,
            event_type="tool.completed",
            payload={"tool": name},
        )
        return result

    # OUTERMOST first (list head executes first, empirically verified on
    # agno 2.6.22 — the design doc's "列表尾=最外层" is inverted).
    # Ordering note (M7): realpath runs BEFORE sanitize so escape detection
    # sees the RAW argument values (sanitize would redact an absolute path
    # into "[REDACTED_PATH]" and the C010 deny could never fire). Sanitize
    # still precedes allowlist/approval/domain/event (design §2.4 chain
    # assertion).
    return [
        realpath_hook,
        sanitize_hook,
        allowlist_hook,
        approval_verify_hook,
        domain_hook,
        event_hook,
    ]


# ---------------------------------------------------------------------------
# Domain hook (layer 5): per-governance-tool kernel judgments
# ---------------------------------------------------------------------------


def _build_domain_hook(
    *,
    scope: RunScope,
    config: Any,
    approvals: Any,
    evidence_index: Any,
    event_log: Any,
    workspace_root: Path,
    harness_release: str,
    reviewer_runner: Any,
    native_runner: Callable[..., Any] | None,
    ir_workflows: Mapping[str, Any] | None = None,
    runbook_registry: Mapping[str, Any] | None = None,   # A1（IR+manifest 派生）
    deployment: Any = None,    # Phase 2 (Q4): deployment boundary authority
) -> Callable[..., Any]:
    """Dispatch per governance tool; every judgment goes through the Kernel."""

    def _deny(name: str, decision: GateDecision) -> dict[str, Any]:
        payload = _deny_payload(name, rule_ids=decision.rule_ids,
                                evidence_refs=decision.evidence_refs,
                                reason=decision.reason,
                                recovery=decision.recovery)
        _emit_tool_blocked(event_log, scope, name, payload)
        return payload

    def _deny_raw(name: str, *, rule_ids: tuple[str, ...], reason: str,
                  recovery: str, evidence_refs: tuple[str, ...] = ()) -> dict:
        payload = _deny_payload(name, rule_ids=rule_ids,
                                evidence_refs=evidence_refs, reason=reason,
                                recovery=recovery)
        _emit_tool_blocked(event_log, scope, name, payload)
        return payload

    def _record(name: str, step_id: str, entry: EvidenceEntry) -> None:
        _record_evidence_file(scope=scope, evidence_index=evidence_index,
                              workspace_root=workspace_root,
                              harness_release=harness_release,
                              entry=entry, step_id=step_id)
        _emit_evidence_recorded(event_log, scope, entry)

    def _request() -> dict[str, Any]:
        return dict(scope.request or {})

    # --- chatbi_record_request -------------------------------------------
    def _record_request(name: str, func: Callable[..., Any],
                        args: Mapping[str, Any]) -> Any:
        request = args.get("request")
        payload_request = dict(request) if isinstance(request, Mapping) else _request()
        try:
            validate_request(payload_request)
        except GateError as error:
            return _deny(name, _clarify_request_decision(error.decision))
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="request",
            rule_ids=("REQ-001", "HOOK-001"), payload=payload_request,
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "request", entry)
        scope.request = payload_request
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "validated": True}
        return result

    def _request_deny(name: str) -> dict | None:
        """Analyze-scoped request-first precondition (REQ-001): evidence,
        candidate, review, crosscheck and query steps are reachable only
        after chatbi_record_request recorded the request. Deterministic
        flow-order enforcement — the model follows, the hook enforces
        (real-model live 2026-08-12: the model skipped record_request and
        jumped to chatbi_semantic_discover). Returns the deny payload when
        the request is missing, else None."""
        if scope.workflow_id != "chatbi-analyze":
            return None
        if isinstance(scope.request, Mapping) and scope.request:
            return None
        return _deny_raw(
            name, rule_ids=("REQ-001", "HOOK-004"),
            reason=("the analysis request must be recorded before any "
                    "evidence/review/query step (REQ-001)"),
            recovery=("Call chatbi_record_request first — fill the standard "
                      "defaults (actor=operator, purpose=decision_support, "
                      "supported_decision=analysis) and ask the user only "
                      "for the analysis window / ambiguous entity"))

    # --- chatbi_record_evidence -------------------------------------------
    def _record_evidence(name: str, func: Callable[..., Any],
                         args: Mapping[str, Any]) -> Any:
        tier = str(args.get("tier", ""))
        denied = _request_deny(name)
        if denied is not None:
            return denied
        content = args.get("content")
        if tier not in ("T1", "T2", "T3") or content is None:
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason=f"record_evidence requires tier T1|T2|T3 and content",
                recovery="Provide tier and content")
        # Tier-gap precondition (IR when): T2 needs a recorded T1 gap, T3 a
        # recorded T2 gap (C002/C003/C004 semantics).
        when_expr = _TIER_WHEN.get(tier)
        if when_expr and not evaluate_step_condition(
            when_expr, evidence_chain=tuple(scope.evidence_chain),
            request=_request(),
        ):
            return _deny_raw(
                name, rule_ids=("SEM-001", "HOOK-004"),
                reason=(
                    f"tier {tier} requires a recorded "
                    f"{'T1' if tier == 'T2' else 'T2'} gap; no gap evidence "
                    "was recorded (SEM-001)"
                ),
                recovery="Record the upper-tier gap evidence first, or stay "
                         "on the covered tier")
        payload: dict[str, Any] = (
            dict(content) if isinstance(content, Mapping)
            else {"content": content}
        )
        entry = EvidenceEntry.create(
            source_tier=tier, evidence_source=_TIER_SOURCE[tier],
            rule_ids=_TIER_RULE_IDS[tier], payload=payload,
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        scope.evidence_chain.append(entry.to_dict())
        step_id = {"T1": "t1_semantic", "T2": "t2_curated", "T3": "t3_raw"}[tier]
        _record(name, step_id, entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "evidence_source": _TIER_SOURCE[tier],
                    "content_sha256": entry.content_sha256, "recorded": True}
        return result

    # --- chatbi_submit_candidate ------------------------------------------
    def _submit_candidate(name: str, func: Callable[..., Any],
                          args: Mapping[str, Any]) -> Any:
        content = args.get("content")
        denied = _request_deny(name)
        if denied is not None:
            return denied
        if content is None:
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason="submit_candidate requires content",
                             recovery="Provide the final candidate content")
        try:
            sha = compute_candidate_sha(content)
        except (TypeError, ValueError) as error:
            return _deny_raw(name, rule_ids=("SEC-003", "HOOK-001"),
                             reason=f"candidate is not JSON-serializable: {error}",
                             recovery="Provide a JSON-serializable candidate")
        scope.candidate_sha = sha
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="candidate-bind",
            rule_ids=("REV-001",),
            payload={"candidate_sha": sha, "content": content},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "candidate_bind", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "candidate_sha": sha, "frozen": True}
        return result

    # --- chatbi_review -----------------------------------------------------
    def _review(name: str, func: Callable[..., Any],
                args: Mapping[str, Any]) -> Any:
        from .events import emit_standard_event
        denied = _request_deny(name)
        if denied is not None:
            return denied

        # B1 (design-runbook-completion): the run-level BLOCK ceiling is
        # enforced BEFORE the reviewer is invoked — the 4th review attempt
        # is denied at the tool edge (terminal, payload.terminal=true), the
        # reviewer is never called (zero model cost), and NO review.started /
        # review.completed is emitted (no review happened). The agent sees
        # the deny payload and the runbook tells it to stop this run and
        # hand off to the user (conversational handover).
        if _review_block_count(event_log, scope.run_id or "run") >= REVIEW_BLOCK_LIMIT:
            payload = _deny_payload(
                name, rule_ids=("REV-003", "HOOK-001"),
                reason=(f"review attempts exhausted: {REVIEW_BLOCK_LIMIT} "
                        "BLOCKED reviews in this run (REV-003)"),
                recovery=("Stop this run. Report the blocking findings with "
                          "their recovery actions to the user and wait for "
                          "user instructions; do not re-review in this run."))
            payload["terminal"] = True
            _emit_tool_blocked(event_log, scope, name, payload)
            return payload

        candidate_sha = str(args.get("candidate_sha") or scope.candidate_sha or "")
        emit_standard_event(
            event_log, run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-analyze",
            step_id=name, event_type="review.started",
            payload={"candidate_sha": candidate_sha},
        )
        def _fail(rule_ids: tuple[str, ...], reason: str,
                  recovery: str) -> dict[str, Any]:
            entry = EvidenceEntry.create(
                source_tier="T2", evidence_source="candidate-review",
                rule_ids=("REV-001", "REV-002", "REV-003"),
                payload={"status": "BLOCKED", "round": scope.review_round,
                         "candidate_sha": candidate_sha,
                         "findings": list(rule_ids), "reason": reason},
                runtime_name="agno", native_run_id=scope.run_id or "",
                harness_release=harness_release,
            )
            _record(name, "candidate_review", entry)
            emit_standard_event(
                event_log, run_id=scope.run_id or "run",
                session_id=scope.session_id or "session",
                workflow_id=scope.workflow_id or "chatbi-analyze",
                step_id=name, event_type="review.completed",
                payload={"status": "BLOCKED", "candidate_sha": candidate_sha,
                         "rule_ids": list(rule_ids)},
            )
            return _deny_raw(name, rule_ids=rule_ids, reason=reason,
                             recovery=recovery)

        try:
            result = func(**args)  # tool body invoked the reviewer runner
        except Exception as error:  # noqa: BLE001 - reviewer unavailable
            return _fail(_RULES_UNAVAILABLE,
                         f"reviewer unavailable: {type(error).__name__} "
                         f"(fail-closed, HOOK-004)",
                         "Restore the reviewer and re-review")
        verdict = (result or {}).get("verdict") if isinstance(result, Mapping) else None

        if not isinstance(verdict, Mapping):
            return _fail(_RULES_UNAVAILABLE,
                         "reviewer verdict is not a JSON object (fail-closed)",
                         "Correct the reviewer output and re-review")
        try:
            validate_review(verdict)
        except GateError as error:
            return _fail(_RULES_UNAVAILABLE,
                         f"review verdict violates review.schema.json: "
                         f"{error.decision.reason}",
                         "Correct the payload to match the declared schema")
        if verdict.get("candidate_sha") != candidate_sha:
            return _fail(
                _RULES_STALE_SHA,
                "Reviewer PASS is only valid for the exact candidate SHA "
                f"(verdict {verdict.get('candidate_sha')!r} != "
                f"{candidate_sha!r}); the candidate changed and must be "
                "re-reviewed (REV-001)",
                "Re-review the current candidate")
        round_no = int(verdict.get("round", 1) or 1)
        scope.review_round = max(scope.review_round, round_no)
        if round_no >= 4:
            return _fail(_RULES_ROUND,
                         "review round exceeded the limit (REV-003)",
                         "Do not keep re-reviewing indefinitely")
        findings = verdict.get("findings", []) or []
        blocking = [f for f in findings
                    if isinstance(f, Mapping) and f.get("severity") == "block"]
        if verdict.get("status") != "PASS" or blocking:
            return _fail(_RULES_NOT_PASS,
                         "Review verdict is not a clean PASS for the frozen "
                         "candidate",
                         "Address every blocking finding and re-review")
        # PASS: record the auditable review evidence.
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="candidate-review",
            rule_ids=("REV-001", "REV-002", "REV-003"),
            payload={"status": "PASS", "round": round_no,
                     "candidate_sha": candidate_sha, "findings": [],
                     "reason": "Independent reviewer PASS for the exact "
                               "candidate SHA"},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "candidate_review", entry)
        emit_standard_event(
            event_log, run_id=scope.run_id or "run",
            session_id=scope.session_id or "session",
            workflow_id=scope.workflow_id or "chatbi-analyze",
            step_id=name, event_type="review.completed",
            payload={"status": "PASS", "candidate_sha": candidate_sha,
                     "round": round_no},
        )
        if isinstance(result, Mapping):
            return {**dict(result), "review": {
                "status": "PASS", "round": round_no,
                "candidate_sha": candidate_sha,
                "reason": "Independent reviewer PASS"}}
        return result

    # --- chatbi_crosscheck -------------------------------------------------
    def _crosscheck(name: str, func: Callable[..., Any],
                    args: Mapping[str, Any]) -> Any:
        codebase = str(args.get("codebase") or "")
        denied = _request_deny(name)
        if denied is not None:
            return denied
        business = {}
        if config is not None:
            business = config.get("business_codebases") or {}
        if not business or not codebase:
            # Vacuously satisfied when no external Business Codebases are
            # configured (analyze command prose §Historical SQL).
            result = func(**args)
            if isinstance(result, Mapping):
                return {**dict(result), "crosscheck": {"vacuous": True}}
            return result
        selection = select_codebase_reader(config, alias=codebase)
        if selection.status == "stopped":
            return _deny(name, selection.stop_decision)
        try:
            if bool(args.get("search", False)):
                # Phase 2 (module E, design §8.2): search=True ->
                # literal-substring search over the aliased root
                # (root-contained; reader.search rejects escapes).
                evidence = selection.reader.search(
                    alias=codebase, pattern=str(args.get("query") or ""))
            else:
                evidence = selection.reader.read(
                    alias=codebase, target=str(args.get("query") or ""))
        except Exception as error:  # noqa: BLE001 - fail-closed (HOOK-004)
            return _deny_raw(
                name, rule_ids=("SRC-002", "HOOK-004"),
                reason=f"codebase cross-check failed: {type(error).__name__}",
                recovery="Resolve the codebase read error and re-run")
        # A blocked/error cross-check is RECORDED as evidence (NOT denied at
        # the tool edge): the SRC-002 route decision belongs to the
        # build-plan hook / delivery gate (classify_src002_finding routes
        # blocked evidence to route A — owner adjudication, E010 semantics).
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="codebase-crosscheck",
            rule_ids=("SRC-002",),
            payload=evidence.to_dict() if hasattr(evidence, "to_dict")
            else {"status": evidence.status},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        scope.evidence_chain.append(entry.to_dict())
        _record(name, "src002_crosscheck", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "crosscheck": {"status": evidence.status}}
        return result

    # --- chatbi_build_plan -------------------------------------------------
    def _bfr_ir_rules() -> tuple[str, ...]:
        wf = ir_workflows.get("chatbi-build-from-requirement")
        if wf is not None and getattr(wf, "gates", None) is not None:
            delivery = getattr(wf.gates, "delivery", None)
            if delivery is not None and getattr(delivery, "rule_ids", ()):
                return tuple(delivery.rule_ids)
        return ("SRC-002", "SEM-003", "REQ-001", "REQ-002")

    def _build_plan(name: str, func: Callable[..., Any],
                    args: Mapping[str, Any]) -> Any:
        from chatbi_governance.build_plan import BuildPlan, build_model_entry

        requirement = args.get("requirement")
        req = dict(requirement) if isinstance(requirement, Mapping) else _request()
        try:
            # SRC-002 route decision (E010: a blocked cross-check -> route A
            # -> owner adjudication; the delivery gate blocks with the IR
            # rule set).
            crosscheck = None
            for entry in reversed(scope.evidence_chain):
                if isinstance(entry, Mapping) and entry.get(
                    "evidence_source"
                ) == "codebase-crosscheck":
                    crosscheck = entry
                    break
            if crosscheck is not None:
                payload = crosscheck.get("payload") or {}
                if isinstance(payload, Mapping):
                    # CodebaseEvidence semantics: a blocked/error status, an
                    # error_category, or a nested block decision all mean the
                    # cross-check did NOT pass (route A, E010).
                    decision = None
                    nested = payload.get("payload")
                    if isinstance(nested, Mapping):
                        decision = (nested.get("data") or {}).get("decision")
                    blocked = (
                        payload.get("status") in ("blocked", "error")
                        or bool(payload.get("error_category"))
                        or (isinstance(decision, Mapping)
                            and decision.get("status") == "block")
                    )
                    if blocked:
                        return _deny_raw(
                            name, rule_ids=_bfr_ir_rules(),
                            reason=payload.get("reason")
                            or "SRC-002 cross-check blocked -> route A "
                               "(domain-owner adjudication, REQ-001/002)",
                            recovery="Ask the domain owner for the correct "
                                     "alias/path")
            entries = []
            for raw in (req.get("models") or []):
                if not isinstance(raw, Mapping):
                    continue
                entries.append(build_model_entry(
                    name=str(raw.get("name", "")),
                    layer=str(raw.get("layer", "dwd")),
                    change_kind=str(raw.get("change_kind", "create")),
                    created_rev=harness_release,
                    owner=str(raw.get("owner",
                                      req.get("actor", "operator"))),
                    upstream_deps=tuple(raw.get("upstream_deps", ()) or ()),
                    join_or_aggregate_summary=str(
                        raw.get("join_or_aggregate_summary", "")),
                ))
            plan = BuildPlan(
                schema_version=1,
                session_id=scope.session_id or "session",
                models=tuple(entries),
            )
            validate_build_plan(plan, layer_rules=(), known_models=frozenset())
            validate_layer_dependency(plan, layer_rules=())
        except GateError as error:
            return _deny(name, error.decision)
        except Exception as error:  # noqa: BLE001 - fail-closed (HOOK-004)
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason=f"build plan derivation failed: "
                                    f"{type(error).__name__}",
                             recovery="Correct the requirement and re-run")
        plan_entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="build-plan",
            rule_ids=_bfr_ir_rules(),
            payload={"models": [entry.to_dict() for entry in entries],
                     "status": "pass"},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "build_plan", plan_entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "build_plan": {
                "models": [entry.to_dict() for entry in entries]}}
        return result

    # --- chatbi_impact_manifest --------------------------------------------
    def _impact_manifest(name: str, func: Callable[..., Any],
                         args: Mapping[str, Any]) -> Any:
        request = _request()
        entry_raw = args.get("model_entry")
        if isinstance(entry_raw, Mapping):
            request = {**request, **dict(entry_raw)}
        assets = []
        for spec in request.get("affected_assets", []) or []:
            if isinstance(spec, Mapping):
                assets.append(AffectedAsset(
                    asset_kind=str(spec.get("asset_kind", "")),
                    asset_ref=str(spec.get("asset_ref", "")),
                    change_required=bool(spec.get("change_required", False)),
                    synced=bool(spec.get("synced", False)),
                ))
        try:
            manifest = build_impact_manifest(
                run_id=scope.run_id or "run",
                change_kind=str(request.get("change_kind", "")),
                target=str(request.get("target", "")),
                affected_assets=assets,
                evidence_state=str(request.get("evidence_state", "")),
                p0_eval_failed=bool(request.get("p0_eval_failed", False)),
                protected_action=bool(request.get("protected", False)),
                candidate_payload=request.get("candidate_payload"),
                created_rev=harness_release,
            )
        except GateError as error:
            return _deny(name, error.decision)
        if manifest.has_blocking_drift():
            return _deny_raw(
                name, rule_ids=("DOC-004",),
                reason="DOC-004 full-sync gate not passed: " + "; ".join(
                    list(manifest.blocking_reasons())[:3]),
                recovery="Sync every affected asset with sufficient evidence "
                         "and no P0 evaluation failure")
        scope.impact = manifest.to_dict()
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="impact-manifest",
            rule_ids=("DOC-004",),
            payload={"status": "pass", "target": str(request.get("target", ""))},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "impact_manifest", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "impact": manifest.to_dict()}
        return result

    # --- chatbi_registry_append --------------------------------------------
    def _registry_append(name: str, func: Callable[..., Any],
                         args: Mapping[str, Any]) -> Any:
        from chatbi_governance.policy import PolicyDecision

        request = _request()
        entry_raw = args.get("entry")
        if isinstance(entry_raw, Mapping):
            request = {**request, **dict(entry_raw)}
        # DOC-004 sync gate: only after a passing impact manifest.
        impact = getattr(scope, "impact", None) or {}
        if impact and impact.get("has_blocking_drift"):
            return _deny_raw(
                name, rule_ids=("DOC-004",),
                reason="DOC-004 sync gate not passed; a failed-sync model is "
                       "not recorded (fail-closed)",
                recovery="Sync every affected asset and re-run")
        try:
            entry = build_model_entry(
                name=str(request.get("target", "")),
                layer=str(request.get("layer", "dwd")),
                change_kind=str(request.get("change_kind", "create")),
                created_rev=harness_release,
                owner=str(request.get("actor", "operator")),
                upstream_deps=tuple(request.get("upstream_deps", ()) or ()),
                join_or_aggregate_summary=str(request.get("summary", "")),
            )
            registry_path = workspace_root / ".chatbi" / "model_registry.json"
            append_model_registry(registry_path, entry)
        except GateError as error:
            return _deny(name, error.decision)
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="model-registry",
            rule_ids=("DOC-004", "SEM-003"),
            payload={"appended": True,
                     "name": str(request.get("target", ""))},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "registry_append", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "registry_appended": True}
        return result

    # --- chatbi_lint_reference ---------------------------------------------
    def _lint_reference(name: str, func: Callable[..., Any],
                        args: Mapping[str, Any]) -> Any:
        ref = str(args.get("ref", ""))
        issues = lint_reference(ref)
        if issues:
            return _deny_raw(
                name, rule_ids=("DOC-002", "DOC-003"),
                reason="reference lint found issues: " + "; ".join(
                    f"{i.field}: {i.message}" for i in issues[:3]),
                recovery="Resolve the lint issues via the governed reference "
                         "authoring flow")
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="knowledge-lint",
            rule_ids=("DOC-002", "DOC-003"),
            payload={"ready": True, "issue_count": 0},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "lint", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "lint": {"ready": True}}
        return result

    # --- chatbi_evaluate ---------------------------------------------------
    def _evaluate(name: str, func: Callable[..., Any],
                  args: Mapping[str, Any]) -> Any:
        from chatbi_governance.evaluator import (
            GroundTruthVault,
            build_evaluation_run,
            validate_evaluation,
        )

        request = _request()
        suite = args.get("suite_request")
        if isinstance(suite, Mapping):
            request = {**request, **dict(suite)}
        answers = request.get("answers")
        if not answers:
            return _deny_raw(name, rule_ids=("EVAL-001", "HOOK-004"),
                             reason="evaluation requires isolated ground-truth "
                                    "answers",
                             recovery="Provide the owner-isolated answers")
        try:
            vault = GroundTruthVault(dict(answers))
            actuals = {}
            if native_runner is not None:
                native = native_runner("chatbi-evaluate", "run_suite",
                                       dict(request))
                if isinstance(native, Mapping):
                    actuals = native.get("actuals") or {}
            run = build_evaluation_run(
                run_id=scope.run_id or "run",
                skill_version=str(request.get("skill_version",
                                              "chatbi-evaluation@1")),
                model_id=str(request.get("model_id", "")),
                vault=vault,
                actuals=actuals,
                tokens=int(request.get("tokens", 0)),
                latency_ms=int(request.get("latency_ms", 0)),
                seen=bool(request.get("seen", True)),
                threshold_owner_confirmed=bool(
                    request.get("threshold_owner_confirmed", False)),
                release=bool(request.get("release", False)),
                release_threshold=(
                    float(request["release_threshold"])
                    if request.get("release_threshold") is not None else None
                ),
                content_payload=request.get("content_payload", {}),
            )
            validate_evaluation(run.to_dict())  # EVAL-004 fail-closed
        except GateError as error:
            return _deny(name, error.decision)
        run_dict = run.to_dict()
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="evaluation-run",
            rule_ids=("EVAL-003", "EVAL-004", "FBK-003"),
            payload={"passed": run.passed_count,
                     "total": run_dict.get("total_count", len(run.assertions)),
                     "all_passed": run.all_passed,
                     "release": bool(request.get("release", False)),
                     "fbk_003_statement": run_dict.get("fbk_003_statement", "")},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "evaluation", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "evaluation": {
                "passed": run.passed_count,
                "total": run_dict.get("total_count", len(run.assertions)),
                "all_passed": run.all_passed,
                "fbk_003_statement": run_dict.get("fbk_003_statement", "")}}
        return result

    # --- chatbi_correction -------------------------------------------------
    def _correction(name: str, func: Callable[..., Any],
                    args: Mapping[str, Any]) -> Any:
        from chatbi_governance.evaluator import (
            build_correction_record,
            validate_correction,
        )

        request = _request()
        corr = args.get("correction")
        if isinstance(corr, Mapping):
            request = {**request, **dict(corr)}
        action = str(request.get("action_type", "approve_metric"))
        actor = str(request.get("actor", "operator"))
        if config is not None:
            decision = decide(
                config,
                PolicyRequest(request_type=action, target_entity="",
                              actor=actor, purpose="governed protected action"),
            )
            if decision.status == "block":
                return _deny_raw(name, rule_ids=("SEM-003",),
                                 reason=decision.reason,
                                 recovery="Wait for the human owner to approve "
                                          "the protected action")
        try:
            record = build_correction_record(
                correction_id=str(request.get("correction_id", "")),
                fix_kind=str(request.get("fix_kind", "")),
                fix_target=str(request.get("fix_target", "")),
                fix_change_summary=str(request.get("fix_change_summary", "")),
                eval_case_assertion_id=str(
                    request.get("eval_case_assertion_id", "")),
                eval_case_expected_hash=str(
                    request.get("eval_case_expected_hash", "")),
                rule_ids=tuple(request.get("rule_ids",
                                           ("FBK-001", "FBK-002"))),
                owner_approved=False,
                description=str(request.get("description", "")),
            )
            validate_correction(record)
        except GateError as error:
            return _deny(name, error.decision)
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="correction-record",
            rule_ids=("FBK-001", "FBK-002", "FBK-003", "ABL-001"),
            payload={"correction_id": str(request.get("correction_id", "")),
                     "validated": True,
                     "owner_approved": False},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "correction", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "correction_validated": True}
        return result

    # --- chatbi_drift_report -----------------------------------------------
    def _drift_report(name: str, func: Callable[..., Any],
                      args: Mapping[str, Any]) -> Any:
        request = _request()
        fresh = args.get("fresh_inventory")
        if isinstance(fresh, Mapping):
            request = {**request, "fresh_inventory": dict(fresh)}
        scope_name = str(request.get("scope", "all"))
        if scope_name not in ("references", "sources", "models", "all"):
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason=f"unknown drift scope: {scope_name!r}",
                             recovery="Use one of references|sources|models|all")
        try:
            fresh_obj = None
            raw_fresh = request.get("fresh_inventory")
            if isinstance(raw_fresh, Mapping):
                fresh_obj = SourceInventory(
                    source_database=str(raw_fresh.get("source_database", "")),
                    tables=tuple(
                        SourceTable(
                            name=str(t.get("name", "")),
                            columns=tuple(
                                SourceColumn(
                                    name=str(c.get("name", "")),
                                    data_type=str(c.get("data_type", "")),
                                    is_primary_key=bool(
                                        c.get("is_primary_key", False)),
                                )
                                for c in t.get("columns", [])
                            ),
                        )
                        for t in raw_fresh.get("tables", [])
                    ),
                )
            report = detect_drift(
                workspace_root, config, scope=scope_name,
                since=request.get("since"),
                fresh_source_inventory=fresh_obj,
            )
        except GateError as error:
            # Missing baseline -> hard STOP (class-2 precondition).
            return _deny(name, error.decision)
        candidates = [
            c for class_candidates in report.classes.values()
            for c in class_candidates
        ]
        routes = []
        triage = False
        for candidate in candidates:
            decision = classify_finding(candidate)
            routes.append(decision.to_dict())
            if decision.target_command in ("owner", "STOP human triage"):
                triage = True
        persisted = write_state(
            workspace_root, _safe_session_id(scope.session_id or "drift"),
            "drift_report.json", report.to_dict(),
        )
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="drift-report",
            rule_ids=("HOOK-001", "PORT-001"),
            payload={"status": report.status, "scope": report.scope,
                     "candidate_count": len(candidates),
                     "routes": routes, "triage": triage},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "audit_drift", entry)
        result = func(**args)
        if isinstance(result, Mapping):
            return {**dict(result), "drift": {
                "status": report.status, "candidate_count": len(candidates),
                "routes": routes, "triage": triage,
                "persisted": str(persisted)}}
        return result

    # --- chatbi_init_diagnostic --------------------------------------------
    def _init_diagnostic(name: str, func: Callable[..., Any],
                         args: Mapping[str, Any]) -> Any:
        from .probe import probe_agno

        request = _request()
        payload = args.get("request")
        if isinstance(payload, Mapping):
            request = {**request, **dict(payload)}
        shared = request.get("shared_config") or request.get(
            "shared_config_path")
        if not shared:
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason="init requires a shared configuration path",
                             recovery="Provide a workspace-relative "
                                      "shared_config in the run request")
        shared_rel = Path(str(shared))
        shared_abs = (shared_rel if shared_rel.is_absolute()
                      else workspace_root / shared_rel)
        local = request.get("local_config") or request.get("local_config_path")
        local_abs = None
        if local:
            local_rel = Path(str(local))
            local_abs = (local_rel if local_rel.is_absolute()
                         else workspace_root / local_rel)
        manifest = probe_agno()
        runtime_ok = manifest.runtime_version != "unavailable"
        try:
            result = run_init_diagnostic(
                shared_abs, local_abs,
                probe=(lambda: _agno_capability_snapshot(runtime_ok)),
                workspace_root=workspace_root,
            )
        except Exception as error:  # noqa: BLE001 - fail-closed (HOOK-004)
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason=f"init diagnostic failed: "
                                    f"{type(error).__name__}",
                             recovery="Inspect the diagnostic chain and re-run")
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="init-diagnostic",
            rule_ids=("PORT-001", "SEC-003", "HOOK-004"),
            payload={"status": result.status,
                     "production_ready": result.production_ready,
                     "recovery_actions": list(result.recovery_actions)},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "init_diagnostic", entry)
        out = func(**args)
        if isinstance(out, Mapping):
            return {**dict(out), "diagnostic": {
                "status": result.status,
                "production_ready": result.production_ready,
                "checks": [check.to_dict() for check in result.checks],
                "recovery_actions": list(result.recovery_actions)}}
        return out

    # --- chatbi_bootstrap --------------------------------------------------
    def _bootstrap(name: str, func: Callable[..., Any],
                   args: Mapping[str, Any]) -> Any:
        request = _request()
        spec_raw = args.get("spec")
        if isinstance(spec_raw, Mapping):
            request = {**request, **dict(spec_raw)}
        try:
            spec = build_mysql_adapter_spec(
                str(request.get("host", "")),
                int(request.get("port", 0)),
                str(request.get("user", "")),
                database=str(request.get("database", "")),
                credential_env_name=request.get("credential_env_name"),
            )
            # Merge base (live-found, 2026-08-12): the DEPLOYMENT-BOUNDARY
            # local config file is the authority when the request does not
            # carry the existing local_config — a governed bootstrap must
            # PRESERVE pre-declared path_bindings (e.g. the fypro Business
            # Codebase root) instead of wiping them (PORT-001: machine paths
            # live only in this file). SEC-003: the file holds path bindings
            # + credential env-var NAMES, never secret values.
            request_local = request.get("local_config")
            if not isinstance(request_local, dict):
                request_local = None
                local_source = workspace_root / ".claude" \
                    / "chatbi-harness.local.json"
                try:
                    if local_source.is_file():
                        existing = json.loads(
                            local_source.read_text(encoding="utf-8"))
                        if isinstance(existing, dict):
                            request_local = existing
                except (OSError, ValueError):
                    request_local = None
            merged = merge_local_config(
                request_local,
                path_bindings=request.get("path_bindings"),
                cli_adapters=request.get("cli_adapters"),
            )
            # Phase 2 (module F, technical-design-agno-phase2 §4.1 "bootstrap
            # 写正式 argv"): persist the merged local config with the formal
            # mysql adapter spec (argv + credential env NAMES only, no
            # values — SEC-003) into the deployment-boundary local config.
            # query_source reads it fresh, so a long-running server sees the
            # adapter without restart.
            cli_adapters_merged = merged.get("cli_adapters") or {}
            cli_adapters_merged["mysql"] = dict(spec)
            merged["cli_adapters"] = cli_adapters_merged
            # Semantic-layer docs dir (conversation-configurable, agno 验收
            # 2026-08-12): when the user specifies where the semantic-layer
            # docs live during initialization, persist it in the local
            # config — semantic_discover reads it fresh (same pattern as
            # query_source reading the mysql adapter).
            if request.get("semantic_docs"):
                merged["semantic_docs_dir"] = str(request["semantic_docs"])
            local_target = workspace_root / ".claude" \
                / "chatbi-harness.local.json"
            try:
                local_target.parent.mkdir(parents=True, exist_ok=True)
                tmp_local = local_target.with_name(local_target.name + ".tmp")
                tmp_local.write_text(json.dumps(
                    merged, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8")
                os.replace(tmp_local, local_target)
            except OSError as error:
                return _deny_raw(
                    name, rule_ids=("HOOK-004",),
                    reason=f"local config persist failed: "
                           f"{type(error).__name__} (fail-closed)",
                    recovery="Check the .claude directory permissions and "
                             "re-run the bootstrap chain")
            # Phase 2 (Q4, technical-design-agno-phase2 §3.3): the
            # executable allowlist comes from the DEPLOYMENT config
            # (deployment authority — the request body no longer carries
            # it). Compatibility bridge: only when NO deployment config
            # exists (config_path None, e.g. the conformance E002 scenario
            # scripted with a temp mysql executable) the request-body
            # allowlist is honored; a configured deployment NEVER falls back
            # to the request body (fail-closed).
            allowlist = tuple(getattr(deployment, "cli_allowlist", ()) or ())
            if not allowlist and deployment is not None \
                    and getattr(deployment, "config_path", None) is None:
                allowlist = tuple(request.get("cli_allowlist", []) or ())
            exe = resolve_executable("mysql", allowlist)
            if exe is None:
                return _deny_raw(
                    name, rule_ids=("SEC-001", "PORT-001"),
                    reason="resolve_executable failed (fail-closed)",
                    recovery="Confirm the mysql executable on the "
                             "operator allowlist (deployment "
                             "cli_allowlist)")
            if native_runner is None:
                return _deny_raw(
                    name, rule_ids=("HOOK-004",),
                    reason="bootstrap requires a wired native runner "
                           "(fail-closed, FBK-003)",
                    recovery="Wire the runtime native runner before "
                             "bootstrapping")
            native = native_runner("chatbi-bootstrap", "run_mysql",
                                   {**request, "spec": spec,
                                    "local_config": merged,
                                    "executable": str(exe)})
            if isinstance(native, Mapping) and native.get("status") == "error":
                return _deny_raw(
                    name, rule_ids=("HOOK-004",),
                    reason="mysql run failed: "
                           f"{native.get('error_category', 'unknown')}",
                    recovery="Inspect the mysql CLI error and re-run the "
                             "bootstrap chain")
            inventory_path = (native or {}).get("inventory_path") if isinstance(
                native, Mapping) else None
            if not inventory_path:
                return _deny_raw(name, rule_ids=("HOOK-004",),
                                 reason="mysql introspection produced no "
                                        "source inventory",
                                 recovery="Re-run the mysql introspection")
            inventory = read_source_inventory(Path(str(inventory_path)))
            # Phase 2 (module F, technical-design-agno-phase2 §3.3): the
            # bootstrap chain continues with the workspace scaffold
            # (dbt_project.yml + models/{ods,dwd,dws,ads} + blueprint stub).
            # A scaffold failure DENIES the bootstrap (never silent).
            scaffold = native_runner("chatbi-bootstrap", "scaffold", {})
            if not (isinstance(scaffold, Mapping)
                    and scaffold.get("status") == "ok"):
                return _deny_raw(name, rule_ids=("HOOK-004",),
                                 reason="workspace scaffold failed "
                                        "(fail-closed)",
                                 recovery="Inspect the scaffold step and "
                                          "re-run the bootstrap chain")
        except GateError as error:
            return _deny(name, error.decision)
        except Exception as error:  # noqa: BLE001 - fail-closed
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason=f"bootstrap failed: {type(error).__name__}",
                             recovery="Inspect the bootstrap chain and re-run")
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="bootstrap-inventory",
            rule_ids=("PORT-001", "SEC-003", "SEM-003"),
            payload={"source_database": inventory.source_database,
                     "table_count": len(inventory.tables)},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        _record(name, "bootstrap", entry)
        out = func(**args)
        if isinstance(out, Mapping):
            return {**dict(out), "bootstrap": {
                "status": "planned",
                "source_db": str(request.get("database", "")),
                "table_count": len(inventory.tables)}}
        return out

    # --- chatbi_query_source (Phase 2, module A) ---------------------------
    def _mysql_adapter_spec() -> dict | None:
        """cli_adapters.mysql spec: effective config first, then a fresh
        read of the local config file (the bootstrap writes the formal argv;
        a long-running server sees it without restart). None = not
        configured (fail-closed deny)."""
        try:
            if config is not None:
                adapters = config.get("cli_adapters") or {}
                if isinstance(adapters, Mapping):
                    spec = adapters.get("mysql")
                    if isinstance(spec, Mapping) and spec.get("argv"):
                        return dict(spec)
        except Exception:  # noqa: BLE001 - fall through to the file read
            pass
        local = workspace_root / ".claude" / "chatbi-harness.local.json"
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
            spec = (data.get("cli_adapters") or {}).get("mysql")
            if isinstance(spec, Mapping) and spec.get("argv"):
                return dict(spec)
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _query_source(name: str, func: Callable[..., Any],
                      args: Mapping[str, Any]) -> Any:
        denied = _request_deny(name)
        if denied is not None:
            return denied
        import hashlib

        statement = args.get("statement")
        tier = args.get("tier") or "T2"
        if not isinstance(statement, str) or not statement.strip():
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason="query_source requires a non-empty SQL statement",
                recovery="Provide the read-only SELECT statement")
        if tier not in ("T2", "T3"):
            return _deny_raw(
                name, rule_ids=("SEM-001", "HOOK-004"),
                reason=f"tier {tier!r} is not supported for direct queries; "
                       "T1 queries come only from the semantic layer "
                       "(SEM-001)",
                recovery="Use tier T2 (curated) or T3 (raw exploration)")
        invalid = validate_readonly_select(statement)
        if invalid is not None:
            return _deny_raw(
                name, rule_ids=("SEC-001", "HOOK-004"),
                reason=f"SQL statement rejected: {invalid} (read-only "
                       "SELECT whitelist, SEC-001)",
                recovery="Send a single read-only SELECT without comments, "
                         "multi-statements or forbidden keywords")
        refs = resolve_table_refs(statement)
        if not refs:
            return _deny_raw(
                name, rule_ids=("SEC-001", "HOOK-004"),
                reason="no table reference detected in the statement",
                recovery="Reference a governed table explicitly (FROM/JOIN)")
        models, public, source_db = _load_query_allowlists(workspace_root)
        warehouse_db = str(getattr(deployment, "warehouse_db",
                                   "dw_agno")).lower()
        union = models | public
        allowed_surface = ", ".join(sorted(union)[:20])
        for ref in refs:
            parts = ref.split(".", 1)
            if len(parts) == 1:
                ok = ref in union
            else:
                db, table = parts
                ok = (
                    (db == warehouse_db and table in models)
                    or (bool(source_db) and db == source_db and table in public)
                )
            if not ok:
                return _deny_raw(
                    name, rule_ids=("SEM-001", "SEC-001"),
                    reason=f"table {ref!r} is not on the governed query "
                           "allowlist (SEM-001/SEC-001)",
                    recovery="Query only dw_agno models or inventoried "
                             "source tables; allowed surface (sample): "
                             + (allowed_surface or "(none — run "
                               "chatbi-bootstrap first)"))
        # Tier-gap precondition (IR when): T2 needs a recorded T1 gap, T3 a
        # recorded T2 gap (SEM-001 degradation semantics, same as
        # chatbi_record_evidence).
        when_expr = _TIER_WHEN.get(tier)
        if when_expr and not evaluate_step_condition(
            when_expr, evidence_chain=tuple(scope.evidence_chain),
            request=_request(),
        ):
            return _deny_raw(
                name, rule_ids=("SEM-001", "HOOK-004"),
                reason=f"tier {tier} requires a recorded "
                       f"{'T1' if tier == 'T2' else 'T2'} gap; no gap "
                       "evidence was recorded (SEM-001)",
                recovery="Record the upper-tier gap evidence first "
                         "(chatbi_semantic_discover / "
                         "chatbi_record_evidence)")
        spec = _mysql_adapter_spec()
        if spec is None:
            return _deny_raw(
                name, rule_ids=("PORT-001", "SEC-001"),
                reason="cli_adapters.mysql is not configured (deployment "
                       "boundary, PORT-001)",
                recovery="Run chatbi-bootstrap to write the mysql adapter "
                         "argv, or configure cli_adapters.mysql in the "
                         "local config")
        exe = resolve_executable(
            "mysql", tuple(getattr(deployment, "cli_allowlist", ()) or ()))
        if exe is None:
            return _deny_raw(
                name, rule_ids=("PORT-001", "SEC-001"),
                reason="mysql executable is not on the deployment "
                       "cli_allowlist (fail-closed)",
                recovery="Approve the mysql executable realpath in the "
                         "deployment config cli_allowlist")
        if native_runner is None or not hasattr(native_runner,
                                                "run_mysql_query"):
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason="query_source requires a wired native runner "
                       "(fail-closed, FBK-003)",
                recovery="Wire the runtime native runner before querying")
        result = native_runner.run_mysql_query(
            statement=statement, spec=spec, executable=exe)
        if not isinstance(result, Mapping) or result.get("status") != "ok":
            category = (result or {}).get("error_category", "unknown") \
                if isinstance(result, Mapping) else "unknown"
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason=f"mysql query failed: {category}",
                recovery="Inspect the mysql CLI error and re-run with a "
                         "corrected statement")
        rows = result.get("rows") or []
        row_count = int(result.get("row_count", len(rows)) or 0)
        truncated = bool(result.get("truncated", False))
        sha = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        # Evidence (t2_curated / t3_raw step ids — inside the delivery gate
        # _tier_chain source set, zero gate changes): the statement text is
        # NEVER in the payload (SEC-003); rows are capped at 100.
        entry = EvidenceEntry.create(
            source_tier=tier,
            evidence_source=("curated-reference" if tier == "T2"
                             else "raw-exploration"),
            rule_ids=_TIER_RULE_IDS[tier] + ("PORT-001",),
            payload={"statement_sha256": sha, "row_count": row_count,
                     "truncated": truncated, "rows": rows[:100]},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        scope.evidence_chain.append(entry.to_dict())
        _record(name, "t2_curated" if tier == "T2" else "t3_raw", entry)
        out = func(**args)
        if isinstance(out, Mapping):
            return {**dict(out), "query": {
                "status": "ok", "row_count": row_count,
                "truncated": truncated, "rows": rows[:200],
                "untrusted": bool(result.get("untrusted", True)),
                "statement_sha256": sha}}
        return out

    # --- chatbi_dbt_draft / chatbi_dbt_execute (Phase 2, module B) ---------
    def _dbt_draft(name: str, func: Callable[..., Any],
                   args: Mapping[str, Any]) -> Any:
        relative_path = args.get("relative_path")
        content = args.get("content")
        if not isinstance(relative_path, str) or not relative_path:
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason="dbt_draft requires a non-empty relative_path",
                recovery="Provide a workspace-relative model path under "
                         "models/")
        if not isinstance(content, str):
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason="dbt_draft requires string content",
                recovery="Provide the model file content as a string")
        rel = Path(relative_path)
        # Absolute path: double insurance (the realpath hook already
        # rejected it, PORT-001).
        if rel.is_absolute() or any(part == ".." for part in rel.parts):
            return _deny_raw(
                name, rule_ids=("SEC-001", "PORT-001"),
                reason="draft path must be workspace-relative with no .. "
                       "traversal (PORT-001)",
                recovery="Use a models/** relative path")
        if rel.suffix not in (".sql", ".yml"):
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason=f"draft suffix {rel.suffix!r} is not allowed "
                       "(only .sql model files and .yml schema files)",
                recovery="Draft a .sql model or a .yml schema definition")
        if len(content.encode("utf-8")) > 256 * 1024:
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason="draft content exceeds 256 KiB",
                recovery="Split the model file below 256 KiB")
        candidate = workspace_root / rel
        try:
            if candidate.exists():
                resolved = candidate.resolve(strict=True)
            else:
                resolved = candidate.parent.resolve(strict=True) / candidate.name
            models_root = (workspace_root / "models").resolve()
            resolved.relative_to(models_root)
        except (OSError, ValueError):
            return _deny_raw(
                name, rule_ids=("SEC-001", "PORT-001"),
                reason="draft path escapes the ws/models/ root "
                       "(fail-closed)",
                recovery="Use a models/** relative path")
        sanitized = _sanitize_text(content)
        # Atomic write (same-dir temp + os.replace) — the file is written
        # ONLY through this governed tool (Q5: no bare Write surface).
        candidate.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = candidate.with_name(candidate.name + ".tmp")
        try:
            tmp_path.write_text(sanitized, encoding="utf-8")
            os.replace(tmp_path, candidate)
        except OSError as error:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason=f"draft write failed: {type(error).__name__}",
                recovery="Check the models/ directory permissions and "
                         "re-draft")
        sha = compute_candidate_sha(sanitized)
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="model-candidate",
            rule_ids=("REV-001", "DOC-004"),
            payload={"relative_path": str(rel), "content_sha256": sha},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        # model-candidate evidence is NOT appended to the analyze tier chain
        # (the delivery gate reads only the 4 tier sources); the generic
        # workflows (maintain-model / bfr) see it via _run_evidence_sources.
        _record(name, "dbt_draft", entry)
        out = func(**args)
        if isinstance(out, Mapping):
            return {**dict(out), "drafted": True,
                    "relative_path": str(rel), "content_sha256": sha}
        return out

    def _dbt_execute(name: str, func: Callable[..., Any],
                     args: Mapping[str, Any]) -> Any:
        operation = args.get("operation")
        select = args.get("select")
        if operation not in ("run", "test"):
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason=f"dbt operation {operation!r} is not supported "
                       "(run|test)",
                recovery="Use operation run or test")
        if not isinstance(select, str) or not re.fullmatch(
                r"[a-z0-9_]+(,[a-z0-9_]+)*", select):
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason="dbt select must match ^[a-z0-9_]+(,[a-z0-9_]+)*$ "
                       "(no +/*/path/metacharacters, argv injection "
                       "surface)",
                recovery="Select one or more model names, comma-separated")
        names = [n for n in select.split(",") if n]
        models_root = workspace_root / "models"
        hit_files: list[Path] = []
        if models_root.is_dir():
            for model_name in names:
                hit_files.extend(models_root.rglob(f"{model_name}.sql"))
        if not hit_files:
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason="select does not hit any ws/models/**/<name>.sql "
                       "model file",
                recovery="Draft the model first (chatbi_dbt_draft) or "
                         "correct the select list")
        # dbt_bin resolution (Q4): deployment boundary only.
        dbt_bin = str(getattr(deployment, "dbt_bin", "") or "")
        if not dbt_bin:
            return _deny_raw(
                name, rule_ids=("PORT-001", "SEC-001"),
                reason="deployment dbt_bin is not configured (fail-closed)",
                recovery="Configure dbt_bin in the deployment config")
        allowlist = tuple(getattr(deployment, "cli_allowlist", ()) or ())
        resolved_dbt = resolve_executable(dbt_bin, allowlist)
        if resolved_dbt is None:
            return _deny_raw(
                name, rule_ids=("PORT-001", "SEC-001"),
                reason="deployment dbt_bin does not resolve to an "
                       "allowlisted executable (fail-closed)",
                recovery="Approve the dbt executable realpath in the "
                         "deployment cli_allowlist")
        # Review prerequisite (Q6, REV-001): every hit model file's content
        # sha must be among this run's review.completed(PASS) candidate
        # shas — deterministic binding (the draft -> review -> execute chain
        # is the ONLY execution path; no --force channel).
        reviewed = _reviewed_candidate_shas(event_log, scope.run_id or "run")
        for hit in hit_files:
            file_sha = compute_candidate_sha(hit.read_text(encoding="utf-8"))
            if file_sha not in reviewed:
                return _deny_raw(
                    name, rule_ids=("REV-001",),
                    reason=f"model {hit.name} has no review PASS for its "
                           "exact content (deterministic binding, REV-001)",
                    recovery="Use chatbi_dbt_draft to submit the candidate "
                             "and complete a chatbi_review PASS before "
                             "executing")
        if native_runner is None:
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason="dbt execute requires a wired native runner "
                       "(fail-closed, FBK-003)",
                recovery="Wire the runtime native runner before executing")
        profiles_dir = str(getattr(deployment, "dbt_profiles_dir", "") or "")
        result = native_runner(
            "chatbi-maintain-model", "dbt_run" if operation == "run"
            else "dbt_test",
            {"operation": operation, "select": select,
             "profiles_dir": profiles_dir, "dbt_bin": dbt_bin})
        if not isinstance(result, Mapping) or result.get("status") != "ok":
            category = (result or {}).get("error_category", "unknown") \
                if isinstance(result, Mapping) else "unknown"
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason=f"dbt execution failed: {category}",
                recovery="Read the dbt log and correct the model or "
                         "environment")
        log_tail = str(result.get("log_tail", "") or "")[:_LOG_TAIL_CAP]
        entry = EvidenceEntry.create(
            source_tier="T2", evidence_source="dbt-run",
            rule_ids=("REV-001", "PORT-001"),
            payload={"operation": operation, "select": select,
                     "returncode": int(result.get("returncode", 0)),
                     "log_tail": log_tail},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        # dbt-run evidence is not in the analyze tier chain (delivery gate
        # reads 4 tier sources); generic workflows see it via
        # _run_evidence_sources.
        _record(name, "dbt_run" if operation == "run" else "dbt_test", entry)
        out = func(**args)
        if isinstance(out, Mapping):
            return {**dict(out), "dbt": {"returncode": 0,
                                         "log_tail": log_tail}}
        return out

    # --- chatbi_semantic_discover (Phase 2, module D) ----------------------
    def _semantic_discover(name: str, func: Callable[..., Any],
                           args: Mapping[str, Any]) -> Any:
        denied = _request_deny(name)
        if denied is not None:
            return denied
        import hashlib

        metric = args.get("metric")
        if metric is None:
            metric = ""
        metric = str(metric)
        fixture_enabled = False
        if config is not None:
            try:
                adapters = config.get("adapters") or {}
                fixture_enabled = bool(
                    (adapters.get("fixture_enabled") or False))
            except Exception:  # noqa: BLE001 - fail-closed: no fixture
                fixture_enabled = False
        run_mode = str(getattr(deployment, "run_mode", "production") or "")
        fixture_mode = run_mode in ("test", "example") and fixture_enabled

        def _doc_meta(relative_path: str, content: str) -> dict[str, Any]:
            return {
                "relative_path": relative_path,
                "content_sha256": compute_candidate_sha(content),
                "byte_length": len(content.encode("utf-8")),
                "truncated": len(content.encode("utf-8")) > 256 * 1024,
            }

        docs: list[dict[str, Any]] = []
        rejected_paths: list[str] = []
        if fixture_mode:
            # Fixture fallback (test/example AND fixture_enabled): the
            # runtime-relative catalog (PORT-001, same source as CC). An
            # unresolvable/malformed catalog FAILS CLOSED (PORT-001) — never
            # a silent mix with the workspace scan.
            catalog_path = _fixture_catalog_path()
            if catalog_path is None:
                return _deny_raw(
                    name, rule_ids=("PORT-001", "HOOK-004"),
                    reason="semantic fixture catalog is unresolvable while "
                           "fixture mode is active (fail-closed, PORT-001)",
                    recovery="Restore the fixture catalog or disable "
                             "fixture mode")
            try:
                catalog = json.loads(
                    catalog_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                return _deny_raw(
                    name, rule_ids=("PORT-001", "HOOK-004"),
                    reason=f"semantic fixture catalog is malformed: "
                           f"{type(error).__name__} (fail-closed)",
                    recovery="Restore the fixture catalog or disable "
                             "fixture mode")
            for entry in (catalog.get("metrics") or []):
                if not isinstance(entry, Mapping):
                    continue
                entry_metric = str(entry.get("name", ""))
                entry_id = str(entry.get("id", ""))
                if metric and metric.lower() not in (
                        entry_metric.lower() + " " + entry_id.lower()):
                    continue
                description = str(entry.get("description", ""))
                docs.append({**_doc_meta(
                    f"fixture:{entry_id}", description),
                    "content": description,
                    "metric": entry_metric})
        else:
            # Semantic docs dir resolution (agno 验收 2026-08-12): local
            # config override (written by the bootstrap conversation) first,
            # then the deployment field, then the default "semantic".
            docs_rel = str(getattr(deployment, "semantic_docs_dir", "")
                           or "semantic")
            try:
                local_source = workspace_root / ".claude" \
                    / "chatbi-harness.local.json"
                if local_source.is_file():
                    local = json.loads(local_source.read_text(
                        encoding="utf-8"))
                    docs_rel = str(local.get("semantic_docs_dir")
                                   or docs_rel)
            except (OSError, ValueError):
                pass
            semantic_root = workspace_root / docs_rel
            if semantic_root.is_dir():
                for path in sorted(semantic_root.rglob("*.md")):
                    try:
                        resolved = path.resolve(strict=True)
                        resolved.relative_to(semantic_root.resolve())
                    except (OSError, ValueError):
                        # Symlink escape / unresolvable: reject + record
                        # (CodebaseReader.search 手法).
                        try:
                            rejected_paths.append(
                                str(path.relative_to(workspace_root)))
                        except ValueError:
                            rejected_paths.append(str(path))
                        continue
                    basename = path.stem
                    header_metric = _semantic_metric_of(path)
                    if metric and metric.lower() not in (
                            basename.lower() + " "
                            + (header_metric or "").lower()):
                        continue
                    try:
                        content = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    try:
                        relative_path = str(path.relative_to(workspace_root))
                    except ValueError:
                        relative_path = path.name
                    docs.append({**_doc_meta(relative_path, content),
                                 "content": content[: _SEMANTIC_DOC_CAP],
                                 "metric": header_metric or basename})

        if docs:
            entry = EvidenceEntry.create(
                source_tier="T1", evidence_source="semantic-layer",
                rule_ids=("SEM-001", "SEM-002"),
                payload={"metric": metric,
                         "docs": [{k: v for k, v in d.items()
                                   if k != "content"} for d in docs]},
                runtime_name="agno", native_run_id=scope.run_id or "",
                harness_release=harness_release,
            )
            scope.evidence_chain.append(entry.to_dict())
            _record(name, "t1_semantic", entry)
            out = func(**args)
            if isinstance(out, Mapping):
                return {**dict(out), "discovered": True,
                        "docs": docs,
                        "rejected_paths": rejected_paths}
            return out
        # No hit: record the T1 GAP evidence (SEM-001) — the _entry_marks_gap
        # convention automatically satisfies the T2 degradation precondition
        # (C002 semantics, same as CC).
        entry = EvidenceEntry.create(
            source_tier="T1", evidence_source="semantic-layer",
            rule_ids=("SEM-001",),
            payload={"status": "gap", "metric": metric,
                     "reason": "no semantic doc matched"},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release,
        )
        scope.evidence_chain.append(entry.to_dict())
        _record(name, "t1_semantic", entry)
        out = func(**args)
        if isinstance(out, Mapping):
            return {**dict(out), "discovered": False, "gap": True,
                    "metric": metric, "rejected_paths": rejected_paths}
        return out

    # --- chatbi_load_runbook (A1) ------------------------------------------
    def _load_runbook_handler(name: str, func: Callable[..., Any],
                              args: Mapping[str, Any]) -> Any:
        registry = dict(runbook_registry) if runbook_registry else {}
        workflow_id = str(args.get("workflow_id") or "")
        entry = registry.get(workflow_id)
        if entry is None:
            return _deny_raw(name, rule_ids=("HOOK-004",),
                             reason=f"unknown or unregistered workflow_id "
                                    f"{workflow_id!r} (registry: "
                                    f"{sorted(registry)})",
                             recovery="Load the runbook for a workflow "
                                      "listed in the routing table")
        # Evidence (evidence_source="runbook-load"): recorded but NOT
        # appended to scope.evidence_chain — it must not pollute the tier
        # chain or the review context; the delivery gate does not read this
        # source, so it has no gate side effect.
        entry_ev = EvidenceEntry.create(
            source_tier="T2", evidence_source="runbook-load",
            rule_ids=("HOOK-001", "PORT-001"),
            payload={"workflow_id": workflow_id,
                     "runbook_path": entry.path,
                     "sha256": entry.sha256,
                     "content_bytes": len(entry.content)},
            runtime_name="agno", native_run_id=scope.run_id or "",
            harness_release=harness_release)
        _record(name, "runbook_load", entry_ev)
        return func(**args)

    _DISPATCH: dict[str, Callable[..., Any]] = {
        "chatbi_record_request": _record_request,
        "chatbi_record_evidence": _record_evidence,
        "chatbi_submit_candidate": _submit_candidate,
        "chatbi_review": _review,
        "chatbi_crosscheck": _crosscheck,
        "chatbi_build_plan": _build_plan,
        "chatbi_impact_manifest": _impact_manifest,
        "chatbi_registry_append": _registry_append,
        "chatbi_lint_reference": _lint_reference,
        "chatbi_evaluate": _evaluate,
        "chatbi_correction": _correction,
        "chatbi_drift_report": _drift_report,
        "chatbi_init_diagnostic": _init_diagnostic,
        "chatbi_bootstrap": _bootstrap,
        "chatbi_load_runbook": _load_runbook_handler,
        "chatbi_query_source": _query_source,
        "chatbi_dbt_draft": _dbt_draft,
        "chatbi_dbt_execute": _dbt_execute,
        "chatbi_semantic_discover": _semantic_discover,
    }

    def domain_hook(name: str, func: Callable[..., Any],
                    args: Mapping[str, Any]) -> Any:
        handler = _DISPATCH.get(name)
        if handler is None:
            return func(**args)
        try:
            return handler(name, func, args)
        except GateError as error:
            # Any kernel GateError escapes a handler -> fail-closed deny.
            return _deny(name, error.decision)
        except Exception as error:  # noqa: BLE001 - HOOK-004 fail-closed
            return _deny_raw(
                name, rule_ids=("HOOK-004",),
                reason=f"governance hook failed: {type(error).__name__}",
                recovery="Inspect the sanitized evidence and correct the "
                         "tool input")

    return domain_hook


def _agno_capability_snapshot(runtime_ok: bool) -> Any:
    """Agno-target capability snapshot for run_init_diagnostic injection.

    Honest detection (FBK-003): claude_available stays False (this runtime
    is not Claude Code); the runtime checks report the agno runtime state.
    """
    from chatbi_governance.diagnostics import CapabilitySnapshot

    # The Kernel's CapabilitySnapshot contract is Claude-shaped (module 2
    # kept the diagnostic vocabulary stable): doctor_status must be one of
    # the five Claude doctor states and available_adapters uses the
    # managed/cli/fixture id grammar. The honest Agno projection:
    # claude_available=False and the runtime checks report unavailable when
    # the agno runtime is not importable.
    return CapabilitySnapshot(
        claude_available=False,
        claude_version=None,
        doctor_status="pass" if runtime_ok else "unavailable",
        logged_in=None,
        sandbox_available=runtime_ok,
        available_adapters=("fixture",) if runtime_ok else (),
        evidence_source="synthetic",
    )


# ---------------------------------------------------------------------------
# Run-level guardrails
# ---------------------------------------------------------------------------


def _parse_run_input(content: Any) -> tuple[str, Mapping[str, Any] | None]:
    """Parse a structured run input.

    Returns ``(workflow_id, request)``. An envelope
    ``{"workflow_id": ..., "request": {...}}`` selects the workflow; a bare
    mapping is the request itself (default workflow ``chatbi-analyze``).
    Non-JSON / non-mapping content -> ``("", None)`` (free-text session
    input; entry is lenient, the terminal gate is authoritative).
    """
    if isinstance(content, Mapping):
        raw = dict(content)
    elif isinstance(content, str):
        text = content.strip()
        if not text:
            return "", None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return "", None
        if not isinstance(parsed, Mapping):
            return "", None
        raw = dict(parsed)
    else:
        return "", None
    if isinstance(raw.get("workflow_id"), str) and isinstance(
        raw.get("request"), Mapping
    ):
        return raw["workflow_id"], dict(raw["request"])
    return "chatbi-analyze", raw


class ChatbiRequestGuardrail(BaseGuardrail):
    """pre_hooks[0]: run-level request preflight (design §5.2 ①).

    Structured run inputs (ChatBI request JSON) are validated against
    ``request.schema.json`` for the analyze workflow; a violation raises
    ``InputCheckError`` carrying the minimal clarifying question (the
    Kernel decision's recovery) + the blocked rule IDs. Free-text session
    inputs pass (entry lenient); the terminal delivery gate re-checks the
    evidence chain for every entry point (③ — never fail-open).
    """

    def __init__(
        self,
        *,
        config: Any,
        event_log: Any,
        run_scope: RunScope | None = None,
        deployment: Any = None,
    ) -> None:
        self.config = config
        self.event_log = event_log
        self.run_scope = run_scope
        self.deployment = deployment

    def check(self, run_input: Any, run_context: Any = None) -> None:
        from agno.exceptions import InputCheckError

        content = getattr(run_input, "input_content", run_input)
        workflow_id, request = _parse_run_input(content)
        if request is None:
            return  # free-text session input (lenient entry)
        if self.run_scope is not None:
            new_run_id = (
                getattr(run_context, "run_id", "") if run_context is not None
                else ""
            )
            if new_run_id and new_run_id != self.run_scope.run_id:
                # Run boundary (multi-turn isolation, design-runbook C2-1):
                # the RequestGuardrail is the FIRST pre-hook of a new run —
                # the transition run_id -> new_run_id is visible here, BEFORE
                # any tool call of the new run (the sanitize_hook fallback
                # covers free-text inputs that return early above). RunScope
                # is a process-shared object (F9): stale per-run state from a
                # previous run (evidence chain, review round, frozen candidate
                # SHA, impact manifest) must not leak into this run — a prior
                # run's T1 gap could otherwise satisfy this run's T2/T3
                # tier-gap precondition (SEM-001) at the tool edge.
                self.run_scope.evidence_chain.clear()
                self.run_scope.review_round = 1
                self.run_scope.candidate_sha = ""
                self.run_scope.impact = None
            self.run_scope.workflow_id = workflow_id or "chatbi-analyze"
            self.run_scope.request = request
            if run_context is not None:
                self.run_scope.run_id = (
                    getattr(run_context, "run_id", "") or self.run_scope.run_id
                )
                self.run_scope.session_id = (
                    getattr(run_context, "session_id", "")
                    or self.run_scope.session_id
                )
        if workflow_id != "chatbi-analyze":
            return  # other workflows validate inside their tool hooks
        try:
            validate_request(request)
        except GateError as error:
            from .events import emit_standard_event

            run_id = getattr(run_context, "run_id", "") or "run"
            session_id = getattr(run_context, "session_id", "") or "session"
            try:
                emit_standard_event(
                    self.event_log, run_id=run_id, session_id=session_id,
                    workflow_id="chatbi-analyze", step_id="request_preflight",
                    event_type="gate.blocked",
                    payload={"gate": "request_preflight",
                             "decision": error.decision.to_dict()},
                    evidence_refs=error.decision.evidence_refs,
                )
            except ValueError:
                pass  # no event log yet; the raise below is authoritative
            raise InputCheckError(
                message=(
                    "ChatBI request preflight failed: "
                    f"{error.decision.reason} — recovery: "
                    f"{error.decision.recovery}"
                ),
                additional_data={"rule_ids": list(error.decision.rule_ids)},
            )

    async def async_check(self, run_input: Any, run_context: Any = None) -> None:
        self.check(run_input, run_context)


class ChatbiPolicyGuardrail(BaseGuardrail):
    """pre_hooks[1]: trusted subject recording + SEM-003 intent precheck.

    Records the run-level trusted subject into the ``run_subject``
    contextvar — the subject comes from the run context ONLY (the verified
    user_id), never from the request body (SEC-003). For structured requests
    the Kernel ``policy.decide`` prechecks the declared protected intent
    (an agent-declared self-approval is blocked at the boundary, C005); the
    authoritative SEM-003 judgment still happens at the protected tool's
    approval hook.
    """

    def __init__(self, *, config: Any, event_log: Any,
                 deployment: Any = None) -> None:
        self.config = config
        self.event_log = event_log
        self.deployment = deployment

    def check(self, run_input: Any, run_context: Any = None,
              user_id: str | None = None) -> None:
        from agno.exceptions import InputCheckError

        subject = ""
        if isinstance(user_id, str) and user_id:
            subject = user_id
        elif run_context is not None:
            subject = getattr(run_context, "user_id", "") or ""
        if subject:
            run_subject.set(subject)
        content = getattr(run_input, "input_content", run_input)
        workflow_id, request = _parse_run_input(content)
        if request is None or self.config is None:
            return
        action_type = request.get("action_type")
        if not isinstance(action_type, str) or not action_type:
            return
        actor = request.get("actor") or subject or "operator"
        decision = decide(
            self.config,
            PolicyRequest(request_type=action_type, target_entity="",
                          actor=str(actor), purpose="governed protected action"),
        )
        if decision.status == "block":
            raise InputCheckError(
                message=(
                    f"protected-intent precheck blocked: {decision.reason} "
                    f"— recovery: {decision.recovery}"
                ),
                additional_data={"rule_ids": list(decision.rule_ids)},
            )

    async def async_check(self, run_input: Any, run_context: Any = None,
                          user_id: str | None = None) -> None:
        self.check(run_input, run_context, user_id)


class ChatbiDeliveryGuardrail(BaseGuardrail):
    """post_hooks[0]: the ONLY terminal authority (ADR-002).

    ``check(run_output)`` (spike R2 verified: agno 2.6.22
    ``filter_hook_args`` passes ``run_output`` by actual parameter name):

      1. read the run's evidence chain from the evidence index
         (T1/T2/T3/crosscheck/candidate/review/request);
      2. kernel delivery semantics (REV-001/002/003): a review PASS exists
         AND its candidate_sha == ``compute_candidate_sha(最终候选)`` (the
         final output, parsed as JSON when possible); rule_ids per failure
         mode (M5-S6);
      3. ``evidence.validate_provenance`` (17 fields, F1 contract) on the
         assembled provenance footer;
      4. PASS -> emit ``run.completed`` (payload.gate="delivery",
         decision="pass" — the contract validator enforces this) and return;
         failure -> emit ``gate.blocked`` + raise ``OutputCheckError``.

    The agno-native RunCompleted is never mapped to ChatBI completion —
    this kernel judgment is the only source (modification §6.2).
    """

    def __init__(
        self,
        *,
        config: Any,
        event_log: Any,
        evidence_index: Any,
        workspace_root: Path,
        harness_release: str,
        run_scope: RunScope | None = None,
        ir_workflows: Mapping[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.event_log = event_log
        self.evidence_index = evidence_index
        self.workspace_root = Path(workspace_root)
        self.harness_release = harness_release
        self.run_scope = run_scope
        self.ir_workflows = ir_workflows or {}

    # ------------------------------------------------------------------
    def _run_evidence_rows(self, run_id: str) -> list:
        rows = self.evidence_index.lookup(run_id=run_id) if self.evidence_index else []
        return rows or []

    def _read_entry(self, row: Any) -> dict[str, Any] | None:
        try:
            path = self.workspace_root / row.path
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, Mapping) else None
        except (OSError, ValueError, TypeError):
            return None

    def _tier_chain(self, run_id: str) -> tuple[dict[str, Any], ...]:
        """The (source_tier, content_sha256, payload) tier chain, in tier
        order, from the persisted .chatbi evidence (ADR-003 authority)."""
        chain: list[dict[str, Any]] = []
        for row in self._run_evidence_rows(run_id):
            entry = self._read_entry(row)
            if not entry:
                continue
            source = entry.get("evidence_source", "")
            if source in ("semantic-layer", "curated-reference",
                          "raw-exploration", "codebase-crosscheck"):
                chain.append({
                    "source_tier": entry.get("source_tier", ""),
                    "content_sha256": entry.get("content_sha256", ""),
                    "payload": entry.get("payload", {}),
                    "evidence_source": source,
                })
        order = {"T1": 0, "T2": 1, "T3": 2}
        chain.sort(key=lambda e: order.get(e["source_tier"], 9))
        return tuple(chain)

    def _latest_review(self, run_id: str) -> dict[str, Any] | None:
        review: dict[str, Any] | None = None
        for row in self._run_evidence_rows(run_id):
            entry = self._read_entry(row)
            if not entry or entry.get("evidence_source") != "candidate-review":
                continue
            payload = entry.get("payload") or {}
            # A BLOCKED review carries the failure-mode rule_ids in its
            # findings (the review hook writes them there); PASS reviews fall
            # back to the entry's declared rule set.
            findings = payload.get("findings") or []
            rule_ids = tuple(
                f for f in findings if isinstance(f, str)
            ) or tuple(entry.get("rule_ids", ()))
            review = {
                "status": payload.get("status"),
                "round": payload.get("round"),
                "candidate_sha": payload.get("candidate_sha"),
                "rule_ids": rule_ids,
                "reason": payload.get("reason", ""),
            }
        return review

    # ------------------------------------------------------------------
    def _ir_delivery_rules(self, workflow_id: str,
                           default: tuple[str, ...]) -> tuple[str, ...]:
        """The IR workflow's ``gates.delivery.rule_ids`` (fail-closed fallback
        to the module-5 defaults)."""
        workflow = self.ir_workflows.get(workflow_id)
        if workflow is not None and getattr(workflow, "gates", None) is not None:
            delivery = getattr(workflow.gates, "delivery", None)
            if delivery is not None and getattr(delivery, "rule_ids", ()):
                return tuple(delivery.rule_ids)
        return default

    def _run_evidence_sources(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Every recorded evidence entry for the run, keyed by source."""
        sources: dict[str, dict[str, Any]] = {}
        for row in self._run_evidence_rows(run_id):
            entry = self._read_entry(row)
            if not entry:
                continue
            source = entry.get("evidence_source", "")
            if source:
                sources[source] = entry
        return sources

    def _tool_blocked_rules(self, run_id: str) -> tuple[str, ...]:
        """Union of rule_ids across the run's tool.blocked events (the
        domain-hook denies are the specific verdicts for the generic
        workflows)."""
        rules: list[str] = []
        seen: set[str] = set()
        try:
            events = self.event_log.replay(run_id).events
        except Exception:  # noqa: BLE001 - no log -> no blocked signal
            return ()
        for event in events:
            if event.get("event_type") != "tool.blocked":
                continue
            payload = event.get("payload") or {}
            for rule in payload.get("rule_ids", []) or []:
                if isinstance(rule, str) and rule not in seen:
                    seen.add(rule)
                    rules.append(rule)
        return tuple(rules)

    def _approval_resolved(self, run_id: str) -> bool:
        """True when the run's event log carries an approval.resolved event
        with resolution="approved" (the AgentOS HITL confirmation passed
        Kernel re-verification in the approval_verify_hook)."""
        try:
            events = self.event_log.replay(run_id).events
        except Exception:  # noqa: BLE001 - no log -> not resolved
            return False
        for event in events:
            if event.get("event_type") != "approval.resolved":
                continue
            payload = event.get("payload") or {}
            if payload.get("resolution") == "approved":
                return True
        return False

    def _check_generic(self, run_id: str, session_id: str,
                       workflow_id: str) -> tuple[tuple[str, ...], str, str]:
        """Non-analyze delivery verdict (E-series semantics, mirroring the
        module-5 per-workflow verdict dispatch):

        - any tool.blocked deny -> block with its rule_ids;
        - init: the diagnostic evidence status BLOCKED -> block with the IR
          rule set;
        - bootstrap/bfr/evaluate/audit-drift: their recorded evidence
          decides pass/block with the IR rule set;
        - maintain-model/correction: a protected-action run reaching the
          terminal gate without the approval resolution is blocked.
        Returns ``(rule_ids, reason, recovery)``; empty rule_ids = PASS.
        """
        blocked_rules = self._tool_blocked_rules(run_id)
        if blocked_rules:
            return (blocked_rules,
                    "a governance tool was denied (see tool.blocked)",
                    "Resolve the blocked tool's recovery action and re-run")
        sources = self._run_evidence_sources(run_id)
        ir_rules = self._ir_delivery_rules(
            workflow_id, ("PORT-001", "SEC-003", "HOOK-004"))
        if workflow_id == "chatbi-init":
            diag = sources.get("init-diagnostic", {})
            payload = diag.get("payload") or {}
            if payload.get("status") == "BLOCKED":
                return (ir_rules,
                        "init diagnostic reports blocking failures "
                        "(production_ready stays False)",
                        "; ".join(payload.get("recovery_actions", []) or [])
                        or "Fix the blocked checks and re-run init")
            if not diag:
                return (ir_rules, "init diagnostic did not run",
                        "Re-run the init diagnostic")
            return (), "", ""
        if workflow_id == "chatbi-bootstrap":
            if not sources.get("bootstrap-inventory"):
                return (ir_rules,
                        "bootstrap did not produce a validated source "
                        "inventory",
                        "Re-run the bootstrap chain")
            return (), "", ""
        if workflow_id == "chatbi-build-from-requirement":
            if not sources.get("build-plan"):
                return (ir_rules,
                        "SRC-002 route not resolved to a validated build "
                        "plan (route A requires owner adjudication, "
                        "REQ-001/002)",
                        "Resolve the SRC-002 route and re-run")
            return (), "", ""
        if workflow_id == "chatbi-evaluate":
            run_entry = sources.get("evaluation-run", {})
            payload = run_entry.get("payload") or {}
            if not run_entry or not payload.get("all_passed"):
                return (ir_rules,
                        "evaluation release gate not passed (EVAL-004)",
                        "Meet the owner-confirmed release threshold and "
                        "re-run")
            return (), "", ""
        if workflow_id == "chatbi-audit-drift":
            if not sources.get("drift-report"):
                return (ir_rules, "drift audit produced no report",
                        "Fix the drift detection chain and re-run")
            return (), "", ""
        if workflow_id == "chatbi-maintain-knowledge":
            if not sources.get("knowledge-lint"):
                return (ir_rules,
                        "reference lint found issues (DOC-002/003)",
                        "Resolve the lint issues via the governed reference "
                        "authoring flow")
            return (), "", ""
        # maintain-model / correction (M-2, eval round 1): the protected
        # action pauses at the AgentOS HITL boundary; once the human-owner
        # approval is RESOLVED (approval.resolved=approved after Kernel
        # re-verification) and the governed record exists, the workflow has a
        # completion path — never a dead-end block.
        if workflow_id == "chatbi-maintain-model":
            if self._approval_resolved(run_id) and sources.get(
                "model-registry"
            ):
                return (), "", ""
            return (ir_rules,
                    "protected-action approval not resolved or the model "
                    "registry record is missing (DOC-004/SEM-003)",
                    "Resolve the human-owner approval and re-run")
        if workflow_id == "chatbi-correction":
            if self._approval_resolved(run_id) and sources.get(
                "correction-record"
            ):
                return (), "", ""
            return (ir_rules,
                    "protected-action approval not resolved or the "
                    "correction record is missing (FBK-002/SEM-003)",
                    "Resolve the human-owner approval and re-run")
        if not sources:
            return (ir_rules,
                    "no governed evidence was recorded for the run",
                    "Re-run the governed flow with the required evidence")
        return (ir_rules,
                "delivery gate requirement not met for this workflow",
                "Complete the governed flow and re-run")

    @staticmethod
    def _is_conversational_handoff(text: str) -> bool:
        """True when the run output is a question/handoff to the user rather
        than a delivery attempt. Multi-turn model (agno 验收 3.1): the agent
        asks for a missing time range / segment before any evidence exists —
        that ending must not C002-block. Heuristic, deliberately narrow:
        ends with '?' or mentions 'clarif' (case-insensitive). The governance
        preamble tells the agent to phrase handoffs as questions, so the
        question-mark signal is the self-consistent contract. Prose data
        answers (no '?', no 'clarif') stay fail-closed C002-blocks."""
        if not text:
            return False
        stripped = text.strip()
        # ASCII "?" and full-width "？" (U+FF1F): real-model live (agno 验收
        # 2026-08-12) — the model answered a handoff question in Chinese with
        # a full-width question mark and was C002-blocked.
        if stripped.endswith("?") or stripped.endswith("？"):
            return True
        return "clarif" in stripped.lower()

    def _final_candidate(self, run_output: Any) -> Any:
        content = getattr(run_output, "content", None)
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, Mapping):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            # Real-model integration (live, 2026-08-11): DeepSeek wraps the
            # exact candidate JSON in a prose preamble even when the delivery
            # contract instructs JSON-only output. Extract the outermost
            # well-formed JSON object (string/escape-aware, same helper as the
            # reviewer path) so the REV-001 SHA binding compares the candidate
            # the model actually delivered. No well-formed object -> the raw
            # content is hashed and the gate blocks (fail-closed, REV-001).
            from .reviewer import extract_json_object

            extracted = extract_json_object(content)
            if isinstance(extracted, Mapping):
                return extracted
        return content

    def _assemble_footer(self, run_id: str, workflow_id: str,
                         request: Mapping[str, Any],
                         tier_chain: tuple[dict[str, Any], ...],
                         review: dict[str, Any] | None) -> dict[str, Any]:
        """Assemble the provenance footer (F1, 17 fields) from the evidence
        chain + request — mirrors the old footer_assembly step."""
        tiers = [e["source_tier"] for e in tier_chain if e["source_tier"]]
        source_tier = tiers[-1] if tiers else "T1"
        # C004: raw exploration (T3) is lower-confidence evidence — the
        # semantic is registered by the tier, not a payload marker (the
        # payload hash must stay the golden-pinned content).
        low_confidence = "T3" in tiers
        return {
            "question": request.get("question", ""),
            "time_range": request.get("time_range", ""),
            "entity": request.get("entity", ""),
            "segment": request.get("segment", ""),
            "method": "governed_analysis_agno",
            "source_tier": source_tier,
            "filters": ["time_range:last_month"],
            "inclusions": ["semantic_layer" if source_tier == "T1"
                           else "curated_references"],
            "exclusions": [],
            "denominator": "none",
            "quality": "governed_evidence",
            "limitations": (
                "raw exploration fallback requires high-risk review warning "
                "(ANS-003)" if low_confidence else "governed evidence chain"
            ),
            "review_round": (review or {}).get("round") or 1,
            "freshness": "snapshot_2024_01" if low_confidence else "current",
            "owner": "domain_owner_example",
            "confidence": "low" if low_confidence else "medium",
            "provenance_refs": [f"evidence:run:{run_id}"],
        }

    # ------------------------------------------------------------------
    def check(self, run_output: Any, run_context: Any = None) -> None:
        from agno.exceptions import OutputCheckError

        from .events import emit_standard_event

        run_id = getattr(run_output, "run_id", "") or (
            getattr(run_context, "run_id", "") or "run"
        )
        session_id = getattr(run_output, "session_id", "") or (
            getattr(run_context, "session_id", "") or "session"
        )
        # A native Agent's RunOutput carries no workflow_id (the envelope
        # routing lives in the run scope, set by the RequestGuardrail) —
        # the scope is the authoritative workflow selector (M7 note).
        scope_workflow = ""
        if self.run_scope is not None:
            scope_workflow = self.run_scope.workflow_id or ""
        workflow_id = (
            scope_workflow
            or getattr(run_output, "workflow_id", "")
            or (getattr(run_context, "workflow_id", "") or "")
            or "chatbi-analyze"
        )
        if self.run_scope is not None:
            self.run_scope.run_id = run_id
            self.run_scope.session_id = session_id
            self.run_scope.workflow_id = workflow_id
            self.run_scope.request = self.run_scope.request or {}

        tier_chain = self._tier_chain(run_id)
        review = self._latest_review(run_id)

        rule_ids: tuple[str, ...] = ()
        reason = ""
        recovery = "Re-run the governed flow with a complete evidence chain"
        if workflow_id == "chatbi-analyze":
            if not tier_chain and review is None:
                # Real-model integration (agno 验收 3.1): a run that ends
                # with a conversational handoff (clarification question,
                # request for input) has no evidence chain BY DESIGN — the
                # flow has not produced anything to deliver. The delivery
                # gate binds deliveries only; a handoff to the user is not
                # a delivery. Delivery is DEFINED by the §7.1 contract: the
                # final message is the frozen candidate object. When the
                # output carries no candidate object, the run ended
                # conversationally (clarify/status) regardless of
                # punctuation (real-model live 2026-08-12: the model asked
                # for the time range ending with '。' — a question-ending
                # heuristic is whack-a-mole); a candidate JSON without the
                # governed chain still C002-blocks, fail-closed.
                if not isinstance(self._final_candidate(run_output), Mapping):
                    return
                rule_ids = ("REV-003", "HOOK-004")
                reason = ("no evidence chain and no review were recorded; "
                          "the candidate cannot be delivered (C002)")
                recovery = (
                    "The run ended without a governed evidence chain — the "
                    "analysis request was never completed. Provide the "
                    "missing request details (e.g. the analysis time range) "
                    "in your next message and re-ask (REQ-001)"
                )
            elif review is None:
                rule_ids = ("REV-001", "REV-003")
                reason = "no independent review was recorded (REV-001/002/003)"
            elif review.get("status") != "PASS":
                rule_ids = tuple(review.get("rule_ids") or _RULES_NOT_PASS)
                reason = review.get("reason") or (
                    "review verdict is not a clean PASS for the frozen "
                    "candidate")
                # B2 (design-runbook-completion): a run whose BLOCK ceiling
                # is exhausted must not keep getting "fix and re-review" —
                # the recovery hands off to the user (the block itself stays
                # the ordinary REV-001/003 logic; only the message changes).
                if _review_block_count(self.event_log, run_id) >= REVIEW_BLOCK_LIMIT:
                    recovery = (
                        "Review attempts exhausted (REV-003): hand off to "
                        "the user with the blocking findings and their "
                        "recovery actions; do not re-review in this run")
                else:
                    recovery = "Address every blocking finding and re-review"
            else:
                final_candidate = self._final_candidate(run_output)
                try:
                    final_sha = compute_candidate_sha(final_candidate)
                except (TypeError, ValueError):
                    final_sha = ""
                if review.get("candidate_sha") != final_sha:
                    rule_ids = _RULES_STALE_SHA
                    reason = (
                        "final candidate changed after the review PASS; "
                        "REV-001: the answer must be re-reviewed")
                    recovery = "Re-submit the reviewed candidate unchanged"
                else:
                    rule_ids = ()
        else:
            # Generic workflows: per-workflow delivery verdict (E-series).
            rule_ids, reason, recovery = self._check_generic(
                run_id, session_id, workflow_id)

        if rule_ids:
            decision = GateDecision.block(
                rule_ids=rule_ids,
                evidence_refs=("evidence:candidate-review",),
                reason=reason,
                recovery=recovery,
            )
            try:
                emit_standard_event(
                    self.event_log, run_id=run_id, session_id=session_id,
                    workflow_id=workflow_id, step_id="delivery_gate",
                    event_type="gate.blocked",
                    payload={"gate": "delivery",
                             "decision": decision.to_dict()},
                    evidence_refs=decision.evidence_refs,
                )
            except ValueError:
                pass  # the raise below is authoritative
            raise OutputCheckError(
                message=(
                    f"ChatBI delivery gate blocked: {reason} — recovery: "
                    f"{recovery}"
                ),
                additional_data={"rule_ids": list(rule_ids)},
            )

        # PASS: provenance footer (F1 contract) then run.completed (ADR-002).
        # The footer contract is ANALYZE-specific (the E-series workflows
        # carry their own governed evidence; no analyze footer exists).
        if workflow_id == "chatbi-analyze":
            request = self.run_scope.request if self.run_scope is not None else {}
            try:
                footer = self._assemble_footer(
                    run_id, workflow_id, request, tier_chain, review)
                validate_provenance(footer)
            except GateError as error:
                decision = error.decision
                try:
                    emit_standard_event(
                        self.event_log, run_id=run_id, session_id=session_id,
                        workflow_id=workflow_id, step_id="footer_assembly",
                        event_type="gate.blocked",
                        payload={"gate": "delivery",
                                 "decision": decision.to_dict()},
                        evidence_refs=decision.evidence_refs,
                    )
                except ValueError:
                    pass
                raise OutputCheckError(
                    message=(
                        f"ChatBI provenance footer failed: {decision.reason} "
                        f"— recovery: {decision.recovery}"
                    ),
                    additional_data={"rule_ids": list(decision.rule_ids)},
                )

        emit_standard_event(
            self.event_log, run_id=run_id, session_id=session_id,
            workflow_id=workflow_id, step_id="delivery_gate",
            event_type="run.completed",
            payload={"gate": "delivery", "decision": "pass",
                     "candidate_sha": (review or {}).get("candidate_sha", "")},
            evidence_refs=("evidence:candidate-review",),
        )

    async def async_check(self, run_output: Any, run_context: Any = None) -> None:
        self.check(run_output, run_context)
