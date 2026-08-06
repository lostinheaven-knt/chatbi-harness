"""Portable path identities for the ChatBI Harness.

Cycle 1 validation is a point-in-time check. Callers must invoke this module
again before each operation; continuous TOCTOU enforcement belongs to Cycle 2.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Protocol

from .config import EffectiveConfig
from .gates import GateDecision, GateError


_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")


class _DigestWriter(Protocol):
    def update(self, data: bytes) -> None: ...


_SAFE_SYSTEM_PATH = os.pathsep.join(
    component
    for component in os.defpath.split(os.pathsep)
    if component and Path(component).is_absolute()
)


def _resolve_trusted_git() -> str | None:
    if not _SAFE_SYSTEM_PATH:
        return None
    candidate = shutil.which("git", path=_SAFE_SYSTEM_PATH)
    if candidate is None:
        return None
    try:
        executable = Path(candidate).resolve(strict=True)
        executable_mode = executable.stat(follow_symlinks=False).st_mode
    except (OSError, RuntimeError):
        return None
    if (
        not executable.is_absolute()
        or not stat.S_ISREG(executable_mode)
        or not os.access(executable, os.X_OK)
    ):
        return None
    return str(executable)


_TRUSTED_GIT = _resolve_trusted_git()


def _path_error(
    *,
    alias: str,
    location: str,
    category: str,
    reason: str,
    recovery: str,
    rule_ids: tuple[str, ...] = ("SCOPE-001", "PORT-001", "HOOK-004"),
) -> GateError:
    safe_alias = alias if _ALIAS.fullmatch(alias) else "invalid-alias"
    return GateError(
        GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=(f"path:{safe_alias}:{location}:{category}",),
            reason=reason,
            recovery=recovery,
        )
    )


def _resolve_root(path: Path, alias: str) -> Path:
    try:
        root_mode = path.lstat().st_mode
    except FileNotFoundError:
        raise _path_error(
            alias=alias,
            location="root",
            category="missing",
            reason=f"Configured root does not exist for alias {alias}",
            recovery="Bind the alias to an existing directory",
        ) from None
    except (OSError, RuntimeError):
        raise _path_error(
            alias=alias,
            location="root",
            category="unreadable",
            reason=f"Configured root cannot be inspected for alias {alias}",
            recovery="Bind the alias to an accessible real directory",
        ) from None
    if stat.S_ISLNK(root_mode):
        raise _path_error(
            alias=alias,
            location="root",
            category="symlink",
            reason=f"Configured root is a symlink for alias {alias}",
            recovery="Bind the alias directly to a real directory",
        )
    if not stat.S_ISDIR(root_mode):
        raise _path_error(
            alias=alias,
            location="root",
            category="not-directory",
            reason=f"Configured root is not a directory for alias {alias}",
            recovery="Bind the alias to an existing directory",
        )
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _path_error(
            alias=alias,
            location="root",
            category="unreadable",
            reason=f"Configured root cannot be resolved for alias {alias}",
            recovery="Bind the alias to an accessible real directory",
        ) from None


@dataclass(frozen=True, slots=True)
class PortablePathReference:
    alias: str
    relative_path: str
    revision: str
    revision_kind: str

    def to_dict(self) -> dict[str, str]:
        return {
            "alias": self.alias,
            "relative_path": self.relative_path,
            "revision": self.revision,
            "revision_kind": self.revision_kind,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)


def _configured_roots(
    config: EffectiveConfig,
    workspace_root: Path | None = None,
) -> dict[str, Path]:
    """Resolve the configured roots; ``workspace_root`` is an explicit override
    for the soft "process cwd == Workspace root" coupling (feature-flow §3.7.2).
    ``None`` keeps the historical ``Path.cwd()`` derivation unchanged.
    """
    workspace_alias = config["workspace"]["id"]
    try:
        if workspace_root is None:
            workspace_root = Path.cwd()
    except (OSError, RuntimeError):
        raise _path_error(
            alias=workspace_alias,
            location="root",
            category="unreadable",
            reason="Workspace root cannot be determined",
            recovery="Restore an accessible working directory and retry",
        ) from None
    roots = {workspace_alias: _resolve_root(workspace_root, workspace_alias)}
    for alias, codebase in config["business_codebases"].items():
        path_ref = codebase["path_ref"]
        if path_ref not in config["path_bindings"]:
            raise _path_error(
                alias=alias,
                location="root",
                category="unconfigured",
                reason=f"Business Codebase root is unconfigured for alias {alias}",
                recovery="Add the declared path binding in local configuration",
            )
        roots[alias] = _resolve_root(Path(config["path_bindings"][path_ref]), alias)
    root_items = list(roots.items())
    for index, (left_alias, left_root) in enumerate(root_items):
        for right_alias, right_root in root_items[index + 1 :]:
            if (
                left_root == right_root
                or left_root.is_relative_to(right_root)
                or right_root.is_relative_to(left_root)
            ):
                raise _path_error(
                    alias=right_alias,
                    location="root",
                    category="overlap",
                    reason=(
                        f"Configured roots overlap for aliases {left_alias} and "
                        f"{right_alias}"
                    ),
                    recovery="Bind every alias to a separate, non-nested directory",
                )
    return roots


def _reject_symlink(*, path: Path, root: Path, alias: str, location: str) -> None:
    try:
        path_mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    except (OSError, RuntimeError):
        raise _path_error(
            alias=alias,
            location=location,
            category="unreadable",
            reason="Target path component cannot be inspected",
            recovery="Restore an accessible real target and retry validation",
        ) from None
    if not stat.S_ISLNK(path_mode):
        return
    try:
        symlink_target = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _path_error(
            alias=alias,
            location=location,
            category="broken-symlink",
            reason="Target contains a broken symlink",
            recovery="Replace the broken symlink with a real path inside the root",
        ) from None
    if not symlink_target.is_relative_to(root):
        raise _path_error(
            alias=alias,
            location=location,
            category="symlink-escape",
            reason="Target symlink resolves outside the configured root",
            recovery="Use a real target that remains inside the configured root",
        )
    raise _path_error(
        alias=alias,
        location=location,
        category="symlink",
        reason="Target contains a symlink",
        recovery="Use the real path inside the configured root",
    )


def _file_sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _update_frame(digest: _DigestWriter, *fields: bytes) -> None:
    for field in fields:
        digest.update(len(field).to_bytes(8, "big"))
        digest.update(field)


def _content_sha256(path: Path) -> str:
    initial_stat = path.stat(follow_symlinks=False)
    if stat.S_ISREG(initial_stat.st_mode):
        return _file_sha256(path).hex()
    if not stat.S_ISDIR(initial_stat.st_mode):
        raise OSError("Unsupported target type for revision evidence")

    digest = hashlib.sha256()
    digest.update(b"chatbi-directory-v1\0")
    for child in sorted(
        path.rglob("*"),
        key=lambda item: item.relative_to(path).as_posix(),
    ):
        relative = child.relative_to(path)
        if ".git" in relative.parts:
            continue
        encoded_relative = relative.as_posix().encode("utf-8")
        child_mode = child.stat(follow_symlinks=False).st_mode
        if stat.S_ISDIR(child_mode):
            _update_frame(digest, b"directory", encoded_relative)
        elif stat.S_ISREG(child_mode):
            _update_frame(
                digest,
                b"file",
                encoded_relative,
                _file_sha256(child),
            )
        else:
            raise OSError("Unsupported directory entry for revision evidence")
    final_stat = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(final_stat.st_mode)
        or initial_stat.st_dev != final_stat.st_dev
        or initial_stat.st_ino != final_stat.st_ino
    ):
        raise OSError("Target directory changed while producing revision evidence")
    return digest.hexdigest()


def _git_revision(root: Path, relative_path: str) -> str | None:
    if _TRUSTED_GIT is None:
        return None
    safe_environment = {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": _SAFE_SYSTEM_PATH,
    }

    def run_git(*args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                [
                    _TRUSTED_GIT,
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    f"core.hooksPath={os.devnull}",
                    *args,
                ],
                cwd=root,
                env=safe_environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    head = run_git("rev-parse", "--verify", "HEAD")
    if head is None or head.returncode != 0:
        return None
    revision = head.stdout.strip()
    if len(revision) not in (40, 64) or any(
        character not in "0123456789abcdefABCDEF" for character in revision
    ):
        return None

    tracked = run_git("ls-files", "--", relative_path)
    if tracked is None or tracked.returncode != 0 or not tracked.stdout.strip():
        return None
    for comparison in (
        ("diff-files", "--quiet", "--", relative_path),
        ("diff-index", "--cached", "--quiet", "HEAD", "--", relative_path),
    ):
        result = run_git(*comparison)
        if result is None or result.returncode != 0:
            return None
    untracked = run_git(
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        relative_path,
    )
    if untracked is None or untracked.returncode != 0 or untracked.stdout:
        return None
    return revision.lower()


def resolve_path_reference(
    config: EffectiveConfig,
    *,
    alias: str,
    target: str,
) -> PortablePathReference:
    """Resolve one explicit alias/target against the current validated roots."""

    roots = _configured_roots(config)
    if alias not in roots:
        raise _path_error(
            alias=alias,
            location="alias",
            category="unknown",
            reason="Unknown path alias",
            recovery="Use a configured alias for the Workspace or a Business Codebase",
        )
    root = roots[alias]
    target_path = Path(target)
    if target_path.is_absolute() or PureWindowsPath(target).is_absolute():
        raise _path_error(
            alias=alias,
            location="target",
            category="absolute",
            reason="Absolute targets are not allowed",
            recovery="Use a relative target within the configured root",
        )
    if ".." in target_path.parts or ".." in PureWindowsPath(target).parts:
        raise _path_error(
            alias=alias,
            location=target.replace("\\", "/"),
            category="traversal",
            reason="Parent traversal is not allowed in a target",
            recovery="Use a normalized relative target without '..' components",
        )
    cursor = root
    for part in target_path.parts:
        cursor = cursor / part
        _reject_symlink(
            path=cursor,
            root=root,
            alias=alias,
            location=target.replace("\\", "/"),
        )
    candidate = root / target_path
    try:
        resolved_target = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise _path_error(
            alias=alias,
            location=target.replace("\\", "/"),
            category="missing",
            reason="Target does not exist",
            recovery="Use an existing target within the configured root",
        ) from None
    except (OSError, RuntimeError):
        raise _path_error(
            alias=alias,
            location=target.replace("\\", "/"),
            category="unreadable",
            reason="Target cannot be resolved",
            recovery="Restore an accessible target within the configured root",
        ) from None
    if not resolved_target.is_relative_to(root):
        raise _path_error(
            alias=alias,
            location=target.replace("\\", "/"),
            category="symlink-escape",
            reason="Target symlink resolves outside the configured root",
            recovery="Use a real target that remains inside the configured root",
        )
    try:
        target_mode = resolved_target.stat(follow_symlinks=False).st_mode
        target_is_file = stat.S_ISREG(target_mode)
        target_is_directory = stat.S_ISDIR(target_mode)
        if not target_is_file and not target_is_directory:
            raise OSError("Unsupported target type")
        if target_is_directory:
            for child in sorted(
                resolved_target.rglob("*"),
                key=lambda item: item.relative_to(resolved_target).as_posix(),
            ):
                _reject_symlink(
                    path=child,
                    root=root,
                    alias=alias,
                    location=child.relative_to(root).as_posix(),
                )
    except (OSError, RuntimeError):
        raise _path_error(
            alias=alias,
            location=target.replace("\\", "/"),
            category="unreadable",
            reason="Target directory cannot be inspected",
            recovery="Restore readable target content and retry path validation",
        ) from None
    relative_path = resolved_target.relative_to(root).as_posix()
    git_revision = _git_revision(root, relative_path) if target_is_file else None
    try:
        revision = git_revision or _content_sha256(resolved_target)
    except (OSError, RuntimeError):
        raise _path_error(
            alias=alias,
            location=relative_path,
            category="unreadable",
            reason="Target content cannot be read for revision evidence",
            recovery="Restore readable target content and retry path validation",
        ) from None
    return PortablePathReference(
        alias=alias,
        relative_path=relative_path,
        revision=revision,
        revision_kind="git_sha" if git_revision else "content_sha256",
    )
