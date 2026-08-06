"""Semantic validation for ``chatbi.harness/v1`` workflow declarations.

``validate_workflow`` returns a list of violation strings (empty = valid).
Checks cover: schema version gating, identifier patterns, step uniqueness
and executor enum, the controlled condition grammar (no eval), kernel
function references (importable dotted paths), tool allow/deny separation,
prompt reference hashes against ``prompts/manifest.json``, requirement
levels, evidence/route shapes, and the PORT-001/SEC-003 content scan (no
machine paths, no secrets, no command names other than ``/chatbi-xxx``).

``validate_registry`` adds the registry-level invariants: globally unique
``workflow_id`` and route targets that resolve to a loaded workflow or the
``none`` / ``owner`` sentinels (MR-002).

Applicable rules: HOOK-001 (determinism), PORT-001, SEC-003, MR-004.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Iterable, Mapping

from .conditions import validate_condition
from .schema import (
    ROUTE_NONE,
    ROUTE_OWNER,
    SCHEMA_MAJOR,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    ExecutorKind,
    RequirementLevel,
    Workflow,
)

# Re-exported so callers have a single validation entry point.
__all__ = ["validate_workflow", "validate_registry", "validate_condition"]

_WORKFLOW_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_STEP_ID_RE = re.compile(r"^[a-z0-9_]+$")
_SIMPLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RULE_ID_RE = re.compile(r"^[A-Z]{2,6}-\d{3}$")
_RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")

#: Any slash-prefixed command name inside the IR must be a /chatbi-xxx
#: workflow command; everything else (e.g. /help) is rejected. The slash
#: must not be glued to a word/separator (avoids false positives on prose
#: like "model/semantic", prompt paths or the "chatbi.harness/v1" version
#: literal — those are not command invocations).
_COMMAND_NAME_RE = re.compile(r"(?<![A-Za-z0-9_./:-])/([a-z][a-z0-9-]*)")

_KERNEL_PREFIX = "chatbi_governance."

#: Executors that must never carry a kernel function reference (they are not
#: deterministic kernel calls).
_NO_FUNCTION_EXECUTORS = frozenset(
    {ExecutorKind.HUMAN_APPROVAL, ExecutorKind.INDEPENDENT_REVIEWER}
)

_FORBIDDEN_PATH_FRAGMENTS = ("..", "\\")


def _resolve_kernel_symbol(dotted: str) -> bool:
    """True iff ``dotted`` resolves to a real attribute of the kernel package.

    Walks the dotted path from the longest importable module prefix (e.g.
    ``chatbi_governance.harness_state``) and then attributes, so submodules
    the kernel does not eagerly expose as package attributes still resolve.
    """
    parts = dotted.split(".")
    resolved = None
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        try:
            resolved = importlib.import_module(prefix)
        except ImportError:
            continue
        rest = parts[i:]
        break
    else:
        return False
    for part in rest:
        if not hasattr(resolved, part):
            return False
        resolved = getattr(resolved, part)
    return True


def _validate_identifier(name: str, pattern: re.Pattern, field: str, errors) -> None:
    if not pattern.match(name):
        errors.append(f"{field}: invalid identifier {name!r} (pattern {pattern.pattern})")


def _validate_path(path: str, field: str, errors) -> None:
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        errors.append(f"{field}: absolute path is forbidden (PORT-001): {path!r}")
        return
    if any(frag in path for frag in _FORBIDDEN_PATH_FRAGMENTS):
        errors.append(f"{field}: path must be relative and must not contain '..': {path!r}")
        return
    if not _RELATIVE_PATH_RE.match(path):
        errors.append(f"{field}: invalid relative path {path!r}")


def _check_prompt_manifest(
    prompt_path: str,
    sha256: str,
    manifest_path: Path | None,
    errors: list[str],
) -> None:
    if manifest_path is None:
        return
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"prompts manifest {manifest_path} unreadable: {exc}")
        return
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, dict):
        errors.append(f"prompts manifest {manifest_path}: missing 'entries' mapping")
        return
    entry = entries.get(prompt_path)
    if not isinstance(entry, dict):
        errors.append(
            f"prompt {prompt_path!r} is not registered in "
            f"prompts/manifest.json"
        )
        return
    registered = entry.get("sha256")
    if not isinstance(registered, str) or registered != sha256:
        errors.append(
            f"prompt {prompt_path!r}: sha256 does not match the registered "
            f"content hash (IR {sha256}, manifest {registered!r})"
        )


def _scan_forbidden_content(text: str, errors: list[str]) -> None:
    """PORT-001/SEC-003 content scan over the serialized workflow.

    Two complementary mechanisms: (1) the kernel ``gates._sanitize_text``
    idempotence check — if redacting machine paths/secrets changes the text,
    the declaration itself carries something that must never reach IR; (2) a
    command-name scan — every slash-prefixed token must be a ``/chatbi-xxx``
    workflow command.
    """
    try:
        from chatbi_governance.gates import _sanitize_text
    except Exception:  # kernel unavailable -> fail closed (HOOK-004)
        errors.append(
            "PORT-001/SEC-003 scan unavailable: chatbi_governance.gates "
            "could not be imported (fail-closed)"
        )
        return
    # The scan runs on the JSON-serialized declaration; JSON doubles every
    # backslash, which would blind the Windows-path pattern. Undo the JSON
    # backslash escaping for the scan (IR text never legitimately needs one).
    text = text.replace("\\\\", "\\")
    sanitized = _sanitize_text(text)
    if sanitized != text:
        errors.append(
            "IR contains content that must be redacted by the governance "
            "sanitizer (machine path or secret, PORT-001/SEC-003): "
            f"{text!r} -> {sanitized!r}"
        )
    for match in _COMMAND_NAME_RE.finditer(text):
        token = match.group(1)
        if not token.startswith("chatbi-"):
            errors.append(
                f"IR references non-chatbi command {('/' + token)!r}; only "
                f"'/chatbi-xxx' workflow commands are allowed (design §4.1)"
            )


def validate_workflow(
    wf: Workflow,
    *,
    prompts_manifest: Path | None = None,
) -> list[str]:
    """Return violation strings for one workflow declaration ([] = valid)."""
    errors: list[str] = []

    # --- schema version gate (design §13 rule 1, MR-004) ---
    if wf.schema_version != SCHEMA_VERSION:
        errors.append(
            f"schema_version {wf.schema_version!r} != {SCHEMA_VERSION!r} "
            "(only chatbi.harness/v1 with major 1 is accepted)"
        )

    # --- identity ---
    _validate_identifier(wf.workflow_id, _WORKFLOW_ID_RE, "workflow_id", errors)
    if not isinstance(wf.workflow_version, int) or wf.workflow_version <= 0:
        errors.append("workflow_version must be a positive integer")
    if not wf.title:
        errors.append("title must be a non-empty string")
    if not wf.description:
        errors.append("description must be a non-empty string")
    if wf.entry.command != wf.workflow_id:
        errors.append(
            f"entry.command {wf.entry.command!r} must equal workflow_id "
            f"{wf.workflow_id!r}"
        )
    _validate_identifier(wf.entry.command, _WORKFLOW_ID_RE, "entry.command", errors)

    # --- steps ---
    if not wf.steps:
        errors.append("workflow must declare at least one step")
    step_ids: set[str] = set()
    for i, step in enumerate(wf.steps):
        ctx = f"steps[{i}].{step.id}"
        if step.id in step_ids:
            errors.append(f"duplicate step id {step.id!r}")
        step_ids.add(step.id)
        _validate_identifier(step.id, _STEP_ID_RE, ctx, errors)
        if not isinstance(step.executor, ExecutorKind):
            errors.append(f"{ctx}.executor: not a valid executor enum value")
            continue
        if step.when is not None and not validate_condition(step.when):
            errors.append(
                f"{ctx}.when: {step.when!r} does not conform to the "
                "controlled condition grammar (no eval/exec/arbitrary calls)"
            )
        if step.function is not None:
            if step.executor in _NO_FUNCTION_EXECUTORS:
                errors.append(
                    f"{ctx}.function is not allowed for executor "
                    f"{step.executor.value!r}"
                )
            elif not step.function.startswith(_KERNEL_PREFIX):
                errors.append(
                    f"{ctx}.function {step.function!r} must reference the "
                    f"governance kernel ({_KERNEL_PREFIX}...) — target-specific "
                    "logic is not allowed (invariant 2)"
                )
            elif not _resolve_kernel_symbol(step.function):
                errors.append(
                    f"{ctx}.function {step.function!r} does not resolve to a "
                    "kernel symbol"
                )
        if step.executor is ExecutorKind.AGENT_WITH_TOOLS:
            if step.tools is None or not step.tools.allow:
                errors.append(
                    f"{ctx}: agent_with_tools steps must declare a non-empty "
                    "tools allow list"
                )
        if step.tools is not None:
            for tool in (*step.tools.allow, *step.tools.deny):
                _validate_identifier(tool, _TOOL_NAME_RE, f"{ctx}.tools", errors)
            overlap = set(step.tools.allow) & set(step.tools.deny)
            if overlap:
                errors.append(
                    f"{ctx}.tools: allow and deny must not overlap: "
                    f"{sorted(overlap)}"
                )

    # --- workflow-level tools ---
    if wf.tools is not None:
        overlap = set(wf.tools.allow) & set(wf.tools.deny)
        if overlap:
            errors.append(
                f"tools: allow and deny must not overlap: {sorted(overlap)}"
            )

    # --- prompts ---
    prompt_names: set[str] = set()
    for prompt in wf.prompts:
        if prompt.name in prompt_names:
            errors.append(f"duplicate prompt name {prompt.name!r}")
        prompt_names.add(prompt.name)
        _validate_identifier(prompt.name, _SIMPLE_NAME_RE, "prompts.name", errors)
        _validate_path(prompt.path, f"prompts.{prompt.name}.path", errors)
        if not _SHA256_RE.match(prompt.sha256):
            errors.append(
                f"prompts.{prompt.name}.sha256: must be a 64-hex content hash"
            )
        _check_prompt_manifest(prompt.path, prompt.sha256, prompts_manifest, errors)

    # --- requirements / capabilities ---
    for field in ("requirements", "capabilities"):
        table = getattr(wf, field)
        for name, level in table.items():
            _validate_identifier(name, _SIMPLE_NAME_RE, f"{field}.{name}", errors)
            if not isinstance(level, RequirementLevel):
                errors.append(
                    f"{field}.{name}: invalid level {level!r} (expected "
                    "required|optional|protected_actions|none)"
                )
            elif level.value not in ("required", "optional", "protected_actions", "none"):
                errors.append(f"{field}.{name}: invalid level {level.value!r}")

    # --- evidence ---
    if wf.evidence is not None:
        _validate_path(wf.evidence.root, "evidence.root", errors)
        for name, schema_file in wf.evidence.schema_versions.items():
            if not _RELATIVE_PATH_RE.match(schema_file) or schema_file.startswith("/"):
                errors.append(
                    f"evidence.schema_versions.{name}: invalid schema file "
                    f"reference {schema_file!r}"
                )

    # --- gates ---
    if wf.gates is not None and wf.gates.delivery is not None:
        for rule_id in wf.gates.delivery.rule_ids:
            if not _RULE_ID_RE.match(rule_id):
                errors.append(
                    f"gates.delivery.rule_ids: invalid governed rule id {rule_id!r}"
                )

    # --- routes ---
    for key, target in wf.routes.items():
        _validate_identifier(key, _SIMPLE_NAME_RE, f"routes.{key}", errors)
        if target in (ROUTE_NONE, ROUTE_OWNER):
            continue
        if not _WORKFLOW_ID_RE.match(target):
            errors.append(
                f"routes.{key}: invalid route target {target!r} (expected a "
                "workflow id, 'none' or 'owner')"
            )

    # --- PORT-001 / SEC-003 content scan ---
    try:
        serialized = json.dumps(wf.to_dict(), sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        errors.append(f"workflow not serializable for content scan: {exc}")
    else:
        _scan_forbidden_content(serialized, errors)

    return errors


def validate_registry(
    workflows: Iterable[Workflow],
    *,
    prompts_manifest: Path | None = None,
) -> list[str]:
    """Validate a set of workflows plus registry-level invariants.

    Registry invariants (MR-002): globally unique ``workflow_id`` and route
    targets that resolve to a loaded workflow or the ``none``/``owner``
    sentinels. Returns violation strings ([] = valid).
    """
    errors: list[str] = []
    by_id: dict[str, Workflow] = {}
    for wf in workflows:
        errors.extend(validate_workflow(wf, prompts_manifest=prompts_manifest))
        if wf.workflow_id in by_id:
            errors.append(f"duplicate workflow_id {wf.workflow_id!r}")
        by_id[wf.workflow_id] = wf
    for wf in workflows:
        for key, target in wf.routes.items():
            if target in (ROUTE_NONE, ROUTE_OWNER):
                continue
            if target not in by_id:
                errors.append(
                    f"{wf.workflow_id}.routes.{key}: unknown route target "
                    f"{target!r}"
                )
    return errors
