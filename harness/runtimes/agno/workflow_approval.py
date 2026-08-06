"""Module-5 spike approval seam for the analyze workflow (MR-D3).

The analyze IR declares no ``human_approval`` step (its requests are
read-only ``discover`` operations); the protected-action pause/approve/
continue chain must still be proven end-to-end on the Agno target. This
module builds the target-specific approval step used ONLY when the workflow
is constructed with ``approval_action_type`` (deployment/test config):

- the step carries Agno's native ``requires_confirmation`` HITL pause: when
  the workflow reaches it, the engine emits ``StepPausedEvent`` and the run
  pauses — the controller bridges the pause to the ChatBI
  ApprovalCoordinator (kernel-gated, §11.1);
- the step executor itself is a pass-through: the protected action was never
  executed; after Kernel re-verification PASS the controller confirms the
  requirement and continues the run (先验后续).

No second business rule lives here: the kernel decides the action type,
expiry, SHA and Evidence binding (invariant 2).
"""

from __future__ import annotations

from typing import Any


def make_approval_gate_step(action_type: str) -> Any:
    """Build the Agno Step that pauses for ChatBI human approval."""
    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.workflow import Step

    def _approval_gate(input: Any) -> Any:
        # Runs only AFTER the superuser resolve passed Kernel re-verification
        # (the controller confirms the requirement). The step itself performs
        # no protected action — the pause + resolve is the entire gate.
        return input

    return Step(
        executor=_approval_gate,
        name="human_approval",
        step_id="human_approval",
        description=(
            f"ChatBI human approval required for protected action "
            f"{action_type} (SEM-003); resolved by the superuser through the "
            "ChatBI approval API with Kernel re-verification"
        ),
        requires_confirmation=True,
        confirmation_message=(
            f"Protected action {action_type} requires human owner approval "
            "(SEM-003). Resolve it at the ChatBI approval endpoint; the run "
            "stays paused until the Kernel re-verification passes."
        ),
        on_error="fail",
    )
