"""Claude Code runtime adapter (multi-runtime module 4, MR-C3).

Closes the loop between the framework-independent IR
(``chatbi_harness_ir``) and the Claude Code target source tree
(``.claude/commands|skills|agents|hooks``):

- IR ``workflow_id`` / ``entry.command``  ->  ``.claude/commands/<id>.md``
- IR ``prompts[*].path``                   ->  ``.claude/<path>`` (skill/agent
  bodies stay in the target tree, design §9.1 "first stage: target source
  managed by the Adapter, no code generation yet")
- hook events (PreToolUse / PostToolUse / SubagentStop / Stop / ConfigChange)
  -> standard ``chatbi.event/v1`` envelopes (impl doc §5.3 table): the
  normalization STARTING POINT that downstream product surfaces consume.
- capability probe -> :class:`CapabilityManifest` (impl doc §6.2 Claude draft,
  adjudication six: development/local expert mode; headless ``claude -p``
  stays ``partial`` per design §9.1).

This module only maps and normalizes; it never executes workflows and never
rewrites the target tree (module 4 keeps ``.claude`` as the audited source,
reconcile.py proves the equivalence).

Applicable rules: HOOK-001 (determinism), invariant 2 (no business rules
outside the kernel), MR-005 (fail-closed capability judgment).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from chatbi_harness_ir import WORKFLOW_IDS, Workflow
from chatbi_runtime_contract.capabilities import (
    CapabilityEntry,
    CapabilityManifest,
    CapabilityStatus,
)
from chatbi_runtime_contract.events import EventType


ADAPTER_NAME = "claude_code"
ADAPTER_VERSION = "0.1.0"

#: Standard event envelope schema version (design §13 field).
EVENT_SCHEMA_VERSION = "chatbi.event/v1"

#: Claude Code target capability manifest draft (impl doc §6.2, adjudication
#: six). ``streaming`` stays partial: ``claude -p`` headless is not
#: production-certified in this module (design §9.1 — module 4 does NOT
#: enable headless production authentication). ``human_approval`` stays
#: partial: product-layer interaction lands on the Agno side; the Claude
#: target relies on STOP + owner (protected_actions level does not block,
#: capabilities.py).
#:
#: The capability NAMES follow the IR requirement tables (module 3) so the
#: fail-closed check (``missing_required``) can match them across BOTH the
#: ``requirements`` and ``capabilities`` layers (OBS-A):
#: ``persistent_session``/``resumable_run`` are provided by the Claude
#: session model in development mode (session_id/current fallback,
#: FF§3.7/§3.8.4); ``realpath_sandbox`` and ``tool_allowlist`` are enforced
#: by the PreToolUse hook + Kernel — the adapter-side tool/realpath
#: precondition (design §9.1 table), hence ``provided_by_adapter``.
#: ``session_resume`` is kept as the §6.2 draft name for compatibility
#: (superseded by ``resumable_run``).
_CLAUDE_CAPABILITIES: dict[str, CapabilityEntry] = {
    "streaming": CapabilityEntry(CapabilityStatus.PARTIAL),
    "persistent_session": CapabilityEntry(CapabilityStatus.PROVIDED_BY_RUNTIME),
    "resumable_run": CapabilityEntry(CapabilityStatus.PROVIDED_BY_RUNTIME),
    "session_resume": CapabilityEntry(CapabilityStatus.PROVIDED_BY_RUNTIME),
    "human_approval": CapabilityEntry(CapabilityStatus.PARTIAL),
    "independent_reviewer": CapabilityEntry(CapabilityStatus.PROVIDED_BY_RUNTIME),
    "realpath_sandbox": CapabilityEntry(CapabilityStatus.PROVIDED_BY_ADAPTER),
    "tool_allowlist": CapabilityEntry(CapabilityStatus.PROVIDED_BY_ADAPTER),
    "evidence_store": CapabilityEntry(CapabilityStatus.PROVIDED_BY_RUNTIME),
}


@dataclass(frozen=True)
class HookEventNormalization:
    """One hook observation normalized to a standard event (impl §5.3).

    ``event_type`` is None when the observation intentionally produces no
    run event (e.g. SessionStart diagnostics land in product state, not in
    the event stream).
    """

    event_type: EventType | None
    envelope: Mapping[str, Any] | None


class ClaudeCodeAdapter:
    """IR <-> Claude Code target source mapping + event normalization.

    Pure mapping/diagnostic object: no side effects, no subprocesses. The
    capability probe is injected as a ``CapabilityProbe``-compatible callable
    so offline tests can pin the manifest without a real ``claude`` binary.
    """

    runtime = "claude_code"
    adapter_name = ADAPTER_NAME
    adapter_version = ADAPTER_VERSION

    def __init__(self, harness_root: Path | None = None) -> None:
        self.harness_root = Path(harness_root) if harness_root is not None else None

    # -- IR -> target tree mapping -------------------------------------------

    def command_for_workflow(self, workflow_id: str) -> Path:
        """IR entry.command -> ``.claude/commands/<workflow_id>.md``.

        Raises ``ValueError`` for unknown workflow ids (the registry of nine
        governed workflows is the closed set, MR-002).
        """
        if workflow_id not in WORKFLOW_IDS:
            raise ValueError(
                f"unknown workflow id {workflow_id!r} (expected one of "
                f"{sorted(WORKFLOW_IDS)})"
            )
        return Path(".claude") / "commands" / f"{workflow_id}.md"

    def prompt_target(self, prompt_path: str) -> Path:
        """IR prompt ref (relative path) -> path under ``.claude/``.

        Prompt bodies stay in the target tree (skills/agents); the IR only
        carries the reference plus the pinned content hash (impl §4.1).
        """
        path = Path(prompt_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"prompt path must be relative: {prompt_path!r}")
        return Path(".claude") / path

    def mapping(self, workflow: Workflow) -> dict[str, Path]:
        """Declared mapping of one IR workflow onto the target tree."""
        result: dict[str, Path] = {
            "entry": self.command_for_workflow(workflow.workflow_id),
        }
        for prompt in workflow.prompts:
            result[prompt.name] = self.prompt_target(prompt.path)
        return result

    # -- hook event normalization (impl §5.3) --------------------------------

    def normalize_hook_event(
        self,
        *,
        hook_event_name: str,
        payload: Mapping[str, Any] | None = None,
        exit_code: int | None = None,
        step_id: str | None = None,
        workflow_id: str | None = None,
    ) -> HookEventNormalization:
        """Normalize one hook observation into the standard event model.

        This is the STARTING POINT of the §5.3 mapping: it selects the
        standard ``event_type`` and carries a PARTIAL 7-field envelope
        (schema_version / event_type / hook_event / exit_code / payload /
        step_id / workflow_id). The full ``chatbi.event/v1`` envelope
        (13 required fields, monotonic ``event_index``, run/session
        identity, ``occurred_at``) is assembled by the product-layer event
        sink that consumes this normalization — full-envelope validation
        lives in ``chatbi_runtime_contract.events.validate_envelope`` and
        is wired in the module-5 adapter/events work. Observations that
        intentionally produce no run event (SessionStart diagnostics)
        return ``event_type=None``.

        Mapping table (impl §5.3):
        - SessionStart diagnostic output -> no run event (product state);
        - PreToolUse allow -> ``tool.requested`` (execution follows);
        - PreToolUse exit 2 -> ``tool.blocked`` (payload = GateDecision JSON);
        - PostToolUse impact record -> ``evidence.recorded``;
        - SubagentStop review gate -> ``review.completed``;
        - Stop gate exit 2 / ConfigChange degradation exit 2 ->
          ``gate.blocked``;
        - Delivery (stop gate exit 0 with delivery context) ->
          ``run.completed`` ONLY after the delivery gate PASSes
          (``payload.gate == "delivery"`` and ``payload.decision == "pass"``,
          events.py constraint).
        """
        name = (hook_event_name or "").lower()
        if name == "sessionstart":
            return HookEventNormalization(event_type=None, envelope=None)
        if name == "pretooluse":
            blocked = bool(exit_code and exit_code == 2)
            event_type = EventType.TOOL_BLOCKED if blocked else EventType.TOOL_REQUESTED
        elif name == "posttooluse":
            event_type = EventType.EVIDENCE_RECORDED
        elif name == "subagentstop":
            event_type = EventType.REVIEW_COMPLETED
        elif name in ("stop", "configchange"):
            if name == "stop" and not (exit_code and exit_code == 2):
                # Delivery: run.completed is only legal after the delivery
                # gate PASS (payload contract below).
                event_type = EventType.RUN_COMPLETED
            else:
                event_type = EventType.GATE_BLOCKED
        else:
            raise ValueError(f"unknown hook event name {hook_event_name!r}")

        envelope_payload = dict(payload or {})
        if event_type is EventType.RUN_COMPLETED:
            envelope_payload.setdefault("gate", "delivery")
            envelope_payload.setdefault("decision", "pass")
        if event_type is EventType.GATE_BLOCKED:
            envelope_payload.setdefault("decision", "block")
        envelope = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_type": event_type.value,
            "hook_event": name,
            "exit_code": exit_code,
            "payload": envelope_payload,
            "step_id": step_id,
            "workflow_id": workflow_id,
        }
        return HookEventNormalization(event_type=event_type, envelope=envelope)

    # -- capability probe -> manifest (impl §6.2) ---------------------------

    def probe(
        self,
        *,
        probe_snapshot: Any | None = None,
        runtime_version: str = "unknown",
        issued_by: str = ADAPTER_NAME,
        generated_at: str | None = None,
    ) -> CapabilityManifest:
        """Build the Claude target capability manifest.

        ``probe_snapshot`` is a kernel ``CapabilitySnapshot`` (from
        ``runtimes.claude_code.probe.probe_local_capabilities``); when the
        probe could not run (None) the runtime version stays unverified and
        the manifest still declares the §6.2 draft statuses (honest
        reporting, FBK-003 — a missing probe never upgrades a capability).
        """
        import datetime

        runtime_version = runtime_version or "unknown"
        if probe_snapshot is not None:
            version = getattr(probe_snapshot, "claude_version", None)
            if version:
                runtime_version = version
        generated = generated_at or datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        manifest = CapabilityManifest(
            runtime=self.runtime,
            runtime_version=runtime_version,
            capabilities=dict(_CLAUDE_CAPABILITIES),
            issued_by=issued_by,
            generated_at=generated,
        )
        violations = manifest.validate()
        if violations:
            raise ValueError(
                "internal capability manifest invalid: " + "; ".join(violations)
            )
        return manifest
