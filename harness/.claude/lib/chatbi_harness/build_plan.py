"""Deterministic primitives for the ``/chatbi-build-from-requirement`` command.

A thin layer over existing :mod:`chatbi_harness.bootstrap` /
:mod:`chatbi_harness.gates` / :mod:`chatbi_harness.evidence` /
:mod:`chatbi_harness.impact` primitives. Mirrors :mod:`chatbi_harness.impact`
discipline: does NOT duplicate secret/path validation (delegates to
:func:`chatbi_harness.gates._sanitize_text`); raises :class:`GateError`
(HOOK-004) on validation violation, mirroring
:func:`chatbi_harness.bootstrap._bootstrap_gate_error` and
:func:`chatbi_harness.impact._impact_gate_error`. **Does NOT derive**
join/aggregate logic (agent reasoning); only reads + validates plan shape +
appends registry evidence.

Applicable rules: SCOPE-001, SEC-001/003, RAW-003, SEM-003, PORT-001,
DOC-002, META-003, HOOK-001/004.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .gates import GateDecision, GateError, _sanitize_text  # noqa: E402 (sanctioned reuse, mirrors impact.py:24)
from .bootstrap import (  # noqa: E402 (Q4: reader+merge live in bootstrap.py)
    SourceInventory,
    merge_source_inventories,
    read_source_inventory,
)
from .evidence import _get_schema, _validate_against_schema  # noqa: E402 (sanctioned reuse, mirrors impact.py:25-29)
from .impact import _CHANGE_KINDS  # noqa: E402 (reuse, not re-declare)


_LAYERS = frozenset({"ods", "dwd", "dws", "ads", "dim"})
_PROTECTED_ACTIONS = frozenset({
    "approve_metric", "change_access_policy", "production_publish",
    "destructive_migration",
})  # mirrors impact.py:39-42 + chatbi-harness.schema.json:44-49
_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")  # PORT-001; source: chatbi-harness.schema.json:36 (workspace.id)
_SCHEMA_VERSION = 1


def _build_plan_gate_error(
    *,
    rule_ids: tuple[str, ...],
    evidence_ref: str,
    reason: str,
    recovery: str,
) -> GateError:
    """Build a fail-closed ``GateError`` mirroring ``impact._impact_gate_error``."""
    return GateError(
        GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=(evidence_ref,),
            reason=reason,
            recovery=recovery,
        )
    )


@dataclass(frozen=True, slots=True)
class HumanApproval:
    """Extend-source human approval (Q1). Mirrors correction.owner_approved
    default-False (evaluator.py:222,253,226 'no auto-merge; SEM-003')."""

    approved: bool = False
    approver: str | None = None
    rule_ids: tuple[str, ...] = ()  # SCOPE-001/SEC-001/RAW-003 for extend-source

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "approver": self.approver,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True, slots=True)
class CrossLayerException:
    """Explicit cross-layer exception (Q2). Stays in plan metadata + registry;
    does NOT enter the blueprint (blueprint holds the declarative rule)."""

    reason: str
    approved_by: str

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "approved_by": self.approved_by}


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """One model in a build plan. ``name`` IS the logical-alias target
    (PORT-001); there is no separate ``target`` field."""

    name: str                              # logical alias (PORT-001); == target
    layer: str                             # ods|dwd|dws|ads|dim
    upstream_deps: tuple[str, ...]         # model names this depends on
    change_kind: str                       # impact.py:31-33 _CHANGE_KINDS
    created_rev: str
    owner: str
    cross_layer_exception: CrossLayerException | None = None  # Q2
    join_or_aggregate_summary: str = ""    # agent-derived; sanitized on persist (Q5)
    protected_action_flags: tuple[str, ...] = ()  # subset of _PROTECTED_ACTIONS (SEM-003)
    requires_human_approval: bool = False  # extend-source flag (not in enum)
    human_approval: HumanApproval = HumanApproval()  # Q1; default approved=False

    def to_dict(self) -> dict[str, Any]:
        cle = self.cross_layer_exception
        return {
            "name": self.name,
            "layer": self.layer,
            "upstream_deps": list(self.upstream_deps),
            "change_kind": self.change_kind,
            "created_rev": self.created_rev,
            "owner": self.owner,
            "cross_layer_exception": cle.to_dict() if cle is not None else None,
            "join_or_aggregate_summary": self.join_or_aggregate_summary,
            "protected_action_flags": list(self.protected_action_flags),
            "requires_human_approval": self.requires_human_approval,
            "human_approval": self.human_approval.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class LayerRule:
    """One layer + the set of layers it may depend on (Q6b). Parsed from
    blueprint ## Layers by the AGENT and passed in; the lib does NOT parse
    markdown (META-003/PORT-001)."""

    layer: str
    may_depend_on: frozenset[str]


@dataclass(frozen=True, slots=True)
class BuildPlan:
    """Ordered model build plan. Mirrors :class:`SourceInventory`
    (``bootstrap.py:217-250``): frozen-slots + ``to_dict()`` that produces the
    shape validated by ``build-plan.schema.json`` and persisted to
    ``.chatbi/runs/<sid>/build_plan.json``."""

    schema_version: int            # = 1
    session_id: str
    models: tuple[ModelEntry, ...] # ordered ODS->DWD->DWS->ADS; each carries human_approval (Q1)

    def to_dict(self) -> dict[str, Any]:
        # Persistence shape. Text fields are already sanitized at ModelEntry
        # construction (Q5); to_dict does not re-sanitize (mirrors
        # ImpactManifest.to_dict, impact.py:93-104).
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "models": [m.to_dict() for m in self.models],
        }


def build_model_entry(
    *,
    name: str,
    layer: str,
    change_kind: str,
    created_rev: str,
    owner: str,
    upstream_deps: Iterable[str] = (),
    cross_layer_exception: CrossLayerException | Mapping[str, Any] | None = None,
    join_or_aggregate_summary: str = "",
    protected_action_flags: Iterable[str] = (),
    requires_human_approval: bool = False,
    human_approval: HumanApproval | Mapping[str, Any] | None = None,
) -> ModelEntry:
    """Build a validated, sanitized :class:`ModelEntry`. Raises
    :class:`GateError` (fail-closed) on invalid aliases, unknown
    layers/change_kinds, bad protected_action_flags (SEM-003), empty-reason
    cross_layer_exception (Q2), or non-sanitizable text (Q5/SEC-003).

    Mirrors :func:`chatbi_harness.impact.build_impact_manifest` discipline:
    sanitize text fields BEFORE constructing (Q5), validate enums/aliases,
    then return the frozen dataclass.
    """
    # --- Sanitize + validate name (PORT-001) ---
    sanitized_name = _sanitize_text(name) if isinstance(name, str) else ""
    if not sanitized_name:
        raise _build_plan_gate_error(
            rule_ids=("PORT-001", "HOOK-004"),
            evidence_ref="build-plan:model:name",
            reason="name is required and must sanitize to a non-empty logical alias",
            recovery="Provide a logical alias matching ^[a-z][a-z0-9_-]{1,62}$",
        )
    if not _ALIAS.fullmatch(sanitized_name):
        raise _build_plan_gate_error(
            rule_ids=("PORT-001", "HOOK-004"),
            evidence_ref="build-plan:model:name",
            reason=f"name {sanitized_name!r} does not match the alias pattern",
            recovery="Use a logical alias matching ^[a-z][a-z0-9_-]{1,62}$",
        )
    # --- Validate layer ---
    if layer not in _LAYERS:
        raise _build_plan_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="build-plan:model:layer",
            reason=f"Unknown layer: {layer!r}",
            recovery=f"Use one of {sorted(_LAYERS)}",
        )
    # --- Validate change_kind ---
    if change_kind not in _CHANGE_KINDS:
        raise _build_plan_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="build-plan:model:change-kind",
            reason=f"Unknown change_kind: {change_kind!r}",
            recovery=f"Use one of {sorted(_CHANGE_KINDS)}",
        )
    # --- Validate created_rev (required, non-empty) ---
    if not isinstance(created_rev, str) or not created_rev:
        raise _build_plan_gate_error(
            rule_ids=("PORT-001", "HOOK-004"),
            evidence_ref="build-plan:model:created-rev",
            reason="created_rev is required and must be a non-empty string",
            recovery="Provide a non-empty revision identifier",
        )
    # --- Sanitize + validate owner ---
    sanitized_owner = _sanitize_text(owner) if isinstance(owner, str) else ""
    if not sanitized_owner:
        raise _build_plan_gate_error(
            rule_ids=("PORT-001", "HOOK-004"),
            evidence_ref="build-plan:model:owner",
            reason="owner is required and must sanitize cleanly",
            recovery="Provide a non-empty owner alias",
        )
    # --- Sanitize upstream_deps (model name aliases; Q5 defense-in-depth) ---
    deps_list: list[str] = []
    for dep in upstream_deps:
        sanitized_dep = _sanitize_text(dep) if isinstance(dep, str) else ""
        if not sanitized_dep:
            raise _build_plan_gate_error(
                rule_ids=("PORT-001", "HOOK-004"),
                evidence_ref="build-plan:model:upstream-deps",
                reason="upstream_dep must be a non-empty sanitized model name",
                recovery="Provide non-empty model name aliases",
            )
        deps_list.append(sanitized_dep)
    deps_tuple = tuple(deps_list)
    # --- Validate protected_action_flags (SEM-003) ---
    flags_list: list[str] = []
    for flag in protected_action_flags:
        if flag not in _PROTECTED_ACTIONS:
            raise _build_plan_gate_error(
                rule_ids=("SEM-003", "HOOK-004"),
                evidence_ref="build-plan:model:protected-action-flags",
                reason=f"Unknown protected_action_flag: {flag!r}",
                recovery=f"Use one of {sorted(_PROTECTED_ACTIONS)}",
            )
        flags_list.append(flag)
    flags_tuple = tuple(flags_list)
    # --- Sanitize join_or_aggregate_summary ---
    sanitized_summary = _sanitize_text(
        join_or_aggregate_summary
    ) if isinstance(join_or_aggregate_summary, str) else ""
    # --- cross_layer_exception (Q2) ---
    cle: CrossLayerException | None = None
    if cross_layer_exception is not None:
        if isinstance(cross_layer_exception, CrossLayerException):
            cle = cross_layer_exception
        elif isinstance(cross_layer_exception, Mapping):
            cle_reason_raw = cross_layer_exception.get("reason", "")
            cle_approver_raw = cross_layer_exception.get("approved_by", "")
            sanitized_reason = _sanitize_text(
                cle_reason_raw
            ) if isinstance(cle_reason_raw, str) else ""
            sanitized_approver = _sanitize_text(
                cle_approver_raw
            ) if isinstance(cle_approver_raw, str) else ""
            if not sanitized_reason:
                raise _build_plan_gate_error(
                    rule_ids=("DOC-002", "HOOK-004"),
                    evidence_ref="build-plan:model:cross-layer-exception:reason",
                    reason="cross_layer_exception reason is required and must sanitize cleanly",
                    recovery="Provide a non-empty reason for the cross-layer exception",
                )
            if not sanitized_approver:
                raise _build_plan_gate_error(
                    rule_ids=("DOC-002", "HOOK-004"),
                    evidence_ref="build-plan:model:cross-layer-exception:approved-by",
                    reason="cross_layer_exception approved_by is required and must sanitize cleanly",
                    recovery="Provide a non-empty approver for the cross-layer exception",
                )
            cle = CrossLayerException(
                reason=sanitized_reason, approved_by=sanitized_approver,
            )
        else:
            raise _build_plan_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref="build-plan:model:cross-layer-exception",
                reason="cross_layer_exception must be a CrossLayerException or a mapping",
                recovery="Provide a CrossLayerException or a dict with reason + approved_by",
            )
    # --- human_approval (Q1) ---
    if human_approval is None:
        ha = HumanApproval()
    elif isinstance(human_approval, HumanApproval):
        ha = human_approval
    elif isinstance(human_approval, Mapping):
        ha_approved = bool(human_approval.get("approved", False))
        ha_approver_raw = human_approval.get("approver")
        ha_approver = (
            _sanitize_text(ha_approver_raw)
            if isinstance(ha_approver_raw, str) and ha_approver_raw
            else None
        )
        ha_rule_ids_raw = human_approval.get("rule_ids", ())
        ha_rule_ids = tuple(
            _sanitize_text(r) if isinstance(r, str) else ""
            for r in ha_rule_ids_raw
        )
        ha = HumanApproval(
            approved=ha_approved, approver=ha_approver, rule_ids=ha_rule_ids,
        )
    else:
        raise _build_plan_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="build-plan:model:human-approval",
            reason="human_approval must be a HumanApproval or a mapping",
            recovery="Provide a HumanApproval or a dict with approved/approver/rule_ids",
        )
    return ModelEntry(
        name=sanitized_name,
        layer=layer,
        upstream_deps=deps_tuple,
        change_kind=change_kind,
        created_rev=created_rev,
        owner=sanitized_owner,
        cross_layer_exception=cle,
        join_or_aggregate_summary=sanitized_summary,
        protected_action_flags=flags_tuple,
        requires_human_approval=bool(requires_human_approval),
        human_approval=ha,
    )


def read_model_registry(path: Path) -> tuple[ModelEntry, ...]:
    """Read ``.chatbi/model_registry.json`` (derived evidence under
    ``runtime.evidence_root`` = ``.chatbi``). Returns ``()`` if the file is
    absent (first build) - absence is an empty registry, NOT an error (Q3: not
    fail-closed on absent). On present-but-malformed: raises :class:`GateError`
    (HOOK-004). ``schema_version`` must be 1 (Q3, mirrors source_inventory).

    Each ``models[i]`` is re-validated through :func:`build_model_entry` (the
    same factory + ``_sanitize_text``), so a tampered registry entry (bad alias,
    unknown layer/change_kind, unsanitizable text) raises :class:`GateError` on
    read (fail-closed on tampered evidence; only absent is non-error).
    """
    if not path.is_file():
        return ()
    try:
        raw = path.read_bytes()
        if len(raw) > 256 * 1024:
            raise ValueError("model_registry.json exceeds 256 KiB")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _build_plan_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="build-plan:registry:malformed",
            reason=f"model_registry.json is malformed: {type(error).__name__}",
            recovery="Restore or regenerate .chatbi/model_registry.json",
        ) from error
    if not isinstance(data, dict):
        raise _build_plan_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="build-plan:registry:shape",
            reason="model_registry.json must be a JSON object",
            recovery="Restore or regenerate .chatbi/model_registry.json",
        )
    if data.get("schema_version") != _SCHEMA_VERSION:
        raise _build_plan_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="build-plan:registry:schema-version",
            reason=(
                f"model_registry schema_version must be {_SCHEMA_VERSION}; "
                f"got {data.get('schema_version')!r}"
            ),
            recovery="Restore or regenerate .chatbi/model_registry.json",
        )
    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        raise _build_plan_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="build-plan:registry:models",
            reason="model_registry models must be an array",
            recovery="Restore or regenerate .chatbi/model_registry.json",
        )
    entries: list[ModelEntry] = []
    for index, raw_entry in enumerate(raw_models):
        if not isinstance(raw_entry, dict):
            raise _build_plan_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref=f"build-plan:registry:models[{index}]",
                reason=f"registry entry {index} must be a JSON object",
                recovery="Restore or regenerate .chatbi/model_registry.json",
            )
        try:
            entries.append(build_model_entry(**raw_entry))
        except TypeError as error:
            raise _build_plan_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref=f"build-plan:registry:models[{index}]:fields",
                reason=f"registry entry {index} has unexpected fields: {error}",
                recovery="Restore or regenerate .chatbi/model_registry.json",
            ) from error
    return tuple(entries)


def validate_build_plan(
    plan: BuildPlan,
    layer_rules: tuple[LayerRule, ...],
    known_models: frozenset[str] = frozenset(),
) -> None:
    """Pure shape validation (HOOK-004 fail-closed). No derivation.

    Raises :class:`GateError` on:
    1. Topology order (Q6a) + SCOPE-001 cross-plan-boundary (open point 6):
       each ``upstream_dep`` must either appear BEFORE its dependent in
       ``plan.models`` (intra-plan topology, DOC-002/HOOK-004) or be in
       ``known_models`` (pre-existing model in the registry). A dep in neither
       -> GateError (SCOPE-001, evidence_ref ``build-plan:scope:<name>:<dep>``).
    2. Alias (PORT-001): every ``entry.name`` matches ``_ALIAS``.
    3. Protected-action consistency (SEM-003): non-empty
       ``protected_action_flags`` requires ``requires_human_approval=True``.
    4. Extend-source human approval (Q1): ``requires_human_approval=True``
       with ``human_approval.approved`` not True -> GateError
       (SCOPE-001/SEC-001/RAW-003/HOOK-004).
    5. Schema validation against ``build-plan.schema.json`` (single contract,
       mirrors :func:`chatbi_harness.impact.validate_impact_manifest`).
    """
    # 1. Topology + SCOPE-001 (open point 6: known_models cross-plan-boundary)
    name_to_index: dict[str, int] = {}
    for index, entry in enumerate(plan.models):
        if entry.name in name_to_index:
            raise _build_plan_gate_error(
                rule_ids=("DOC-002", "HOOK-004"),
                evidence_ref=f"build-plan:topology:duplicate:{entry.name}",
                reason=f"Duplicate model name in plan: {entry.name}",
                recovery="Remove the duplicate model entry",
            )
        name_to_index[entry.name] = index
    for index, entry in enumerate(plan.models):
        for dep in entry.upstream_deps:
            if dep in name_to_index:
                # Intra-plan topology: dep must appear before the entry (Q6a)
                if name_to_index[dep] >= index:
                    raise _build_plan_gate_error(
                        rule_ids=("DOC-002", "HOOK-004"),
                        evidence_ref=f"build-plan:topology:{entry.name}",
                        reason=(
                            f"Model {entry.name} depends on {dep} which "
                            "appears later in the plan"
                        ),
                        recovery="Reorder the plan so upstream deps appear before dependents",
                    )
            elif dep not in known_models:
                # SCOPE-001: dep is neither in the plan nor a known pre-existing
                # model (open point 6 decision, overrides technical-design §2.7
                # v1 simplification).
                raise _build_plan_gate_error(
                    rule_ids=("SCOPE-001", "HOOK-004"),
                    evidence_ref=f"build-plan:scope:{entry.name}:{dep}",
                    reason=(
                        f"Model {entry.name} depends on {dep} which is not in "
                        "the plan or the known model registry"
                    ),
                    recovery=(
                        "Add the missing model to the plan, or confirm it "
                        "exists in the model registry"
                    ),
                )
            # else: dep in known_models -> OK (pre-existing model, no ordering)
    # 2. Alias re-assertion (PORT-001; already enforced at construction)
    for entry in plan.models:
        if not _ALIAS.fullmatch(entry.name):
            raise _build_plan_gate_error(
                rule_ids=("PORT-001", "HOOK-004"),
                evidence_ref=f"build-plan:alias:{entry.name}",
                reason=f"Model name {entry.name!r} does not match the alias pattern",
                recovery="Use a logical alias matching ^[a-z][a-z0-9_-]{1,62}$",
            )
    # 3. Protected-action consistency (SEM-003)
    for entry in plan.models:
        for flag in entry.protected_action_flags:
            if flag not in _PROTECTED_ACTIONS:
                raise _build_plan_gate_error(
                    rule_ids=("SEM-003", "HOOK-004"),
                    evidence_ref=f"build-plan:protected-action:{entry.name}:{flag}",
                    reason=f"Unknown protected_action_flag: {flag!r}",
                    recovery=f"Use one of {sorted(_PROTECTED_ACTIONS)}",
                )
        if entry.protected_action_flags and not entry.requires_human_approval:
            raise _build_plan_gate_error(
                rule_ids=("SEM-003", "HOOK-004"),
                evidence_ref=f"build-plan:sem-003:{entry.name}",
                reason=(
                    f"Model {entry.name} has protected_action_flags but "
                    "requires_human_approval=False"
                ),
                recovery="Set requires_human_approval=True when declaring protected actions",
            )
    # 4. Extend-source human approval (Q1)
    for entry in plan.models:
        if entry.requires_human_approval and not entry.human_approval.approved:
            raise _build_plan_gate_error(
                rule_ids=("SCOPE-001", "SEC-001", "RAW-003", "HOOK-004"),
                evidence_ref=f"build-plan:human-approval:{entry.name}",
                reason=(
                    f"Model {entry.name} requires human approval but is not "
                    "approved"
                ),
                recovery=(
                    "Obtain human approval before proceeding with the "
                    "extend-source entry"
                ),
            )
    # 5. Schema validation (single contract, mirrors impact.py:229)
    _validate_against_schema(
        plan.to_dict(),
        _get_schema("build-plan.schema.json"),
        "build-plan.schema.json",
    )


def validate_layer_dependency(
    plan: BuildPlan,
    layer_rules: tuple[LayerRule, ...],
) -> None:
    """Layer-permission matrix (Q6b), INDEPENDENT of
    :func:`validate_build_plan`'s topology check (Q6: two separate checks).

    For each entry, every ``upstream_dep``'s layer must be in this entry's
    :class:`LayerRule.may_depend_on`. A :class:`CrossLayerException` with
    non-empty ``reason`` does NOT raise (Q2: an explicit, documented exception
    is allowed; it stays in plan metadata + registry, not the blueprint).

    Pre-existing models (in ``known_models``, already validated by
    :func:`validate_build_plan`) are skipped - their cross-layer dependencies
    were checked when they were built. Only plan-internal deps are checked
    here.
    """
    layer_of: dict[str, str] = {entry.name: entry.layer for entry in plan.models}
    rule_for: dict[str, frozenset[str]] = {
        rule.layer: rule.may_depend_on for rule in layer_rules
    }
    for entry in plan.models:
        allowed = rule_for.get(entry.layer)
        if allowed is None:
            # No rule for this layer -> skip (the SKILL/operator must supply
            # rules; META-003: the lib does not invent cross-layer rules).
            continue
        for dep in entry.upstream_deps:
            dep_layer = layer_of.get(dep)
            if dep_layer is None:
                # dep is a pre-existing model (in known_models, validated by
                # validate_build_plan) or not in the plan. Skip layer check
                # for pre-existing models (validated when built).
                continue
            if dep_layer not in allowed:
                if (
                    entry.cross_layer_exception is not None
                    and entry.cross_layer_exception.reason
                ):
                    # Q2: explicit, documented exception is allowed
                    continue
                raise _build_plan_gate_error(
                    rule_ids=("DOC-002", "HOOK-004"),
                    evidence_ref=f"build-plan:layer:{entry.name}:{dep}",
                    reason=(
                        f"Model {entry.name} (layer {entry.layer}) depends on "
                        f"{dep} (layer {dep_layer}) which is not permitted"
                    ),
                    recovery=(
                        f"Permit {dep_layer} in the layer rule for "
                        f"{entry.layer}, or add a cross_layer_exception with "
                        "a reason"
                    ),
                )


def append_model_registry(path: Path, entry: ModelEntry) -> Path:
    """Append one :class:`ModelEntry` to ``.chatbi/model_registry.json`` (create
    if absent). Called by maintain-model ONLY after sync gate + stop_gate pass
    (DOC-004/HOOK-001 - a failed-sync model is NOT recorded, fail-closed).

    Atomic temp+rename mirroring :func:`chatbi_harness.harness_state.write_state`
    discipline (``harness_state.py:104-122``, ``0o600``). Idempotent on
    ``(name, created_rev)`` (v1 = append-with-history; a rebuild at a new rev
    keeps both entries). Returns the registry path.

    Cannot reuse :func:`chatbi_harness.harness_state.write_state` directly: that
    function is path-constrained to ``.chatbi/runs/<session_id>/<name>.json``
    (``harness_state.py:29,47-60``); the registry lives at
    ``.chatbi/model_registry.json`` (evidence_root direct child, not under
    ``runs/``). So this function mirrors the discipline inline.
    """
    # --- Read existing ---
    if path.is_file():
        try:
            raw = path.read_bytes()
            if len(raw) > 256 * 1024:
                raise ValueError("model_registry.json exceeds 256 KiB")
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise _build_plan_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref="build-plan:registry:malformed",
                reason=f"model_registry.json is malformed: {type(error).__name__}",
                recovery="Restore or regenerate .chatbi/model_registry.json",
            ) from error
        if not isinstance(data, dict):
            raise _build_plan_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref="build-plan:registry:shape",
                reason="model_registry.json must be a JSON object",
                recovery="Restore or regenerate .chatbi/model_registry.json",
            )
        if data.get("schema_version") != _SCHEMA_VERSION:
            raise _build_plan_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref="build-plan:registry:schema-version",
                reason=(
                    f"model_registry schema_version must be {_SCHEMA_VERSION}; "
                    f"got {data.get('schema_version')!r}"
                ),
                recovery="Restore or regenerate .chatbi/model_registry.json",
            )
        models = data.get("models")
        if not isinstance(models, list):
            raise _build_plan_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref="build-plan:registry:models",
                reason="model_registry models must be an array",
                recovery="Restore or regenerate .chatbi/model_registry.json",
            )
    else:
        data = {"schema_version": _SCHEMA_VERSION, "models": []}
        models = data["models"]
    # --- Idempotency: skip if (name, created_rev) already recorded ---
    for existing in models:
        if (
            isinstance(existing, dict)
            and existing.get("name") == entry.name
            and existing.get("created_rev") == entry.created_rev
        ):
            return path  # already recorded, no rewrite
    # --- Append (v1: append-with-history) ---
    # Build a new list (does not mutate the on-disk list in place).
    new_models = list(models)
    new_models.append(entry.to_dict())
    data["models"] = new_models
    # --- Atomic write (mirror harness_state.write_state:106-122) ---
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        data, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def collect_known_models(workspace_root: Path) -> frozenset[str]:
    """Union of model names from the registry + on-disk ``models/`` directory.

    The registry may lag actual models (built before the registry feature
    landed are absent from it); the directory scan closes that gap. Used as
    ``known_models`` for :func:`validate_build_plan` (SCOPE-001 cross-plan-
    boundary check: a plan entry may depend on an existing model that is on
    disk but not yet in the registry).

    Registry absent -> ``()`` (not an error, :func:`read_model_registry`);
    ``models/`` absent -> skipped. Returns the union of registry model names +
    ``models/{ods,dwd,dws,dim,ads}/*.sql`` stems.
    """
    names: set[str] = set()
    registry_path = workspace_root / ".chatbi" / "model_registry.json"
    for entry in read_model_registry(registry_path):
        names.add(entry.name)
    models_dir = workspace_root / "models"
    if models_dir.is_dir():
        for layer_dir in models_dir.iterdir():
            if layer_dir.is_dir():
                for sql_file in layer_dir.glob("*.sql"):
                    names.add(sql_file.stem)
    return frozenset(names)


__all__ = [
    "BuildPlan",
    "CrossLayerException",
    "HumanApproval",
    "LayerRule",
    "ModelEntry",
    "append_model_registry",
    "build_model_entry",
    "collect_known_models",
    "read_model_registry",
    "validate_build_plan",
    "validate_layer_dependency",
]
