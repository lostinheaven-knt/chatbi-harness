#!/usr/bin/python3
"""Validate the local Python binding before starting the SessionStart Hook."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


MAX_LOCAL_CONFIG_BYTES = 256 * 1024
_FAILURE = {
    "evidence_refs": ["hook:session-start:python-binding"],
    "reason": (
        "Confirmed Python binding is unavailable or outside the approved "
        "runtime boundary"
    ),
    "recovery": (
        "Set CHATBI_PYTHON to a confirmed absolute executable outside "
        "Workspace and Business roots"
    ),
    "rule_ids": ["SCOPE-001", "SEC-001", "PORT-001", "HOOK-004"],
    "status": "block",
}


class _BindingError(ValueError):
    pass


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _BindingError("duplicate-key")
        result[key] = value
    return result


def _reject_non_finite(_value):
    raise _BindingError("non-finite-number")


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _load_business_roots(workspace_root: Path):
    local_config = workspace_root / ".claude" / "chatbi-harness.local.json"
    try:
        local_mode = local_config.lstat().st_mode
    except FileNotFoundError:
        return ()
    if stat.S_ISLNK(local_mode) or not stat.S_ISREG(local_mode):
        raise _BindingError("unsafe-local-config")
    if os.stat(local_config, follow_symlinks=False).st_size > MAX_LOCAL_CONFIG_BYTES:
        raise _BindingError("oversized-local-config")
    try:
        raw = local_config.read_bytes()
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _BindingError("invalid-local-config") from error
    if not isinstance(data, dict):
        raise _BindingError("invalid-local-config-shape")
    path_bindings = data.get("path_bindings", {})
    if not isinstance(path_bindings, dict):
        raise _BindingError("invalid-path-bindings")

    roots = []
    for root_value in path_bindings.values():
        if not isinstance(root_value, str):
            raise _BindingError("invalid-business-root")
        declared_root = Path(root_value)
        if not declared_root.is_absolute():
            raise _BindingError("relative-business-root")
        try:
            declared_mode = declared_root.lstat().st_mode
            resolved_root = declared_root.resolve(strict=True)
            resolved_mode = os.stat(resolved_root, follow_symlinks=False).st_mode
        except (OSError, RuntimeError) as error:
            raise _BindingError("unavailable-business-root") from error
        if stat.S_ISLNK(declared_mode) or not stat.S_ISDIR(resolved_mode):
            raise _BindingError("unsafe-business-root")
        roots.append(resolved_root)
    return tuple(roots)


def _validated_python(workspace_root: Path, business_roots) -> Path:
    binding = os.environ.get("CHATBI_PYTHON")
    if not binding:
        raise _BindingError("missing-python-binding")
    declared_python = Path(binding)
    if not declared_python.is_absolute() or ".." in declared_python.parts:
        raise _BindingError("invalid-python-binding")
    try:
        resolved_python = declared_python.resolve(strict=True)
        python_mode = os.stat(resolved_python, follow_symlinks=False).st_mode
    except (OSError, RuntimeError) as error:
        raise _BindingError("unavailable-python-binding") from error
    if not stat.S_ISREG(python_mode) or not os.access(resolved_python, os.X_OK):
        raise _BindingError("non-executable-python-binding")
    if _is_within(resolved_python, workspace_root):
        raise _BindingError("workspace-python-binding")
    if any(_is_within(resolved_python, root) for root in business_roots):
        raise _BindingError("business-python-binding")
    return resolved_python


def main() -> int:
    try:
        workspace_root = Path.cwd().resolve(strict=True)
        business_roots = _load_business_roots(workspace_root)
        python_executable = _validated_python(workspace_root, business_roots)
        hook = workspace_root / ".claude" / "hooks" / "session_diagnose.py"
        hook_mode = hook.lstat().st_mode
        if stat.S_ISLNK(hook_mode) or not stat.S_ISREG(hook_mode):
            raise _BindingError("unsafe-hook")
        safe_environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": os.defpath,
        }
        for name in ("HOME", "XDG_CONFIG_HOME"):
            value = os.environ.get(name)
            if value and Path(value).is_absolute():
                safe_environment[name] = value
        os.execve(
            str(python_executable),
            [str(python_executable), "-B", "-I", str(hook)],
            safe_environment,
        )
    except Exception:
        sys.stderr.write(
            json.dumps(_FAILURE, separators=(",", ":"), sort_keys=True)
        )
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
