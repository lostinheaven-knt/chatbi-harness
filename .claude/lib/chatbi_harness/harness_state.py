"""Cycle 5 real-CC adapter: persisted harness run-state.

Real Claude Code hook events carry only CC-level fields (``session_id``,
``transcript_path``, ``cwd``, ``tool_name``, ``tool_input``, ``tool_response``,
``stop_hook_active``). They do NOT carry the gates' business fields
(``open_findings`` / ``review``+``candidate_sha`` / ``impact_manifest``). Those
business fields are produced by the governed flow (the reviewer verdict, the
impact manifest, the open findings) and must be PERSISTED so the gates can read
them keyed by ``session_id``.

This module reads/writes per-run JSON state under ``<workspace>/.chatbi/runs/
<session_id>/<name>.json``. Gates call :func:`read_state` to fall back to a
state file when the business field is absent from the event (real CC); offline
tests feed the field on the event directly, so they are unaffected (event field
takes precedence). State files are untrusted data on disk: readers must validate
structure after loading.

Applicable rules: HOOK-001/003/004, SEC-003, PORT-001, REV-001..003, DOC-004.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_STATE_DIR = Path(".chatbi") / "runs"
# session_id is a CC-provided string (typically hex/UUID). Keep only safe
# filename characters to prevent path traversal (PORT-001, SEC-003).
_SAFE_SESSION = re.compile(r"[^A-Za-z0-9._-]")
_MAX_SESSION_LEN = 128


def _safe_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id is required")
    cleaned = _SAFE_SESSION.sub("_", session_id)[:_MAX_SESSION_LEN]
    # Defend against ".." / empty / leading-dot path components.
    cleaned = cleaned.lstrip(".")
    if not cleaned:
        raise ValueError("session_id has no safe characters")
    return cleaned


def state_path(workspace_root: Path, session_id: str, name: str) -> Path:
    """Path to a run state file. The session_id is sanitized to a safe dirname
    component; the resulting path is constrained under ``.chatbi/runs/``."""
    if not isinstance(name, str) or not name or "/" in name or ".." in name:
        raise ValueError(f"invalid state name: {name!r}")
    safe_sid = _safe_session_id(session_id)
    path = (Path(workspace_root) / _STATE_DIR / safe_sid / name).resolve()
    root_check = (Path(workspace_root) / _STATE_DIR).resolve()
    # Ensure the resolved path stays under the state root (no traversal escape).
    try:
        path.relative_to(root_check / safe_sid)
    except ValueError as exc:
        raise ValueError("state path escapes the runs dir") from exc
    return path


def read_state(workspace_root: Path, session_id: str, name: str) -> Any | None:
    """Read a run state JSON file. Returns None if the file is absent, malformed,
    or escapes the state root (fail-safe: treat as not-recorded). Never raises
    on missing/malformed state; the caller decides whether missing is blocking."""
    try:
        path = state_path(workspace_root, session_id, name)
    except ValueError:
        return None
    try:
        if not path.is_file():
            return None
        raw = path.read_bytes()
        if len(raw) > 256 * 1024:
            return None
        return json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


_CURRENT_FALLBACK = "current"


def read_state_with_fallback(
    workspace_root: Path, session_id: str | None, name: str,
) -> Any | None:
    """Read run state keyed by ``session_id`` first; if absent, fall back to a
    session-agnostic ``current`` path (``.chatbi/runs/current/<name>``). The
    ``current`` fallback lets a flow or a human operator persist state without
    discovering the CC ``session_id`` — useful for single-session runs and for
    live E2E exercise. Returns None if neither is present/parseable."""
    if isinstance(session_id, str) and session_id:
        found = read_state(workspace_root, session_id, name)
        if found is not None:
            return found
    return read_state(workspace_root, _CURRENT_FALLBACK, name)


def write_state(workspace_root: Path, session_id: str, name: str, data: Any) -> Path:
    """Atomically write a run state JSON file (temp + rename). Creates parents.
    Used by the governed flow (agent) to persist business state keyed by the
    session_id, so the corresponding gate can read it on the real CC event."""
    path = state_path(workspace_root, session_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(
        tmp,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


__all__ = ["read_state", "read_state_with_fallback", "state_path", "write_state"]
