"""Main-agent FileTools allowlist (P0-3).

The product launcher attaches a read-only FileTools bundle to the governed
agent. Without a prefix allowlist the bundle's ``base_dir`` is the whole
Warehouse Workspace, so the model can search ``runtimes/`` / ``packages/``
and treat Harness implementation as documentation.

This module keeps the reviewer FileTools unchanged (foreign-session isolation
only). The main agent may read only:

- ``models/``
- ``docs/org/``
- ``.chatbi/bootstrap/``
- ``semantic/``

Deny text must not leak machine absolute paths (PORT-001).

T2 runaway (session d96c0bad): ``str.lstrip("./")`` stripped the leading
dot of ``.chatbi/...``, so the advertised bootstrap allowlist denied every
inventory read. pathlib ``Path.glob("**/*")`` also skips hidden directories,
so ``search_files`` never listed ``.chatbi/bootstrap/source_inventory.json``.
"""
from __future__ import annotations

from pathlib import Path

_ALLOWED_PREFIXES = (
    "models/",
    "docs/org/",
    ".chatbi/bootstrap/",
    "semantic/",
)

_DENY = (
    "Error: path outside the agent's read allowlist "
    "(models/, docs/org/, .chatbi/bootstrap/, semantic/)"
)


def _normalize_rel(rel: str) -> str:
    """POSIX-ish relative path without stripping the leading dot of ``.chatbi``."""
    rel = rel.replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def _in_allowlist(rel: str) -> bool:
    rel = _normalize_rel(rel)
    if not rel or rel == ".":
        return False
    for prefix in _ALLOWED_PREFIXES:
        allowed = prefix.rstrip("/")
        if rel == allowed or rel.startswith(allowed + "/"):
            return True
        # Ancestors of an allowed prefix (``.chatbi``, ``docs``) so list/search
        # can enter the tree without opening sibling dirs (``.chatbi/runs``).
        if allowed.startswith(rel + "/"):
            return True
    return False


def build_main_agent_file_tools(workspace_root: Path):
    """Read-only FileTools scoped to the warehouse allowlist."""
    from agno.tools.file import FileTools

    class _MainAgentFileTools(FileTools):  # type: ignore[misc]
        def _rel(self, path: Path) -> str:
            try:
                return (
                    Path(path).resolve()
                    .relative_to(Path(self.base_dir).resolve())
                    .as_posix()
                )
            except (OSError, ValueError):
                return ""

        def _is_excluded(self, path: Path) -> bool:  # noqa: N802
            if super()._is_excluded(path):
                return True
            return not _in_allowlist(self._rel(path))

        def read_file(self, file_name: str, encoding: str = "utf-8") -> str:
            safe, file_path = self.check_escape(file_name)
            if not safe or not _in_allowlist(self._rel(file_path)):
                return _DENY
            return super().read_file(file_name, encoding=encoding)

        def list_files(self, **kwargs) -> str:
            import json

            directory = kwargs.get("directory", ".")
            safe, d = self.check_escape(directory)
            if not safe:
                return _DENY
            if d.resolve() != Path(self.base_dir).resolve() and self._is_excluded(d):
                return _DENY
            try:
                names = []
                for file_path in sorted(d.iterdir(), key=lambda p: p.name):
                    if self._is_excluded(file_path):
                        continue
                    names.append(file_path.relative_to(self.base_dir).as_posix())
                return json.dumps(names, indent=4)
            except OSError as error:
                return f"Error reading files: {error}"

        def search_files(self, pattern: str) -> str:
            import json

            if not pattern or not pattern.strip():
                return "Error: Pattern cannot be empty"
            seen: set[str] = set()
            files: list[str] = []

            def _maybe(path: Path) -> None:
                if self._is_excluded(path):
                    return
                rel = self._rel(path)
                if not rel or rel in seen:
                    return
                if Path(rel).match(pattern) or Path(path.name).match(pattern):
                    seen.add(rel)
                    files.append(rel)

            # pathlib glob skips hidden directories; walk allowlisted trees
            # explicitly so ``.chatbi/bootstrap/source_inventory.json`` is
            # discoverable (T2 session d96c0bad).
            for prefix in _ALLOWED_PREFIXES:
                root = Path(self.base_dir) / prefix.rstrip("/")
                if not root.exists():
                    continue
                _maybe(root)
                if root.is_dir():
                    for path in root.rglob("*"):
                        _maybe(path)
            try:
                for path in Path(self.base_dir).glob(pattern):
                    _maybe(path)
            except OSError:
                pass
            files.sort()
            return json.dumps(
                {"pattern": pattern, "matches_found": len(files), "files": files},
                indent=2,
            )

    return _MainAgentFileTools(
        base_dir=workspace_root,
        enable_save_file=False,
        enable_delete_file=False,
        enable_read_file=True,
        enable_list_files=True,
        enable_search_files=True,
        enable_search_content=True,
        enable_read_file_chunk=False,
        enable_replace_file_chunk=False,
    )
