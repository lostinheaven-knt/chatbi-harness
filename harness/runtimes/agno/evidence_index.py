"""``.chatbi`` Evidence ↔ runtime DB index (module 5, MR-D2).

Implements the evidence-store capability of the Agno adapter (impl §8.3,
deployment design §10.2 / ADR-003): the ``.chatbi`` file Evidence stays the
governance authority; the runtime only keeps a ``path + content_sha256``
index that detects drift and can be rebuilt from the Evidence when the DB is
lost (never the reverse — the DB cannot recreate missing Evidence).

Write discipline (design §10.2, MVP single writer): the index file is written
atomically (same-directory temp file + fsync + os.replace) under a file lock
(flock), so a crash leaves either the old or the new index, never a partial
one. A failed write raises and never produces a false-success claim.

Index rows carry: ``path`` (workspace-relative, PORT-001: no machine paths),
``content_sha256``, plus optional product metadata parsed from the Evidence
file content when present (run_id/session_id/workflow_id/event_index/
harness_release).

Applicable rules: ADR-003, MR-005, HOOK-001, PORT-001, SEC-003, invariant 3/5.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

INDEX_SCHEMA_VERSION = "chatbi.evidence-index/v1"

#: Evidence tree root relative to the workspace root (matches the shared
#: ``.chatbi`` convention and harness_state._STATE_DIR).
EVIDENCE_ROOT_NAME = ".chatbi"


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_row_metadata(
    path: Path, content: Mapping[str, Any], workspace_root: Path,
) -> dict[str, Any]:
    """Best-effort product metadata from an Evidence JSON document.

    The EvidenceEntry runtime fields use ``native_run_id`` (module-5
    additive fields) and the session id is encoded in the relative path
    (``.chatbi/runs/<session>/<name>.json``); both map onto the index row
    keys (run_id / session_id).
    """
    out: dict[str, Any] = {}
    run_id = content.get("run_id") or content.get("native_run_id")
    if isinstance(run_id, str) and run_id:
        out["run_id"] = run_id
    try:
        relative = path.relative_to(workspace_root).as_posix()
        parts = relative.split("/")
        # .chatbi/runs/<session_id>/<name>.json
        if len(parts) >= 4 and parts[0] == ".chatbi" and parts[1] == "runs":
            out["session_id"] = parts[2]
    except ValueError:
        pass
    for key in ("workflow_id", "event_index", "harness_release"):
        value = content.get(key)
        if isinstance(value, (str, int)) and value not in (None, ""):
            out[key] = value
    return out


@dataclass(frozen=True)
class IndexRow:
    path: str                      # workspace-relative, forward-slash form
    content_sha256: str
    run_id: str | None = None
    session_id: str | None = None
    workflow_id: str | None = None
    event_index: int | None = None
    harness_release: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "path": self.path,
            "content_sha256": self.content_sha256,
        }
        for key in ("run_id", "session_id", "workflow_id", "event_index",
                    "harness_release"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


class EvidenceIndex:
    """Runtime index over the workspace ``.chatbi`` Evidence tree."""

    def __init__(self, workspace_root: Path, state_dir: Path) -> None:
        # .resolve() normalizes filesystem symlink prefixes (macOS temp
        # dirs) so relative_to comparisons against kernel-written (resolved)
        # paths are stable.
        self.workspace_root = Path(workspace_root).resolve()
        self.evidence_root = self.workspace_root / EVIDENCE_ROOT_NAME
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.state_dir / "evidence-index.json"

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan(self) -> list[IndexRow]:
        """Walk ``.chatbi`` and produce rows for every JSON document found.

        A malformed/unreadable document produces a row with
        ``content_sha256="<unreadable>"`` so the drift detector reports it
        instead of silently dropping the file (fail-closed, FBK-003).
        """
        rows: list[IndexRow] = []
        if not self.evidence_root.is_dir():
            return rows
        for path in sorted(self.evidence_root.rglob("*.json")):
            relative = path.relative_to(self.workspace_root).as_posix()
            try:
                raw = path.read_bytes()
                if len(raw) > 256 * 1024:
                    content_sha = "<too-large>"
                else:
                    content_sha = _sha256_bytes(raw)
                try:
                    metadata = _parse_row_metadata(
                        path, json.loads(raw.decode("utf-8")), self.workspace_root
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    metadata = {}
                rows.append(
                    IndexRow(
                        path=relative,
                        content_sha256=content_sha,
                        run_id=metadata.get("run_id"),
                        session_id=metadata.get("session_id"),
                        workflow_id=metadata.get("workflow_id"),
                        event_index=metadata.get("event_index"),
                        harness_release=metadata.get("harness_release"),
                    )
                )
            except OSError:
                rows.append(
                    IndexRow(path=relative, content_sha256="<unreadable>")
                )
        return rows

    # ------------------------------------------------------------------
    # Index file (atomic write + file lock + single writer)
    # ------------------------------------------------------------------

    def _write_index_atomic(self, rows: list[IndexRow]) -> None:
        document = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "rows": [row.to_dict() for row in rows],
        }
        payload = (
            json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1)
            + "\n"
        ).encode("utf-8")
        lock_path = self._index_path.with_suffix(".lock")
        with open(lock_path, "wb") as lock:  # noqa: PTH123
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                tmp = self._index_path.with_suffix(".json.tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, self._index_path)
                except Exception:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read_index(self) -> list[IndexRow]:
        if not self._index_path.is_file():
            return []
        try:
            raw = self._index_path.read_bytes()
            if len(raw) > 64 * 1024 * 1024:
                return []
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return []
        rows: list[IndexRow] = []
        for item in data.get("rows", []):
            try:
                rows.append(
                    IndexRow(
                        path=item["path"],
                        content_sha256=item["content_sha256"],
                        run_id=item.get("run_id"),
                        session_id=item.get("session_id"),
                        workflow_id=item.get("workflow_id"),
                        event_index=item.get("event_index"),
                        harness_release=item.get("harness_release"),
                    )
                )
            except (KeyError, TypeError):
                continue
        return rows

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_index(self) -> Path:
        """Scan the Evidence tree and atomically (re)write the index."""
        rows = self.scan()
        self._write_index_atomic(rows)
        return self._index_path

    def rebuild(self) -> Path:
        """Rebuild the index from the Evidence tree (DB/index lost)."""
        return self.build_index()

    def add(self, path: Path) -> IndexRow:
        """Index ONE Evidence file (called after each kernel .chatbi write)."""
        relative = path.relative_to(self.workspace_root).as_posix()
        raw = path.read_bytes()
        content_sha = _sha256_bytes(raw)
        try:
            metadata = _parse_row_metadata(
                path, json.loads(raw.decode("utf-8")), self.workspace_root
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            metadata = {}
        row = IndexRow(
            path=relative,
            content_sha256=content_sha,
            run_id=metadata.get("run_id"),
            session_id=metadata.get("session_id"),
            workflow_id=metadata.get("workflow_id"),
            event_index=metadata.get("event_index"),
            harness_release=metadata.get("harness_release"),
        )
        existing = [r for r in self._read_index() if r.path != relative]
        self._write_index_atomic([*existing, row])
        return row

    def verify(self) -> list[str]:
        """Detect drift between the index and the Evidence files.

        Returns violation strings; [] = consistent. Evidence is the authority:
        a drift is reported for manual rebuild (never silently auto-fixed in
        a way that could mask tampering, design §17 row 11 / ADR-003).
        """
        indexed = self._read_index()
        if not indexed:
            return ["index is empty or missing; run rebuild() from the "
                    "Evidence tree (ADR-003)"]
        violations: list[str] = []
        for row in indexed:
            path = self.workspace_root / row.path
            if not path.is_file():
                violations.append(
                    f"drift: indexed Evidence missing on disk: {row.path}"
                )
                continue
            try:
                actual = _sha256_bytes(path.read_bytes())
            except OSError as error:
                violations.append(
                    f"drift: cannot read {row.path}: {type(error).__name__}"
                )
                continue
            if actual != row.content_sha256:
                violations.append(
                    f"drift: content changed: {row.path} "
                    f"(indexed {row.content_sha256}, on disk {actual})"
                )
        return violations

    def lookup(self, *, session_id: str | None = None,
               run_id: str | None = None) -> list[IndexRow]:
        """Rows matching the product key (empty when nothing matches)."""
        rows = self._read_index()
        if session_id is not None:
            rows = [r for r in rows if r.session_id == session_id]
        if run_id is not None:
            rows = [r for r in rows if r.run_id == run_id]
        return rows

    def index_path(self) -> Path:
        return self._index_path
