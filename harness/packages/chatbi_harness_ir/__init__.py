"""ChatBI Harness IR: framework-independent workflow declarations (module 3).

The IR package turns the nine governed workflows into versioned, validated
declarations (``chatbi.harness/v1``): a field-level model
(:mod:`chatbi_harness_ir.schema`), a strict YAML loader with the
version-major gate (:mod:`chatbi_harness_ir.loader`), a semantic validator
including the controlled condition grammar (:mod:`chatbi_harness_ir.validator`
and :mod:`chatbi_harness_ir.conditions`).

The IR is declaration + validation only — it never drives execution
(design §9.1) and never contains machine paths or secrets (invariant 5).
"""

from __future__ import annotations

from .conditions import Cond, ConditionSyntaxError, parse_condition, validate_condition
from .loader import IrLoadError, load_all, load_workflow
from .schema import (
    SCHEMA_MAJOR,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    WORKFLOW_IDS,
    Compatibility,
    DeliveryGate,
    Entry,
    EvidenceSpec,
    ExecutorKind,
    Gates,
    PromptRef,
    RequirementLevel,
    Step,
    ToolsSpec,
    Workflow,
)
from .validator import validate_registry, validate_workflow

__all__ = [
    "SCHEMA_MAJOR",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "WORKFLOW_IDS",
    "Compatibility",
    "Cond",
    "ConditionSyntaxError",
    "DeliveryGate",
    "Entry",
    "EvidenceSpec",
    "ExecutorKind",
    "Gates",
    "IrLoadError",
    "PromptRef",
    "RequirementLevel",
    "Step",
    "ToolsSpec",
    "Workflow",
    "load_all",
    "load_workflow",
    "parse_condition",
    "validate_condition",
    "validate_registry",
    "validate_workflow",
]
