#!/usr/bin/env python3
"""Cycle 4 Task 01: PostToolUse impact-record gate (thin entry).

``PostToolUse`` fires AFTER a tool has made a change. This hook RECORDS the
change's impact manifest and flags blocking drift; it does NOT undo, revert, or
modify the change (the first line of defense is the Cycle 2 PreToolUse gate and
the OS sandbox; PostToolUse is an after-the-fact record + flag).

Allow (exit 0) when the impact manifest validates, its ``candidate_sha`` matches
the current candidate, and there is no blocking drift. Block (exit 2) with
rule_ids + sanitized evidence + recovery when: the manifest is missing/malformed,
the SHA is stale/mismatched, evidence is missing or uncertain, a P0 eval failed,
the change is an unapproved protected action, or any affected asset requires a
change but is not synced (DOC-004 blocking drift). Unknown event fields are
tolerated (HOOK-003); only the confirmed fields below are validated.

Confirmed fields:
- ``impact_manifest`` (object): the manifest to record (conforms to
  ``schemas/impact-manifest.schema.json`` via ``impact.validate_impact_manifest``).
- ``candidate_sha`` (string, ``^[0-9a-f]{64}$``, optional): the current
  candidate; when present it must equal ``impact_manifest.candidate_sha`` else
  the manifest is stale and the gate blocks (force a re-synced manifest).
- ``tool_name`` (string, optional): context only, never executed.
- ``hook_event_name`` (string, optional): must be ``PostToolUse`` if present.
- ``stop_hook_active`` (boolean, optional): recursion guard.

Applicable rules: DOC-004, EVAL-001/003, SEM-003, HOOK-001/003/004/005,
SEC-003, PORT-001. Live registration in ``settings.json`` is a Cycle 5 E2E step
(a blocking PostToolUse hook hot-reloads ``settings.json``); this script is
delivered with offline contract tests only.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / ".claude" / "lib"
if str(HARNESS_LIB) not in sys.path:
    sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.gates import (  # noqa: E402
    GateDecision,
    GateError,
    _sanitize_text,
    fail_closed,
)
from chatbi_harness.impact import (  # noqa: E402
    ImpactManifest,
    validate_impact_manifest,
)

MAX_STDIN_BYTES = 64 * 1024
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_FIELDS = frozenset({"impact_manifest"})
_VALID_EVENT_NAMES = frozenset({"PostToolUse"})


class HookInputError(ValueError):
    """Raised when the hook input is malformed/oversized."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise HookInputError(f"duplicate key: {key}")
        seen.add(key)
    return dict(pairs)


def _read_event() -> Any:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise HookInputError("stdin exceeds 64 KiB")
    if not raw:
        raise HookInputError("stdin is empty")
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as error:
        raise HookInputError(f"stdin is not valid JSON: {error}") from error


def _input_failure(message: str) -> int:
    decision = GateDecision.block(
        rule_ids=("HOOK-004",),
        evidence_refs=("posttool:input",),
        reason=f"PostToolUse input is malformed: {message}",
        recovery="Send a JSON object with an impact_manifest field on stdin",
    )
    sys.stderr.write(decision.to_json() + "\n")
    return 2


def _block(manifest_dict: dict[str, Any], *, rule_ids: tuple[str, ...],
           reason: str, recovery: str) -> int:
    # Sanitized record of the impact (never echoes raw secret/path content; the
    # manifest itself is already sanitized by build_impact_manifest).
    evidence_ref = f"impact:{manifest_dict.get('change_kind', 'unknown')}"
    decision = GateDecision.block(
        rule_ids=rule_ids,
        evidence_refs=(evidence_ref,),
        reason=reason,
        recovery=recovery,
    )
    sys.stderr.write(decision.to_json() + "\n")
    return 2


def _allow(manifest_dict: dict[str, Any]) -> int:
    # Record a SANITIZED summary to stdout. This is a RECORD only; it never
    # reverts or modifies the change that already occurred. The full manifest
    # (with refs) is recorded durably by the flow via build_impact_manifest; the
    # gate emits only a leak-safe summary (no asset refs / target strings that
    # could carry untrusted content through the event).
    record = {
        "recorded": True,
        "undo": False,
        "modified_change": False,
        "change_kind": manifest_dict.get("change_kind"),
        "candidate_sha": manifest_dict.get("candidate_sha"),
        "evidence_state": manifest_dict.get("evidence_state"),
        "affected_count": len(manifest_dict.get("affected_assets") or []),
        "p0_eval_failed": bool(manifest_dict.get("p0_eval_failed")),
        "protected_action": bool(manifest_dict.get("protected_action")),
    }
    sys.stdout.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


def _blocking_decision(manifest_dict: dict[str, Any]) -> tuple[tuple[str, ...], str, str] | None:
    """Return (rule_ids, reason, recovery) if the manifest has blocking drift,
    else None. Fail-closed: never assume an unconfirmed sync is clean."""
    evidence_state = manifest_dict.get("evidence_state")
    p0 = bool(manifest_dict.get("p0_eval_failed"))
    protected = bool(manifest_dict.get("protected_action"))
    assets = manifest_dict.get("affected_assets") or []
    unsynced = [_sanitize_text(a.get("asset_ref", "?")) for a in assets
                if a.get("change_required") and not a.get("synced")]
    if protected:
        return (("SEM-003", "DOC-004"),
                "Change is a protected action requiring human approval; "
                "PostToolUse cannot approve it",
                "Obtain human owner approval for the protected action before "
                "re-running; PostToolUse only records, it does not approve")
    if p0:
        return (("EVAL-003", "DOC-004"),
                "A P0 evaluation failed for the affected change",
                "Fix the P0 eval regression and re-run with a passing eval")
    if evidence_state == "missing":
        return (("DOC-004", "EVAL-001"),
                "Impact evidence is missing; the blast radius cannot be confirmed",
                "Produce the impact manifest evidence before recording")
    if evidence_state == "uncertain":
        return (("DOC-004", "HOOK-004"),
                "Impact evidence is uncertain; sync completeness cannot be confirmed",
                "Resolve the uncertainty or record a specific gap; do not assume clean")
    if unsynced:
        return (("DOC-004",),
                "Affected assets require changes but are not synced: "
                + ", ".join(unsynced),
                "Apply the candidate changes to the affected assets and re-run")
    return None


