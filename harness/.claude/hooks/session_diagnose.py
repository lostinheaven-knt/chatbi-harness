#!/usr/bin/env python3
"""Thin SessionStart entrypoint for the shared ChatBI diagnostic core."""

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
        fail_closed,
        run_init_diagnostic,
    )
except Exception:
    sys.stderr.write(
        json.dumps(
            {
                "status": "block",
                "rule_ids": ["HOOK-001", "HOOK-004"],
                "evidence_refs": ["hook:session-start:library"],
                "reason": "SessionStart diagnostic library is unavailable",
                "recovery": "Restore the Workspace Harness library and retry",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    sys.stderr.write("\n")
    raise SystemExit(2)


MAX_STDIN_BYTES = 64 * 1024
_REQUIRED_FIELDS = frozenset(
    {"session_id", "transcript_path", "cwd", "hook_event_name", "source", "model"}
)
_OPTIONAL_FIELDS = frozenset({"permission_mode", "agent_id", "agent_type"})
_SOURCES = frozenset({"startup", "resume", "clear", "compact"})
_PERMISSION_MODES = frozenset(
    {"default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions"}
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/-]{1,256}$")


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
                "SessionStart input contains a duplicate JSON key",
                "Send one value for each documented SessionStart field",
            )
        result[key] = value
    return result


def _read_event() -> object:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise HookInputError(
            "oversized",
            "SessionStart input exceeds the 64 KiB limit",
            "Send only the documented SessionStart fields",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HookInputError(
            "encoding",
            "SessionStart input is not valid UTF-8",
            "Encode the SessionStart JSON as UTF-8",
        ) from None
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except HookInputError:
        raise
    except json.JSONDecodeError:
        raise HookInputError(
            "json",
            "SessionStart input is malformed JSON",
            "Send one valid SessionStart JSON object",
        ) from None


def _shape_error(category: str, reason: str, recovery: str) -> HookInputError:
    return HookInputError(category, reason, recovery)


def _validate_event(value: object, workspace_root: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _shape_error(
            "shape",
            "SessionStart input must be one JSON object",
            "Send the documented SessionStart object shape",
        )
    missing = _REQUIRED_FIELDS - set(value)
    if missing:
        raise _shape_error(
            "missing-field",
            "SessionStart input is missing a required field",
            "Send every documented required SessionStart field",
        )
    unknown = set(value) - _REQUIRED_FIELDS - _OPTIONAL_FIELDS
    if unknown:
        raise _shape_error(
            "unknown-field",
            "SessionStart input contains an undocumented field",
            "Remove fields outside the documented SessionStart shape",
        )

    for field in ("session_id", "model", "agent_id", "agent_type"):
        if field not in value:
            continue
        field_value = value[field]
        if not isinstance(field_value, str) or _IDENTIFIER.fullmatch(field_value) is None:
            raise _shape_error(
                "field-value",
                "SessionStart input contains an invalid identifier field",
                "Use bounded plain text for documented identifier fields",
            )
    transcript_path = value["transcript_path"]
    if (
        not isinstance(transcript_path, str)
        or len(transcript_path) > 4096
        or not Path(transcript_path).is_absolute()
        or ".." in Path(transcript_path).parts
    ):
        raise _shape_error(
            "transcript-shape",
            "SessionStart transcript_path has an invalid shape",
            "Use the normalized absolute transcript_path supplied by Claude Code",
        )
    if value["hook_event_name"] != "SessionStart":
        raise _shape_error(
            "event-name",
            "Hook input is not a SessionStart event",
            "Invoke this entrypoint only for SessionStart",
        )
    source = value["source"]
    if not isinstance(source, str) or source not in _SOURCES:
        raise _shape_error(
            "source",
            "SessionStart source is invalid",
            "Use startup, resume, clear, or compact",
        )
    if "permission_mode" in value and value["permission_mode"] not in _PERMISSION_MODES:
        raise _shape_error(
            "permission-mode",
            "SessionStart permission_mode is invalid",
            "Use a documented Claude Code permission mode",
        )
    event_cwd = value["cwd"]
    if not isinstance(event_cwd, str) or event_cwd != str(workspace_root):
        raise _shape_error(
            "cwd",
            "SessionStart cwd does not match the current Workspace root",
            "Start Claude Code from the real Harness Workspace root",
        )
    return value


def _write_failure(decision: GateDecision) -> int:
    sys.stderr.write(decision.to_json())
    sys.stderr.write("\n")
    return 2


def _input_failure(error: HookInputError) -> GateDecision:
    return GateDecision.block(
        rule_ids=("SEC-003", "HOOK-001", "HOOK-004"),
        evidence_refs=(f"hook:session-start:{error.category}",),
        reason=error.reason,
        recovery=error.recovery,
    )


def main() -> int:
    try:
        workspace_root = Path.cwd().resolve(strict=True)
        event = _validate_event(_read_event(), workspace_root)

        local_config = Path(".claude/chatbi-harness.local.json")
        diagnostic = run_init_diagnostic(
            Path(".claude/chatbi-harness.json"),
            local_config if local_config.exists() else None,
        )
        output = {
            "schema_version": 1,
            "hook_event_name": "SessionStart",
            "source": event["source"],
            "chatbi_commands_available": diagnostic.status != "BLOCKED",
            "diagnostic": diagnostic.to_dict(),
        }
        sys.stdout.write(
            json.dumps(
                output,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        sys.stdout.write("\n")
        return 0
    except HookInputError as error:
        return _write_failure(_input_failure(error))
    except Exception as error:
        return _write_failure(
            fail_closed(
                error,
                rule_ids=("SEC-003", "HOOK-001", "HOOK-004"),
                evidence_refs=("hook:session-start:runtime",),
                recovery="Restore the SessionStart diagnostic and retry",
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
