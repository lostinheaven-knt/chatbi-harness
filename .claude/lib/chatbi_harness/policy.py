"""Deterministic access/PII/risk/approval/capability policy for the ChatBI Harness.

This module is a deterministic primitive (HOOK-001): it performs only field
comparisons on an :class:`~chatbi_harness.config.EffectiveConfig` and an explicit
:class:`PolicyRequest`. It never reads the external Codebase, opens a shell, or
executes a subprocess. It reuses :class:`~chatbi_harness.gates.GateDecision` for
its immutable, sanitized decision shape and introduces no second error protocol:
callers block via :class:`~chatbi_harness.gates.GateError` with a
:class:`PolicyDecision` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .config import EffectiveConfig
from .gates import GateDecision


# Tool capability groups (technical-design §8.3). Each declared request type maps
# to exactly one group; protected actions are governance approvals handled
# separately and do not belong to a tool group.
_CAPABILITY_GROUPS: MappingProxyType[str, str] = MappingProxyType(
    {
        "discover": "discover_read",
        "read_metadata": "discover_read",
        "read_lineage": "discover_read",
        "compile": "query_read",
        "query": "query_read",
        "freshness": "query_read",
        "read_codebase": "codebase_read",
        "search_codebase": "codebase_read",
        "stat_codebase": "codebase_read",
        "git_metadata": "codebase_read",
        "edit_workspace": "workspace_candidate_write",
        "write_workspace": "workspace_candidate_write",
        "mutate_warehouse": "mutate_warehouse",
        "create_remote": "mutate_warehouse",
        "modify_remote": "mutate_warehouse",
        "network": "network",
        "api_call": "network",
    }
)


class PolicyDecision(GateDecision):
    """Immutable policy outcome reusing GateDecision sanitization.

    ``PolicyDecision`` IS-A :class:`GateDecision`: it inherits the frozen/slots
    shape, the ``pass``/``warn``/``block`` factories, and the sanitization in
    ``GateDecision.__post_init__``. No second error protocol is introduced -
    callers raise :class:`~chatbi_harness.gates.GateError` with a
    ``PolicyDecision`` directly when a request must be blocked.
    """

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    """Explicit, immutable request context for a deterministic policy check.

    Fields mirror the four required context dimensions (request type, target
    entity, user/role, decision purpose) plus the optional risk classification
    and network domain context needed by the risk and network checks.
    """

    request_type: str
    target_entity: str = ""
    actor: str = "agent"
    purpose: str = ""
    risk_class: str | None = None
    network_domain: str | None = None
    declared_domains: tuple[str, ...] = ()


def _protected_actions(config: EffectiveConfig) -> tuple[str, ...]:
    return config["workspace"]["protected_actions"]  # type: ignore[return-value]


def _is_high_risk(config: EffectiveConfig, risk_class: str | None) -> bool:
    if risk_class is None:
        return False
    return risk_class in config["governance"]["high_risk_classes"]  # type: ignore[operator]


def decide(config: EffectiveConfig, request: PolicyRequest) -> PolicyDecision:
    """Return a deterministic :class:`PolicyDecision` for one explicit request.

    The check order is fixed so the outcome is stable for identical inputs:

    1. Protected action self-approval (SEM-003): an agent may draft but never
       approve; only a human owner may approve a protected action.
    2. Tool capability group resolution and group-specific access prechecks
       (SEC-001/SEC-002), including PII/disclosure, mutate, network, codebase
       alias, and workspace candidate-write boundaries.
    3. High-risk classification (SEC-001): a hit requires human sign-off/review
       and never auto-escalates.
    4. Pass when no precheck blocks or warns.
    """

    protected = _protected_actions(config)
    is_protected_action = request.request_type in protected

    # 1. SEM-003: an agent cannot self-approve a protected action.
    if is_protected_action and request.actor == "agent":
        return PolicyDecision.block(
            rule_ids=("SEM-003", "SEC-001"),
            evidence_refs=("policy:protected-action",),
            reason=(
                f"Agent cannot self-approve protected action "
                f"{request.request_type}; drafting is not approval"
            ),
            recovery=(
                "Wait for the human owner to approve the protected action"
            ),
        )

    # 2. Tool capability group resolution and group-specific prechecks. A
    #    protected action approved by a human owner is a governance approval, not
    #    a tool operation, so it skips the capability group checks below.
    if not is_protected_action:
        group = _CAPABILITY_GROUPS.get(request.request_type)
        if group is None:
            return PolicyDecision.block(
                rule_ids=("HOOK-004",),
                evidence_refs=("policy:unknown-request-type",),
                reason="Unknown request type is not a declared policy operation",
                recovery="Use a declared request type for the policy check",
            )

        group_block = _check_capability_group(config, request, group)
        if group_block is not None:
            return group_block

    # 3. High-risk classification (SEC-001): needs human sign-off/review; the
    #    decision never auto-escalates to pass.
    if _is_high_risk(config, request.risk_class):
        return PolicyDecision.warn(
            rule_ids=("SEC-001",),
            evidence_refs=(
                "policy:high-risk",
                f"policy:risk-class:{request.risk_class}",
            ),
            reason=(
                f"High-risk class {request.risk_class} requires human "
                f"sign-off or review before proceeding"
            ),
            recovery=(
                "Obtain human owner sign-off or review; do not auto-escalate"
            ),
        )

    # 4. Pass.
    if is_protected_action:
        return PolicyDecision.pass_(
            rule_ids=("SEM-003", "SEC-001"),
            evidence_refs=("policy:protected-action",),
            reason=(
                f"Protected action {request.request_type} approved by a "
                f"human owner"
            ),
            recovery="No action required",
        )
    return PolicyDecision.pass_(
        rule_ids=("SEC-001",),
        evidence_refs=(f"policy:{_CAPABILITY_GROUPS[request.request_type]}",),
        reason=(
            f"Request {request.request_type} permitted under "
            f"{_CAPABILITY_GROUPS[request.request_type]}"
        ),
        recovery="No action required",
    )


def _check_capability_group(
    config: EffectiveConfig,
    request: PolicyRequest,
    group: str,
) -> PolicyDecision | None:
    """Run the group-specific access precheck; return a decision or None to pass."""

    if group == "query_read":
        governance = config["governance"]
        pii_policy_ref = governance["pii_policy_ref"]
        if pii_policy_ref is None:
            return PolicyDecision.block(
                rule_ids=("SEC-002", "SEC-001"),
                evidence_refs=("policy:pii-missing",),
                reason=(
                    "PII policy is not configured; disclosure constraints "
                    "cannot be determined"
                ),
                recovery=(
                    "Configure governance.pii_policy_ref before querying "
                    "restricted data"
                ),
            )
        restricted_disclosure = governance["restricted_disclosure"]
        if restricted_disclosure == "sql_only":
            # SEC-002: sql_withholds results/samples; only runnable SQL may be
            # returned. SQL compilation (purpose=compile) is the allowed path;
            # any result-returning purpose is fail-closed.
            if request.purpose != "compile":
                return PolicyDecision.block(
                    rule_ids=("SEC-002",),
                    evidence_refs=("policy:sql-only",),
                    reason=(
                        "Disclosure policy requires sql_only; results and "
                        "samples are withheld"
                    ),
                    recovery=(
                        "Return only runnable SQL for an authorized user "
                        "to execute"
                    ),
                )
            return PolicyDecision.pass_(
                rule_ids=("SEC-002",),
                evidence_refs=("policy:sql-only-compile",),
                reason=(
                    "SQL compilation permitted under sql_only disclosure; "
                    "do not return results"
                ),
                recovery="Return only the compiled runnable SQL",
            )

    if group == "mutate_warehouse":
        return PolicyDecision.block(
            rule_ids=("SEC-001",),
            evidence_refs=("policy:mutate-warehouse",),
            reason="mutate_warehouse is disabled by default in v1",
            recovery=(
                "Obtain explicit human approval before enabling warehouse "
                "mutation"
            ),
        )

    if group == "network":
        # Default deny: only adapter-declared domains are allowed; everything
        # else is denied (deny priority). Declared domains are supplied by the
        # caller as explicit request context (adapter domain discovery belongs
        # to the adapter layer).
        if (
            request.network_domain is None
            or request.network_domain not in request.declared_domains
        ):
            return PolicyDecision.block(
                rule_ids=("SEC-001",),
                evidence_refs=("policy:network-deny",),
                reason=(
                    "Network access is denied by default; the domain is not "
                    "adapter-declared"
                ),
                recovery=(
                    "Declare the network domain through an approved adapter "
                    "before access"
                ),
            )

    if group == "codebase_read":
        # SEC-001/SCOPE-001: codebase_read is allowed only for explicitly
        # declared Business Codebase aliases.
        if request.target_entity not in config["business_codebases"]:
            return PolicyDecision.block(
                rule_ids=("SEC-001", "SCOPE-001"),
                evidence_refs=("policy:codebase-alias",),
                reason=(
                    "codebase_read requires an explicitly declared Business "
                    "Codebase alias"
                ),
                recovery=(
                    "Use an alias declared in business_codebases"
                ),
            )

    if group == "workspace_candidate_write":
        if not config["workspace"]["allow_candidate_writes"]:
            return PolicyDecision.block(
                rule_ids=("SEC-001", "SCOPE-001"),
                evidence_refs=("policy:workspace-write",),
                reason="Workspace candidate writes are disabled",
                recovery=(
                    "Set workspace.allow_candidate_writes to true to allow "
                    "candidate edits"
                ),
            )

    return None