def _check_impact(value: dict[str, Any]) -> int:
    if "stop_hook_active" in value:
        flag = value["stop_hook_active"]
        if not isinstance(flag, bool):
            return _block({"change_kind": "unknown"},
                          rule_ids=("HOOK-004",),
                          reason="PostToolUse stop_hook_active must be a boolean when present",
                          recovery="Send a boolean stop_hook_active or omit it")
        if flag:
            return _block({"change_kind": "unknown"},
                          rule_ids=("HOOK-001",),
                          reason="PostToolUse recursion guard active",
                          recovery="Resolve the recursion and re-run")
    if "hook_event_name" in value:
        if value["hook_event_name"] not in _VALID_EVENT_NAMES:
            return _block({"change_kind": "unknown"},
                          rule_ids=("HOOK-003",),
                          reason=f"PostToolUse hook_event_name must be one of {sorted(_VALID_EVENT_NAMES)} if present",
                          recovery="Send hook_event_name=PostToolUse or omit it")
    manifest_dict = value.get("impact_manifest")
    if not isinstance(manifest_dict, dict):
        # Real CC PostToolUse events carry session_id/tool_name/tool_response
        # but NOT impact_manifest. Fall back to persisted run state keyed by
        # session_id (HOOK-003). Offline tests put impact_manifest on the event.
        session_id = value.get("session_id")
        if isinstance(session_id, str) and session_id:
            try:
                from chatbi_harness.harness_state import read_state_with_fallback as read_state
                recorded = read_state(
                    Path.cwd().resolve(), session_id, "impact_manifest.json",
                )
                if isinstance(recorded, dict):
                    manifest_dict = recorded
            except Exception:
                pass  # state read failure -> the required-field block below
    if not isinstance(manifest_dict, dict):
        return _block({"change_kind": "unknown"},
                      rule_ids=("HOOK-004",),
                      reason="PostToolUse impact_manifest is required and must be an object",
                      recovery="Send an impact_manifest object conforming to "
                      "impact-manifest.schema.json, or have the flow persist "
                      ".chatbi/runs/<session_id>/impact_manifest.json")
    try:
        validate_impact_manifest(manifest_dict)
    except GateError as error:
        decision = error.decision
        sys.stderr.write(decision.to_json() + "\n")
        return 2
    candidate_sha = value.get("candidate_sha")
    if candidate_sha is not None:
        if (not isinstance(candidate_sha, str)
                or _SHA256_HEX.fullmatch(candidate_sha) is None):
            return _block(manifest_dict,
                          rule_ids=("HOOK-004",),
                          reason="PostToolUse candidate_sha must be a 64-hex SHA-256 when present",
                          recovery="Send a 64-character hex candidate_sha or omit it")
        if candidate_sha != manifest_dict.get("candidate_sha"):
            return _block(manifest_dict,
                          rule_ids=("DOC-004", "HOOK-004"),
                          reason="impact_manifest candidate_sha is stale/mismatched vs the current candidate",
                          recovery="Re-sync the impact manifest to the current candidate and re-run")
    blocking = _blocking_decision(manifest_dict)
    if blocking is not None:
        rule_ids, reason, recovery = blocking
        return _block(manifest_dict, rule_ids=rule_ids, reason=reason, recovery=recovery)
    return _allow(manifest_dict)


def main() -> int:
    try:
        event = _read_event()
    except HookInputError as error:
        return _input_failure(str(error))
    if not isinstance(event, dict):
        return _input_failure("event must be a JSON object")
    # HOOK-003: tolerate unknown event-level fields. Real CC PostToolUse events
    # carry session_id/tool_name/tool_response but NOT impact_manifest; resolve
    # the business field from persisted run state (keyed by session_id) before
    # the required-field check. Offline tests put impact_manifest on the event.
    if "impact_manifest" not in event:
        session_id = event.get("session_id")
        # read_state_with_fallback tries session-keyed state, then a
        # session-agnostic ``current`` path, so the flow/operator can persist
        # state even without knowing the CC session_id.
        try:
            from chatbi_harness.harness_state import read_state_with_fallback as read_state
            recorded = read_state(
                Path.cwd().resolve(), session_id, "impact_manifest.json",
            )
            if isinstance(recorded, dict):
                event["impact_manifest"] = recorded
        except Exception:
            pass
    for required in _REQUIRED_FIELDS:
        if required not in event:
            return _block({"change_kind": "unknown"},
                          rule_ids=("HOOK-004",),
                          reason=f"PostToolUse is missing required field '{required}'",
                          recovery="Send impact_manifest on stdin, or have the "
                          "flow persist .chatbi/runs/<session_id>/impact_manifest.json")
    try:
        return _check_impact(event)
    except GateError as error:
        sys.stderr.write(error.decision.to_json() + "\n")
        return 2
    except Exception as error:  # pragma: no cover - defensive fail-closed
        return fail_closed(
            error,
            evidence_refs=("posttool:unexpected",),
            rule_ids=("HOOK-004",),
        )


if __name__ == "__main__":
    sys.exit(main())
