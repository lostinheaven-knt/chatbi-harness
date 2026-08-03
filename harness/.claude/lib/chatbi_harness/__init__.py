"""Deterministic primitives shared by the ChatBI harness."""

from .bootstrap import (
    SourceInventory,
    build_mysql_adapter_spec,
    merge_local_config,
    merge_source_inventories,
    read_source_inventory,
)
from .build_plan import (
    BuildPlan,
    LayerRule,
    ModelEntry,
    append_model_registry,
    collect_known_models,
    read_model_registry,
    validate_build_plan,
    validate_layer_dependency,
)
from .config import EffectiveConfig, load_effective_config
from .diagnostics import (
    CapabilitySnapshot,
    DiagnosticCheck,
    DiagnosticResult,
    probe_local_capabilities,
    run_init_diagnostic,
)
from .drift import (
    DRIFT_ROUTES,
    DriftCandidate,
    DriftReport,
    RouteDecision,
    classify_finding,
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
    "LayerRule",
    "ModelEntry",
    "PortablePathReference",
    "RouteDecision",
    "SourceInventory",
    "append_model_registry",
    "build_mysql_adapter_spec",
    "classify_finding",
    "collect_known_models",
    "detect_drift",
    "fail_closed",
    "load_effective_config",
    "merge_local_config",
    "merge_source_inventories",
    "probe_local_capabilities",
    "read_model_registry",
    "read_source_inventory",
    "resolve_path_reference",
    "run_init_diagnostic",
    "validate_build_plan",
    "validate_domain_contract",
    "validate_layer_dependency",
]
