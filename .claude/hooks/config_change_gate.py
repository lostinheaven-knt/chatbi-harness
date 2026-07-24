#!/usr/bin/env python3
"""Thin ConfigChange entrypoint for configuration re-validation gating.

This hook is a deterministic gate (HOOK-001): it only reuses
config/paths/gates library primitives. It re-loads the EffectiveConfig from
disk on every invocation (EffectiveConfig invalidation, technical-design §7.3
item 10) and re-validates schema, path boundaries, sandbox, and permission
boundaries. It never evals, opens a shell, or executes external codebase
content.

Blockable sources (project settings/config): an invalid change is rejected
with exit 2 + rule_ids/evidence_refs/reason/recovery. Managed policy changes
are NOT assumed blockable (technical-design §11.1): the gate re-runs
diagnosis and emits clear structured feedback to stdout (never a silent pass,
never a fake block). The fallback for a managed change the project layer
cannot accept is to restart the session and run /chatbi-init.

Exit semantics (ConfigChange contract, technical-design §11.1):
  exit 0 = re-validation passed (blockable source, silent pass) OR clear
           feedback emitted (managed source: cannot block, not a silent pass)
  exit 2 = block an invalid change from a blockable (project) source
  Any unexpected exception -> exit 2 fail-closed (HOOK-004).

Forward compatibility (HOOK-003): real ConfigChange events carry additional
event-level fields beyond `source` and the optional `file_path`. Unknown
event-level fields are IGNORED, never rejected; only `source` is validated for
presence and `file_path` for shape when present. This mirrors the
pretool_guard.py fix that removed the brittle allowlist which self-deadlocked
the dev session in Ticket 05.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


HARNESS_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(HARNESS_LIB))

try:
    from chatbi_harness import (  # noqa: E402
        GateDecision,
        GateError,
        fail_closed,
        load_effective_config,
    )
    # _configured_roots is the canonical root-boundary validator (existence,
    # symlink, overlap). It is a private helper but is the right reuse target
    # for whole-config re-validation: calling resolve_path_reference would
    # require a single existing target and would hash content unnecessarily.
    from chatbi_harness.paths import _configured_roots  # noqa: E402
except Exception:
    sys.stderr.write(
        json.dumps(
            {
                "status": "block",
                "rule_ids": ["HOOK-001", "HOOK-004"],
                "evidence_refs": ["hook:config-change:library"],
                "reason": "ConfigChange guard library is unavailable",
                "recovery": "Restore the Workspace Harness library and retry",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    sys.stderr.write("\n")
    raise SystemExit(2)


MAX_STDIN_BYTES = 64 * 1024
MAX_SETTINGS_BYTES = 256 * 1024

# technical-design §11.1: managed policy changes are not assumed blockable.
# `source` is the only confirmed required field; its value set is not fully
# pinned by official docs. Only "managed" is singled out as non-blockable;
# any other value is treated as a blockable project-layer source (fail-closed
# for unknown sources). If official docs later pin more non-blockable values,
# record a deviation and extend this set.
_MANAGED_SOURCE = "managed"


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
                "ConfigChange input contains a duplicate JSON key",
                "Send one value for each documented ConfigChange field",
            )
        result[key] = value
    return result


def _read_event() -> object:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise HookInputError(
            "oversized",
            "ConfigChange input exceeds the 64 KiB limit",
            "Reduce the ConfigChange input to within the 64 KiB limit",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HookInputError(
            "encoding",
            "ConfigChange input is not valid UTF-8",
            "Encode the ConfigChange JSON as UTF-8",
        ) from None
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except HookInputError:
        raise
    except json.JSONDecodeError:
        raise HookInputError(
            "json",
            "ConfigChange input is malformed JSON",
            "Send one valid ConfigChange JSON object",
        ) from None


def _validate_event(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise HookInputError(
            "shape",
            "ConfigChange input must be one JSON object",
            "Send the documented ConfigChange object shape",
        )
    if "source" not in value:
        raise HookInputError(
            "missing-field",
            "ConfigChange input is missing the required source field",
            "Send the documented ConfigChange source field",
        )
    source = value["source"]
    if not isinstance(source, str) or not source:
        raise HookInputError(
            "source",
            "ConfigChange source must be a non-empty string",
            "Use a documented ConfigChange source value",
        )
    # Unknown event-level fields are ignored for forward compatibility (see
    # the module-level note on HOOK-003); only `source` is required and
    # `file_path` is validated for shape when present. `file_path` is
    # informational only and is NEVER read or opened by this gate.
    if "hook_event_name" in value and value["hook_event_name"] != "ConfigChange":
        raise HookInputError(
            "event-name",
            "Hook input is not a ConfigChange event",
            "Invoke this entrypoint only for ConfigChange",
        )
    if "file_path" in value:
        file_path = value["file_path"]
        if not isinstance(file_path, str) or not file_path:
            raise HookInputError(
                "file-path",
                "ConfigChange file_path must be a non-empty string when present",
                "Send the documented file_path shape or omit it",
            )
    return value


def _write_failure(decision: GateDecision) -> int:
    sys.stderr.write(decision.to_json())
    sys.stderr.write("\n")
    return 2


def _input_failure(error: HookInputError) -> GateDecision:
    return GateDecision.block(
        rule_ids=("SEC-003", "HOOK-001", "HOOK-004"),
        evidence_refs=(f"hook:config-change:{error.category}",),
        reason=error.reason,
        recovery=error.recovery,
    )


def _check_settings_invariants(workspace_root: Path) -> GateDecision | None:
    """Re-validate settings.json security-critical blocks.

    Returns a blocking GateDecision if the project settings.json explicitly
    degraded a security boundary (permissions.deny removed/emptied or sandbox
    disabled), or None to pass. Settings blocks that are absent entirely are
    NOT flagged here: the security boundary may live in organization-managed
    settings the gate cannot observe; only an explicit degradation (block
    present but weakened) is a blockable project-layer downgrade. This avoids
    false positives on a fresh install whose deny/sandbox live in managed
    settings.
    """
    settings_path = workspace_root / ".claude" / "settings.json"
    if not settings_path.is_file():
        return None
    try:
        raw = settings_path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_SETTINGS_BYTES:
        return GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("config-change:settings:oversized",),
            reason="Project settings.json exceeds the 256 KiB size limit",
            recovery="Use a smaller settings.json file",
        )
    try:
        settings = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return GateDecision.block(
            rule_ids=("HOOK-004", "SEC-001"),
            evidence_refs=("config-change:settings:encoding",),
            reason="Project settings.json is not valid UTF-8",
            recovery="Encode settings.json as UTF-8 JSON",
        )
    except json.JSONDecodeError:
        return GateDecision.block(
            rule_ids=("HOOK-004", "SEC-001"),
            evidence_refs=("config-change:settings:malformed",),
            reason="Project settings.json is malformed JSON",
            recovery="Correct the settings.json syntax and retry",
        )
    if not isinstance(settings, dict):
        return None

    permissions = settings.get("permissions")
    if isinstance(permissions, dict):
        deny = permissions.get("deny")
        if not isinstance(deny, list) or not deny:
            return GateDecision.block(
                rule_ids=("SEC-001", "SCOPE-002", "HOOK-004"),
                evidence_refs=("config-change:permissions:deny-removed",),
                reason="Project settings permissions.deny was removed or emptied",
                recovery="Restore the deny-write and deny-read permission rules",
            )

    sandbox = settings.get("sandbox")
    if isinstance(sandbox, dict):
        if sandbox.get("enabled") is not True:
            return GateDecision.block(
                rule_ids=("SEC-001", "HOOK-004"),
                evidence_refs=("config-change:sandbox:disabled",),
                reason="Project settings sandbox was disabled",
                recovery="Set sandbox.enabled to true and retry",
            )

    return None


def _revalidate(workspace_root: Path) -> GateDecision | None:
    """Re-load EffectiveConfig from disk and re-validate all boundaries.

    §7.3 item 10 (EffectiveConfig invalidation): load_effective_config reads
    the current file state on every call; there is no cached config to reuse.
    Re-validation covers: schema, protected actions, fail_if_sandbox_unavailable,
    secret injection, path-binding shape, fixture mode (via load_effective_config);
    root existence/symlink/overlap (via _configured_roots); and project
    settings.json deny/sandbox invariants (via _check_settings_invariants).

    Raises GateError if config loading or root validation fails.
    Returns a blocking GateDecision if settings invariants degraded, else None.
    """
    shared_path = workspace_root / ".claude" / "chatbi-harness.json"
    local_path = workspace_root / ".claude" / "chatbi-harness.local.json"
    config = load_effective_config(
        shared_path,
        local_path if local_path.exists() else None,
    )
    # Re-validate path boundaries (SCOPE-001): root existence, symlink roots,
    # and overlap between workspace and business codebase roots.
    _configured_roots(config)
    return _check_settings_invariants(workspace_root)


def _emit_managed_feedback(workspace_root: Path) -> int:
    """Emit structured feedback for a managed policy change (§11.1).

    Managed changes cannot be blocked by the project layer. The gate re-runs
    diagnosis and emits clear feedback to stdout (never silent, never a fake
    block). Exit 0 because the project layer cannot block managed changes; the
    feedback makes the outcome explicit and recommends restart + /chatbi-init
    when the effective boundary needs re-establishment.
    """
    revalidation = "passed"
    try:
        settings_decision = _revalidate(workspace_root)
        if settings_decision is not None and settings_decision.status == "block":
            revalidation = "failed"
    except GateError:
        revalidation = "failed"
    except Exception:
        revalidation = "failed"

    feedback = {
        "status": "notified",
        "rule_ids": ["HOOK-001", "HOOK-003"],
        "evidence_refs": ["config-change:managed"],
        "reason": (
            "Managed policy change observed; the project layer cannot block "
            "managed changes (technical-design §11.1)"
        ),
        "recovery": (
            "Restart the session and run /chatbi-init to re-diagnose the "
            "effective security boundary"
        ),
        "revalidation": revalidation,
    }
    sys.stdout.write(json.dumps(feedback, separators=(",", ":"), sort_keys=True))
    sys.stdout.write("\n")
    return 0


def main() -> int:
    try:
        workspace_root = Path.cwd().resolve(strict=True)
        event = _validate_event(_read_event())
        source = event["source"]

        if source == _MANAGED_SOURCE:
            # Managed policy is not assumed blockable (§11.1). Re-run diagnosis
            # and emit clear feedback. Never a silent pass, never a fake block.
            return _emit_managed_feedback(workspace_root)

        # Blockable project-layer source: re-validate and block if invalid.
        try:
            settings_decision = _revalidate(workspace_root)
        except GateError as error:
            return _write_failure(error.decision)
        if settings_decision is not None and settings_decision.status == "block":
            return _write_failure(settings_decision)
        return 0
    except HookInputError as error:
        return _write_failure(_input_failure(error))
    except Exception as error:
        return _write_failure(
            fail_closed(
                error,
                rule_ids=("SEC-003", "HOOK-001", "HOOK-004"),
                evidence_refs=("hook:config-change:runtime",),
                recovery="Restore the ConfigChange guard and retry",
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
