"""Cycle 4 Task 01: change-impact manifest primitives.

An :class:`ImpactManifest` records what a model / semantic change affects
(metadata, semantic, reference, Skill, tests, downstream, eval), the evidence
state (sufficient / missing / uncertain), whether a P0 eval failed, and whether
the change is a protected action requiring human approval. Manifests are atomic,
sanitized, SHA-bound, and fail-closed (missing or uncertain evidence is recorded
explicitly, never degraded to an empty placeholder).

Sanitization reuses ``gates`` (sanctioned private reuse, mirroring ``evidence``)
and SHA binding reuses ``evidence.compute_candidate_sha``. Schema validation
reuses ``evidence._get_schema`` / ``_validate_against_schema`` so the
``schemas/impact-manifest.schema.json`` file is the single contract.

Applicable rules: DOC-004, EVAL-001..005, SEM-003, HOOK-001/004, SEC-003,
PORT-001, ABL-001/002.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .gates import GateError, _sanitize_text, _unique  # noqa: E402 (sanctioned reuse)
from .evidence import (  # noqa: E402 (sanctioned reuse)
    _get_schema,
    _validate_against_schema,
    compute_candidate_sha,
)

_CHANGE_KINDS = frozenset({
    "model", "column", "semantic", "reference", "Skill", "downstream", "eval",
})
_ASSET_KINDS = frozenset({
    "metadata", "semantic", "reference", "Skill", "tests", "downstream",
    "eval", "code",
})
_EVIDENCE_STATES = frozenset({"sufficient", "missing", "uncertain"})
_PROTECTED_ACTIONS = frozenset({
    "approve_metric", "change_access_policy", "production_publish",
    "destructive_migration",
})


def _impact_gate_error(
    *, rule_ids: tuple[str, ...], evidence_ref: str, reason: str, recovery: str,
) -> GateError:
    from .gates import GateDecision
    return GateError(
        GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=(evidence_ref,),
            reason=reason,
            recovery=recovery,
        )
    )


@dataclass(frozen=True, slots=True)
class AffectedAsset:
    """One asset affected by a change (metadata/semantic/reference/Skill/
    tests/downstream/eval/code). ``change_required`` and ``synced`` express
    whether a candidate change is needed and whether it has been applied."""

    asset_kind: str
    asset_ref: str
    change_required: bool
    synced: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_kind": self.asset_kind,
            "asset_ref": self.asset_ref,
            "change_required": self.change_required,
            "synced": self.synced,
        }


@dataclass(frozen=True, slots=True)
class ImpactManifest:
    """Atomic, sanitized record of a change's blast radius and evidence state."""

    run_id: str
    change_kind: str
    target: str
    affected_assets: tuple[AffectedAsset, ...]
    evidence_state: str
    p0_eval_failed: bool
    protected_action: bool
    candidate_sha: str
    created_rev: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "change_kind": self.change_kind,
            "target": self.target,
            "affected_assets": [a.to_dict() for a in self.affected_assets],
            "evidence_state": self.evidence_state,
            "p0_eval_failed": self.p0_eval_failed,
            "protected_action": self.protected_action,
            "candidate_sha": self.candidate_sha,
            "created_rev": self.created_rev,
        }

    def has_blocking_drift(self) -> bool:
        """True when the change cannot be confirmed fully synced and safe.

        Blocking conditions (fail-closed): missing OR uncertain evidence, a
        failed P0 eval, an unapproved protected action, or any affected asset
        that requires a change but is not yet synced (DOC-004 blocking drift).
        """
        if self.evidence_state in ("missing", "uncertain"):
            return True
        if self.p0_eval_failed:
            return True
        if self.protected_action:
            return True
        return any(a.change_required and not a.synced for a in self.affected_assets)

    def blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.evidence_state == "missing":
            reasons.append("missing impact evidence")
        if self.evidence_state == "uncertain":
            reasons.append("uncertain impact evidence")
        if self.p0_eval_failed:
            reasons.append("P0 eval failed")
        if self.protected_action:
            reasons.append("protected action requires human approval")
        unsynced = [a.asset_ref for a in self.affected_assets
                    if a.change_required and not a.synced]
        if unsynced:
            reasons.append("unsynced affected assets: " + ", ".join(unsynced))
        return tuple(reasons)


