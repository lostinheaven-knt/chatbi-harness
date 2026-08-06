"""Generic IR->Workflow registry for the remaining eight workflows (module 6,
MR-E1).

The module-5 spike implemented ``chatbi-analyze`` by hand (workflow_analyze.py).
Module 6 extends the Agno target to ALL nine IR workflows by building the other
eight — bootstrap / build-from-requirement / maintain-model /
maintain-knowledge / evaluate / correction / audit-drift / init — from the IR
at runtime with a SHARED registry:

- ``deterministic`` steps resolve the IR ``function`` dotted path inside
  ``chatbi_governance`` and call the Kernel through a per-workflow argument
  wiring table (adapter glue only — every judgment lives in the Kernel,
  MR-003, invariant 2);
- ``agent_with_tools`` steps run through the injected ``agent_runner`` seam
  with the IR allow/deny enforced by ``StepToolPolicy`` (C011 semantics);
- ``human_approval`` steps are registered as Agno native confirmation pauses
  (``requires_confirmation``); because the agno 2.6.22 engine pauses
  UNCONDITIONALLY for such steps, the per-run ``owner.pending(<action>)``
  condition is evaluated by the controller's pause bridge — condition false
  = auto-resume without approval (no approval events, no run.paused
  surfaced); condition true = ChatBI ApprovalCoordinator bridge (SEM-003,
  design §11.1). This is a REGISTERED target extension (see
  docs/feature-flow-multi-runtime-mod6-v1.md §3.1);
- ``runtime_native`` steps run through the injected ``native_runner`` seam
  (live default = fail-closed ``RuntimeError``, FBK-003); two native steps
  have deterministic built-in implementations: init's ``capability_probe``
  (Agno capability probe -> kernel ``CapabilitySnapshot`` injected into
  ``run_init_diagnostic``) and audit-drift's ``persist`` (kernel
  ``write_state``);
- the adapter appends a terminal ``delivery_gate`` assembly step (the eight
  IRs declare the gate at workflow level, not as a step): a per-workflow
  pure function derives the ``GateDecision`` from the Kernel outputs in the
  context — rule_ids come from the IR ``gates.delivery.rule_ids``. The
  Kernel remains the terminal authority (ADR-002): ``run.completed`` is
  emitted by the controller only after this gate PASS.

Semantic-drift registration: the eight workflows share one IR with the
Claude target; any adapter-side behavioral deviation must be recorded in
``docs/feature-flow-multi-runtime-mod6-v1.md`` (no silent drift).

Applicable rules: MR-003, HOOK-001, ADR-002, SEM-003, REV-001..003,
DOC-004, EVAL-003/004, FBK-001..003, ABL-001, PORT-001, SEC-003,
invariant 2/3/5.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from chatbi_governance.adapters import resolve_executable
from chatbi_governance.adapters.codebase_reader import CodebaseEvidence
from chatbi_governance.bootstrap import (
    build_mysql_adapter_spec,
    merge_local_config,
    read_source_inventory,
)
from chatbi_governance.build_plan import (
    BuildPlan,
    build_model_entry,
    validate_build_plan,
)
from chatbi_governance.diagnostics import (
    CapabilitySnapshot,
    run_init_diagnostic,
)
from chatbi_governance.drift import (
    DriftCandidate,
    classify_finding,
    classify_src002_finding,
    detect_drift,
)
from chatbi_governance.evaluator import (
    GroundTruthVault,
    build_correction_record,
    build_evaluation_run,
    validate_correction,
)
from chatbi_governance.gates import GateDecision, GateError
from chatbi_governance.harness_state import write_state
from chatbi_governance.impact import AffectedAsset, build_impact_manifest
from chatbi_governance.knowledge import lint_reference
from chatbi_governance.policy import PolicyRequest, decide
from chatbi_harness_ir.conditions import ConditionSyntaxError, parse_condition
from chatbi_harness_ir.loader import load_workflow

from .workflow_analyze import _agent_call, _ctx_from_step_input, _fresh_ctx

#: The eight workflows this registry implements (analyze stays in
#: workflow_analyze.py; zero regression).
WORKFLOW_IDS = (
    "chatbi-bootstrap",
    "chatbi-build-from-requirement",
    "chatbi-maintain-model",
    "chatbi-maintain-knowledge",
    "chatbi-evaluate",
    "chatbi-correction",
    "chatbi-audit-drift",
    "chatbi-init",
)

#: The four protected actions (kernel config.py; mirrors the IR grammar
#: PROTECTED_ACTIONS) — used to interpret ``owner.pending(<action>)``.
_PROTECTED_ACTIONS = (
    "approve_metric",
    "change_access_policy",
    "production_publish",
    "destructive_migration",
)


class _GenericConfig:
    """Wiring shared by every generic step executor (one object per workflow)."""

    def __init__(
        self,
        *,
        workflow_id: str,
        config: Any,
        agent_runner: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None,
        native_runner: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None,
        on_evidence: Callable[[str, str, Any], None] | None,
        on_tool: Callable[[str, str, str, bool], None] | None,
        on_ctx: Callable[[str, Mapping[str, Any]], None],
        harness_release: str,
        workspace_root: Path,
        harness_config_path: Path | None,
        local_config_path: Path | None,
        deployment: Any,
        ir: Any,
    ) -> None:
        self.workflow_id = workflow_id
        self.config = config
        self.agent_runner = agent_runner
        self.native_runner = native_runner
        self.on_evidence = on_evidence
        self.on_tool = on_tool
        self.on_ctx = on_ctx
        self.harness_release = harness_release
        self.workspace_root = Path(workspace_root).resolve()
        self.harness_config_path = harness_config_path
        self.local_config_path = local_config_path
        self.deployment = deployment
        self.ir = ir
        self.tool_policies: dict[str, Any] = {}
        self.when_conditions: dict[str, str | None] = {
            step.id: step.when for step in ir.steps
        }
        #: IR step ids by executor kind (validated at build time).
        self.step_ids: tuple[str, ...] = tuple(step.id for step in ir.steps)
        #: In-memory full context per run_id (the persisted session_state
        #: snapshot is JSON-safe; step artifacts like the GroundTruthVault
        #: live only here — evaluate's record step needs the vault object).
        self.in_memory_ctx: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _generic_ctx(step_input: Any, cfg: _GenericConfig,
                 session_state: Any = None) -> dict[str, Any]:
    """Resolve the FULL in-memory context for a generic step.

    Prefers the run's in-memory context (which carries step artifacts like
    the GroundTruthVault) over the JSON-safe persisted session_state copy;
    falls back to the session_state/session input when the run has not been
    seen in-process yet (e.g. a fresh pause-resume in another process is out
    of MVP scope, adjudication ten)."""
    raw = getattr(step_input, "input", step_input)
    run_id = ""
    if isinstance(raw, Mapping):
        run_id = raw.get("run_id") or ""
    if run_id and run_id in cfg.in_memory_ctx:
        return cfg.in_memory_ctx[run_id]
    return _ctx_from_step_input(step_input, session_state)


def _eval_when(expr: str | None, ctx: Mapping[str, Any]) -> bool:
    """Evaluate one IR ``when`` condition against the run context.

    Grammar is the whitelist parser from ``chatbi_harness_ir.conditions``
    (HOOK-001: no eval/exec). The eight workflows use only ``always`` /
    ``request.field_is`` / ``owner.pending``.
    """
    if not expr:
        return True
    cond = parse_condition(expr)
    if cond.kind == "always":
        return True
    if cond.kind == "never":
        return False
    if cond.kind == "field_is":
        field, value = cond.args
        request = ctx.get("request") or {}
        return request.get(field) == value
    if cond.kind == "owner_pending":
        # ``owner.pending(<action>)`` — the adapter decides "pending" from the
        # Kernel policy decision (protected action approved by a human owner
        # carries rule_ids (SEM-003, SEC-001)); see _protected_pending.
        return bool(ctx.get("protected_pending"))
    if cond.kind == "delivery_is_pass":
        delivery = ctx.get("delivery")
        return isinstance(delivery, Mapping) and delivery.get("status") == "pass"
    raise ConditionSyntaxError(
        f"condition kind {cond.kind!r} is not supported by the module-6 "
        "workflow registry (no silent skip)"
    )


def _protected_pending(
    cfg: _GenericConfig,
    ctx: Mapping[str, Any],
    action: str,
    actor: str,
) -> bool:
    """True when the Kernel policy decision marks the action as a protected
    action requiring human-owner approval (SEM-003).

    ``policy.decide`` returns ``pass`` with rule_ids (SEM-003, SEC-001) for a
    protected action requested by a human; that exact signal is the adapter's
    "owner approval pending" read — no second business rule (invariant 2).
    ``cfg.config`` None -> fail-closed False (no approval gate is skipped
    silently; the coordinator's own policy check still blocks).
    """
    if cfg.config is None:
        return False
    decision = decide(
        cfg.config,
        PolicyRequest(
            request_type=action,
            target_entity=ctx.get("request", {}).get("target", "")
            if isinstance(ctx.get("request"), Mapping) else "",
            actor=actor,
            purpose="governed protected action",
        ),
    )
    if decision.status == "block":
        raise GateError(decision)
    return decision.rule_ids == ("SEM-003", "SEC-001")


def _record_evidence(
    cfg: _GenericConfig,
    ctx: Mapping[str, Any],
    step_id: str,
    entry: Any,
) -> None:
    if cfg.on_evidence is not None:
        cfg.on_evidence(ctx.get("run_id") or "", step_id, entry)


def _run_id(ctx: Mapping[str, Any]) -> str:
    return str(ctx.get("run_id") or "")


# ---------------------------------------------------------------------------
# chatbi-init
# ---------------------------------------------------------------------------


def _init_args_parse(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    """Parse init args; config paths are WORKSPACE-RELATIVE (the Kernel
    diagnostic rejects absolute paths, SCOPE-001/PORT-001). The absolute
    resolution against the run's workspace is adapter glue; the diagnostic
    receives the raw relative path so its own validation applies."""
    request = ctx.get("request") or {}
    shared = request.get("shared_config") or request.get("shared_config_path")
    if not shared:
        raise GateError(GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("init:shared-config",),
            reason="init requires a shared configuration path",
            recovery="Provide a workspace-relative shared_config in the run "
                     "request",
        ))
    shared_rel = Path(str(shared))
    shared_abs = (
        shared_rel if shared_rel.is_absolute()
        else cfg.workspace_root / shared_rel
    )
    local = request.get("local_config") or request.get("local_config_path")
    local_rel = Path(str(local)) if local else None
    local_abs = None
    if local_rel is not None:
        local_abs = (
            local_rel if local_rel.is_absolute()
            else cfg.workspace_root / local_rel
        )
    return {
        **dict(ctx),
        "shared_config_path": str(shared),
        "shared_config_abs": str(shared_abs),
        "local_config_path": str(local) if local else None,
        "local_config_abs": str(local_abs) if local_abs is not None else None,
        "workspace_root": request.get("workspace_root")
        or str(cfg.workspace_root),
    }


def _init_shared_config(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    from chatbi_governance.config import load_effective_config

    load_effective_config(
        Path(ctx["shared_config_abs"]),
        Path(ctx["local_config_abs"]) if ctx.get("local_config_abs") else None,
    )  # GateError on invalid shared config -> fail-closed
    return {**dict(ctx), "shared_config_valid": True}


def _init_domain_contract(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    from chatbi_governance.gates import validate_domain_contract

    decision = validate_domain_contract(Path(ctx["workspace_root"]))
    if decision.status == "block":
        raise GateError(decision)
    return {**dict(ctx), "domain_contract": decision.to_dict()}


def _init_config_validation(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    from chatbi_governance.config import load_effective_config

    effective = load_effective_config(
        Path(ctx["shared_config_abs"]),
        Path(ctx["local_config_abs"]) if ctx.get("local_config_abs") else None,
    )
    return {**dict(ctx), "effective_config": effective}


def _init_path_resolution(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    from chatbi_governance.paths import resolve_path_reference

    effective = ctx.get("effective_config")
    if effective is None:
        return {
            **dict(ctx),
            "path_resolution": {
                "status": "block",
                "reason": "effective config is unavailable before path "
                          "resolution",
            },
        }
    request = ctx.get("request") or {}
    alias = request.get("path_alias", "workspace")
    target = request.get("path_target", ".")
    try:
        ref = resolve_path_reference(
            effective, alias=alias, target=target,
            workspace_root=cfg.workspace_root,
        )
    except GateError as error:
        # Record the resolution outcome WITHOUT raising: the authoritative
        # paths check lives inside run_init_diagnostic (which re-runs the
        # same kernel resolve loop); raising here would skip the diagnostic
        # assembly and lose the delivery-gate verdict (ADR-002).
        return {
            **dict(ctx),
            "path_resolution": {
                "status": "block",
                "decision": error.decision.to_dict(),
            },
        }
    return {
        **dict(ctx),
        "path_resolution": {"status": "pass"},
        "path_reference": ref.to_dict(),
    }


def _init_capability_probe(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    snapshot = _agno_capability_snapshot()
    return {**dict(ctx), "capability_probe": snapshot}


def _init_checks_assembly(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    snapshot = ctx.get("capability_probe")
    # MAJOR-1 fix (module 6 review): run_init_diagnostic expects the probe to
    # be CALLABLE (it invokes probe()); passing the CapabilitySnapshot object
    # raised TypeError and truncated the diagnostic at the capability check.
    # Wrap the snapshot: probe=lambda returns it unchanged. The workspace is
    # injected explicitly so the diagnostic targets the RUN's workspace, not
    # the server process cwd (module-6 additive kernel parameter).
    result = run_init_diagnostic(
        Path(ctx["shared_config_path"]),
        Path(ctx["local_config_path"]) if ctx.get("local_config_path") else None,
        probe=(lambda: snapshot) if snapshot is not None else None,
        workspace_root=cfg.workspace_root,
    )
    return {**dict(ctx), "diagnostic": result}


def _init_report(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    result = ctx.get("diagnostic")
    if result is None:
        raise GateError(GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("init:report",),
            reason="init diagnostic did not run; a report cannot be produced",
            recovery="Inspect the init chain and re-run",
        ))
    return {
        **dict(ctx),
        "report": {
            "status": result.status,
            "production_ready": result.production_ready,
            "checks": [check.to_dict() for check in result.checks],
            "recovery_actions": list(result.recovery_actions),
        },
    }


def _verdict_init(ctx: Mapping[str, Any], cfg: _GenericConfig) -> GateDecision:
    rule_ids = tuple(cfg.ir.gates.delivery.rule_ids) if (
        cfg.ir.gates and cfg.ir.gates.delivery
    ) else ("PORT-001", "SEC-003", "HOOK-004")
    report = ctx.get("report") or {}
    if report.get("status") == "BLOCKED":
        return GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=("evidence:init-report",),
            reason="init diagnostic reports blocking failures",
            recovery="; ".join(report.get("recovery_actions", []))
            or "Fix the blocked checks and re-run init",
        )
    return GateDecision.pass_(
        rule_ids=rule_ids,
        evidence_refs=("evidence:init-report",),
        reason=(
            "init diagnostic completed; production_ready stays False "
            "(no closed-loop production certification)"
        ),
        recovery="No action required",
    )


# ---------------------------------------------------------------------------
# chatbi-bootstrap
# ---------------------------------------------------------------------------


def _bootstrap_mysql_spec(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    spec = build_mysql_adapter_spec(
        request.get("host", ""),
        int(request.get("port", 0)),
        request.get("user", ""),
        database=request.get("database", ""),
        credential_env_name=request.get("credential_env_name"),
    )
    return {**dict(ctx), "adapter_spec": spec}


def _bootstrap_merge_local_config(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    merged = merge_local_config(
        request.get("local_config") if isinstance(request.get("local_config"), dict)
        else None,
        path_bindings=request.get("path_bindings"),
        cli_adapters=request.get("cli_adapters"),
    )
    return {**dict(ctx), "local_config": merged}


def _bootstrap_adapter_select(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    """CLI-adapter selection for the mysql introspection (registered wiring).

    The bootstrap IR references ``select_adapter`` declaratively; the actual
    Claude-side mechanism for CLI adapters is the kernel
    ``resolve_executable(argv0, cli_allowlist)`` (SEC-001/PORT-001/HOOK-004 —
    the operator confirms the allowlist of absolute executable paths). The
    Agno adapter wires the same kernel function; the confirmed allowlist
    comes from the run request (operator-confirmed at run time, never a
    machine path in the IR).
    """
    request = ctx.get("request") or {}
    allowlist = tuple(request.get("cli_allowlist", []) or ())
    exe = resolve_executable("mysql", allowlist)
    if exe is None:
        return {
            **dict(ctx),
            "adapter_choice": {
                "status": "stopped",
                "minimal_authorization": (
                    "cli_allowlist confirmation required "
                    "(SEC-001/PORT-001/HOOK-004)"
                ),
            },
            "stop": {
                "reason": "adapter_stopped",
                "message": (
                    "mysql CLI adapter is not confirmed on the allowlist; "
                    "resolve_executable failed (fail-closed)"
                ),
            },
        }
    return {
        **dict(ctx),
        "adapter_choice": {
            "status": "selected",
            "adapter_id": "cli_adapters.mysql",
            "argv0": "mysql",
            "realpath": str(exe),
        },
    }


def _bootstrap_source_inventory(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    path = ctx.get("inventory_path") or (
        str(cfg.workspace_root / "source_inventory.json")
    )
    inventory = read_source_inventory(Path(path))  # GateError -> fail-closed
    return {**dict(ctx), "inventory": inventory.to_dict()}


def _bootstrap_scaffold(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    inventory = ctx.get("inventory") or {}
    tables = inventory.get("tables", [])
    return {
        **dict(ctx),
        "scaffold": {
            "status": "planned",
            "source_db": (ctx.get("request") or {}).get("source_db", ""),
            "table_count": len(tables),
        },
    }


def _verdict_bootstrap(ctx: Mapping[str, Any], cfg: _GenericConfig) -> GateDecision:
    rule_ids = tuple(cfg.ir.gates.delivery.rule_ids) if (
        cfg.ir.gates and cfg.ir.gates.delivery
    ) else ("PORT-001", "SEC-003", "SEM-003")
    if not ctx.get("inventory") or not ctx.get("scaffold"):
        return GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=("evidence:bootstrap-inventory",),
            reason="bootstrap did not produce a validated source inventory",
            recovery="Fix the mysql introspection step and re-run bootstrap",
        )
    return GateDecision.pass_(
        rule_ids=rule_ids,
        evidence_refs=("evidence:bootstrap-inventory",),
        reason="bootstrap scaffold planned from a validated source inventory",
        recovery="No action required",
    )


# ---------------------------------------------------------------------------
# chatbi-build-from-requirement
# ---------------------------------------------------------------------------


def _bfr_parse_requirement(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    result = _agent_call(
        ctx, cfg, "parse_requirement",
        {"request": request, "requirement_text": request.get("requirement_text", "")},
    )
    return {**dict(ctx), "parsed": result}


def _bfr_clarify(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    result = _agent_call(
        ctx, cfg, "clarify",
        {"request": request, "requirement_text": request.get("requirement_text", "")},
    )
    return {
        **dict(ctx),
        "stop": {
            "reason": "clarify",
            "message": result.get("message", "Request fields are missing"),
        },
    }


def _bfr_src002_crosscheck(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    business = (
        cfg.config.get("business_codebases") if cfg.config is not None else None
    ) or {}
    if not business:
        ctx = {**dict(ctx), "crosscheck": {"vacuous": True}}
        return ctx
    result = _agent_call(
        ctx, cfg, "src002_crosscheck",
        {"request": ctx.get("request"), "codebases": business},
    )
    return {**dict(ctx), "crosscheck": result}


def _bfr_classify_src002(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    evidence = _crosscheck_evidence(ctx, cfg)
    route = classify_src002_finding(evidence)
    return {**dict(ctx), "src002_route": route.to_dict()}


def _crosscheck_evidence(ctx: Mapping[str, Any], cfg: _GenericConfig) -> CodebaseEvidence:
    """Glue the crosscheck step's output into the Kernel evidence type."""
    crosscheck = ctx.get("crosscheck") or {}
    if crosscheck.get("vacuous"):
        return CodebaseEvidence(
            component="codebase_reader",
            produced_at="",
            operation="stat",
            alias="",
            status="ok",
            content_sha256="v" * 64,
            rule_ids=("SRC-002",),
            reason="no business codebases configured; crosscheck is vacuous",
            recovery="No action required",
        )
    data = crosscheck.get("evidence") if isinstance(crosscheck, Mapping) else None
    if not isinstance(data, Mapping):
        raise GateError(GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("bfr:crosscheck-evidence",),
            reason="src002 crosscheck produced no CodebaseEvidence",
            recovery="Inspect the codebase crosscheck step and re-run",
        ))
    return CodebaseEvidence(
        component=str(data.get("component", "codebase_reader")),
        produced_at=str(data.get("produced_at", "")),
        operation=str(data.get("operation", "stat")),
        alias=str(data.get("alias", "")),
        status=str(data.get("status", "ok")),
        content_sha256=str(data.get("content_sha256", "")),
        rule_ids=tuple(data.get("rule_ids", ("SRC-002",))),
        payload=data.get("payload"),
        reason=str(data.get("reason", "")),
        recovery=str(data.get("recovery", "")),
    )


