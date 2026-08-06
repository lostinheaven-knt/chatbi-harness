"""Deterministic Governance Kernel primitives (multi-runtime module 2).

Vendor-free kernel: no Claude Code / Agno types, no runtime probing. The
Claude-specific capability probe lives in ``runtimes/claude_code/probe.py``
and is re-exported under the legacy name by the compatibility shim
(``.claude/lib/chatbi_harness``).
"""

from .bootstrap import (
    SourceInventory,
    build_mysql_adapter_spec,
    merge_local_config,
    merge_source_inventories,
    read_source_inventory,
)
from .build_plan import (
    BuildPlan,
    KnownModelProvenance,
    LayerRule,
    ModelEntry,
    append_model_registry,
    collect_known_models,
    collect_known_models_with_provenance,
    read_model_registry,
    validate_build_plan,
    validate_layer_dependency,
)
from .config import EffectiveConfig, load_effective_config
from .diagnostics import (
    CapabilitySnapshot,
    DiagnosticCheck,
    DiagnosticResult,
    run_init_diagnostic,
)
from .drift import (
    DRIFT_ROUTES,
    DriftCandidate,
    DriftReport,
    RouteDecision,
    SRC002_ROUTES,
    classify_finding,
    classify_src002_finding,
    detect_drift,
)
from .gates import GateDecision, GateError, fail_closed, validate_domain_contract
from .paths import PortablePathReference, resolve_path_reference

__all__ = [
    "BuildPlan",
    "DRIFT_ROUTES",
    "DriftCandidate",
    "DriftReport",
    "EffectiveConfig",
    "CapabilitySnapshot",
    "DiagnosticCheck",
    "DiagnosticResult",
    "GateDecision",
    "GateError",
    "KnownModelProvenance",
    "LayerRule",
    "ModelEntry",
    "PortablePathReference",
    "RouteDecision",
    "SRC002_ROUTES",
    "SourceInventory",
    "append_model_registry",
    "build_mysql_adapter_spec",
    "classify_finding",
    "classify_src002_finding",
    "collect_known_models",
    "collect_known_models_with_provenance",
    "detect_drift",
    "fail_closed",
    "load_effective_config",
    "merge_local_config",
    "merge_source_inventories",
    "read_model_registry",
    "read_source_inventory",
    "resolve_path_reference",
    "run_init_diagnostic",
    "validate_build_plan",
    "validate_domain_contract",
    "validate_layer_dependency",
]
