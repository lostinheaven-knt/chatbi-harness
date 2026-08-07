"""Manifest-driven prompt asset loading for the ChatBI agent (module C).

Single source of truth without drift (design §10, modification §10): the
``prompts/manifest.json`` (``chatbi.prompts/v1``) pins the content SHA-256 of
every CC skill body + the adversarial-reviewer agent body; this module loads
them into the agno agent's instructions/skills with a fail-closed
four-step validation chain (any failure -> :class:`PromptLoadError`, the
caller refuses startup, MR-005):

1. the manifest exists and ``schema_version == chatbi.prompts/v1``;
2. every entry ``path`` is Workspace-relative (absolute paths rejected,
   PORT-001 — mirroring the Claude-side ``prompt_target`` convention
   ``.claude/<path>``, ``runtimes/claude_code/adapter.py:121-129``) and the
   file exists;
3. the file's SHA-256 equals the manifest-registered value (content drift
   -> reject with a diff summary naming the file and both hashes);
4. skill bodies carry a frontmatter (``name``/``description``) acceptable to
   the agno ``LocalSkills`` loader (spike R5 verified the 7 real CC skills
   load with ``Skills([LocalSkills(root)], raise_on_loader_error=True)``;
   the normalization mapping is identity for the CC format, pinned by tests).

:class:`PromptAssets` carries the assembled instruction texts (governance
skill + runbook skill bodies), the skills root for ``LocalSkills``, the
reviewer instructions (``agents/adversarial-reviewer.md``) and the full
loaded-entry registry for audit. The routing table (9 workflow_id <->
runbook mapping) is injected by :mod:`runtimes.agno.agent_builder` — per
request subset loading is a future optimization (modification §15.1 Q1, not
in this iteration).

The prose bodies themselves stay in ``.claude/skills`` / ``.claude/agents``
(red line: not migrated); this module only READS them.

Applicable rules: MR-005, PORT-001, SEC-003, HOOK-001, invariant 5.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

MANIFEST_SCHEMA_VERSION = "chatbi.prompts/v1"

#: Manifest entry kinds.
KIND_SKILL = "skill"
KIND_AGENT = "agent"

#: Skills whose bodies are injected as the main agent's instructions
#: (design §3.2: governance skill + runbook skill).
_INSTRUCTION_SKILLS = ("skills/chatbi-governance/SKILL.md",
                       "skills/chatbi-runbook/SKILL.md")

#: The reviewer agent manifest entry.
_REVIEWER_ENTRY = "agents/adversarial-reviewer.md"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class PromptLoadError(ValueError):
    """Prompt assets cannot be loaded (fail-closed startup refusal)."""


def resolve_prompt_root(workspace_root: Path) -> Path:
    """The prompts asset root: ``<workspace_root>/prompts`` (dev:
    ``harness/prompts``; install: ``<workspace>/prompts`` — the manifest
    lives here, resources resolve from the same root)."""
    return Path(workspace_root) / "prompts"


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_frontmatter(content: str) -> Mapping[str, Any]:
    """Parse the YAML frontmatter (name/description) of a skill body.

    Fallback to a simple key-value parser when yaml is unavailable; a body
    without frontmatter yields an empty mapping (the LocalSkills loader
    treats that as acceptable — name falls back to the folder name — but the
    manifest loader REQUIRES name/description per step 4).
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}
    try:
        import yaml

        parsed = yaml.safe_load(match.group(1))
        return parsed if isinstance(parsed, Mapping) else {}
    except Exception:  # noqa: BLE001 - yaml unavailable/malformed
        result: dict[str, Any] = {}
        for line in match.group(1).strip().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip().strip('"').strip("'")
        return result


@dataclass(frozen=True)
class PromptEntry:
    """One loaded manifest entry (audit + validation registry)."""

    name: str                            # manifest key (skills/… or agents/…)
    kind: str                            # "skill" | "agent"
    sha256: str
    path: Path                           # resolved Workspace-relative path


@dataclass(frozen=True)
class PromptAssets:
    """Assembled prompt assets for the ChatBI agent (design §3.1)."""

    instructions: tuple[str, ...]        # governance skill + runbook bodies
    skills_root: Path                    # LocalSkills root (7 skills)
    reviewer_instructions: str           # agents/adversarial-reviewer.md body
    entries: tuple[PromptEntry, ...]     # every loaded entry (audit)