def _bfr_derive_plan(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    parsed = ctx.get("parsed") or {}
    models_spec = parsed.get("models") if isinstance(parsed, Mapping) else None
    if not isinstance(models_spec, list) or not models_spec:
        raise GateError(GateDecision.block(
            rule_ids=("REQ-001", "HOOK-004"),
            evidence_refs=("bfr:build-plan",),
            reason="requirement parse produced no build-plan models",
            recovery="Clarify the requirement so a layered plan can be derived",
        ))
    entries = []
    for spec in models_spec:
        if not isinstance(spec, Mapping):
            raise GateError(GateDecision.block(
                rule_ids=("REQ-001", "HOOK-004"),
                evidence_refs=("bfr:build-plan",),
                reason="malformed model entry in the parsed plan",
                recovery="Re-parse the requirement",
            ))
        entries.append(build_model_entry(
            name=str(spec.get("name", "")),
            layer=str(spec.get("layer", "")),
            change_kind=str(spec.get("change_kind", "create")),
            created_rev=cfg.harness_release,
            owner=str(spec.get("owner", request.get("actor", "operator"))),
            upstream_deps=tuple(spec.get("upstream_deps", ())),
            join_or_aggregate_summary=str(
                spec.get("join_or_aggregate_summary", "")),
        ))
    plan = BuildPlan(
        schema_version=1,
        session_id=str(request.get("session_id", "")),
        models=tuple(entries),
    )
    validate_build_plan(plan, layer_rules=(), known_models=frozenset())
    return {**dict(ctx), "build_plan": plan.to_dict()}


def _verdict_bfr(ctx: Mapping[str, Any], cfg: _GenericConfig) -> GateDecision:
    rule_ids = tuple(cfg.ir.gates.delivery.rule_ids) if (
        cfg.ir.gates and cfg.ir.gates.delivery
    ) else ("SRC-002", "SEM-003", "REQ-001", "REQ-002")
    route = ctx.get("src002_route") or {}
    # SRC-002 route A = "STOP: ask domain owner for clarification
    # (REQ-001/002)" — the kernel route class is the deterministic signal
    # (the target string carries the prose, not a stable "owner" token).
    # MEDIUM-2 fix (module 6 review): route A must BLOCK even when a plan
    # was derived — the owner adjudication precedes the build chain.
    if route.get("route_class") == "A":
        return GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=("evidence:bfr-route",),
            reason=(
                "SRC-002 route A: the finding requires domain-owner "
                "adjudication before the build chain may proceed (REQ-001/002)"
            ),
            recovery="Ask the domain owner for clarification",
        )
    if not ctx.get("build_plan"):
        return GateDecision.block(
            rule_ids=("REQ-001", "REQ-002"),
            evidence_refs=("evidence:bfr-plan",),
            reason="no validated build plan was derived",
            recovery="Clarify the requirement and re-run",
        )
    return GateDecision.pass_(
        rule_ids=rule_ids,
        evidence_refs=("evidence:bfr-plan",),
        reason=f"build chain ready (SRC-002 route {route.get('route_class', 'F')})",
        recovery="No action required",
    )


