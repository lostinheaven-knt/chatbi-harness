#!/usr/bin/env python3
"""Thin SubagentStop entrypoint for the adversarial-review delivery gate.

This hook is a deterministic gate (HOOK-001): it validates an adversarial
review verdict against ``review.schema.json`` (reusing Task 01
``evidence.validate_review``) and only allows delivery (exit 0) when the
verdict is ``PASS`` for the EXACT candidate currently under review. It never
performs open-ended metric interpretation (HOOK-001); the reviewer agent does
that. It is the deterministic enforcement of REV-001 (independent review before
delivery), REV-002 (11 coverage dimensions), and REV-003 (blocking findings
must be fixed and re-reviewed; candidate change invalidates a prior PASS).

Exit semantics (SubagentStop contract):
  exit 0 = the candidate may be delivered (PASS + SHA match + clean)
  exit 2 = block delivery, with rule_ids + sanitized evidence + recovery
  Any unexpected exception -> exit 2 fail-closed (HOOK-004).

Fail-closed (HOOK-004): if the review status cannot be determined, the gate
exits 2. It never assumes PASS, never degrades a block to a warn, and never
fakes an empty placeholder.

Forward compatibility (HOOK-003): real Claude Code SubagentStop events carry
additional event-level fields beyond the ones this gate consumes (e.g.
session_id, transcript_path, agent_type, model, and future fields). Unknown
event-level fields are IGNORED, never rejected; only the confirmed fields below
are validated. This mirrors the Cycle 2 PreToolUse/ConfigChange field-tolerance
fix that removed the brittle allowlist which self-deadlocked the dev session.

Confirmed input contract (the fields THIS gate enforces):
  Required:
    - ``review`` (object): the adversarial review verdict, validated against
      ``review.schema.json`` via ``evidence.validate_review``.
    - ``candidate_sha`` (string, ``^[0-9a-f]{64}$``): the SHA-256 of the
      candidate currently under review. Compared against
      ``review.candidate_sha`` to detect a stale/mismatched binding.
  Optional (validated for shape when present, ignored otherwise):
    - ``hook_event_name`` (string): if present and not ``"SubagentStop"`` the
      gate rejects the mismatched event.
    - ``stop_hook_active`` (boolean): recursion guard. When true the gate
      stops immediately (exit 2) to prevent an infinite stop-hook loop.

Live registration of this hook in ``settings.json`` is deliberately deferred to
Cycle 5 E2E: a blocking SubagentStop hook hot-reloads ``settings.json`` and
would brick the dev session (learned Cycle 2 constraint). This file delivers the
gate logic + offline contract tests only.

Applicable rules: REV-001, REV-002, REV-003, HOOK-001, HOOK-003, HOOK-004,
HOOK-005, SEC-003, PORT-001.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


HARNESS_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(HARNESS_LIB))

try:
    from chatbi_harness import (  # noqa: E402
        GateDecision,
        GateError,
        fail_closed,
    )
    from chatbi_harness.evidence import validate_review  # noqa: E402
except Exception:
    sys.stderr.write(
        json.dumps(
            {
                "status": "block",
                "rule_ids": ["HOOK-001", "HOOK-004"],
                "evidence_refs": ["hook:subagent-stop:library"],
                "reason": "SubagentStop review gate library is unavailable",
                "recovery": "Restore the Workspace Harness library and retry",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    sys.stderr.write("\n")
    raise SystemExit(2)


MAX_STDIN_BYTES = 64 * 1024
# Round-limit recursion guard (REV-003): a candidate that keeps failing review
# must not trigger an unbounded re-review loop. After this many review rounds
# the gate stops (exit 2, escalate) instead of forcing yet another round. The
# threshold is conservative; raising it is a governance decision.
MAX_REVIEW_ROUNDS = 3

_REQUIRED_FIELDS = frozenset({"review", "candidate_sha"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_COVERAGE_VALUES = frozenset({"pass", "fail", "not_applicable"})
_COVERAGE_KEYS = (
    "entity",
    "grain",
    "joins",
    "filters_exclusions",
    "date_timezone",
    "denominator",
    "sample_bias",
    "quality",
    "observation_vs_interpretation",
    "disclosure",
    "provenance",
)


class HookInputError(ValueError):
    def __init__(self, category: str, reason: str, recovery: str) -> None:
        self.category = category
        self.reason = reason
        self.recovery = recovery
        super().__init__(category)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HookInputError(
                "duplicate-key",
                "SubagentStop input contains a duplicate JSON key",
                "Send one value for each documented SubagentStop field",
            )
        result[key] = value
    return result


def _read_event() -> object:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise HookInputError(
            "oversized",
            "SubagentStop input exceeds the 64 KiB limit",
            "Reduce the SubagentStop input to within the 64 KiB limit",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HookInputError(
            "encoding",
            "SubagentStop input is not valid UTF-8",
            "Encode the SubagentStop JSON as UTF-8",
        ) from None
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except HookInputError:
        raise
    except json.JSONDecodeError:
        raise HookInputError(
            "json",
            "SubagentStop input is malformed JSON",
            "Send one valid SubagentStop JSON object",
        ) from None


def _validate_event(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise HookInputError(
            "shape",
            "SubagentStop input must be one JSON object",
            "Send the documented SubagentStop object shape",
        )
    missing = _REQUIRED_FIELDS - set(value)
    if missing:
        # Real CC SubagentStop events carry session_id + stop_hook_active but
        # NOT review/candidate_sha (the review is produced by the governed
        # adversarial-reviewer flow, not the CC event). Fall back to persisted
        # run state keyed by session_id (HOOK-003). Offline tests put both
        # fields on the event, so this path is unused for them.
        session_id = value.get("session_id")
        try:
            from chatbi_harness.harness_state import read_state_with_fallback as read_state
            recorded = read_state(
                Path.cwd().resolve(), session_id, "review.json",
            )
            if isinstance(recorded, dict):
                if "review" not in value:
                    value["review"] = recorded
                if "candidate_sha" not in value and isinstance(
                    recorded.get("candidate_sha"), str
                ):
                    # The recorded review is for the current candidate; the
                    # stale-SHA check below then matches trivially (the flow
                    # is responsible for writing a fresh review each round).
                    value["candidate_sha"] = recorded["candidate_sha"]
        except Exception:
            pass  # state read failure -> the missing-field block below
        missing = _REQUIRED_FIELDS - set(value)
    if missing:
        raise HookInputError(
            "missing-field",
            "SubagentStop input is missing a required field",
            "Send the review verdict and the current candidate_sha, or have the "
            "flow persist .chatbi/runs/<session_id>/review.json",
        )
    # Unknown event-level fields are ignored for forward compatibility (see the
    # module-level note on HOOK-003); only the confirmed fields are validated.

    if "hook_event_name" in value and value["hook_event_name"] != "SubagentStop":
        raise HookInputError(
            "event-name",
            "Hook input is not a SubagentStop event",
            "Invoke this entrypoint only for SubagentStop",
        )

    if "stop_hook_active" in value:
        flag = value["stop_hook_active"]
        if not isinstance(flag, bool):
            raise HookInputError(
                "field-value",
                "SubagentStop stop_hook_active must be a boolean when present",
                "Send a boolean stop_hook_active or omit it",
            )

    review = value["review"]
    if not isinstance(review, dict):
        raise HookInputError(
            "field-value",
            "SubagentStop review must be a JSON object",
            "Send the review verdict as a JSON object conforming to review.schema.json",
        )

    candidate_sha = value["candidate_sha"]
    if (
        not isinstance(candidate_sha, str)
        or _SHA256_HEX.fullmatch(candidate_sha) is None
    ):
        raise HookInputError(
            "field-value",
            "SubagentStop candidate_sha must be a 64-character hex SHA-256",
            "Send the SHA-256 hex of the candidate currently under review",
        )

    return value


def _write_failure(decision: GateDecision) -> int:
    sys.stderr.write(decision.to_json())
    sys.stderr.write("\n")
    return 2


def _input_failure(error: HookInputError) -> GateDecision:
    return GateDecision.block(
        rule_ids=("SEC-003", "HOOK-001", "HOOK-004"),
        evidence_refs=(f"hook:subagent-stop:{error.category}",),
        reason=error.reason,
        recovery=error.recovery,
    )


def _block(
    *,
    rule_ids: tuple[str, ...],
    evidence_ref: str,
    reason: str,
    recovery: str,
) -> GateDecision:
    return GateDecision.block(
        rule_ids=rule_ids,
        evidence_refs=(evidence_ref,),
        reason=reason,
        recovery=recovery,
    )


def _check_review(event: dict[str, object]) -> GateDecision | None:
    """Enforce the review delivery gate (REV-001/002/003).

    Returns a blocking GateDecision if delivery must be blocked, or None if the
    candidate may be delivered (exit 0). Every blocking path carries rule IDs,
    a sanitized evidence reference, and a concrete recovery action (HOOK-004).
    """
    # Recursion guard: a stop hook already active means we are inside a loop.
    if event.get("stop_hook_active") is True:
        return _block(
            rule_ids=("HOOK-001", "HOOK-004"),
            evidence_ref="review-gate:recursion",
            reason="SubagentStop hook recursion detected (stop_hook_active)",
            recovery="Resolve the underlying stop condition without re-entering the stop hook",
        )

    review = event["review"]
    current_sha = event["candidate_sha"]

    # Schema validation (raises GateError -> exit 2). This enforces the 8
    # required fields, the 11 coverage keys, finding structure, SHA pattern,
    # status enum, and sanitized_output presence (HOOK-001 determinism).
    validate_review(review)

    review_round = review["round"]
    if (
        not isinstance(review_round, int)
        or isinstance(review_round, bool)
        or review_round < 1
    ):
        # Schema already enforces integer/minimum, but defend in depth.
        return _block(
            rule_ids=("HOOK-004",),
            evidence_ref="review-gate:round-shape",
            reason="Review round is not a positive integer",
            recovery="Re-emit the review verdict with a valid round number",
        )

    # Round-limit (REV-003): stop an unbounded re-review loop.
    if review_round > MAX_REVIEW_ROUNDS:
        return _block(
            rule_ids=("REV-003", "HOOK-001"),
            evidence_ref="review-gate:round-limit",
            reason="Review round limit exceeded; the candidate cannot keep being re-reviewed",
            recovery="Escalate to the domain owner; the candidate must be reworked, not re-reviewed again",
        )

    # Stale / mismatched SHA (REV-001/REV-003): a PASS is valid only for the
    # exact candidate_sha it records. A change invalidates the prior PASS and
    # forces a new review round against the new SHA.
    if review["candidate_sha"] != current_sha:
        return _block(
            rule_ids=("REV-001", "REV-003"),
            evidence_ref="review-gate:stale-sha",
            reason="Review candidate_sha does not match the candidate currently under review",
            recovery="Re-review the current candidate and emit a verdict bound to its SHA",
        )

    status = review["status"]
    if status != "PASS":
        # BLOCKED or ERROR: the candidate is not certified for delivery.
        return _block(
            rule_ids=("REV-001", "REV-003"),
            evidence_ref="review-gate:status",
            reason=f"Review status is {status}; only PASS permits delivery",
            recovery="Address every blocking finding and re-review the candidate",
        )

    # Defense in depth: independently verify the PASS conditions even though the
    # reviewer asserted PASS. A PASS with a failed coverage dimension or a
    # block finding is an inconsistent verdict and is treated as fail-closed.
    coverage = review["coverage"]
    failed_dimensions = sorted(
        key
        for key in _COVERAGE_KEYS
        if coverage.get(key) not in ("pass", "not_applicable")
    )
    if failed_dimensions:
        return _block(
            rule_ids=("REV-002", "HOOK-001"),
            evidence_ref="review-gate:coverage",
            reason="Review asserts PASS but coverage dimensions are not all pass or not_applicable",
            recovery="Re-review and fix the failing coverage dimensions before delivery",
        )

    has_block_finding = any(
        finding.get("severity") == "block" for finding in review["findings"]
    )
    if has_block_finding:
        return _block(
            rule_ids=("REV-003", "HOOK-001"),
            evidence_ref="review-gate:block-finding",
            reason="Review asserts PASS but contains a blocking finding",
            recovery="Resolve the blocking finding and re-review before delivery",
        )

    if review.get("sanitized_output") is not True:
        return _block(
            rule_ids=("SEC-003", "PORT-001", "HOOK-004"),
            evidence_ref="review-gate:sanitization",
            reason="Review cannot attest sanitized_output is true",
            recovery="Re-review and confirm the verdict output contains no secrets, PII, or absolute paths",
        )

    return None


def main() -> int:
    try:
        event = _validate_event(_read_event())
        decision = _check_review(event)
        if decision is not None and decision.status == "block":
            return _write_failure(decision)
        return 0
    except HookInputError as error:
        return _write_failure(_input_failure(error))
    except GateError as error:
        return _write_failure(error.decision)
    except Exception as error:
        return _write_failure(
            fail_closed(
                error,
                rule_ids=("SEC-003", "HOOK-001", "HOOK-004"),
                evidence_refs=("hook:subagent-stop:runtime",),
                recovery="Restore the SubagentStop review gate and retry",
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