def load_prompt_assets(
    *,
    workspace_root: Path,
    manifest_path: Path | None = None,
    workflow_ids: Iterable[str] = (),
) -> PromptAssets:
    """Load and validate the prompt assets (four-step fail-closed chain).

    ``manifest_path`` defaults to ``resolve_prompt_root(workspace_root) /
    manifest.json``. ``workflow_ids`` is accepted for future per-workflow
    subset loading (modification §15.1 Q1 — not implemented in this
    iteration: all manifest entries are loaded and validated; the
    instructions always carry the governance + runbook bodies).

    Raises :class:`PromptLoadError` on any step failure; callers refuse
    startup (fail-closed, MR-005).
    """
    root = Path(workspace_root)
    if manifest_path is None:
        manifest_path = resolve_prompt_root(root) / "manifest.json"

    # -- step 1: manifest exists + schema_version ---------------------------
    try:
        raw = manifest_path.read_bytes()
    except OSError as error:
        raise PromptLoadError(
            f"prompt manifest is missing or unreadable: {manifest_path} "
            f"({type(error).__name__}); refuse startup (fail-closed)"
        ) from error
    if len(raw) > 4 * 1024 * 1024:
        raise PromptLoadError(f"prompt manifest too large: {manifest_path}")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromptLoadError(
            f"prompt manifest is malformed JSON: {manifest_path} "
            f"({type(error).__name__})"
        ) from error
    if not isinstance(manifest, Mapping):
        raise PromptLoadError(f"prompt manifest must be a JSON object: "
                              f"{manifest_path}")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise PromptLoadError(
            f"prompt manifest schema_version must be "
            f"{MANIFEST_SCHEMA_VERSION!r}, got "
            f"{manifest.get('schema_version')!r}; refuse startup"
        )
    entries_raw = manifest.get("entries")
    if not isinstance(entries_raw, Mapping) or not entries_raw:
        raise PromptLoadError(
            f"prompt manifest entries must be a non-empty object: "
            f"{manifest_path}"
        )

    # -- steps 2-4: per entry -------------------------------------------------
    entries: list[PromptEntry] = []
    instruction_bodies: list[str] = []
    reviewer_instructions: str | None = None
    for name, spec in entries_raw.items():
        if not isinstance(name, str) or not name:
            raise PromptLoadError("prompt manifest entry names must be strings")
        if not isinstance(spec, Mapping):
            raise PromptLoadError(f"prompt manifest entry {name!r} must be "
                                  f"an object")
        kind = spec.get("kind")
        if kind not in (KIND_SKILL, KIND_AGENT):
            raise PromptLoadError(
                f"prompt manifest entry {name!r}: kind must be "
                f"skill|agent, got {kind!r}")
        registered_sha = spec.get("sha256")
        if not isinstance(registered_sha, str) or not registered_sha:
            raise PromptLoadError(
                f"prompt manifest entry {name!r}: sha256 must be a string")

        # -- step 2: relative path + file exists (PORT-001) -------------------
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise PromptLoadError(
                f"prompt manifest entry {name!r}: absolute or traversing "
                "paths are rejected (PORT-001)")
        resolved = root / ".claude" / path
        if not resolved.is_file():
            raise PromptLoadError(
                f"prompt asset file missing: {resolved} (entry {name!r})")

        # -- step 3: content sha256 == manifest registration ------------------
        try:
            content = resolved.read_bytes()
        except OSError as error:
            raise PromptLoadError(
                f"prompt asset unreadable: {resolved} "
                f"({type(error).__name__})") from error
        actual_sha = _sha256_bytes(content)
        if actual_sha != registered_sha:
            raise PromptLoadError(
                f"prompt asset content drift (entry {name!r}): "
                f"file {resolved} hashes to {actual_sha}, manifest registers "
                f"{registered_sha}; update the manifest registration before "
                "startup (fail-closed)"
            )

        # -- step 4: skill frontmatter loadable (spike R5 pinned) --------------
        text = content.decode("utf-8")
        if kind == KIND_SKILL:
            frontmatter = _parse_frontmatter(text)
            if not isinstance(frontmatter.get("name"), str) or not frontmatter.get(
                "name"
            ):
                raise PromptLoadError(
                    f"prompt asset {name!r}: skill frontmatter must carry a "
                    "non-empty 'name' (agno LocalSkills compatibility)")
            if not isinstance(frontmatter.get("description"), str):
                raise PromptLoadError(
                    f"prompt asset {name!r}: skill frontmatter must carry a "
                    "'description' (agno LocalSkills compatibility)")
            if name in _INSTRUCTION_SKILLS:
                instruction_bodies.append(text)

        if kind == KIND_AGENT:
            if name != _REVIEWER_ENTRY:
                raise PromptLoadError(
                    f"prompt manifest entry {name!r}: unknown agent entry "
                    f"(expected {_REVIEWER_ENTRY!r})")
            reviewer_instructions = text

        entries.append(PromptEntry(name=name, kind=kind, sha256=actual_sha,
                                   path=path))

    if reviewer_instructions is None:
        raise PromptLoadError(
            f"prompt manifest is missing the reviewer agent entry "
            f"{_REVIEWER_ENTRY!r}")
    if not instruction_bodies:
        raise PromptLoadError(
            "prompt manifest carries none of the instruction skills "
            f"{sorted(_INSTRUCTION_SKILLS)}")

    return PromptAssets(
        instructions=tuple(instruction_bodies),
        skills_root=root / ".claude" / "skills",
        reviewer_instructions=reviewer_instructions,
        entries=tuple(entries),
    )