# ---------------------------------------------------------------------------
# chatbi-maintain-model
# ---------------------------------------------------------------------------


def _mm_parse_request(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    required = ("change_kind", "target", "evidence_state")
    missing = [key for key in required if not request.get(key)]
    if missing:
        raise GateError(GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("maintain-model:request",),
            reason=f"maintain-model request missing fields: {missing}",
            recovery="Provide change_kind / target / evidence_state",
        ))
    return {**dict(ctx), "request": dict(request)}


def _mm_impact_manifest(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    assets = []
    for spec in request.get("affected_assets", []) or []:
        if isinstance(spec, Mapping):
            assets.append(AffectedAsset(
                asset_kind=str(spec.get("asset_kind", "")),
                asset_ref=str(spec.get("asset_ref", "")),
                change_required=bool(spec.get("change_required", False)),
                synced=bool(spec.get("synced", False)),
            ))
    manifest = build_impact_manifest(
        run_id=_run_id(ctx) or "run",
        change_kind=str(request.get("change_kind", "")),
        target=str(request.get("target", "")),
        affected_assets=assets,
        evidence_state=str(request.get("evidence_state", "")),
        p0_eval_failed=bool(request.get("p0_eval_failed", False)),
        protected_action=bool(request.get("protected", False)),
        candidate_payload=request.get("candidate_payload"),
        created_rev=cfg.harness_release,
    )
    return {**dict(ctx), "impact": manifest.to_dict(),
            "impact_blocking": manifest.has_blocking_drift(),
            "impact_reasons": list(manifest.blocking_reasons())}


def _mm_sync_gate(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    if ctx.get("impact_blocking"):
        return {**dict(ctx), "sync": {
            "status": "block",
            "rule_ids": ("DOC-004",),
            "reasons": list(ctx.get("impact_reasons", [])),
        }}
    return {**dict(ctx), "sync": {"status": "pass", "rule_ids": ("DOC-004",)}}


def _mm_registry_append(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    sync = ctx.get("sync") or {}
    if sync.get("status") != "pass":
        # DOC-004: a failed-sync model is NOT recorded (fail-closed).
        return {**dict(ctx), "registry_appended": False}
    request = ctx.get("request") or {}
    entry = build_model_entry(
        name=str(request.get("target", "")),
        layer=str(request.get("layer", "dwd")),
        change_kind=str(request.get("change_kind", "create")),
        created_rev=cfg.harness_release,
        owner=str(request.get("actor", "operator")),
        upstream_deps=tuple(request.get("upstream_deps", ()) or ()),
        join_or_aggregate_summary=str(request.get("summary", "")),
    )
    from chatbi_governance.build_plan import append_model_registry

    registry_path = cfg.workspace_root / ".chatbi" / "model_registry.json"
    append_model_registry(registry_path, entry)
    return {**dict(ctx), "registry_appended": True,
            "registry_path": str(registry_path)}


def _mm_protected_check(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    action = str(request.get("action_type", "approve_metric"))
    decision = decide(
        cfg.config,
        PolicyRequest(
            request_type=action,
            target_entity=str(request.get("target", "")),
            actor=str(request.get("actor", "operator")),
            purpose=str(request.get("purpose", "")),
        ),
    )
    if decision.status == "block":
        raise GateError(decision)
    ctx = {**dict(ctx), "policy_decision": decision.to_dict()}
    return {**dict(ctx),
            "protected_pending": decision.rule_ids == ("SEM-003", "SEC-001")}


def _verdict_mm(ctx: Mapping[str, Any], cfg: _GenericConfig) -> GateDecision:
    rule_ids = tuple(cfg.ir.gates.delivery.rule_ids) if (
        cfg.ir.gates and cfg.ir.gates.delivery
    ) else ("DOC-001", "DOC-004", "SEM-003", "REV-003")
    sync = ctx.get("sync") or {}
    if sync.get("status") != "pass":
        reasons = list(sync.get("reasons", []) or [])
        only_protected = reasons == ["protected action requires human approval"]
        if only_protected and ctx.get("approval_resolved"):
            # Post-approval re-assessment (SEM-003): the protected-action
            # blocker cleared after the human-owner approval passed Kernel
            # re-verification; DOC-004 re-reads as passed.
            return GateDecision.pass_(
                rule_ids=rule_ids,
                evidence_refs=("evidence:maintain-model-sync",
                               "evidence:approval-resolved"),
                reason=(
                    "DOC-004 sync gate passed after human-owner approval "
                    "resolved the protected-action blocker (SEM-003)"
                ),
                recovery="No action required",
            )
        return GateDecision.block(
            rule_ids=("DOC-004",),
            evidence_refs=("evidence:maintain-model-sync",),
            reason="DOC-004 full-sync gate not passed: "
                   + "; ".join(reasons or ["affected assets unsynced"]),
            recovery="Sync every affected asset with sufficient evidence and "
                     "no P0 evaluation failure, then re-run",
        )
    return GateDecision.pass_(
        rule_ids=rule_ids,
        evidence_refs=("evidence:maintain-model-sync",),
        reason="impact assessed, DOC-004 sync gate passed, registry updated",
        recovery="No action required",
    )


# ---------------------------------------------------------------------------
# chatbi-maintain-knowledge
# ---------------------------------------------------------------------------


def _mk_parse_request(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    if not request.get("reference_text") and not request.get("reference_path"):
        raise GateError(GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("maintain-knowledge:request",),
            reason="maintain-knowledge requires reference_text or "
                   "reference_path",
            recovery="Provide the governed reference content or its "
                     "workspace-relative path",
        ))
    return {**dict(ctx), "request": dict(request)}


def _mk_lint(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    text = str(request.get("reference_text", ""))
    issues = lint_reference(text)
    return {**dict(ctx), "lint_issues": [
        {"category": issue.category, "field": issue.field,
         "message": issue.message}
        for issue in issues
    ]}


def _mk_route(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    issues = ctx.get("lint_issues") or []
    downstream = (ctx.get("request") or {}).get("downstream_model_impact")
    return {
        **dict(ctx),
        "route": {
            "ready": not issues,
            "downstream_model_impact": bool(downstream),
            "handoff": "chatbi-maintain-model" if downstream else None,
        },
    }


def _verdict_mk(ctx: Mapping[str, Any], cfg: _GenericConfig) -> GateDecision:
    rule_ids = tuple(cfg.ir.gates.delivery.rule_ids) if (
        cfg.ir.gates and cfg.ir.gates.delivery
    ) else ("DOC-001", "DOC-002", "DOC-003")
    issues = ctx.get("lint_issues") or []
    if issues:
        return GateDecision.block(
            rule_ids=("DOC-002", "DOC-003"),
            evidence_refs=("evidence:maintain-knowledge-lint",),
            reason="reference lint found issues: "
                   + "; ".join(f"{i['field']}: {i['message']}" for i in issues[:3]),
            recovery="Resolve the lint issues via the governed reference "
                     "authoring flow, then re-run",
        )
    return GateDecision.pass_(
        rule_ids=rule_ids,
        evidence_refs=("evidence:maintain-knowledge-lint",),
        reason="reference lint clean; route ready",
        recovery="No action required",
    )


# ---------------------------------------------------------------------------
# chatbi-evaluate
# ---------------------------------------------------------------------------


def _ev_parse_request(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    if not request.get("answers"):
        raise GateError(GateDecision.block(
            rule_ids=("EVAL-001", "HOOK-004"),
            evidence_refs=("evaluate:answers",),
            reason="evaluation requires isolated ground-truth answers",
            recovery="Provide the owner-isolated ground-truth answers",
        ))
    return {**dict(ctx), "request": dict(request)}


def _ev_ground_truth_load(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    vault = GroundTruthVault(dict(request.get("answers", {})))
    return {**dict(ctx), "vault": vault}


def _ev_record(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    vault = ctx.get("vault")
    actuals = ctx.get("actuals") or {}
    run = build_evaluation_run(
        run_id=_run_id(ctx) or "run",
        skill_version=str(request.get("skill_version", "chatbi-evaluation@1")),
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
    return {**dict(ctx), "evaluation_run": run.to_dict(),
            "evaluation_passed": run.all_passed}


def _ev_release_gate(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    run = ctx.get("evaluation_run")
    if not run:
        raise GateError(GateDecision.block(
            rule_ids=("EVAL-003", "HOOK-004"),
            evidence_refs=("evaluate:record",),
            reason="no evaluation run was recorded before the release gate",
            recovery="Re-run the evaluation suite",
        ))
    from chatbi_governance.evaluator import validate_evaluation

    validate_evaluation(run)  # GateError -> fail-closed (EVAL-004)
    return {**dict(ctx), "release": {"status": "pass"}}



def _ev_report(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    run = ctx.get("evaluation_run") or {}
    return {
        **dict(ctx),
        "report": {
            "passed": run.get("passed_count"),
            "total": run.get("total_count"),
            "all_passed": ctx.get("evaluation_passed"),
            "fbk_003_statement": run.get("fbk_003_statement", ""),
        },
    }


def _verdict_ev(ctx: Mapping[str, Any], cfg: _GenericConfig) -> GateDecision:
    rule_ids = tuple(cfg.ir.gates.delivery.rule_ids) if (
        cfg.ir.gates and cfg.ir.gates.delivery
    ) else ("EVAL-003", "EVAL-004", "FBK-003")
    if not ctx.get("evaluation_run") or ctx.get("release", {}).get("status") != "pass":
        return GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=("evidence:evaluate-release",),
            reason="evaluation release gate not passed",
            recovery="Meet the owner-confirmed release threshold and re-run",
        )
    return GateDecision.pass_(
        rule_ids=rule_ids,
        evidence_refs=("evidence:evaluate-release",),
        reason="evaluation recorded and release gate passed (FBK-003: "
               "evaluation is evidence, not a guarantee)",
        recovery="No action required",
    )


# ---------------------------------------------------------------------------
# chatbi-correction
# ---------------------------------------------------------------------------


def _co_parse_request(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    required = ("correction_id", "fix_kind", "fix_target")
    missing = [key for key in required if not request.get(key)]
    if missing:
        raise GateError(GateDecision.block(
            rule_ids=("FBK-002", "HOOK-004"),
            evidence_refs=("correction:request",),
            reason=f"correction request missing fields: {missing}",
            recovery="Provide correction_id / fix_kind / fix_target",
        ))
    request = dict(request)
    ctx = {**dict(ctx), "request": request}
    # IR when: owner.pending(approve_metric) — a correction touching a
    # canonical metric definition needs human approval (SEM-003). The kernel
    # policy decides protectedness, IDENTICALLY to maintain-model's
    # protected_check step (policy.decide with the request's action_type;
    # the coordinator re-verifies at approval request time). A client
    # "protected" flag is NOT consulted — the kernel signal is the only
    # gate (registered wiring decision, feature-flow-mod6-v1.md §3.4).
    action = str(request.get("action_type", "approve_metric"))
    return {
        **ctx,
        "protected_pending": _protected_pending(
            cfg, ctx, action, str(request.get("actor", "operator"))),
    }


def _co_build_correction(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    record = build_correction_record(
        correction_id=str(request.get("correction_id", "")),
        fix_kind=str(request.get("fix_kind", "")),
        fix_target=str(request.get("fix_target", "")),
        fix_change_summary=str(request.get("fix_change_summary", "")),
        eval_case_assertion_id=str(request.get("eval_case_assertion_id", "")),
        eval_case_expected_hash=str(request.get("eval_case_expected_hash", "")),
        rule_ids=tuple(request.get("rule_ids", ("FBK-001", "FBK-002"))),
        owner_approved=False,
        description=str(request.get("description", "")),
    )
    return {
        **dict(ctx),
        "correction": record,
        # ABL-001: one evaluated component changed at a time (single fix target).
        "ablation": {"component": "single", "target": str(request.get("fix_target", ""))},
    }


def _co_record(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    record = ctx.get("correction")
    if record is None:
        raise GateError(GateDecision.block(
            rule_ids=("FBK-002", "HOOK-004"),
            evidence_refs=("correction:record",),
            reason="no correction record was built before record",
            recovery="Re-run the correction flow",
        ))
    validate_correction(record)  # GateError -> fail-closed
    return {**dict(ctx), "correction_validated": True}


def _verdict_co(ctx: Mapping[str, Any], cfg: _GenericConfig) -> GateDecision:
    rule_ids = tuple(cfg.ir.gates.delivery.rule_ids) if (
        cfg.ir.gates and cfg.ir.gates.delivery
    ) else ("FBK-001", "FBK-002", "FBK-003", "ABL-001")
    if not ctx.get("correction_validated"):
        return GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=("evidence:correction-record",),
            reason="correction record was not schema-validated",
            recovery="Re-run the correction flow",
        )
    return GateDecision.pass_(
        rule_ids=rule_ids,
        evidence_refs=("evidence:correction-record",),
        reason="dual-candidate correction record validated (FBK-002; "
               "owner approval gates any merge, SEM-003)",
        recovery="No action required",
    )


# ---------------------------------------------------------------------------
# chatbi-audit-drift
# ---------------------------------------------------------------------------


def _ad_args(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    request = ctx.get("request") or {}
    scope = str(request.get("scope", "all"))
    if scope not in ("references", "sources", "models", "all"):
        raise GateError(GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("audit-drift:scope",),
            reason=f"unknown drift scope: {scope!r}",
            recovery="Use one of references|sources|models|all",
        ))
    return {**dict(ctx), "scope": scope,
            "since": request.get("since")}


def _ad_inventory_baseline(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    # Canonical baseline location (kernel convention, bootstrap.py:882):
    # .chatbi/bootstrap/source_inventory.json.
    path = ctx.get("inventory_path") or (
        str(cfg.workspace_root / ".chatbi" / "bootstrap" / "source_inventory.json")
    )
    try:
        inventory = read_source_inventory(Path(path))
    except GateError as error:
        # IR: "Missing baseline is a hard STOP (class-2 precondition;
        # bootstrap not run)."
        return {
            **dict(ctx),
            "stop": {
                "reason": "baseline_missing",
                "message": error.decision.reason,
            },
        }
    return {**dict(ctx), "baseline": inventory.to_dict()}


def _ad_detect(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    baseline = ctx.get("baseline")
    if baseline is None:
        return dict(ctx)
    # The fresh source inventory (live introspection result, OD3) is supplied
    # by the deployer at run time (request field) — the adapter never fakes
    # live data; absent -> class 2 unavailable (kernel semantics).
    request = ctx.get("request") or {}
    fresh = request.get("fresh_inventory") if isinstance(request, Mapping) else None
    fresh_obj = _inventory_from_dict(fresh) if isinstance(fresh, Mapping) else None
    report = detect_drift(
        cfg.workspace_root,
        cfg.config,
        scope=str(ctx.get("scope", "all")),
        since=ctx.get("since"),
        fresh_source_inventory=fresh_obj,
    )
    # DriftReport carries candidates per class; flatten in class order.
    candidates = [
        candidate
        for class_candidates in report.classes.values()
        for candidate in class_candidates
    ]
    return {**dict(ctx), "drift_report": report.to_dict(),
            "drift_candidates": [
                candidate.to_dict() for candidate in candidates
            ]}


def _inventory_from_dict(data: Mapping[str, Any]) -> Any:
    """Reconstruct the kernel SourceInventory from a dict for detect_drift.

    Adapter glue only; SourceInventory.__post_init__ re-validates every field
    (fail-closed on tampered baseline).
    """
    from chatbi_governance.bootstrap import SourceInventory, SourceTable, SourceColumn

    tables = tuple(
        SourceTable(
            name=str(table.get("name", "")),
            columns=tuple(
                SourceColumn(
                    name=str(column.get("name", "")),
                    data_type=str(column.get("data_type", "")),
                    is_primary_key=bool(column.get("is_primary_key", False)),
                )
                for column in table.get("columns", [])
            ),
        )
        for table in data.get("tables", [])
    )
    return SourceInventory(
        source_database=str(data.get("source_database", "")),
        tables=tables,
    )


def _ad_classify(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    candidates = ctx.get("drift_candidates") or []
    routes = []
    triage = False
    for spec in candidates:
        if not isinstance(spec, Mapping):
            continue
        candidate = DriftCandidate(
            kind=str(spec.get("kind", "")),
            status=str(spec.get("status", "")),
            rule_ids=tuple(spec.get("rule_ids", ())),
            evidence_ref=str(spec.get("evidence_ref", "")),
            reason=str(spec.get("reason", "")),
            recovery=str(spec.get("recovery", "")),
            details=dict(spec.get("details", {})),
        )
        decision = classify_finding(candidate)
        routes.append(decision.to_dict())
        # IR route table: drift_route_triage -> owner (STOP for human
        # triage); the kernel TRIAGE target is "STOP human triage".
        if decision.target_command in ("owner", "STOP human triage"):
            triage = True
    ctx = {**dict(ctx), "routes": routes}
    if triage:
        # IR route table: drift_route_triage -> owner (STOP for human triage).
        ctx = {**ctx, "stop": {
            "reason": "triage",
            "message": "drift findings require human triage (route TRIAGE)",
        }}
    return ctx


def _ad_persist(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    report = ctx.get("drift_report")
    if report is None:
        return dict(ctx)
    path = write_state(
        cfg.workspace_root,
        str(ctx.get("chatbi_session_id") or "drift"),
        "drift_report.json",
        report,
    )
    return {**dict(ctx), "persisted": str(path)}


def _ad_report(ctx: Mapping[str, Any], cfg: _GenericConfig) -> dict[str, Any]:
    report = ctx.get("drift_report") or {}
    return {
        **dict(ctx),
        "report": {
            "status": report.get("status"),
            "scope": report.get("scope"),
            "candidate_count": len(ctx.get("drift_candidates") or []),
            "routes": ctx.get("routes") or [],
            "persisted": ctx.get("persisted"),
        },
    }


def _verdict_ad(ctx: Mapping[str, Any], cfg: _GenericConfig) -> GateDecision:
    rule_ids = tuple(cfg.ir.gates.delivery.rule_ids) if (
        cfg.ir.gates and cfg.ir.gates.delivery
    ) else ("HOOK-001", "PORT-001")
    if not ctx.get("drift_report"):
        return GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=("evidence:audit-drift-report",),
            reason="drift audit produced no report",
            recovery="Fix the drift detection chain and re-run",
        )
    return GateDecision.pass_(
        rule_ids=rule_ids,
        evidence_refs=("evidence:audit-drift-report",),
        reason="drift report produced and persisted; findings routed",
        recovery="No action required",
    )


# ---------------------------------------------------------------------------
# Dispatch tables (IR step id -> executor)
# ---------------------------------------------------------------------------

_DETERMINISTIC_DISPATCH: dict[str, dict[str, Callable[[Mapping[str, Any], _GenericConfig], dict[str, Any]]]] = {
    "chatbi-init": {
        "args_parse": _init_args_parse,
        "shared_config": _init_shared_config,
        "local_config": lambda ctx, cfg: {**dict(ctx), "local_config_valid": True},
        "domain_contract": _init_domain_contract,
        "config_validation": _init_config_validation,
        "path_resolution": _init_path_resolution,
        "checks_assembly": _init_checks_assembly,
        "report": _init_report,
    },
    "chatbi-bootstrap": {
        "mysql_spec": _bootstrap_mysql_spec,
        "merge_local_config": _bootstrap_merge_local_config,
        "adapter_select": _bootstrap_adapter_select,
        "source_inventory": _bootstrap_source_inventory,
        "scaffold": _bootstrap_scaffold,
    },
    "chatbi-build-from-requirement": {
        "classify_src002": _bfr_classify_src002,
        "derive_plan": _bfr_derive_plan,
    },
    "chatbi-maintain-model": {
        "parse_request": _mm_parse_request,
        "impact_manifest": _mm_impact_manifest,
        "sync_gate": _mm_sync_gate,
        "registry_append": _mm_registry_append,
        "protected_check": _mm_protected_check,
    },
    "chatbi-maintain-knowledge": {
        "parse_request": _mk_parse_request,
        "lint": _mk_lint,
        "route": _mk_route,
    },
    "chatbi-evaluate": {
        "parse_request": _ev_parse_request,
        "ground_truth_load": _ev_ground_truth_load,
        "record": _ev_record,
        "release_gate": _ev_release_gate,
        "report": _ev_report,
    },
    "chatbi-correction": {
        "parse_request": _co_parse_request,
        "build_correction": _co_build_correction,
        "record": _co_record,
    },
    "chatbi-audit-drift": {
        "args": _ad_args,
        "inventory_baseline": _ad_inventory_baseline,
        "detect": _ad_detect,
        "classify": _ad_classify,
        "report": _ad_report,
    },
}

_AGENT_DISPATCH: dict[str, set[str]] = {
    "chatbi-build-from-requirement": {"parse_requirement", "clarify",
                                      "src002_crosscheck"},
}

#: Built-in runtime_native implementations (deterministic; no external seam).
_NATIVE_BUILTINS: dict[str, dict[str, Callable[[Mapping[str, Any], _GenericConfig], dict[str, Any]]]] = {
    "chatbi-init": {"capability_probe": _init_capability_probe},
    "chatbi-audit-drift": {"persist": _ad_persist},
}

#: Workflows whose runtime_native steps MUST be injected (fail-closed without).
_NATIVE_INJECTABLE = {
    "chatbi-bootstrap": {"run_mysql"},
    "chatbi-build-from-requirement": {"chain_maintain_model", "handoff_analyze"},
    "chatbi-maintain-knowledge": {"codebase_crosscheck"},
    "chatbi-evaluate": {"run_suite"},
}

_VERDICT_DISPATCH: dict[str, Callable[[Mapping[str, Any], _GenericConfig], GateDecision]] = {
    "chatbi-init": _verdict_init,
    "chatbi-bootstrap": _verdict_bootstrap,
    "chatbi-build-from-requirement": _verdict_bfr,
    "chatbi-maintain-model": _verdict_mm,
    "chatbi-maintain-knowledge": _verdict_mk,
    "chatbi-evaluate": _verdict_ev,
    "chatbi-correction": _verdict_co,
    "chatbi-audit-drift": _verdict_ad,
}

#: Workflows declaring a human_approval step in the IR: the registered step id.
_APPROVAL_STEP_IDS: dict[str, str] = {
    "chatbi-maintain-model": "owner_approval",
    "chatbi-correction": "owner_approval",
}


def workflow_approval_action(workflow_id: str, ir: Any) -> str | None:
    """The protected action of the IR ``owner.pending(<action>)`` condition on
    the workflow's human_approval step (None when the workflow declares none)."""
    step_id = _APPROVAL_STEP_IDS.get(workflow_id)
    if step_id is None:
        return None
    step = next((s for s in ir.steps if s.id == step_id), None)
    if step is None or not step.when:
        return None
    try:
        cond = parse_condition(step.when)
    except ConditionSyntaxError:
        return None
    if cond.kind == "owner_pending" and cond.args:
        return cond.args[0]
    return None


def _agno_capability_snapshot() -> CapabilitySnapshot:
    """Agno-target capability snapshot for ``run_init_diagnostic`` injection.

    The Claude-target probe is replaced by the Agno capability probe: honest
    detection (FBK-003) — claude_available stays False (this runtime is not
    Claude Code), the runtime checks report the agno runtime state.
    """
    from .probe import probe_agno

    manifest = probe_agno()
    runtime_ok = manifest.runtime_version != "unavailable"
    # The Kernel's CapabilitySnapshot contract is Claude-shaped (module 2 kept
    # the diagnostic vocabulary stable): doctor_status must be one of the five
    # Claude doctor states and available_adapters uses the managed/cli/fixture
    # id grammar. The honest Agno projection: claude_available=False and the
    # runtime checks report unavailable when agno is not importable.
    return CapabilitySnapshot(
        claude_available=False,
        claude_version=None,
        doctor_status="unavailable" if not runtime_ok else "pass",
        logged_in=None,
        sandbox_available=None,
        available_adapters=(),
        evidence_source="synthetic",
    )


# ---------------------------------------------------------------------------
# Step executors (uniform wrappers)
# ---------------------------------------------------------------------------


def _jsonable_ctx(ctx: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe context snapshot for the persisted session state.

    Step artifacts that are not JSON-serializable (GroundTruthVault, Kernel
    objects) are replaced by a marker dict — the persisted session state is
    product state, not governance evidence (invariant 3), and a pause/continue
    re-seeds the ctx from the in-memory controller copy anyway.
    """

    def _convert(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): _convert(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_convert(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return {"__nonjson__": type(value).__name__}

    return {str(key): _convert(value) for key, value in ctx.items()}


def _live_agent_runner(main_agent: Any) -> Callable[[str, Mapping[str, Any]], Mapping[str, Any]]:
    """Live agent seam shared with workflow_analyze (JSON in/out contract)."""
    from . import ensure_agno_unshadowed

    def _run(
        step_id: str, payload: Mapping[str, Any], tool_policy: Any = None,
    ) -> dict[str, Any]:
        if main_agent is None:
            raise RuntimeError("main agent unavailable (fail-closed)")
        agent = main_agent
        if tool_policy is not None:
            from .tools import filter_agent_tools

            allowed = filter_agent_tools(list(main_agent.tools or []), tool_policy)
            if len(allowed) != len(list(main_agent.tools or [])):
                ensure_agno_unshadowed()
                from agno.agent import Agent as _Agent

                agent = _Agent(
                    id=main_agent.id,
                    name=main_agent.name,
                    model=main_agent.model,
                    tools=allowed,
                    session_id=main_agent.session_id,
                    markdown=False,
                )
        response = agent.run(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        )
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("main agent returned no content (fail-closed)")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError("main agent output is not JSON (fail-closed)") from error
        if not isinstance(parsed, Mapping):
            raise RuntimeError("main agent output is not a JSON object")
        return parsed

    return _run


def _executor_deterministic(step_id: str) -> Callable[..., Any]:
    def _exec(step_input: Any, cfg: _GenericConfig, session_state: Any = None) -> Any:
        ctx = _generic_ctx(step_input, cfg, session_state)
        if not _eval_when(cfg.when_conditions.get(step_id), ctx):
            return ctx
        fn = _DETERMINISTIC_DISPATCH[cfg.workflow_id].get(step_id)
        if fn is None:
            raise ValueError(
                f"IR step {cfg.workflow_id}/{step_id} has no agno wiring "
                "(refusing to skip silently)"
            )
        return fn(ctx, cfg)

    return _exec


def _executor_agent(step_id: str) -> Callable[..., Any]:
    def _exec(step_input: Any, cfg: _GenericConfig, session_state: Any = None) -> Any:
        ctx = _generic_ctx(step_input, cfg, session_state)
        if not _eval_when(cfg.when_conditions.get(step_id), ctx):
            return ctx
        if step_id == "clarify":
            return _bfr_clarify(ctx, cfg)
        if step_id == "parse_requirement":
            return _bfr_parse_requirement(ctx, cfg)
        if step_id == "src002_crosscheck":
            return _bfr_src002_crosscheck(ctx, cfg)
        raise ValueError(
            f"agent step {cfg.workflow_id}/{step_id} has no agno wiring"
        )

    return _exec


def _executor_native(step_id: str) -> Callable[..., Any]:
    def _exec(step_input: Any, cfg: _GenericConfig, session_state: Any = None) -> Any:
        ctx = _generic_ctx(step_input, cfg, session_state)
        builtin = _NATIVE_BUILTINS.get(cfg.workflow_id, {}).get(step_id)
        if builtin is not None:
            return builtin(ctx, cfg)
        if cfg.native_runner is None:
            raise RuntimeError(
                f"native step {cfg.workflow_id}/{step_id} requires a wired "
                "native_runner on this deployment; refusing to run without "
                "one (fail-closed, FBK-003)"
            )
        result = cfg.native_runner(cfg.workflow_id, step_id, dict(ctx))
        if not isinstance(result, Mapping):
            raise RuntimeError(
                f"native runner for {cfg.workflow_id}/{step_id} returned a "
                "non-object result (fail-closed)"
            )
        return {**dict(ctx), **result}

    return _exec


def _executor_delivery_gate(step_input: Any, cfg: _GenericConfig, session_state: Any = None) -> Any:
    ctx = _generic_ctx(step_input, cfg, session_state)
    if ctx.get("stop"):
        return ctx
    verdict = _VERDICT_DISPATCH[cfg.workflow_id](ctx, cfg)
    return {**dict(ctx), "delivery": verdict.to_dict()}


def _executor_approval_gate(step_id: str, action_type: str) -> Callable[..., Any]:
    """The human_approval step executor (runs only AFTER Kernel re-verification
    PASS and the controller confirms the requirement — 先验后续)."""

    def _exec(step_input: Any, cfg: _GenericConfig, session_state: Any = None) -> Any:
        ctx = _generic_ctx(step_input, cfg, session_state)
        # Reaching this step is the proof the approval resolved (先验后续):
        # mark it so post-approval re-assessments (DOC-004 sync gate) clear
        # the protected-action blocker the same way the Claude command
        # re-assesses after owner approval (SEM-003 flow).
        return {
            **dict(ctx),
            "approval_resolved": True,
            "protected_pending": False,
        }

    return _exec


# ---------------------------------------------------------------------------
# Workflow construction
# ---------------------------------------------------------------------------


def build_generic_workflow(
    workflow_id: str,
    *,
    workflows_dir: str | Path,
    config: Any,
    agent_runner: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    native_runner: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    on_evidence: Callable[[str, str, Any], None] | None = None,
    on_tool: Callable[[str, str, str, bool], None] | None = None,
    on_ctx: Callable[[str, Mapping[str, Any]], None] | None = None,
    harness_release: str = "dev",
    db: Any = None,
    deployment: Any = None,
    workspace_root: str | Path | None = None,
    harness_config_path: str | Path | None = None,
    local_config_path: str | Path | None = None,
    main_agent: Any = None,
) -> Any:
    """Build ONE of the eight generic workflows from its IR at runtime."""
    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.workflow import Step, Workflow

    from chatbi_harness_ir.loader import load_workflow

    if workflow_id not in WORKFLOW_IDS:
        raise ValueError(
            f"{workflow_id!r} is not a generic workflow id; use "
            "workflow_analyze.build_analyze_workflow for chatbi-analyze"
        )
    ir = load_workflow(Path(workflows_dir) / f"{workflow_id}.yaml")
    if ir.workflow_id != workflow_id:
        raise ValueError(f"IR workflow {ir.workflow_id!r} != expected {workflow_id!r}")

    known = set(_DETERMINISTIC_DISPATCH.get(workflow_id, {}))
    known |= _AGENT_DISPATCH.get(workflow_id, set())
    known |= set(_NATIVE_BUILTINS.get(workflow_id, {}))
    known |= set(_NATIVE_INJECTABLE.get(workflow_id, {}))
    if workflow_id in _APPROVAL_STEP_IDS:
        known.add(_APPROVAL_STEP_IDS[workflow_id])
    unknown = [step.id for step in ir.steps if step.id not in known]
    if unknown:
        raise ValueError(
            f"IR declares steps the agno workflow cannot map: {unknown}"
        )

    if agent_runner is None:
        agent_runner = _live_agent_runner(main_agent)

    cfg = _GenericConfig(
        workflow_id=workflow_id,
        config=config,
        agent_runner=agent_runner,
        native_runner=native_runner,
        on_evidence=on_evidence,
        on_tool=on_tool,
        on_ctx=on_ctx or (lambda run_id, ctx: None),
        harness_release=harness_release,
        workspace_root=Path(workspace_root) if workspace_root else Path.cwd(),
        harness_config_path=(
            Path(harness_config_path) if harness_config_path else None
        ),
        local_config_path=(
            Path(local_config_path) if local_config_path else None
        ),
        deployment=deployment,
        ir=ir,
    )
    # IR tool allowlist injection for agent steps (MAJOR-2 pattern shared).
    from .tools import StepToolPolicy

    cfg.tool_policies = {
        step.id: StepToolPolicy.from_ir_step(step, ir.tools)
        for step in ir.steps
        if step.executor.value == "agent_with_tools"
    }

    def _wrap(executor: Callable[..., Any]) -> Callable[..., Any]:
        """Report the context after every step (ADR-002 terminal derivation)."""

        def _executor_wrapper(step_input: Any, session_state: Any = None) -> Any:
            result = executor(step_input, cfg, session_state)
            if isinstance(result, Mapping):
                run_id = result.get("run_id") or ""
                if not run_id and isinstance(session_state, dict):
                    run_id = session_state.get("current_run_id") or ""
                if run_id:
                    # FULL in-memory context (step artifacts like the
                    # GroundTruthVault survive across steps in-process).
                    cfg.in_memory_ctx[run_id] = dict(result)
                if isinstance(session_state, dict):
                    # JSON-safe snapshot for the persisted session state:
                    # non-serializable step artifacts never leave the process.
                    session_state["_ctx"] = _jsonable_ctx(result)
                cfg.on_ctx(run_id, result)
            return result

        _executor_wrapper.__name__ = getattr(executor, "__name__", "step")
        return _executor_wrapper

    steps: list[Any] = []
    for step in ir.steps:
        if step.executor.value == "deterministic":
            steps.append(Step(
                executor=_wrap(_executor_deterministic(step.id)),
                step_id=step.id, name=step.id, on_error="fail",
            ))
        elif step.executor.value == "agent_with_tools":
            steps.append(Step(
                executor=_wrap(_executor_agent(step.id)),
                step_id=step.id, name=step.id, on_error="fail",
            ))
        elif step.executor.value == "runtime_native":
            steps.append(Step(
                executor=_wrap(_executor_native(step.id)),
                step_id=step.id, name=step.id, on_error="fail",
            ))
        elif step.executor.value == "human_approval":
            action = workflow_approval_action(workflow_id, ir)
            if action is None:
                raise ValueError(
                    f"human_approval step {workflow_id}/{step.id} has no "
                    "owner.pending(<action>) condition"
                )
            steps.append(Step(
                executor=_wrap(_executor_approval_gate(step.id, action)),
                step_id=step.id, name=step.id,
                description=(
                    f"ChatBI human approval required for protected action "
                    f"{action} (SEM-003); resolved by the superuser through "
                    "the ChatBI approval API with Kernel re-verification"
                ),
                requires_confirmation=True,
                confirmation_message=(
                    f"Protected action {action} requires human owner approval "
                    "(SEM-003). Resolve it at the ChatBI approval endpoint; "
                    "the run stays paused until the Kernel re-verification "
                    "passes."
                ),
                on_error="fail",
            ))
        elif step.executor.value == "independent_reviewer":
            raise ValueError(
                f"workflow {workflow_id} declares an independent_reviewer "
                "step; the module-6 registry has no reviewer wiring for it"
            )
        else:
            raise ValueError(f"unknown executor {step.executor.value!r}")

    # Appended adapter delivery-gate step (the IR declares the gate at
    # workflow level). Terminal authority stays the Kernel (ADR-002).
    steps.append(Step(
        executor=_wrap(_executor_delivery_gate),
        step_id="delivery_gate", name="delivery_gate",
        description=(
            "Adapter assembly step: derives the workflow delivery verdict "
            "from the Kernel outputs in the context (rule_ids from the IR "
            "gates.delivery declaration); the controller emits "
            "run.completed only after this gate PASS (ADR-002)"
        ),
        on_error="fail",
    ))

    return Workflow(
        id=workflow_id,
        name=ir.title,
        description=ir.description,
        steps=steps,
        db=db,
        store_events=True,
        stream_events=True,
    )


def build_all_workflows(
    *,
    workflows_dir: str | Path,
    config: Any,
    agent_runner: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_runner: Any = None,
    native_runner: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    on_evidence: Callable[[str, str, Any], None] | None = None,
    on_tool: Callable[[str, str, str, bool], None] | None = None,
    on_ctx: Callable[[str, Mapping[str, Any]], None] | None = None,
    harness_release: str = "dev",
    db: Any = None,
    deployment: Any = None,
    workspace_root: str | Path | None = None,
    harness_config_path: str | Path | None = None,
    local_config_path: str | Path | None = None,
    main_agent: Any = None,
    approval_action_type: str | None = None,
) -> list[Any]:
    """Build ALL NINE workflows (analyze + the eight generic ones)."""
    from .workflow_analyze import build_analyze_workflow

    analyze = build_analyze_workflow(
        workflows_dir=workflows_dir,
        config=config,
        agent_runner=agent_runner,
        reviewer_runner=reviewer_runner,
        on_evidence=on_evidence,
        on_tool=on_tool,
        on_ctx=on_ctx,
        harness_release=harness_release,
        db=db,
        approval_action_type=approval_action_type,
        main_agent=main_agent,
    )
    generic = [
        build_generic_workflow(
            workflow_id,
            workflows_dir=workflows_dir,
            config=config,
            agent_runner=agent_runner,
            native_runner=native_runner,
            on_evidence=on_evidence,
            on_tool=on_tool,
            on_ctx=on_ctx,
            harness_release=harness_release,
            db=db,
            deployment=deployment,
            workspace_root=workspace_root,
            harness_config_path=harness_config_path,
            local_config_path=local_config_path,
            main_agent=main_agent,
        )
        for workflow_id in WORKFLOW_IDS
    ]
    return [analyze, *generic]


#: All nine workflow ids the agno target implements after module 6.
ALL_WORKFLOW_IDS = ("chatbi-analyze",) + WORKFLOW_IDS
