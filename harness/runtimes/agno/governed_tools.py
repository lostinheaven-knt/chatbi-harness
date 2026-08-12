"""Governed tool surface for the single ChatBI agent (skill+hooks module A).

The agent does NOT hold bare Write/Edit/Bash tools: every write-capable or
governed operation goes through one of the 19 ``chatbi_*`` governance tools
(design §1.2, modification §3 rule R2). Each governance tool is a plain
Python function + ``@tool``; protected tools additionally carry the
``@approval(type='required')`` sentinel (AgentOS native HITL). Per design the
tool FUNCTIONS are dumb — they only pass arguments through and echo a request
payload; ALL kernel judgment (allowlist, tier-gap preconditions, candidate
SHA binding, review verdict validation, EVAL-004/DOC-004 gates, approval
re-verification, realpath) lives in the ``tool_hooks`` chain
(:mod:`runtimes.agno.hooks`, module B). The tools are the deterministic
ANCHOR POINTS that the hooks hang on; no second business rule lives here
(invariant 2).

The tool registry is derived from the IR at build time:
:func:`build_tool_specs` walks the nine workflows' step declarations and maps
each step id onto the governance tool that carries its deterministic
semantics (design §1.3 — ``workflow_id -> tool-name set``). ``IR tools
allow/deny`` semantics stay authoritative for the allowlist hook (module B).

:class:`RunScope` is the shared mutable run-identity holder between tools and
hooks (adapter glue only: run_id / session_id / workflow_id / request /
evidence chain / candidate SHA / review round). The review tool needs the
current run identity to build the reviewer context; the hooks update the
scope at run boundaries.

Applicable rules: HOOK-001, SEC-001, SEM-003, MR-005, invariant 2/5,
PORT-001, SEC-003 (no keys, no machine paths).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from chatbi_harness_ir.conditions import (
    ConditionSyntaxError,
    parse_condition,
)
from chatbi_harness_ir.schema import ExecutorKind, PROTECTED_ACTIONS

#: The 19 governance tools (design §1.2 + design-runbook-completion A1:
#: ``chatbi_load_runbook`` is the 15th, the fail-closed governed runbook
#: loader; agno Phase 2 adds the 4 runtime-adaptation tools —
#: ``chatbi_query_source`` / ``chatbi_dbt_draft`` / ``chatbi_dbt_execute`` /
#: ``chatbi_semantic_discover``, technical-design-agno-phase2 §5.1). Their
#: ``workflow_ids`` is empty (the IR has no corresponding step ids — honest
#: registration: the tool-surface extension is an agno runtime adaptation,
#: the IR is untouched); the allowlist invariant (``specs_by_name.keys()``)
#: auto-admits them. ``kernel_ref`` is the declarative kernel dotted path
#: (audit only — the real judgment is in the hooks).
_TOOL_NAMES = (
    "chatbi_record_request",
    "chatbi_record_evidence",
    "chatbi_submit_candidate",
    "chatbi_review",
    "chatbi_crosscheck",
    "chatbi_build_plan",
    "chatbi_impact_manifest",
    "chatbi_registry_append",
    "chatbi_lint_reference",
    "chatbi_evaluate",
    "chatbi_correction",
    "chatbi_drift_report",
    "chatbi_init_diagnostic",
    "chatbi_bootstrap",
    "chatbi_load_runbook",
    "chatbi_query_source",
    "chatbi_dbt_draft",
    "chatbi_dbt_execute",
    "chatbi_semantic_discover",
)

#: Step id -> governance tool (base mapping; step ids that collide across
#: workflows — ``report``/``record`` — are disambiguated per workflow below).
_STEP_TO_TOOL: dict[str, str] = {
    "request_preflight": "chatbi_record_request",
    "t1_semantic": "chatbi_record_evidence",
    "t2_curated": "chatbi_record_evidence",
    "t3_raw": "chatbi_record_evidence",
    "src002_crosscheck": "chatbi_crosscheck",
    "codebase_crosscheck": "chatbi_crosscheck",
    "candidate_bind": "chatbi_submit_candidate",
    "candidate_review": "chatbi_review",
    "classify_src002": "chatbi_build_plan",
    "derive_plan": "chatbi_build_plan",
    "impact_manifest": "chatbi_impact_manifest",
    "registry_append": "chatbi_registry_append",
    "lint": "chatbi_lint_reference",
    "run_suite": "chatbi_evaluate",
    "release_gate": "chatbi_evaluate",
    "build_correction": "chatbi_correction",
    "detect": "chatbi_drift_report",
    "classify": "chatbi_drift_report",
    "persist": "chatbi_drift_report",
    "capability_probe": "chatbi_init_diagnostic",
    "checks_assembly": "chatbi_init_diagnostic",
    "mysql_spec": "chatbi_bootstrap",
    "merge_local_config": "chatbi_bootstrap",
    "adapter_select": "chatbi_bootstrap",
    "run_mysql": "chatbi_bootstrap",
    "source_inventory": "chatbi_bootstrap",
    "scaffold": "chatbi_bootstrap",
}

#: Step ids whose tool mapping depends on the workflow (disambiguated).
_WORKFLOW_STEP_OVERRIDES: dict[str, dict[str, str]] = {
    "chatbi-init": {"report": "chatbi_init_diagnostic"},
    "chatbi-evaluate": {"record": "chatbi_evaluate", "report": "chatbi_evaluate"},
    "chatbi-correction": {"record": "chatbi_correction"},
    "chatbi-audit-drift": {"report": "chatbi_drift_report"},
}


def _tool_for_step(workflow_id: str, step_id: str) -> str | None:
    override = _WORKFLOW_STEP_OVERRIDES.get(workflow_id, {})
    if step_id in override:
        return override[step_id]
    return _STEP_TO_TOOL.get(step_id)


@dataclass(frozen=True)
class ToolSpec:
    """One governance tool declaration (design §1.3)."""

    name: str
    workflow_ids: tuple[str, ...]        # workflows this tool serves (allow surface)
    kernel_ref: str                      # declarative kernel dotted path (audit)
    approval: str | None = None          # None | "required"
    read_only: bool = True


def build_tool_specs(ir_workflows: Mapping[str, Any]) -> list[ToolSpec]:
    """Derive the governance tool surface from the nine IR workflows.

    For every step whose id maps onto a governance tool, the tool's
    ``workflow_ids`` records the owning workflow. ``approval="required"`` is
    registered ONLY for workflows whose IR declares a ``human_approval`` step
    with a parseable ``owner.pending(<action>)`` condition (the condition may
    be true at runtime; workflows with a never-true condition do not register
    — design §1.2, M6-S1 semantics).

    ``ir_workflows`` maps ``workflow_id`` -> loaded IR Workflow object.
    """
    specs_by_name: dict[str, set[str]] = {}
    approval_workflows: set[str] = set()
    for workflow_id, workflow in ir_workflows.items():
        steps = getattr(workflow, "steps", ()) or ()
        for step in steps:
            # human_approval steps are detected BEFORE the tool mapping guard
            # (their step ids like ``owner_approval`` have no governance tool).
            if getattr(step, "executor", None) == ExecutorKind.HUMAN_APPROVAL:
                when = getattr(step, "when", None)
                if isinstance(when, str) and when:
                    try:
                        cond = parse_condition(when)
                    except ConditionSyntaxError:
                        cond = None
                    if cond is not None and cond.kind == "owner_pending":
                        approval_workflows.add(workflow_id)
            tool_name = _tool_for_step(workflow_id, getattr(step, "id", ""))
            if tool_name is None:
                continue
            specs_by_name.setdefault(tool_name, set()).add(workflow_id)

    kernel_refs: dict[str, str] = {
        "chatbi_record_request": "chatbi_governance.evidence.validate_request",
        "chatbi_record_evidence": "chatbi_governance.evidence.EvidenceEntry.create",
        "chatbi_submit_candidate": "chatbi_governance.evidence.compute_candidate_sha",
        "chatbi_review": "runtimes.agno.reviewer.run_review",
        "chatbi_crosscheck": "chatbi_governance.adapters.select_codebase_reader",
        "chatbi_build_plan": "chatbi_governance.build_plan.validate_build_plan",
        "chatbi_impact_manifest": "chatbi_governance.impact.build_impact_manifest",
        "chatbi_registry_append": "chatbi_governance.build_plan.append_model_registry",
        "chatbi_lint_reference": "chatbi_governance.knowledge.lint_reference",
        "chatbi_evaluate": "chatbi_governance.evaluator.build_evaluation_run",
        "chatbi_correction": "chatbi_governance.evaluator.build_correction_record",
        "chatbi_drift_report": "chatbi_governance.drift.detect_drift",
        "chatbi_init_diagnostic": "chatbi_governance.diagnostics.run_init_diagnostic",
        "chatbi_bootstrap": "chatbi_governance.bootstrap.build_mysql_adapter_spec",
        #: The load-runbook deterministic semantics = the startup-built
        #: registry (IR prompts[] + manifest, A1) — the tool body is a dumb
        #: registry lookup; the hook records the runbook-load evidence.
        "chatbi_load_runbook": "runtimes.agno.prompt_loader.build_runbook_registry",
        #: Phase 2 audit anchors (technical-design-agno-phase2 §5.1): the
        #: kernel primitives the corresponding hooks hang on.
        "chatbi_query_source": "chatbi_governance.adapters.CliAdapter",
        "chatbi_dbt_draft": "chatbi_governance.build_plan.build_model_entry",
        "chatbi_dbt_execute": "chatbi_governance.adapters.validate_cli_argv",
        "chatbi_semantic_discover": "chatbi_governance.evidence.EvidenceEntry.create",
    }
    #: Tools whose execution may write governed artifacts (registry/correction).
    #: Phase 2 (Q5): chatbi_dbt_draft writes model files under ws/models/**
    #: and chatbi_dbt_execute runs dbt against the warehouse — both are
    #: write-capable governed operations (no bare Write surface, R2).
    _WRITE_TOOLS = frozenset({"chatbi_registry_append", "chatbi_correction",
                              "chatbi_bootstrap", "chatbi_drift_report",
                              "chatbi_record_evidence", "chatbi_submit_candidate",
                              "chatbi_dbt_draft", "chatbi_dbt_execute"})
    #: Tools that carry AgentOS @approval when their workflow declares a
    #: human_approval step (SEM-003 protected actions).
    _APPROVAL_TOOLS = frozenset({"chatbi_registry_append", "chatbi_correction"})

    specs: list[ToolSpec] = []
    for tool_name in _TOOL_NAMES:
        workflow_ids = tuple(sorted(specs_by_name.get(tool_name, ())))
        approval = (
            "required"
            if tool_name in _APPROVAL_TOOLS
            and any(wid in approval_workflows for wid in workflow_ids)
            else None
        )
        specs.append(
            ToolSpec(
                name=tool_name,
                workflow_ids=workflow_ids,
                kernel_ref=kernel_refs[tool_name],
                approval=approval,
                read_only=tool_name not in _WRITE_TOOLS,
            )
        )
    return specs


@dataclass
class RunScope:
    """Shared mutable run-identity holder (tools <-> hooks adapter glue).

    Updated by the run-boundary hooks (module B) at run start / tool-call
    boundaries; read by the review tool body to build the reviewer context.
    Never serialized; never a second business rule (invariant 2).
    """

    run_id: str = ""
    session_id: str = ""
    workflow_id: str = ""
    request: Mapping[str, Any] = field(default_factory=dict)
    candidate_sha: str = ""
    #: Evidence chain entries (source_tier / evidence_source / content_sha256).
    evidence_chain: list[Mapping[str, Any]] = field(default_factory=list)
    review_round: int = 1
    #: Latest impact manifest dict (maintain-model DOC-004 sync gate state).
    impact: Mapping[str, Any] | None = None


def evaluate_step_condition(
    expr: str | None,
    *,
    evidence_chain: tuple[Mapping[str, Any], ...],
    request: Mapping[str, Any],
    action: str | None = None,
) -> bool:
    """Evaluate an IR step ``when`` condition (adapter-side, fail-closed).

    Grammar is the whitelist parser ``chatbi_harness_ir.conditions``
    (HOOK-001: no eval/exec). Supported families:

    - ``always`` / ``never``;
    - ``evidence.has_gap(<tier>)`` — no evidence of that tier in the chain;
    - ``evidence.has(<tier>)`` — evidence of that tier present;
    - ``request.field_is(<field>, <value>)`` — run request field equality;
    - ``owner.pending(<action>)`` — ``action`` is a protected action AND the
      run's protected-action context (``action`` parameter) matches — the
      kernel's policy.decide is the authoritative protectedness judge;
    - ``delivery_decision.is_pass`` — not evaluable at tool level -> False
      (fail-closed; the delivery gate is the only authority, ADR-002).

    Any unknown symbol / syntax error / group with unknown content evaluates
    to False (fail-closed, never a silent pass).
    """
    if not isinstance(expr, str) or not expr:
        return True
    try:
        cond = parse_condition(expr)
    except ConditionSyntaxError:
        return False
    return _eval_cond(
        cond, evidence_chain=evidence_chain, request=request, action=action
    )


def _entry_marks_gap(entry: Mapping[str, Any]) -> bool:
    """True when an evidence-chain entry records a tier GAP (deterministic).

    The convention: the evidence payload (or raw content) carries a gap
    marker — ``status == "gap"`` or a truthy ``gap`` / ``t1_gap`` /
    ``t2_gap`` field. This mirrors the controller's ``ctx["gaps"]`` dict:
    a tier is "gapped" when its attempt recorded a gap, not when evidence
    is absent.
    """
    payload = entry.get("payload")
    if isinstance(payload, Mapping):
        if payload.get("status") == "gap":
            return True
        for key in ("gap", "t1_gap", "t2_gap"):
            if payload.get(key):
                return True
    content = entry.get("content")
    if isinstance(content, str):
        for marker in ('"status": "gap"', "'status': 'gap'", '"gap"', "'gap'"):
            if marker in content:
                return True
    return False


def _eval_cond(
    cond: Any,
    *,
    evidence_chain: tuple[Mapping[str, Any], ...],
    request: Mapping[str, Any],
    action: str | None,
) -> bool:
    kind = cond.kind
    if kind == "always":
        return True
    if kind == "never":
        return False
    if kind in ("has_gap", "has"):
        tier = cond.args[0] if cond.args else ""
        present = any(
            isinstance(e, Mapping) and e.get("source_tier") == tier
            for e in evidence_chain
        )
        if kind == "has":
            return present
        # ``has_gap`` follows the module-5 controller semantics
        # (``ctx["gaps"]``): TRUE when a RECORDED GAP exists for the tier —
        # an attempted tier with a recorded gap marker, NOT "no evidence at
        # all". T2 requires a recorded T1 gap (design §1.2 C003), so the
        # marker must be derivable from the evidence chain.
        return any(
            isinstance(e, Mapping) and e.get("source_tier") == tier
            and _entry_marks_gap(e)
            for e in evidence_chain
        )
    if kind == "field_is":
        field, value = cond.args if len(cond.args) >= 2 else ("", "")
        return bool(request.get(field) == value)
    if kind == "owner_pending":
        cond_action = cond.args[0] if cond.args else ""
        if cond_action not in PROTECTED_ACTIONS:
            return False
        if not action:
            return False
        return action == cond_action
    if kind == "delivery_is_pass":
        # The tool-level adapter has no delivery verdict; the delivery
        # guardrail (module B) is the only authority (ADR-002).
        return False
    if kind == "group":
        inner = cond.args[0] if cond.args else None
        if inner is None:
            return False
        return _eval_cond(
            inner, evidence_chain=evidence_chain, request=request, action=action
        )
    return False


# ---------------------------------------------------------------------------
# Tool function bodies (dumb pass-throughs; kernel judgment lives in hooks)
# ---------------------------------------------------------------------------


def _echo(tool: str, **fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"tool": tool, "status": "requested"}
    for key, value in fields.items():
        if value is not None:
            out[key] = value
    return out


def _make_record_request(scope: RunScope) -> Callable[..., dict]:
    def chatbi_record_request(request: dict) -> dict[str, Any]:
        """Record the analysis request; the request-preflight hook validates
        it against request.schema.json and DENIES the call on any missing or
        malformed field (fail-closed).

        The request dict REQUIRES exactly these 7 fields:
          question: str            - the business question
          time_range: str          - analysis window, format
                                     "YYYY-MM-DD_to_YYYY-MM-DD"
                                     (e.g. "2024-01-01_to_2024-01-31")
          entity: str              - the entity analyzed (canonical name)
          segment: str             - the user/entity segment
          actor: str               - who is asking (e.g. "operator")
          purpose: str             - decision purpose (e.g. "decision_support")
          supported_decision: str  - the decision this analysis supports
        All 7 fields are REQUIRED with no empty values. If the user's
        question does not provide a field (e.g. no time range), DO NOT guess
        or send an empty value: first ask the user for the missing
        information (REQ-001 clarify), then record once you have it."""
        return _echo("chatbi_record_request", request=dict(request or {}))

    return chatbi_record_request


def _make_record_evidence(scope: RunScope) -> Callable[..., dict]:
    def chatbi_record_evidence(
        tier: str, content: Any, refs: list | None = None
    ) -> dict[str, Any]:
        """Record one evidence entry for a source tier (T1/T2/T3); the
        tier-gap hook enforces the T2/T3 preconditions and persists the
        sanitized EvidenceEntry."""
        return _echo("chatbi_record_evidence", tier=tier, content=content,
                     refs=list(refs) if refs else None)

    return chatbi_record_evidence


def _make_submit_candidate(scope: RunScope) -> Callable[..., dict]:
    def chatbi_submit_candidate(content: Any) -> dict[str, Any]:
        """Freeze the final candidate; the candidate-bind hook computes its
        SHA-256 (kernel) and binds the evidence chain to it."""
        return _echo("chatbi_submit_candidate", content=content)

    return chatbi_submit_candidate


def _make_crosscheck(scope: RunScope) -> Callable[..., dict]:
    def chatbi_crosscheck(query: str, codebase: str = "",
                          search: bool = False) -> dict[str, Any]:
        """Cross-check evidence against an external Business Codebase
        (read-only; select_codebase_reader + reader.read/search, realpath
        enforced by the realpath hook). search=True -> literal-substring
        search over the aliased root (reader.search); otherwise read the
        target path (existing semantics)."""
        return _echo("chatbi_crosscheck", query=query, codebase=codebase,
                     search=search)

    return chatbi_crosscheck


def _make_build_plan(scope: RunScope) -> Callable[..., dict]:
    def chatbi_build_plan(requirement: dict) -> dict[str, Any]:
        """Derive and validate a layered build plan from a requirement; the
        build-plan hook runs classify_src002_finding + validate_build_plan +
        validate_layer_dependency."""
        return _echo("chatbi_build_plan", requirement=dict(requirement or {}))

    return chatbi_build_plan


def _make_impact_manifest(scope: RunScope) -> Callable[..., dict]:
    def chatbi_impact_manifest(model_entry: dict) -> dict[str, Any]:
        """Build the impact manifest for a model change (kernel
        build_impact_manifest; DOC-004 sync gate follows)."""
        return _echo("chatbi_impact_manifest", model_entry=dict(model_entry or {}))

    return chatbi_impact_manifest


def _make_registry_append(scope: RunScope) -> Callable[..., dict]:
    def chatbi_registry_append(entry: dict) -> dict[str, Any]:
        """Append a model to the governed registry after the DOC-004 sync
        gate; protected action -> AgentOS human approval + kernel
        re-verification (approval hook)."""
        return _echo("chatbi_registry_append", entry=dict(entry or {}))

    return chatbi_registry_append


def _make_lint_reference(scope: RunScope) -> Callable[..., dict]:
    def chatbi_lint_reference(ref: str) -> dict[str, Any]:
        """Lint a governed knowledge reference (kernel lint_reference,
        DOC-002/003)."""
        return _echo("chatbi_lint_reference", ref=ref)

    return chatbi_lint_reference


def _make_evaluate(scope: RunScope) -> Callable[..., dict]:
    def chatbi_evaluate(suite_request: dict) -> dict[str, Any]:
        """Run the fixed evaluation suite against the isolated ground truth
        and enforce the EVAL-004 release gate (evaluate hook)."""
        return _echo("chatbi_evaluate", suite_request=dict(suite_request or {}))

    return chatbi_evaluate


def _make_correction(scope: RunScope) -> Callable[..., dict]:
    def chatbi_correction(correction: dict) -> dict[str, Any]:
        """Build and validate a dual-candidate correction record (kernel);
        protected -> AgentOS human approval + kernel re-verification."""
        return _echo("chatbi_correction", correction=dict(correction or {}))

    return chatbi_correction


def _make_drift_report(scope: RunScope) -> Callable[..., dict]:
    def chatbi_drift_report(fresh_inventory: dict | None = None) -> dict[str, Any]:
        """Produce the FM-STALE drift audit report (missing baseline ->
        STOP; classify + persist via the drift hook)."""
        return _echo("chatbi_drift_report",
                     fresh_inventory=dict(fresh_inventory or {}) if fresh_inventory else None)

    return chatbi_drift_report


def _make_init_diagnostic(scope: RunScope) -> Callable[..., dict]:
    def chatbi_init_diagnostic(request: dict | None = None) -> dict[str, Any]:
        """Run the nine-check installation diagnostic (kernel
        run_init_diagnostic with the runtime capability snapshot; report
        production_ready stays False)."""
        payload = dict(request) if isinstance(request, dict) else dict(scope.request or {})
        return _echo("chatbi_init_diagnostic", request=payload)

    return chatbi_init_diagnostic


def _make_bootstrap(scope: RunScope) -> Callable[..., dict]:
    def chatbi_bootstrap(spec: dict) -> dict[str, Any]:
        """Bootstrap a Warehouse from a MySQL spec (kernel spec builder ->
        resolve_executable allowlist -> CLI native runner -> source
        inventory -> scaffold).

        The mysql connection details (host, port, user, credential,
        executable path) come from the DEPLOYMENT configuration — the user
        does not provide them and you must not ask for them. The spec only
        needs the high-level fields, e.g.:
        {"source_database": "public", "target_warehouse_db": "dw_agno"}."""
        return _echo("chatbi_bootstrap", spec=dict(spec or {}))

    return chatbi_bootstrap


def _make_query_source(scope: RunScope) -> Callable[..., dict]:
    def chatbi_query_source(statement: str, tier: str = "T2") -> dict[str, Any]:
        """Query a governed table (dw_agno model or inventoried source
        table) through the mysql CLI (read-only SELECT; the query hook
        enforces the SQL readonly whitelist + table allowlist + tier
        precondition and records T2/T3 evidence)."""
        return _echo("chatbi_query_source", statement=statement, tier=tier)

    return chatbi_query_source


def _make_dbt_draft(scope: RunScope) -> Callable[..., dict]:
    def chatbi_dbt_draft(relative_path: str, content: str) -> dict[str, Any]:
        """Draft a model file under ws/models/** (the draft hook enforces
        the models/ path containment + suffix allowlist + size cap and
        records the candidate SHA; the file is written ONLY through this
        governed tool, no bare Write)."""
        return _echo("chatbi_dbt_draft", relative_path=relative_path,
                     content=content)

    return chatbi_dbt_draft


def _make_dbt_execute(scope: RunScope) -> Callable[..., dict]:
    def chatbi_dbt_execute(operation: str, select: str) -> dict[str, Any]:
        """Run dbt run/test for reviewed model candidates (the execute
        hook enforces the operation/select whitelist, the REV-001 review
        SHA binding and the dbt argv discipline)."""
        return _echo("chatbi_dbt_execute", operation=operation, select=select)

    return chatbi_dbt_execute


def _make_semantic_discover(scope: RunScope) -> Callable[..., dict]:
    def chatbi_semantic_discover(metric: str = "") -> dict[str, Any]:
        """Discover semantic-layer metric docs under ws/semantic/** (or the
        runtime fixture catalog in explicit test/example mode); a miss
        records the T1 gap evidence (SEM-001 degradation)."""
        return _echo("chatbi_semantic_discover", metric=metric)

    return chatbi_semantic_discover


#: Unified runtime adaptation note prepended to every chatbi_load_runbook
#: response (design-runbook-completion A3): the 9 runbook bodies stay the
#: CC-authored prose (no per-book edits -> no hash churn); this preamble
#: tells the model how the CC-native vocabulary maps onto THIS runtime's
#: governance tool surface.
_RUNTIME_ADAPTATION_PREAMBLE = (
    "Runtime adaptation: this runbook was authored for the Claude Code "
    "harness. In this agno runtime, its references to /chatbi-* commands, "
    "CC hooks and CC subagent gates are carried by the chatbi_* governance "
    "tools: every governed operation (record evidence, submit candidate, "
    "review, …) goes through those tools, and the deterministic edges "
    "(allowlist, evidence preconditions, candidate SHA binding, review "
    "verdict validation, approval, realpath, delivery gate) are enforced by "
    "the tool hooks and guardrails. The /chatbi-* commands do NOT exist in "
    "this runtime — this runbook you just loaded IS the procedure. "
    "Non-analyze workflows (init, bootstrap, maintain-model, "
    "build-from-requirement, evaluate, correction, audit-drift, "
    "maintain-knowledge): execute them with their workflow tool directly — "
    "e.g. chatbi_bootstrap for chatbi-bootstrap, chatbi_init_diagnostic for "
    "chatbi-init, chatbi_registry_append for maintain-model registration. "
    "There are no native skill tools (get_skill_*) on this agent's surface "
    "— never try to call them; load runbooks only via chatbi_load_runbook."
)


def _make_load_runbook(
    scope: RunScope,
    registry: Mapping[str, Any],
) -> Callable[..., dict]:
    def chatbi_load_runbook(workflow_id: str) -> dict[str, Any]:
        """Load the governed runbook for a workflow (fail-closed).

        The registry is built at startup from the IR ``prompts[]`` + the
        prompt manifest (sha256-pinned); an unknown or unregistered
        workflow_id is denied (double insurance — the domain hook already
        denies it). The response carries the pinned sha256, the
        manifest-relative path (PORT-001), the unified runtime adaptation
        note (A3) and the workflow's route_contract (cross-workflow handoff,
        F7)."""
        entry = registry.get(str(workflow_id or ""))
        if entry is None:
            return {"tool": "chatbi_load_runbook", "status": "blocked",
                    "workflow_id": str(workflow_id or ""),
                    "error": "unknown or unregistered workflow_id"}
        return {"tool": "chatbi_load_runbook", "status": "loaded",
                "workflow_id": entry.workflow_id,
                "runbook_path": entry.path,          # manifest-relative (PORT-001)
                "sha256": entry.sha256,
                "runtime_note": _RUNTIME_ADAPTATION_PREAMBLE,
                "runbook": entry.content,
                "route_contract": dict(entry.route_contract)}   # IR routes (F7)

    return chatbi_load_runbook


def build_governed_tools(
    *,
    specs: list[ToolSpec],
    deployment: Any,
    config: Any,                         # EffectiveConfig (policy.decide etc.)
    evidence_index: Any,
    event_log: Any,
    approvals: Any,                      # ChatBIApprovalCoordinator
    reviewer_agent: Any,
    workspace_root: Any,
    harness_release: str,
    native_runner: Callable[..., Any] | None = None,
    reviewer_runner: Any = None,         # stub 注入 seam（conformance）
    clock: Any = None,
    run_scope: RunScope | None = None,   # shared tools<->hooks run identity
    runbook_registry: Mapping[str, Any] | None = None,   # A1: IR+manifest 派生
) -> tuple[list[Callable[..., dict]], dict[str, ToolSpec]]:
    """Build the agent-visible governance tool functions (dumb pass-throughs).

    Returns ``(tools, spec_by_name)``. The tool functions carry NO kernel
    judgment: every deterministic edge is enforced by the tool_hooks chain
    (module B) that :func:`runtimes.agno.hooks.build_tool_hooks` attaches to
    the same agent. ``@approval(type='required')`` is stamped on the raw
    callables of protected tools (spec.approval == "required") so the agent's
    tool registration detects the sentinel and sets the AgentOS HITL flags
    (agno 2.6.22 ``agent/_tools.py`` callable branch).

    ``reviewer_runner`` is the injected stub seam for conformance; when None
    the live reviewer agent is used (``reviewer.py._default_reviewer_runner``).

    ``run_scope`` lets the caller share ONE :class:`RunScope` between the
    tools and the tool_hooks (the hooks update the run identity at run
    boundaries; the review tool reads it to build the reviewer context).
    """
    from .reviewer import build_review_tool

    scope = run_scope if run_scope is not None else RunScope()
    builders: dict[str, Callable[..., dict]] = {
        "chatbi_record_request": _make_record_request(scope),
        "chatbi_record_evidence": _make_record_evidence(scope),
        "chatbi_submit_candidate": _make_submit_candidate(scope),
        "chatbi_review": build_review_tool(
            reviewer_agent=reviewer_agent, reviewer_runner=reviewer_runner,
            run_scope=scope,
        ),
        "chatbi_crosscheck": _make_crosscheck(scope),
        "chatbi_build_plan": _make_build_plan(scope),
        "chatbi_impact_manifest": _make_impact_manifest(scope),
        "chatbi_registry_append": _make_registry_append(scope),
        "chatbi_lint_reference": _make_lint_reference(scope),
        "chatbi_evaluate": _make_evaluate(scope),
        "chatbi_correction": _make_correction(scope),
        "chatbi_drift_report": _make_drift_report(scope),
        "chatbi_init_diagnostic": _make_init_diagnostic(scope),
        "chatbi_bootstrap": _make_bootstrap(scope),
        "chatbi_load_runbook": _make_load_runbook(
            scope, dict(runbook_registry) if runbook_registry else {}),
        "chatbi_query_source": _make_query_source(scope),
        "chatbi_dbt_draft": _make_dbt_draft(scope),
        "chatbi_dbt_execute": _make_dbt_execute(scope),
        "chatbi_semantic_discover": _make_semantic_discover(scope),
    }

    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.approval import approval as _approval
    from agno.tools import tool as _tool

    spec_by_name: dict[str, ToolSpec] = {spec.name: spec for spec in specs}
    tools: list[Callable[..., dict]] = []
    for spec in specs:
        func = builders[spec.name]
        if spec.approval == "required":
            # Stamps the _agno_approval_type sentinel on the raw callable;
            # @tool() below detects it and sets requires_confirmation=True
            # (AgentOS native HITL, agno 2.6.22 tools/decorator.py:222).
            func = _approval(type="required")(func)
        # name= pins the governance tool name (the raw callable name may be
        # an internal builder name, e.g. ``_review``).
        tools.append(_tool(name=spec.name)(func))
    return tools, spec_by_name
