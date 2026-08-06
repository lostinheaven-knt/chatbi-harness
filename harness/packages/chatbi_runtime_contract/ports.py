"""RuntimePort: the narrow vendor-neutral Runtime boundary (design §8.3).

Nine methods, verbatim from the design: ``probe``, ``start_run``,
``continue_run``, ``cancel_run``, ``stream_events``, ``invoke_tool``,
``invoke_reviewer``, ``request_approval``, ``load_session``. Adapters
(Claude Code in module 4, Agno in module 5) implement this Protocol; the
port deliberately does not expose framework internals — everything crosses
the boundary through the frozen types in :mod:`chatbi_runtime_contract.types`
and the :class:`~chatbi_runtime_contract.capabilities.CapabilityManifest`.

No judgment lives in this module: every gate/review/approval decision is a
Governance Kernel call on the adapter side (invariant 2).

Applicable rules: MR-005, invariant 2.
"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from .capabilities import CapabilityManifest
from .types import (
    ApprovalHandle,
    ApprovalRequest,
    CancelRun,
    ContinueRun,
    ReviewRequest,
    ReviewResult,
    RunHandle,
    RunState,
    SessionView,
    StartRun,
    ToolRequest,
    ToolResult,
)


@runtime_checkable
class RuntimePort(Protocol):
    """The nine-method Runtime Port (design §8.3, verbatim surface)."""

    def probe(self) -> CapabilityManifest: ...

    def start_run(self, request: StartRun) -> RunHandle: ...

    def continue_run(self, request: ContinueRun) -> RunHandle: ...

    def cancel_run(self, request: CancelRun) -> RunState: ...

    def stream_events(
        self, run_id: str, cursor: str | None = None
    ) -> Iterator[dict]: ...

    def invoke_tool(self, request: ToolRequest) -> ToolResult: ...

    def invoke_reviewer(self, request: ReviewRequest) -> ReviewResult: ...

    def request_approval(self, request: ApprovalRequest) -> ApprovalHandle: ...

    def load_session(self, session_id: str) -> SessionView: ...
