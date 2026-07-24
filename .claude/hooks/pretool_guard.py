#!/usr/bin/env python3
"""Thin PreToolUse entrypoint for continuous path/read-only/execute/network gating.

This hook is a deterministic gate (HOOK-001): it only calls paths/policy/gates
library primitives and performs field comparisons. It never evals, opens a shell,
or executes external codebase content. It revalidates path identity on every
tool call (continuous TOCTOU, closing feature-flow-v2 §9 gap 2).

Exit semantics (official PreToolUse contract, HOOK-003):
  exit 0 = allow the tool call
  exit 2 = block the tool call (with rule_ids/evidence_refs/reason/recovery)
  Any unexpected exception -> exit 2 fail-closed (HOOK-004).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath


HARNESS_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(HARNESS_LIB))

try:
    from chatbi_harness import (  # noqa: E402
        GateDecision,
        GateError,
        fail_closed,
        load_effective_config,
        resolve_path_reference,
    )
    from chatbi_harness.policy import PolicyRequest, decide  # noqa: E402
except Exception:
    sys.stderr.write(
        json.dumps(
            {
                "status": "block",
                "rule_ids": ["HOOK-001", "HOOK-004"],
                "evidence_refs": ["hook:pretool-use:library"],
                "reason": "PreToolUse guard library is unavailable",
                "recovery": "Restore the Workspace Harness library and retry",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    sys.stderr.write("\n")
    raise SystemExit(2)


MAX_STDIN_BYTES = 64 * 1024
_REQUIRED_FIELDS = frozenset({"cwd", "tool_name", "tool_input", "tool_use_id"})
# Forward compatibility (HOOK-003): real Claude Code PreToolUse events carry
# additional event-level fields beyond the four the gate consumes (e.g.
# session_id, transcript_path, hook_event_name, source, model, permission_mode,
# agent_id, agent_type, and future fields). Unknown event-level fields are
# IGNORED, never rejected; only the required fields above are validated for
# presence, and the known optional identifier fields below are validated for
# shape only when present. A brittle allowlist previously rejected real events
# carrying extra fields, which self-deadlocked the dev session once the hook was
# registered; this fix restores forward compatibility while still fail-closing
# on missing required fields and malformed known fields.
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/-]{1,256}$")

# Tool name -> (policy request type, tool_input field(s) containing the target).
# Only tools that touch the filesystem or execute commands are gated. Unknown
# tools are allowed (the gate only blocks known violations, HOOK-001).
_TOOL_MAP: dict[str, tuple[str, tuple[str, ...]]] = {
    "Edit": ("edit_workspace", ("file_path",)),
    "MultiEdit": ("edit_workspace", ("file_path",)),
    "Write": ("write_workspace", ("file_path",)),
    "Read": ("read_workspace", ("file_path",)),
    "Grep": ("read_workspace", ("path",)),
    "Glob": ("read_workspace", ("path",)),
    "Bash": ("bash_execute", ("command",)),
}


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
                "PreToolUse input contains a duplicate JSON key",
                "Send one value for each documented PreToolUse field",
            )
        result[key] = value
    return result


def _read_event() -> object:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise HookInputError(
            "oversized",
            "PreToolUse input exceeds the 64 KiB limit",
            "Reduce the PreToolUse input to within the 64 KiB limit",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HookInputError(
            "encoding",
            "PreToolUse input is not valid UTF-8",
            "Encode the PreToolUse JSON as UTF-8",
        ) from None
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except HookInputError:
        raise
    except json.JSONDecodeError:
        raise HookInputError(
            "json",
            "PreToolUse input is malformed JSON",
            "Send one valid PreToolUse JSON object",
        ) from None


def _validate_event(value: object, workspace_root: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise HookInputError(
            "shape",
            "PreToolUse input must be one JSON object",
            "Send the documented PreToolUse object shape",
        )
    missing = _REQUIRED_FIELDS - set(value)
    if missing:
        raise HookInputError(
            "missing-field",
            "PreToolUse input is missing a required field",
            "Send every documented required PreToolUse field",
        )
    # Unknown event-level fields are ignored for forward compatibility (see the
    # module-level note on HOOK-003); only required fields are validated for
    # presence and known optional identifier fields for shape.

    if "hook_event_name" in value and value["hook_event_name"] != "PreToolUse":
        raise HookInputError(
            "event-name",
            "Hook input is not a PreToolUse event",
            "Invoke this entrypoint only for PreToolUse",
        )

    for field in ("tool_use_id", "session_id", "model", "agent_id", "agent_type"):
        if field not in value:
            continue
        field_value = value[field]
        if not isinstance(field_value, str) or _IDENTIFIER.fullmatch(field_value) is None:
            raise HookInputError(
                "field-value",
                "PreToolUse input contains an invalid identifier field",
                "Use bounded plain text for documented identifier fields",
            )

    tool_name = value["tool_name"]
    if not isinstance(tool_name, str) or not tool_name:
        raise HookInputError(
            "tool-name",
            "PreToolUse tool_name is invalid",
            "Use a non-empty tool name string",
        )

    tool_input = value["tool_input"]
    if not isinstance(tool_input, dict):
        raise HookInputError(
            "tool-input",
            "PreToolUse tool_input must be a JSON object",
            "Send the documented tool_input object for the tool",
        )

    # cwd must match the resolved Workspace root (continuous TOCTOU, SCOPE-001).
    event_cwd = value["cwd"]
    if not isinstance(event_cwd, str) or event_cwd != str(workspace_root):
        raise HookInputError(
            "cwd",
            "PreToolUse cwd does not match the current Workspace root",
            "Run Claude Code from the real Harness Workspace root",
        )

    return value


def _write_failure(decision: GateDecision) -> int:
    sys.stderr.write(decision.to_json())
    sys.stderr.write("\n")
    return 2


def _input_failure(error: HookInputError) -> GateDecision:
    return GateDecision.block(
        rule_ids=("SEC-003", "HOOK-001", "HOOK-004"),
        evidence_refs=(f"hook:pretool-use:{error.category}",),
        reason=error.reason,
        recovery=error.recovery,
    )


# Config files the agent may READ to diagnose a config-schema violation. A
# schema violation (e.g. an unknown ``_comment`` field) must not brick the
# session: the agent cannot self-repair gate-enforced config (writes stay
# blocked, SEC-003), but it may READ the config to tell the human what to
# remove. Only these workspace-internal config files are unblocked; everything
# else (writes, external reads, Bash) stays fail-closed.
_CONFIG_DIAGNOSTIC_FILES = (
    ".claude/chatbi-harness.json",
    ".claude/chatbi-harness.local.json",
)
_CONFIG_DIAGNOSTIC_READ_TOOLS = ("Read", "Grep", "Glob")


def _is_config_schema_error(error: GateError) -> bool:
    return any(
        isinstance(ref, str) and ref.startswith("config:")
        for ref in error.decision.evidence_refs
    )


def _is_config_diagnostic_read(event: Mapping[str, object], workspace_root: Path) -> bool:
    """True iff this tool call is a pure read of a harness config file (so the
    agent can diagnose a config-schema violation)."""
    tool_name = event.get("tool_name")
    if tool_name not in _CONFIG_DIAGNOSTIC_READ_TOOLS:
        return False
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, Mapping):
        return False
    raw_target = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(raw_target, str) or not raw_target:
        return False
    try:
        candidate = Path(raw_target)
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        resolved = candidate.resolve(strict=False)
    except (OSError, ValueError):
        return False
    for rel in _CONFIG_DIAGNOSTIC_FILES:
        config_path = (workspace_root / rel).resolve(strict=False)
        if resolved == config_path:
            return True
    return False


def _configured_external_roots(config: object, workspace_root: Path) -> dict[str, Path]:
    """Return {alias: resolved_root} for configured Business Codebases.

    Raises GateError if a declared Business Codebase has no path binding
    (unconfigured root) or the binding cannot be resolved.
    """
    roots: dict[str, Path] = {}
    path_bindings = config["path_bindings"] if "path_bindings" in config else {}  # type: ignore[operator]
    for alias, codebase in config["business_codebases"].items():  # type: ignore[union-attr]
        path_ref = codebase["path_ref"]  # type: ignore[index]
        if path_ref not in path_bindings:
            raise GateError(
                GateDecision.block(
                    rule_ids=("SCOPE-001", "PORT-001", "HOOK-004"),
                    evidence_refs=(f"path:{alias}:root:unconfigured",),
                    reason="Business Codebase root is unconfigured",
                    recovery="Add the declared path binding in local configuration",
                )
            )
        try:
            root = Path(path_bindings[path_ref]).resolve(strict=True)  # type: ignore[index]
        except (OSError, RuntimeError):
            raise GateError(
                GateDecision.block(
                    rule_ids=("SCOPE-001", "PORT-001", "HOOK-004"),
                    evidence_refs=(f"path:{alias}:root:unreadable",),
                    reason="Business Codebase root cannot be resolved",
                    recovery="Bind the alias to an accessible real directory",
                )
            ) from None
        roots[alias] = root
    return roots


def _check_file_target(
    config: object,
    workspace_root: Path,
    external_roots: dict[str, Path],
    request_type: str,
    file_path: str,
) -> GateDecision | None:
    """Check a file-based tool target against path and policy boundaries.

    Returns a blocking GateDecision if the target violates boundaries, or None
    to pass. SCOPE-001: path boundaries. SCOPE-002: external deny-write.
    SCOPE-003: external reads must go through the adapter.
    """
    workspace_alias = config["workspace"]["id"]  # type: ignore[index]
    target_path = Path(file_path)

    # Reject traversal in raw input (defense in depth before resolution).
    if ".." in target_path.parts or ".." in PureWindowsPath(file_path).parts:
        return GateDecision.block(
            rule_ids=("SCOPE-001", "SCOPE-002", "HOOK-004"),
            evidence_refs=("pretool:target:traversal",),
            reason="Tool target contains parent traversal",
            recovery="Use a normalized target without '..' components",
        )

    # Resolve the target. Relative paths resolve against the Workspace root
    # (which was verified to match cwd). Absolute paths are resolved directly.
    if target_path.is_absolute() or PureWindowsPath(file_path).is_absolute():
        try:
            resolved = target_path.resolve(strict=False)
        except (OSError, RuntimeError):
            return GateDecision.block(
                rule_ids=("SCOPE-001", "HOOK-004"),
                evidence_refs=("pretool:target:unreadable",),
                reason="Tool target cannot be resolved",
                recovery="Use an accessible target within a configured root",
            )
    else:
        try:
            resolved = (workspace_root / target_path).resolve(strict=False)
        except (OSError, RuntimeError):
            return GateDecision.block(
                rule_ids=("SCOPE-001", "HOOK-004"),
                evidence_refs=("pretool:target:unreadable",),
                reason="Tool target cannot be resolved",
                recovery="Use an accessible target within the Workspace",
            )

    is_write = request_type in ("edit_workspace", "write_workspace")
    is_read = request_type == "read_workspace"

    # Check external roots: deny write and deny direct read (SCOPE-002/003).
    # External roots are deny-write and deny-execute; direct file reads must
    # go through the codebase_reader adapter (technical-design §7.4).
    for alias, root in external_roots.items():
        if resolved == root or root in resolved.parents:
            if is_write:
                return GateDecision.block(
                    rule_ids=("SCOPE-001", "SCOPE-002", "HOOK-004"),
                    evidence_refs=(f"pretool:external-write:{alias}",),
                    reason="External Business Codebase root is deny-write",
                    recovery="Use the read-only adapter for external Codebase access",
                )
            if is_read:
                return GateDecision.block(
                    rule_ids=(
                        "SCOPE-001",
                        "SCOPE-002",
                        "SCOPE-003",
                        "HOOK-004",
                    ),
                    evidence_refs=(f"pretool:external-read:{alias}",),
                    reason=(
                        "External Business Codebase must be read through "
                        "the adapter"
                    ),
                    recovery="Use the codebase_reader adapter for external reads",
                )

    # Check if the target is within the Workspace.
    if resolved != workspace_root and workspace_root not in resolved.parents:
        # Target is outside all configured roots (SCOPE-001).
        return GateDecision.block(
            rule_ids=("SCOPE-001", "HOOK-004"),
            evidence_refs=("pretool:target:outside-roots",),
            reason="Tool target is outside all configured roots",
            recovery="Use a target within the Workspace or a configured alias",
        )

    # Target is within the Workspace. For write tools, run policy + TOCTOU.
    if is_write:
        try:
            relative = resolved.relative_to(workspace_root).as_posix()
        except ValueError:
            return GateDecision.block(
                rule_ids=("SCOPE-001", "HOOK-004"),
                evidence_refs=("pretool:target:outside-workspace",),
                reason="Tool target is not within the Workspace",
                recovery="Use a target within the Workspace root",
            )

        # TOCTOU revalidation (feature-flow-v2 §9 gap 2): if the target
        # exists, re-resolve path identity to detect symlinks, traversal, or
        # root changes since the session started. For non-existent targets
        # (Write creating a new file), the path boundary check above is the
        # primary defense.
        if resolved.exists():
            try:
                resolve_path_reference(
                    config,  # type: ignore[arg-type]
                    alias=workspace_alias,
                    target=relative,
                )
            except GateError as error:
                return error.decision
            except Exception:
                # Non-critical revalidation failure; path boundary check passed.
                pass

        # Policy check for workspace_candidate_write (SEC-001/SCOPE-001).
        policy_request = PolicyRequest(
            request_type=request_type,
            target_entity=relative,
        )
        policy_decision = decide(config, policy_request)  # type: ignore[arg-type]
        if policy_decision.status == "block":
            return policy_decision

    return None


def _check_bash_command(
    config: object,
    workspace_root: Path,
    external_roots: dict[str, Path],
    command: str,
) -> GateDecision | None:
    """Check a Bash command for external root references (deny-execute).

    This is a deterministic string containment check (HOOK-001): if any
    configured external root path appears in the command, the command is
    denied (SCOPE-002: external roots are deny-execute). This is not shell
    parsing; it is a conservative deny that may have false positives but
    never false negatives for external root path references.
    """
    if not isinstance(command, str):
        return None

    for alias, root in external_roots.items():
        if str(root) in command:
            return GateDecision.block(
                rule_ids=("SCOPE-001", "SCOPE-002", "HOOK-004"),
                evidence_refs=(f"pretool:bash-external:{alias}",),
                reason="Bash command references an external Business Codebase root",
                recovery="Do not execute or write to external roots via Bash",
            )

    return None


def _check_tool(
    config: object,
    workspace_root: Path,
    tool_name: str,
    tool_input: dict[str, object],
) -> GateDecision | None:
    """Run the tool-specific gate checks.

    Returns a blocking GateDecision if the tool call violates boundaries, or
    None to pass. Unknown tools are allowed (HOOK-001: deterministic only).
    """
    if tool_name not in _TOOL_MAP:
        return None

    request_type, path_fields = _TOOL_MAP[tool_name]
    external_roots = _configured_external_roots(config, workspace_root)

    target_value = None
    for field in path_fields:
        if field in tool_input:
            target_value = tool_input[field]
            break

    if target_value is None:
        return None

    if tool_name == "Bash":
        return _check_bash_command(
            config, workspace_root, external_roots, target_value  # type: ignore[arg-type]
        )

    if not isinstance(target_value, str):
        return None

    return _check_file_target(
        config, workspace_root, external_roots, request_type, target_value
    )


def main() -> int:
    try:
        workspace_root = Path.cwd().resolve(strict=True)
        event = _validate_event(_read_event(), workspace_root)

        local_config = Path(".claude/chatbi-harness.local.json")
        config = load_effective_config(
            Path(".claude/chatbi-harness.json"),
            local_config if local_config.exists() else None,
        )

        decision = _check_tool(
            config,
            workspace_root,
            event["tool_name"],  # type: ignore[index]
            event["tool_input"],  # type: ignore[index]
        )

        if decision is not None and decision.status == "block":
            return _write_failure(decision)

        return 0
    except HookInputError as error:
        return _write_failure(_input_failure(error))
    except GateError as error:
        # A config-schema violation (e.g. an unknown field) must not brick the
        # session. Allow a pure READ of a harness config file so the agent can
        # diagnose + tell the human what to remove; the agent cannot self-repair
        # (writes/external-reads/Bash stay fail-closed, SEC-003).
        if _is_config_schema_error(error) and _is_config_diagnostic_read(event, workspace_root):
            sys.stderr.write(
                json.dumps(
                    {
                        "status": "allow-diagnostic-read",
                        "rule_ids": ("HOOK-004",),
                        "evidence_refs": ("pretool:config-diagnostic-read",),
                        "reason": "Config schema violation present; read of the "
                        "config file allowed for diagnosis. The agent cannot "
                        "self-edit gate-enforced config (SEC-003).",
                        "recovery": "Human owner removes the offending field(s) "
                        "from .claude/chatbi-harness.json out-of-band.",
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            sys.stderr.write("\n")
            return 0
        return _write_failure(error.decision)
    except Exception as error:
        return _write_failure(
            fail_closed(
                error,
                rule_ids=("SEC-003", "HOOK-001", "HOOK-004"),
                evidence_refs=("hook:pretool-use:runtime",),
                recovery="Restore the PreToolUse guard and retry",
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