def build_impact_manifest(
    *,
    run_id: str,
    change_kind: str,
    target: str,
    affected_assets: Iterable[AffectedAsset | Mapping[str, Any]],
    evidence_state: str,
    p0_eval_failed: bool = False,
    protected_action: bool = False,
    candidate_payload: Any,
    created_rev: str = "",
) -> ImpactManifest:
    """Build a validated, sanitized, SHA-bound ImpactManifest. Raises
    :class:`GateError` (fail-closed) on invalid enums, missing required values,
    or non-sanitizable refs (SEC-003/PORT-001)."""
    if change_kind not in _CHANGE_KINDS:
        raise _impact_gate_error(
            rule_ids=("DOC-004", "HOOK-004"),
            evidence_ref="impact:change-kind",
            reason=f"Unknown change_kind: {change_kind}",
            recovery=f"Use one of {sorted(_CHANGE_KINDS)}",
        )
    if evidence_state not in _EVIDENCE_STATES:
        raise _impact_gate_error(
            rule_ids=("DOC-004", "HOOK-004"),
            evidence_ref="impact:evidence-state",
            reason=f"Unknown evidence_state: {evidence_state}",
            recovery=f"Use one of {sorted(_EVIDENCE_STATES)}",
        )
    if not run_id or not isinstance(run_id, str):
        raise _impact_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="impact:run-id",
            reason="run_id is required",
            recovery="Provide a non-empty run_id",
        )
    sanitized_target = _sanitize_text(target) if isinstance(target, str) else ""
    if not sanitized_target:
        raise _impact_gate_error(
            rule_ids=("PORT-001", "HOOK-004"),
            evidence_ref="impact:target",
            reason="target is required and must sanitize cleanly",
            recovery="Provide a logical alias / relative target ref",
        )
    assets: list[AffectedAsset] = []
    for raw in affected_assets:
        if isinstance(raw, AffectedAsset):
            assets.append(raw)
            continue
        if not isinstance(raw, Mapping):
            raise _impact_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref="impact:affected-asset",
                reason="affected asset must be a mapping or AffectedAsset",
                recovery="Provide asset_kind/asset_ref/change_required/synced",
            )
        kind = raw.get("asset_kind")
        ref = raw.get("asset_ref")
        if kind not in _ASSET_KINDS:
            raise _impact_gate_error(
                rule_ids=("DOC-004", "HOOK-004"),
                evidence_ref="impact:asset-kind",
                reason=f"Unknown asset_kind: {kind}",
                recovery=f"Use one of {sorted(_ASSET_KINDS)}",
            )
        sanitized_ref = _sanitize_text(ref) if isinstance(ref, str) else ""
        if not sanitized_ref:
            raise _impact_gate_error(
                rule_ids=("PORT-001", "HOOK-004"),
                evidence_ref="impact:asset-ref",
                reason="asset_ref is required and must sanitize cleanly",
                recovery="Provide a logical alias / relative asset ref",
            )
        assets.append(AffectedAsset(
            asset_kind=kind,
            asset_ref=sanitized_ref,
            change_required=bool(raw.get("change_required", False)),
            synced=bool(raw.get("synced", False)),
        ))
    candidate_sha = compute_candidate_sha(candidate_payload)
    manifest = ImpactManifest(
        run_id=run_id,
        change_kind=change_kind,
        target=sanitized_target,
        affected_assets=tuple(assets),
        evidence_state=evidence_state,
        p0_eval_failed=bool(p0_eval_failed),
        protected_action=bool(protected_action),
        candidate_sha=candidate_sha,
        created_rev=created_rev or f"candidate_sha:{candidate_sha[:12]}",
    )
    validate_impact_manifest(manifest.to_dict())
    return manifest


def validate_impact_manifest(payload: Mapping[str, Any]) -> None:
    """Validate a manifest dict against ``schemas/impact-manifest.schema.json``
    (fail-closed ``GateError`` on schema failure, HOOK-004)."""
    _validate_against_schema(
        payload, _get_schema("impact-manifest.schema.json"),
        "impact-manifest.schema.json",
    )


__all__ = [
    "AffectedAsset",
    "ImpactManifest",
    "build_impact_manifest",
    "validate_impact_manifest",
]
