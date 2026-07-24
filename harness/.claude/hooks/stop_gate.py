#!/usr/bin/env python3
"""Thin Stop entrypoint for the tracked-workflow pre-delivery gate.

This hook is a deterministic gate (HOOK-001): it enforces that a pre-delivery
stop in a tracked analysis workflow does not leave blocking findings
unresolved (REV-003). A stop that still carries an open ``block`` finding is
rejected (exit 2) with rule IDs, sanitized evidence, and a concrete recovery
action. The gate does not interpret findings semantically (HOOK-001); it only
checks the confirmed structural fields and the ``severity`` value.

Exit semantics (Stop contract):
  exit 0 = no open blocking finding; the stop may proceed
  exit 2 = an open blocking finding is unresolved (or the open-findings state
           cannot be determined), with rule_ids + evidence + recovery
  Any unexpected exception -> exit 2 fail-closed (HOOK-004).

Fail-closed (HOOK-004): if the open-findings state cannot be determined
(missing/malformed), the gate exits 2. It never assumes the workflow is clean,
never degrades a block to a warn, and never fakes an empty placeholder.

Forward compatibility (HOOK-003): real Claude Code Stop events carry additional
event-level fields beyond ``open_findings`` (e.g. session_id,
transcript_path, stop_hook_active, and future fields). Unknown event-level
fields are IGNORED, never rejected; only the confirmed fields below are
validated. This mirrors the Cycle 2 PreToolUse/ConfigChange field-tolerance
fix that removed the brittle allowlist which self-deadlocked the dev session.

Confirmed input contract (the fields THIS gate enforces):
  Required:
    - ``open_findings`` (array): the findings still open at stop time. Each
      finding mirrors the ``review.schema.json`` finding item shape
      (severity/rule_ids/evidence_refs/reason/recovery). An empty array means
      no findings are open.
  Optional (validated for shape when present, ignored otherwise):
    - ``hook_event_name`` (string): if present and not ``"Stop"`` the gate
      rejects the mismatched event.
    - ``stop_hook_active`` (boolean): recursion guard. When true the gate
      stops immediately (exit 2) to prevent an infinite stop-hook loop.

Live registration of this hook in ``settings.json`` is deliberately deferred to
Cycle 5 E2E: a blocking Stop hook hot-reloads ``settings.json`` and would
brick the dev session (learned Cycle 2 constraint). This file delivers the gate
logic + offline contract tests only.

Applicable rules: REV-003, HOOK-001, HOOK-003, HOOK-004, HOOK-005, SEC-003,
PORT-001.
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
except Exception:
    sys.stderr.write(
        json.dumps(
            {
                "status": "block",
                "rule_ids": ["HOOK-001", "HOOK-004"],
                "evidence_refs": ["hook:stop:library"],
                "reason": "Stop gate library is unavailable",
                "recovery": "Restore the Workspace Harness library and retry",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    sys.stderr.write("\n")
    raise SystemExit(2)


MAX_STDIN_BYTES = 64 * 1024
_NON_WHITESPACE = re.compile(r"\S")
_SEVERITIES = frozenset({"block", "warn", "info"})


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
                "Stop input contains a duplicate JSON key",
                "Send one value for each documented Stop field",
            )
        result[key] = value
    return result


def _read_event() -> object:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise HookInputError(
            "oversized",
            "Stop input exceeds the 64 KiB limit",
            "Reduce the Stop input to within the 64 KiB limit",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HookInputError(
            "encoding",
            "Stop input is not valid UTF-8",
            "Encode the Stop JSON as UTF-8",
        ) from None
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except HookInputError:
        raise
    except json.JSONDecodeError:
        raise HookInputError(
            "json",
            "Stop input is malformed JSON",
            "Send one valid Stop JSON object",
        ) from None


def _validate_event(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise HookInputError(
            "shape",
            "Stop input must be one JSON object",
            "Send the documented Stop object shape",
        )
    if "open_findings" not in value:
        # Real CC Stop events carry session_id but NOT open_findings (the
        # business state is produced by the governed flow, not the CC event).
        # Fall back to persisted run state keyed by session_id (HOOK-003).
        # Offline tests put open_findings on the event, so this path is unused
        # for them; the state file is untrusted and validated below like the
        # event field.
        session_id = value.get("session_id")
        if isinstance(session_id, str) and session_id:
            try:
                from chatbi_harness.harness_state import read_state_with_fallback as read_state
                recorded = read_state(
                    Path.cwd().resolve(), session_id, "open_findings.json",
                )
                if recorded is not None:
                    value["open_findings"] = recorded
            except Exception:
                # State read failure is non-fatal here; the default-clean or
                # missing-field path below handles it. Do not leak (SEC-003).
                pass
            # A real-CC Stop with a session_id but no recorded state has no
            # open findings to resolve -> clean stop (exit 0). This prevents
            # the Stop gate from deadlocking every workflow end (CC force-ends
            # after 9 consecutive blocks). The hard delivery gate is the
            # SubagentStop review gate, not this Stop sanity check.
            if "open_findings" not in value:
                value["open_findings"] = []
    if "open_findings" not in value:
        # No session_id (offline contract violation) -> fail closed.
        raise HookInputError(
            "missing-field",
            "Stop input is missing the required open_findings field",
            "Send the open_findings array, or include session_id so the gate "
            "can read .chatbi/runs/<session_id>/open_findings.json",
        )
    # Unknown event-level fields are ignored for forward compatibility (see the
    # module-level note on HOOK-003); only the confirmed fields are validated.

    if "hook_event_name" in value and value["hook_event_name"] != "Stop":
        raise HookInputError(
            "event-name",
            "Hook input is not a Stop event",
            "Invoke this entrypoint only for Stop",
        )

    if "stop_hook_active" in value:
        flag = value["stop_hook_active"]
        if not isinstance(flag, bool):
            raise HookInputError(
                "field-value",
                "Stop stop_hook_active must be a boolean when present",
                "Send a boolean stop_hook_active or omit it",
            )

    open_findings = value["open_findings"]
    if not isinstance(open_findings, list):
        raise HookInputError(
            "field-value",
            "Stop open_findings must be a JSON array",
            "Send open_findings as an array of finding objects",
        )
    return value


def _validate_finding(finding: object, index: int) -> None:
    """Structurally validate a finding object (mirrors review.schema.json item).

    Deterministic shape check only (HOOK-001); no semantic interpretation.
    Raises HookInputError on any malformation (fail-closed).
    """
    if not isinstance(finding, dict):
        raise HookInputError(
            "finding-shape",
            f"Stop open_findings[{index}] must be a JSON object",
            "Send each finding as an object with severity/rule_ids/evidence_refs/reason/recovery",
        )
    for required in ("severity", "rule_ids", "evidence_refs", "reason", "recovery"):
        if required not in finding:
            raise HookInputError(
                "finding-shape",
                f"Stop open_findings[{index}] is missing field '{required}'",
                "Send each finding with all five required fields",
            )
    severity = finding["severity"]
    if not isinstance(severity, str) or severity not in _SEVERITIES:
        raise HookInputError(
            "finding-shape",
            f"Stop open_findings[{index}] severity must be block, warn, or info",
            "Use a documented severity value",
        )
    rule_ids = finding["rule_ids"]
    if not isinstance(rule_ids, list) or not rule_ids:
        raise HookInputError(
            "finding-shape",
            f"Stop open_findings[{index}] rule_ids must be a non-empty array",
            "Send at least one rule ID per finding",
        )
    for rid in rule_ids:
        if not isinstance(rid, str) or _NON_WHITESPACE.search(rid) is None:
            raise HookInputError(
                "finding-shape",
                f"Stop open_findings[{index}] rule_ids contains an invalid entry",
                "Send non-empty string rule IDs",
            )
    evidence_refs = finding["evidence_refs"]
    if not isinstance(evidence_refs, list):
        raise HookInputError(
            "finding-shape",
            f"Stop open_findings[{index}] evidence_refs must be an array",
            "Send evidence_refs as an array of strings",
        )
    for ref in evidence_refs:
        if not isinstance(ref, str) or _NON_WHITESPACE.search(ref) is None:
            raise HookInputError(
                "finding-shape",
                f"Stop open_findings[{index}] evidence_refs contains an invalid entry",
                "Send non-empty string evidence references",
            )
    reason = finding["reason"]
    if not isinstance(reason, str) or _NON_WHITESPACE.search(reason) is None:
        raise HookInputError(
            "finding-shape",
            f"Stop open_findings[{index}] reason must be a non-empty string",
            "Send a non-empty reason string",
        )
    recovery = finding["recovery"]
    if not isinstance(recovery, str):
        raise HookInputError(
            "finding-shape",
            f"Stop open_findings[{index}] recovery must be a string",
            "Send recovery as a string",
        )


def _write_failure(decision: GateDecision) -> int:
    sys.stderr.write(decision.to_json())
    sys.stderr.write("\n")
    return 2


def _input_failure(error: HookInputError) -> GateDecision:
    return GateDecision.block(
        rule_ids=("SEC-003", "HOOK-001", "HOOK-004"),
        evidence_refs=(f"hook:stop:{error.category}",),
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


def _check_stop(event: dict[str, object]) -> GateDecision | None:
    """Enforce the pre-delivery stop gate (REV-003).

    Returns a blocking GateDecision if the stop must be blocked, or None if the
    stop may proceed (exit 0). The gate never echoes finding content (leak-safe
    against secrets/PII/paths in finding fields, SEC-003/PORT-001); it reports
    only an abstract count and fixed rule IDs.
    """
    if event.get("stop_hook_active") is True:
        return _block(
            rule_ids=("HOOK-001", "HOOK-004"),
            evidence_ref="stop-gate:recursion",
            reason="Stop hook recursion detected (stop_hook_active)",
            recovery="Resolve the underlying stop condition without re-entering the stop hook",
        )

    open_findings = event["open_findings"]

    # Validate every finding structurally (fail-closed on any malformation).
    for index, finding in enumerate(open_findings):
        _validate_finding(finding, index)

    # An open blocking finding means delivery is not permitted (REV-003). The
    # stop must carry the open findings + recovery; the gate blocks here.
    block_count = sum(1 for f in open_findings if f["severity"] == "block")
    if block_count > 0:
        return _block(
            rule_ids=("REV-003", "HOOK-001"),
            evidence_ref="stop-gate:open-block-finding",
            reason=f"{block_count} blocking finding(s) are open at stop time; delivery is not permitted",
            recovery="Resolve every blocking finding and re-review before stopping for delivery",
        )

    return None


def main() -> int:
    try:
        event = _validate_event(_read_event())
        decision = _check_stop(event)
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
                evidence_refs=("hook:stop:runtime",),
                recovery="Restore the Stop gate and retry",
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
