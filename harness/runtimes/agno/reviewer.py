"""Independent adversarial reviewer for the Agno target (module 5, MR-D3).

Implements the reviewer constraints (deployment design §11.2, REV-001/002/003)
as an Agno Adapter component — every judgment still goes through the
Governance Kernel:

- Reviewer is an INDEPENDENT actor: a distinct ``Agent`` with its own id and
  session; it does not share the main Agent's memory/checkpoint (each Agent
  owns its session state).
- The reviewer exposes READ-ONLY tools only (FileTools with read/list/search
  enabled and every write/delete/save disabled) — no Bash/Write/Edit.
- The verdict must validate against ``review.schema.json`` (kernel
  ``evidence.validate_review``) and its ``candidate_sha`` must match the
  frozen candidate EXACTLY (REV-001: PASS is only valid for the exact SHA).
- Reviewer unavailable / timeout / output that cannot be parsed or validated
  → fail-closed BLOCK (never an implicit pass, HOOK-004).

In stub mode (conformance / unit tests) ``reviewer_runner`` is injected and
returns scripted verdicts; the SHA binding and schema validation still run
through the kernel for both modes — the spike never trusts the model output.

Applicable rules: REV-001/002/003, HOOK-004, SEC-003, invariant 2/4.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from chatbi_governance.evidence import GateError, validate_review
from chatbi_governance.harness_state import _safe_session_id
from chatbi_runtime_contract.types import ReviewResult, ReviewVerdict

#: Live reviewer FileTools reads this to hide other sessions' .chatbi/runs.
_reviewer_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chatbi_reviewer_session_id", default=""
)

#: Relative paths the live runner should hint on enum deny (never a file list).
_reviewer_locatable_paths: contextvars.ContextVar[tuple[str, ...]] = (
    contextvars.ContextVar("chatbi_reviewer_locatable_paths", default=())
)


def is_foreign_session_evidence(
    path: Path, *, workspace_root: Path, session_id: str,
) -> bool:
    """True when ``path`` is unpublished evidence from another session.

    Published ``semantic/`` and ``models/`` are never foreign. Unbound
    session_id hides every ``.chatbi/runs/<sid>/`` file so a reviewer
    cannot promote a locatable T3 JSON into REQ-002 authority.
    """
    try:
        rel = Path(path).resolve().relative_to(Path(workspace_root).resolve())
    except (OSError, ValueError):
        return False
    parts = rel.parts
    if len(parts) < 3 or parts[0] != ".chatbi" or parts[1] != "runs":
        return False
    owner = parts[2]
    if not session_id:
        return True
    try:
        safe = _safe_session_id(session_id)
    except ValueError:
        return True
    return owner != safe

REVIEWER_ID = "chatbi-reviewer"

#: The reviewer may only see read-only tools (design §11.2).
#: Live 51fb2aee: disabling list/search made the reviewer miss query-result
#: evidence files and BLOCK twice. Keep list/search; locatable_paths stay.
_READ_ONLY_FILE_TOOLS = {
    "read": True,
    "list": True,
    "search": True,
    "save": False,
    "delete": False,
    "chunk": False,
    "write": False,
}


_REVIEWER_ALLOWED_PREFIXES = (
    "models/",
    "semantic/",
    "docs/org/",
)

#: list/search may walk these trees (plus this-session ``.chatbi/runs``).
_REVIEWER_ENUM_PREFIXES = (
    "semantic/",
    "docs/org/",
)

_REVIEWER_DENY = (
    "Error: path outside the reviewer's read allowlist "
    "(models/, semantic/, docs/org/, .chatbi/runs/<this-session>/)"
)

_ENUM_DENY = (
    "Error: listing/searching models/ is outside the reviewer's enumerate "
    "allowlist (.chatbi/runs/<this-session>/, semantic/, docs/org/). "
    "Use read_file on models cited in locatable_paths."
)

_MODEL_PATH_RE = re.compile(r"\bmodels/[A-Za-z0-9_./-]+\.(?:sql|yml)\b")
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_EVIDENCE_SHA_MAP_CAP = 64


def locatable_review_paths(
    *,
    candidate_content: Any = None,
    workspace_root: Path | str | None = None,
    session_id: str = "",
) -> list[str]:
    """Relative paths the reviewer should ``read_file`` without listing.

    P2 ``0bf4c64d``: footer ``models/**`` plus this-session ``evidence-*.json``.
    list/search stay enabled; locatable_paths are a hint, not a FileTools off
    switch (live ``51fb2aee``).
    """
    found: list[str] = []
    try:
        blob = (
            candidate_content if isinstance(candidate_content, str)
            else json.dumps(candidate_content, ensure_ascii=False, default=str)
        )
    except (TypeError, ValueError):
        blob = ""
    if blob:
        for hit in _MODEL_PATH_RE.findall(blob):
            if hit not in found:
                found.append(hit)
    if workspace_root and session_id:
        root = Path(workspace_root).resolve()
        try:
            safe = _safe_session_id(session_id)
        except ValueError:
            safe = session_id
        runs = root / ".chatbi" / "runs" / safe
        if runs.is_dir():
            for path in sorted(runs.glob("evidence-*.json")):
                try:
                    rel = path.resolve().relative_to(root).as_posix()
                except ValueError:
                    continue
                if rel not in found:
                    found.append(rel)
    return found[:32]


def compact_review_context(review_context: Mapping[str, Any]) -> dict[str, Any]:
    """Drop bulky session adjudication before the independent Agent.run.

    The live runner dumps this mapping as the user message. A full
    operator-adjudication dict re-inflates T3/T4 chat into the reviewer
    prompt (P1). Keep identity + authority; shrink adjudication to flags.
    """
    allowed = (
        "task", "candidate_sha", "reviewer_context_hash", "run_id",
        "session_id", "round", "candidate_kind", "authority",
        "evidence_refs", "locatable_paths", "evidence_sha_map",
    )
    out: dict[str, Any] = {}
    for key in allowed:
        if key in review_context:
            out[key] = review_context[key]
    adj = review_context.get("session_adjudication")
    if isinstance(adj, Mapping):
        out["session_adjudication"] = {
            "approve_execute": bool(adj.get("approve_execute")),
            "kind": str(adj.get("kind") or "")[:64],
        }
    return out


def _normalize_reviewer_rel(rel: str) -> str:
    rel = rel.replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def reviewer_rel_allowed(rel: str, *, session_id: str = "") -> bool:
    """True when a workspace-relative path is in the reviewer allowlist."""
    rel = _normalize_reviewer_rel(rel)
    if not rel or rel == ".":
        return False
    prefixes = list(_REVIEWER_ALLOWED_PREFIXES)
    if session_id:
        prefixes.append(f".chatbi/runs/{session_id}/")
        try:
            prefixes.append(f".chatbi/runs/{_safe_session_id(session_id)}/")
        except ValueError:
            pass
    for prefix in prefixes:
        allowed = prefix.rstrip("/")
        if rel == allowed or rel.startswith(allowed + "/"):
            return True
        if allowed.startswith(rel + "/"):
            return True
    return False


def reviewer_enum_allowed(rel: str, *, session_id: str = "") -> bool:
    """True when list/search may enumerate this workspace-relative path."""
    rel = _normalize_reviewer_rel(rel)
    if not rel or rel == ".":
        return True  # root listing; children still filtered
    prefixes = list(_REVIEWER_ENUM_PREFIXES)
    if session_id:
        prefixes.append(f".chatbi/runs/{session_id}/")
        try:
            prefixes.append(f".chatbi/runs/{_safe_session_id(session_id)}/")
        except ValueError:
            pass
    for prefix in prefixes:
        allowed = prefix.rstrip("/")
        if rel == allowed or rel.startswith(allowed + "/"):
            return True
        if allowed.startswith(rel + "/"):
            return True
    return False


def _rel_is_models_tree(rel: str) -> bool:
    rel = _normalize_reviewer_rel(rel)
    return rel == "models" or rel.startswith("models/")


def _enum_deny_message() -> str:
    extra: list[str] = []
    for raw in _reviewer_locatable_paths.get() or ():
        if not isinstance(raw, str):
            continue
        path = _normalize_reviewer_rel(raw)
        if not path or path.startswith("/") or (len(path) > 1 and path[1] == ":"):
            continue
        extra.append(path)
        if len(extra) >= 4:
            break
    if extra:
        return _ENUM_DENY + " Cited locatable_paths: " + ", ".join(extra)
    return _ENUM_DENY


def evidence_sha_map(
    *,
    workspace_root: Path | str | None = None,
    session_id: str = "",
) -> dict[str, str]:
    """Map EvidenceEntry / payload SHA-256 → workspace-relative evidence path.

    Keys come from on-disk ``evidence-*.json`` JSON fields, never from the
    file-bytes hash stored by EvidenceIndex.
    """
    if not workspace_root or not session_id:
        return {}
    try:
        safe = _safe_session_id(session_id)
    except ValueError:
        return {}
    root = Path(workspace_root).resolve()
    runs = root / ".chatbi" / "runs" / safe
    if not runs.is_dir():
        return {}
    mapped: dict[str, str] = {}

    def _add(sha: Any, rel: str) -> None:
        if len(mapped) >= _EVIDENCE_SHA_MAP_CAP:
            return
        if not isinstance(sha, str) or not _SHA256_HEX_RE.fullmatch(sha):
            return
        if sha not in mapped:
            mapped[sha] = rel

    for path in sorted(runs.glob("evidence-*.json")):
        if len(mapped) >= _EVIDENCE_SHA_MAP_CAP:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        _add(data.get("content_sha256"), rel)
        payload = data.get("payload")
        if isinstance(payload, Mapping):
            _add(payload.get("content_sha256"), rel)
    return mapped


def _make_scoped_file_tools(workspace_root: Path | None) -> Any:
    from agno.tools.file import FileTools

    class _Scoped(FileTools):  # type: ignore[misc]
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
            if is_foreign_session_evidence(
                path, workspace_root=self.base_dir,
                session_id=_reviewer_session_id.get() or "",
            ):
                return True
            rel = self._rel(path)
            if not rel:
                return True
            return not reviewer_enum_allowed(
                rel, session_id=_reviewer_session_id.get() or "")

        def read_file(self, file_name: str, encoding: str = "utf-8") -> str:
            safe, file_path = self.check_escape(file_name)
            if not safe:
                return "Error reading file"
            if is_foreign_session_evidence(
                file_path, workspace_root=self.base_dir,
                session_id=_reviewer_session_id.get() or "",
            ):
                return (
                    "Error: unpublished evidence from another session is "
                    "not a review authority (foreign_session_evidence="
                    "not_canonical)"
                )
            rel = self._rel(file_path)
            if not reviewer_rel_allowed(
                    rel, session_id=_reviewer_session_id.get() or ""):
                return _REVIEWER_DENY
            return super().read_file(file_name, encoding=encoding)

        def list_files(self, **kwargs) -> str:
            directory = kwargs.get("directory", ".")
            safe, d = self.check_escape(directory)
            if not safe:
                return _enum_deny_message()
            rel = self._rel(d)
            if _rel_is_models_tree(rel):
                return _enum_deny_message()
            if rel and rel not in (".",) and self._is_excluded(d):
                return _enum_deny_message()
            try:
                names = []
                for file_path in sorted(d.iterdir(), key=lambda p: p.name):
                    if self._is_excluded(file_path):
                        continue
                    names.append(
                        file_path.relative_to(self.base_dir).as_posix())
                return json.dumps(names, indent=4)
            except OSError as error:
                return f"Error reading files: {error}"

        def search_files(self, pattern: str) -> str:
            if not pattern or not pattern.strip():
                return "Error: Pattern cannot be empty"
            norm = _normalize_reviewer_rel(pattern.replace("\\", "/"))
            if _rel_is_models_tree(norm) or norm.startswith("models/"):
                return _enum_deny_message()
            seen: set[str] = set()
            files: list[str] = []
            session_id = _reviewer_session_id.get() or ""

            def _maybe(path: Path) -> None:
                if self._is_excluded(path):
                    return
                rel = self._rel(path)
                if not rel or rel in seen or _rel_is_models_tree(rel):
                    return
                if Path(rel).match(pattern) or Path(path.name).match(pattern):
                    seen.add(rel)
                    files.append(rel)

            prefixes = list(_REVIEWER_ENUM_PREFIXES)
            if session_id:
                prefixes.append(f".chatbi/runs/{session_id}/")
                try:
                    prefixes.append(
                        f".chatbi/runs/{_safe_session_id(session_id)}/")
                except ValueError:
                    pass
            for prefix in prefixes:
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
                {"pattern": pattern, "matches_found": len(files),
                 "files": files},
                indent=2,
            )

        def search_content(
            self, query: str, directory: str | None = None, limit: int = 10,
        ) -> str:
            from agno.tools.file import (
                TEXT_EXTENSIONS, _extract_snippet, _format_size,
            )

            if not query or not query.strip():
                return "Error: Query cannot be empty"
            search_roots: list[Path] = []
            if directory:
                safe, search_dir = self.check_escape(directory)
                if not safe:
                    return _enum_deny_message()
                rel = self._rel(search_dir)
                if _rel_is_models_tree(rel):
                    return _enum_deny_message()
                if self._is_excluded(search_dir):
                    return _enum_deny_message()
                if not search_dir.is_dir():
                    return f"Error: '{directory}' is not a directory"
                search_roots = [search_dir]
            else:
                session_id = _reviewer_session_id.get() or ""
                prefixes = list(_REVIEWER_ENUM_PREFIXES)
                if session_id:
                    prefixes.append(f".chatbi/runs/{session_id}/")
                    try:
                        prefixes.append(
                            f".chatbi/runs/{_safe_session_id(session_id)}/")
                    except ValueError:
                        pass
                for prefix in prefixes:
                    root = Path(self.base_dir) / prefix.rstrip("/")
                    if root.is_dir():
                        search_roots.append(root)
            matches: list[dict[str, Any]] = []
            max_file_size = 500 * 1024
            lower_query = query.lower()
            walk_done = False
            for search_dir in search_roots:
                if walk_done:
                    break
                for dirpath, dirnames, filenames in os.walk(search_dir):
                    if walk_done:
                        break
                    dirnames[:] = [
                        d for d in dirnames
                        if not self._is_excluded(Path(dirpath) / d)
                    ]
                    for filename in filenames:
                        if len(matches) >= limit:
                            walk_done = True
                            break
                        file_path = Path(dirpath) / filename
                        if self._is_excluded(file_path):
                            continue
                        rel = self._rel(file_path)
                        if not rel or _rel_is_models_tree(rel):
                            continue
                        if file_path.suffix.lower() not in TEXT_EXTENSIONS:
                            continue
                        try:
                            if file_path.stat().st_size > max_file_size:
                                continue
                        except OSError:
                            continue
                        try:
                            content = file_path.read_text(
                                encoding="utf-8", errors="ignore")
                        except Exception:
                            continue
                        if lower_query in content.lower():
                            matches.append({
                                "file": rel,
                                "size": _format_size(file_path.stat().st_size),
                                "snippet": _extract_snippet(content, query),
                            })
            return json.dumps(
                {"query": query, "matches_found": len(matches),
                 "files": matches},
                indent=2,
            )

    return _Scoped(
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


def build_reviewer_agent(
    deployment: Any,
    model_config: Any,
    instructions: str | None = None,
    workspace_root: Path | None = None,
) -> Any:
    """Build the independent reviewer Agent (agno 2.6.22).

    ``deployment`` is the resolved runtimes.agno.config.DeploymentConfig and
    ``model_config`` the resolved ModelConfig. ``instructions`` carries the
    adversarial-reviewer protocol body (``prompts/manifest.json`` entry
    ``agents/adversarial-reviewer.md``, sha256-pinned by the prompt loader —
    skill+hooks module C; closes the live-integration boundary registered in
    test-report-agno-live-v1.md §6). Returns an ``agno.agent.Agent``
    configured with read-only tools, the reviewer protocol instructions, an
    explicit distinct id, and no shared memory wiring.
    """
    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.agent import Agent

    tools = [_make_scoped_file_tools(workspace_root)]
    if model_config.api_key:
        import os

        os.environ.setdefault("OPENAI_API_KEY", model_config.api_key)
        os.environ.setdefault(
            "OPENAI_BASE_URL", model_config.base_url
        )
    from agno.models.openai import OpenAIResponses

    return Agent(
        id=REVIEWER_ID,
        name="ChatBI Adversarial Reviewer (independent, read-only)",
        description=(
            "Independent least-privilege reviewer: read-only tools, "
            "adversarial review of a frozen candidate bound to a SHA-256."
        ),
        instructions=instructions,
        model=OpenAIResponses(
            id=model_config.model,
            base_url=model_config.base_url or None,
        ),
        tools=tools,
        markdown=False,
        # Independent session + no shared memory wiring with the main agent
        # (agno 2.6.22: memory_manager=None + no agentic/user memories).
        memory_manager=None,
        enable_agentic_memory=False,
        enable_user_memories=False,
        add_memories_to_context=False,
        session_id=f"reviewer-{REVIEWER_ID}",
        store_events=False,
    )


#: Type of the injected reviewer runner: scripted (stub) or live Agent.
#: Signature: ``(review_context: Mapping[str, Any]) -> Mapping[str, Any]``
#: returning the raw model verdict payload.
ReviewerRunner = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def extract_json_object(text: str) -> Mapping[str, Any] | None:
    """Parse a model response that may wrap the JSON object in prose.

    Real-model integration: DeepSeek frequently PREPENDS a sentence to the
    verdict object even when instructed to return only JSON (observed in the
    live demo: ``"Based on my review ... {\"run_id\": ...}"``). This helper
    tries a plain parse first, then scans for the outermost ``{...}`` span
    (string/escape-aware), and returns ``None`` when no well-formed object
    can be located — the callers fail closed on ``None`` (HOOK-004).
    """

    def _as_object(raw: str) -> Mapping[str, Any] | None:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, Mapping) else None

    direct = _as_object(text)
    if direct is not None:
        return direct
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return _as_object(text[start:i + 1])
    return None


def _metric_int(metrics: Any, *names: str) -> int:
    if metrics is None:
        return 0
    getter = metrics.get if isinstance(metrics, Mapping) else None
    for name in names:
        value = getter(name) if getter else getattr(metrics, name, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _metric_float(metrics: Any, *names: str) -> float | None:
    if metrics is None:
        return None
    getter = metrics.get if isinstance(metrics, Mapping) else None
    for name in names:
        value = getter(name) if getter else getattr(metrics, name, None)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _tool_call_name(item: Any) -> str:
    if isinstance(item, Mapping):
        for key in ("tool_name", "name", "tool"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
        return ""
    for key in ("tool_name", "name", "tool"):
        value = getattr(item, key, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _collect_reviewer_tool_calls(response: Any) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    tools = getattr(response, "tools", None) or []
    found = False
    for item in tools:
        name = _tool_call_name(item)
        if not name:
            continue
        found = True
        counts[name] = counts.get(name, 0) + 1
    if not found:
        for message in getattr(response, "messages", None) or []:
            role = ""
            if isinstance(message, Mapping):
                role = str(message.get("role") or "")
            else:
                role = str(getattr(message, "role", "") or "")
            if role != "tool":
                continue
            name = _tool_call_name(message)
            if not name:
                name = "tool"
            counts[name] = counts.get(name, 0) + 1
    return [{"name": name, "count": counts[name]} for name in sorted(counts)]


def _write_reviewer_trace(
    *,
    workspace_root: Path | str | None,
    session_id: str,
    response: Any,
    wall_s: float,
) -> None:
    if not workspace_root or not session_id:
        return
    metrics = getattr(response, "metrics", None)
    duration = _metric_float(metrics, "duration")
    payload = {
        "tool": "chatbi_review",
        "session_id": session_id,
        "wall_s": duration if duration is not None else wall_s,
        "tool_calls": _collect_reviewer_tool_calls(response),
        "tokens": {
            "input": _metric_int(metrics, "input_tokens", "input"),
            "output": _metric_int(metrics, "output_tokens", "output"),
            "total": _metric_int(metrics, "total_tokens", "total"),
            "cache_read": _metric_int(
                metrics, "cache_read_tokens", "cache_read"),
            "cache_write": _metric_int(
                metrics, "cache_write_tokens", "cache_write"),
            "reasoning": _metric_int(
                metrics, "reasoning_tokens", "reasoning"),
        },
    }
    from chatbi_governance.harness_state import write_state

    write_state(Path(workspace_root), session_id, "reviewer-trace.json", payload)


def _default_reviewer_runner(
    agent: Any, workspace_root: Path | str | None = None,
) -> ReviewerRunner:
    """Live runner: call the independent reviewer Agent and parse its output."""

    def _run(review_context: Mapping[str, Any]) -> Mapping[str, Any]:
        if agent is None:
            raise RuntimeError("reviewer agent unavailable (fail-closed)")
        session_id = str(review_context.get("session_id") or "")
        locatable = review_context.get("locatable_paths") or ()
        if not isinstance(locatable, (list, tuple)):
            locatable = ()
        token_s = _reviewer_session_id.set(session_id)
        token_p = _reviewer_locatable_paths.set(
            tuple(str(p) for p in locatable if isinstance(p, str)))
        response = None
        try:
            t0 = time.monotonic()
            response = agent.run(
                json.dumps(compact_review_context(review_context),
                           ensure_ascii=False, sort_keys=True)
            )
            wall = time.monotonic() - t0
            try:
                _write_reviewer_trace(
                    workspace_root=workspace_root,
                    session_id=session_id,
                    response=response,
                    wall_s=wall,
                )
            except Exception:
                pass
        finally:
            _reviewer_session_id.reset(token_s)
            _reviewer_locatable_paths.reset(token_p)
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("reviewer returned no content (fail-closed)")
        verdict = extract_json_object(content)
        if verdict is None:
            raise RuntimeError(
                "reviewer output is not JSON (fail-closed)"
            )
        return verdict

    return _run


def build_review_tool(
    *,
    reviewer_agent: Any,
    reviewer_runner: Any = None,
    run_scope: Any = None,
    reviewer_context_hash: str = "",
    workspace_root: Path | str | None = None,
) -> Callable[[str], dict]:
    """``chatbi_review`` governance-tool function body (skill+hooks module A).

    The tool body calls the reviewer runner (the injected stub for
    conformance, or ``_default_reviewer_runner(reviewer_agent)`` live) and
    returns the RAW verdict payload. All verdict validation (JSON parsing is
    inside the runner; schema ``validate_review``, exact candidate-SHA match
    REV-001/002, REV-003 round limit) is performed by the review hook
    (``runtimes.agno.hooks``, module B) on the RETURN path — the tool
    function only calls the reviewer and fetches the verdict (design §1.3).

    ``run_scope`` is the shared tools<->hooks run-identity holder
    (``governed_tools.RunScope``): the review context carries the frozen
    candidate SHA + the evidence chain (references only) + run id + round,
    so the reviewer cannot drift from the exact candidate (design §7).

    A missing runner is fail-closed: the tool returns an error payload and
    the review hook blocks (HOOK-004) — never an implicit pass.
    """
    runner = reviewer_runner
    if runner is None:
        runner = _default_reviewer_runner(
            reviewer_agent, workspace_root=workspace_root)

    def _review(candidate_sha: str) -> dict:
        if runner is None:
            return {
                "tool": "chatbi_review",
                "status": "requested",
                "candidate_sha": candidate_sha,
                "error": "reviewer runner unavailable (fail-closed)",
            }
        drafts = getattr(run_scope, "draft_model_shas", None) or set()
        adj = getattr(run_scope, "session_adjudication", None)
        kind = (
            "model-draft" if candidate_sha and candidate_sha in drafts
            else "analysis"
        )
        evidence_refs = [
            e.get("evidence_source")
            for e in (getattr(run_scope, "evidence_chain", ()) or ())
            if isinstance(e, Mapping)
        ]
        session_id = str(getattr(run_scope, "session_id", "") or "")
        context = {
            "task": "adversarial_review",
            "candidate_sha": candidate_sha,
            # M1 (review-binding): the harness injects the manifest-pinned
            # governing-context hash (NOT the candidate sha); the reviewer
            # echoes it in the verdict and the kernel verifies equality.
            "reviewer_context_hash": reviewer_context_hash,
            "run_id": getattr(run_scope, "run_id", None) or "run",
            "session_id": getattr(run_scope, "session_id", None) or "",
            "round": int(getattr(run_scope, "review_round", 1) or 1),
            "candidate_kind": kind,
            "session_adjudication": (
                dict(adj) if isinstance(adj, Mapping) else None),
            "authority": {
                "foreign_session_evidence": "not_canonical",
                "published_semantic": "canonical",
                "session_adjudication": "session_scoped",
                "unpublished_models": "draft",
            },
            "evidence_refs": evidence_refs,
            "locatable_paths": locatable_review_paths(
                candidate_content=getattr(run_scope, "candidate_content", None),
                workspace_root=workspace_root,
                session_id=session_id,
            ),
            "evidence_sha_map": evidence_sha_map(
                workspace_root=workspace_root,
                session_id=session_id,
            ),
        }
        verdict = runner(compact_review_context(context))
        result: dict[str, Any] = {
            "tool": "chatbi_review",
            "status": "requested",
            "candidate_sha": candidate_sha,
            "verdict": dict(verdict) if isinstance(verdict, Mapping) else verdict,
        }
        #: The hook's kernel verification compares the verdict's
        #: reviewer_context_hash against THIS value (tool-boundary
        #: carrier; no extra hook plumbing). Emitted only when the
        #: harness injected a context hash (live path) — direct legacy
        #: callers without one keep the pre-M1 hook behavior.
        if reviewer_context_hash:
            result["expected_context_hash"] = reviewer_context_hash
        return result

    return _review


def run_review(
    *,
    run_record: Any,
    candidate_sha: str,
    evidence_chain: tuple[Mapping[str, Any], ...],
    reviewer_runner: ReviewerRunner,
    reviewer_context_hash: str = "",
) -> ReviewResult:
    """Run the independent review and enforce kernel binding.

    The review context carries the frozen candidate SHA + the evidence chain
    (references only) so the reviewer cannot drift from the exact candidate.
    The returned verdict is:

    1. parsed (fail-closed on non-JSON output);
    2. schema-validated via kernel ``evidence.validate_review``
       (fail-closed on any violation, including a missing candidate_sha);
    3. SHA-bound: ``verdict.candidate_sha == candidate_sha`` exactly —
       otherwise BLOCKED (REV-001/002: a PASS bound to another SHA is invalid);
    4. PASS requires verdict status "PASS" AND no blocking findings.

    Any failure path returns a BLOCKED ReviewResult — never an implicit pass.
    """
    try:
        if reviewer_runner is None:
            raise RuntimeError("reviewer runner unavailable (fail-closed)")
        verdict = dict(reviewer_runner(
            {
                "task": "adversarial_review",
                "candidate_sha": candidate_sha,
                # M1 (review-binding): inject the governing-context hash
                # verbatim; the verdict must echo it (kernel-verified).
                "reviewer_context_hash": reviewer_context_hash,
                "run_id": run_record.run_id,
                "round": run_record.round,
                "authority": {"foreign_session_evidence": "not_canonical"},
                "evidence_refs": [
                    e.get("evidence_source")
                    for e in evidence_chain
                    if isinstance(e, Mapping)
                ],
            }
        ))
    except Exception as error:
        return ReviewResult(
            verdict=ReviewVerdict.BLOCKED,
            candidate_sha=candidate_sha,
            # Real-model integration: the message is what makes the failure
            # diagnosable (e.g. a provider/auth error surfaces in Evidence).
            findings=(f"reviewer unavailable: {type(error).__name__}: {error}",),
            sanitized_output=False,
            reason="Reviewer unavailable or unparseable; fail-closed (HOOK-004)",
        )

    try:
        validate_review(verdict)
    except GateError as error:
        return ReviewResult(
            verdict=ReviewVerdict.BLOCKED,
            candidate_sha=candidate_sha,
            findings=tuple(error.decision.rule_ids),
            sanitized_output=False,
            reason=f"Review verdict violates review.schema.json: {error.decision.reason}",
        )

    # M1 (review-binding): the verdict must echo the injected
    # governing-context hash — a mismatch means the verdict was produced
    # under a different review context and cannot bind this candidate
    # (HOOK-001 deterministic edge, REV-002 coverage integrity). Empty
    # param = caller declared no expected context (legacy direct calls):
    # nothing to compare against, so the check is skipped (the LIVE path
    # always injects — agent_builder fails closed when the pin is missing).
    if (reviewer_context_hash
            and verdict.get("reviewer_context_hash") != reviewer_context_hash):
        return ReviewResult(
            verdict=ReviewVerdict.BLOCKED,
            candidate_sha=candidate_sha,
            findings=("HOOK-001", "REV-002"),
            sanitized_output=False,
            reason=(
                "Reviewer verdict does not echo the injected governing-"
                "context hash (REV-002); the verdict is not bound to the "
                "current review context"
            ),
        )

    verdict_sha = verdict.get("candidate_sha")
    if verdict_sha != candidate_sha:
        return ReviewResult(
            verdict=ReviewVerdict.BLOCKED,
            candidate_sha=candidate_sha,
            findings=("REV-001", "REV-003"),
            sanitized_output=bool(verdict.get("sanitized_output", False)),
            reason=(
                "Reviewer PASS is only valid for the exact candidate SHA "
                f"(verdict {verdict_sha!r} != current {candidate_sha!r}); "
                "the candidate changed and must be re-reviewed (REV-001)"
            ),
        )

    # REV-003: round-limit recursion guard — a review beyond the permitted
    # round escalates and never keeps being re-reviewed indefinitely.
    if int(verdict.get("round", 1) or 1) >= 4:
        return ReviewResult(
            verdict=ReviewVerdict.BLOCKED,
            candidate_sha=candidate_sha,
            findings=("REV-003",),
            sanitized_output=bool(verdict.get("sanitized_output", False)),
            reason=(
                "review round exceeded the limit (REV-003); the approval "
                "does not keep being re-reviewed indefinitely"
            ),
        )

    findings = verdict.get("findings", [])
    blocking = [
        f for f in findings
        if isinstance(f, Mapping) and f.get("severity") == "block"
    ]
    if verdict.get("status") != "PASS" or blocking:
        return ReviewResult(
            verdict=ReviewVerdict.BLOCKED,
            candidate_sha=candidate_sha,
            findings=tuple(
                f.get("rule_ids", ("REV-003",))[0]
                if isinstance(f, Mapping) and f.get("rule_ids")
                else "REV-003"
                for f in blocking
            ) or ("REV-003",),
            sanitized_output=bool(verdict.get("sanitized_output", False)),
            reason="Review verdict is not a clean PASS for the frozen candidate",
        )

    return ReviewResult(
        verdict=ReviewVerdict.PASS,
        candidate_sha=candidate_sha,
        findings=(),
        sanitized_output=bool(verdict.get("sanitized_output", False)),
        reason="Independent reviewer PASS for the exact candidate SHA",
    )
