"""Field-level IR data model for ``chatbi.harness/v1`` workflow declarations.

The IR is a read-only declaration surface: it describes the nine governed
workflows (steps, gates, evidence, routes, prompt references, runtime
requirements) so any Runtime Adapter can interpret the same behavior
contract. This module defines the model only; parsing lives in
``chatbi_harness_ir.loader`` and semantic checks in
``chatbi_harness_ir.validator``.

The IR never drives execution by itself (stage C-1: declaration + validation
only, design §4.1 / §9.1) and must never contain machine paths or secrets
(PORT-001 / SEC-003, invariant 5).

Applicable rules: HOOK-001 (determinism), PORT-001 (no machine paths),
MR-002 (nine-workflow IR coverage).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


SCHEMA_NAME = "chatbi.harness"
SCHEMA_MAJOR = 1
SCHEMA_VERSION = f"{SCHEMA_NAME}/v{SCHEMA_MAJOR}"

#: The nine governed workflows the first IR release covers (feature-flow §2.1).
WORKFLOW_IDS = (
    "chatbi-init",
    "chatbi-analyze",
    "chatbi-maintain-model",
    "chatbi-maintain-knowledge",
    "chatbi-evaluate",
    "chatbi-correction",
    "chatbi-bootstrap",
    "chatbi-build-from-requirement",
    "chatbi-audit-drift",
)


class ExecutorKind(str, Enum):
    """Step executor taxonomy (design §4.1).

    ``deterministic`` steps reference a Governance Kernel function;
    ``agent_with_tools`` steps run an agent restricted to an explicit tool
    allow/deny list; ``independent_reviewer`` is the least-privilege reviewer;
    ``human_approval`` blocks on a human owner; ``runtime_native`` delegates to
    the Runtime (CLI adapter invocation, capability probe, state persistence).
    """

    DETERMINISTIC = "deterministic"
    AGENT_WITH_TOOLS = "agent_with_tools"
    INDEPENDENT_REVIEWER = "independent_reviewer"
    HUMAN_APPROVAL = "human_approval"
    RUNTIME_NATIVE = "runtime_native"


class RequirementLevel(str, Enum):
    """Runtime requirement/capability level (design §4.1, §8.2).

    ``required`` is the fail-closed bar: a Runtime whose manifest cannot
    provide it must refuse to deploy (MR-005). ``protected_actions`` marks
    capabilities that are only engaged for human-gated actions (SEM-003).
    """

    REQUIRED = "required"
    OPTIONAL = "optional"
    PROTECTED_ACTIONS = "protected_actions"
    NONE = "none"


#: The four mandatory protected actions (kernel config.py; SEM-003). These are
#: the only legal ``owner.pending(<action>)`` arguments in the condition
#: grammar (design §4.4).
PROTECTED_ACTIONS = (
    "approve_metric",
    "change_access_policy",
    "production_publish",
    "destructive_migration",
)

#: Route value sentinels: "none" = no handoff, "owner" = human adjudication.
ROUTE_NONE = "none"
ROUTE_OWNER = "owner"


@dataclass(frozen=True)
class ToolsSpec:
    """Explicit tool allow/deny list for agent steps (no intersection)."""

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()


@dataclass(frozen=True)
class Step:
    """One IR step: a deterministic kernel call, an agent step, a reviewer
    step, a human approval, or a runtime-native action."""

    id: str
    executor: ExecutorKind
    #: Dotted kernel path (e.g. ``chatbi_governance.evidence.validate_request``).
    #: Optional even for deterministic steps (pure argument/presentation steps
    #: have no kernel counterpart; the reference is declarative, the real
    #: decision lives in the Adapter orchestration).
    function: str | None = None
    #: Controlled condition expression (``chatbi_harness_ir.conditions``).
    when: str | None = None
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    tools: ToolsSpec | None = None
    review_required: bool = False
    review_schema: str | None = None


@dataclass(frozen=True)
class DeliveryGate:
    """Workflow-level delivery gate declaration."""

    rule_ids: tuple[str, ...] = ()
    #: True = the gate must PASS before ``run.completed`` may be emitted
    #: (design §5.2).
    terminal_only: bool = False


@dataclass(frozen=True)
class Gates:
    delivery: DeliveryGate | None = None


@dataclass(frozen=True)
class PromptRef:
    """A prompt/template reference pinned to the registered content hash."""

    name: str
    #: Workspace-relative path (PORT-001: no absolute paths).
    path: str
    sha256: str


@dataclass(frozen=True)
class Entry:
    """Workflow entry point (Claude side = slash command shape, FF§3.3)."""

    command: str
    argument_hint: str | None = None


@dataclass(frozen=True)
class EvidenceSpec:
    root: str
    schema_versions: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Compatibility:
    deprecated_fields: tuple[str, ...] = ()
    migration: str | None = None


@dataclass(frozen=True)
class Workflow:
    """A complete ``chatbi.harness/v1`` workflow declaration."""

    schema_version: str
    workflow_id: str
    workflow_version: int
    title: str
    description: str
    entry: Entry
    steps: tuple[Step, ...]
    requirements: Mapping[str, RequirementLevel] = field(default_factory=dict)
    capabilities: Mapping[str, RequirementLevel] = field(default_factory=dict)
    prompts: tuple[PromptRef, ...] = ()
    tools: ToolsSpec | None = None
    evidence: EvidenceSpec | None = None
    gates: Gates | None = None
    routes: Mapping[str, str] = field(default_factory=dict)
    compatibility: Compatibility | None = None

    def to_dict(self) -> dict:
        """Plain-JSON representation (lists/dicts/strings) used by the
        PORT-001/SEC-003 content scan and by tests."""

        def _plain(value):
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, tuple):
                return [_plain(v) for v in value]
            if isinstance(value, dict):
                return {k: _plain(v) for k, v in value.items()}
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            return str(value)

        steps = []
        for s in self.steps:
            step: dict = {
                "id": s.id,
                # getattr guard: an invalid executor type still serializes so
                # the validator can report it (fail-closed, not crash).
                "executor": getattr(s.executor, "value", s.executor),
            }
            if s.function is not None:
                step["function"] = s.function
            if s.when is not None:
                step["when"] = s.when
            if s.inputs:
                step["inputs"] = list(s.inputs)
            if s.outputs:
                step["outputs"] = list(s.outputs)
            if s.tools is not None:
                step["tools"] = {
                    "allow": list(s.tools.allow),
                    "deny": list(s.tools.deny),
                }
            if s.review_required:
                step["review_required"] = True
            if s.review_schema is not None:
                step["review_schema"] = s.review_schema
            steps.append(step)

        out: dict = {
            "schema_version": self.schema_version,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "title": self.title,
            "description": self.description,
            "entry": _plain(self.entry),
            "steps": steps,
        }
        if self.requirements:
            out["requirements"] = _plain(self.requirements)
        if self.capabilities:
            out["capabilities"] = _plain(self.capabilities)
        if self.prompts:
            out["prompts"] = [_plain(p) for p in self.prompts]
        if self.tools is not None:
            out["tools"] = _plain(self.tools)
        if self.evidence is not None:
            out["evidence"] = _plain(self.evidence)
        if self.gates is not None and self.gates.delivery is not None:
            out["gates"] = _plain(self.gates)
        if self.routes:
            out["routes"] = _plain(self.routes)
        if self.compatibility is not None:
            out["compatibility"] = _plain(self.compatibility)
        return out
