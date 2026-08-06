"""ChatBI Runtime Capability Contract (module 3, stage C-1).

Vendor-neutral boundary between Runtimes (Claude Code, Agno, …) and the
ChatBI governance layer:

- :mod:`chatbi_runtime_contract.types` — frozen value types exchanged
  across the boundary (StartRun/ContinueRun/CancelRun/ToolRequest/
  ToolResult/ReviewRequest/ReviewResult/ApprovalRequest/ApprovalHandle/
  SessionView/RunHandle/RunState);
- :mod:`chatbi_runtime_contract.ports` — ``RuntimePort`` Protocol, the
  nine-method Port surface (design §8.3, verbatim);
- :mod:`chatbi_runtime_contract.capabilities` — five-class capability
  model, manifest validation and the fail-closed ``missing_required``
  judgment (MR-005);
- :mod:`chatbi_runtime_contract.events` — the 17 standard events and the
  ``chatbi.event/v1`` envelope validator, including the Delivery Gate PASS
  precondition on ``run.completed`` (design §8.4, ADR-002).

No runtime-specific imports live in this package (no agno/fastapi/CC hook
types), and no governance judgment is implemented here (invariant 2).
"""

from __future__ import annotations

from .capabilities import (
    CapabilityEntry,
    CapabilityManifest,
    CapabilityProbe,
    CapabilityStatus,
)
from .events import (
    EVENT_TYPES,
    SCHEMA_VERSION as EVENT_SCHEMA_VERSION,
    EventType,
    is_delivery_completion,
    validate_envelope,
    validate_event_stream,
)
from .ports import RuntimePort
from .types import (
    ApprovalHandle,
    ApprovalRequest,
    CancelRun,
    ContinueRun,
    ReviewRequest,
    ReviewResult,
    ReviewVerdict,
    RunHandle,
    RunState,
    SessionView,
    StartRun,
    ToolRequest,
    ToolResult,
)

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "EVENT_TYPES",
    "ApprovalHandle",
    "ApprovalRequest",
    "CancelRun",
    "CapabilityEntry",
    "CapabilityManifest",
    "CapabilityProbe",
    "CapabilityStatus",
    "ContinueRun",
    "EventType",
    "ReviewRequest",
    "ReviewResult",
    "ReviewVerdict",
    "RunHandle",
    "RunState",
    "RuntimePort",
    "SessionView",
    "StartRun",
    "ToolRequest",
    "ToolResult",
    "is_delivery_completion",
    "validate_envelope",
    "validate_event_stream",
]
