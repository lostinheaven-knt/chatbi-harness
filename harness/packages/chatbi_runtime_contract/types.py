"""Vendor-neutral runtime contract value types (frozen dataclasses).

These types are the boundary between a Runtime (Claude Code, Agno, …) and
the ChatBI governance layer. They must stay free of any runtime-specific
import — no ``agno.*``, no ``fastapi``, no Claude Code hook types
(design §6.1). All dataclasses are frozen so exchanged values cannot be
mutated after construction.

The ApprovalRequest field set follows the design's minimum field list
(design §11.1): approval_id / tenant / requester_subject / required_role /
action_type / candidate_sha / evidence_refs / reason / created_at /
expires_at / status / resolved_by / resolved_at / resolution.

Applicable rules: MR-005 (fail-closed), PORT-001 (no machine paths in
transit), invariant 2 (single kernel for all judgments).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class RunState(str, Enum):
    """Product-level run state.

    Terminal states are ``failed``, ``cancelled``, ``blocked`` and
    ``completed``. A run whose delivery gate did not PASS must end
    ``blocked`` — never ``completed`` (ADR-002: Runtime completed !=
    ChatBI completed).
    """

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    RESUMED = "resumed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    COMPLETED = "completed"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunState.FAILED,
            RunState.CANCELLED,
            RunState.BLOCKED,
            RunState.COMPLETED,
        }


class ReviewVerdict(str, Enum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class StartRun:
    workflow_id: str
    request: Mapping[str, Any]
    session_id: str | None = None
    actor: str = ""
    purpose: str = ""


@dataclass(frozen=True)
class ContinueRun:
    run_id: str
    approval_evidence_refs: tuple[str, ...] = ()
    cursor: str | None = None


@dataclass(frozen=True)
class CancelRun:
    run_id: str


@dataclass(frozen=True)
class ToolRequest:
    run_id: str
    step_id: str
    tool_name: str
    tool_input: Mapping[str, Any]
    actor: str = ""


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    payload: Mapping[str, Any] | None = None
    blocked_reason: str | None = None
    content_sha256: str | None = None


@dataclass(frozen=True)
class ReviewRequest:
    run_id: str
    candidate_sha: str
    evidence_refs: tuple[str, ...] = ()
    reviewer_id: str = ""


@dataclass(frozen=True)
class ReviewResult:
    verdict: ReviewVerdict
    candidate_sha: str
    findings: tuple[str, ...] = ()
    sanitized_output: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    tenant: str
    requester_subject: str
    required_role: str
    action_type: str
    candidate_sha: str
    evidence_refs: tuple[str, ...] = ()
    reason: str = ""
    created_at: str = ""
    expires_at: str | None = None
    status: str = "pending"
    resolved_by: str | None = None
    resolved_at: str | None = None
    resolution: str | None = None


@dataclass(frozen=True)
class ApprovalHandle:
    approval_id: str
    status: str = "pending"


@dataclass(frozen=True)
class SessionView:
    session_id: str
    run_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    session_id: str | None = None
    status: RunState = RunState.CREATED
