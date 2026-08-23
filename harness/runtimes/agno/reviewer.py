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
from pathlib import Path
from typing import Any, Callable, Mapping

from chatbi_governance.evidence import GateError, validate_review
from chatbi_governance.harness_state import _safe_session_id
from chatbi_runtime_contract.types import ReviewResult, ReviewVerdict

#: Live reviewer FileTools reads this to hide other sessions' .chatbi/runs.
_reviewer_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chatbi_reviewer_session_id", default=""
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
_READ_ONLY_FILE_TOOLS = {
    "read": True,
    "list": True,
    "search": True,
    "save": False,
    "delete": False,
    "chunk": False,
    "write": False,
}


def _make_scoped_file_tools(workspace_root: Path | None) -> Any:
    from agno.tools.file import FileTools

    class _Scoped(FileTools):  # type: ignore[misc]
        def _is_excluded(self, path: Path) -> bool:  # noqa: N802
            if super()._is_excluded(path):
                return True
            return is_foreign_session_evidence(
                path, workspace_root=self.base_dir,
                session_id=_reviewer_session_id.get() or "",
            )

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
            return super().read_file(file_name, encoding=encoding)

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


def _default_reviewer_runner(agent: Any) -> ReviewerRunner:
    """Live runner: call the independent reviewer Agent and parse its output."""

    def _run(review_context: Mapping[str, Any]) -> Mapping[str, Any]:
        if agent is None:
            raise RuntimeError("reviewer agent unavailable (fail-closed)")
        token = _reviewer_session_id.set(
            str(review_context.get("session_id") or ""))
        try:
            response = agent.run(
                json.dumps(review_context, ensure_ascii=False, sort_keys=True)
            )
        finally:
            _reviewer_session_id.reset(token)
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
        runner = _default_reviewer_runner(reviewer_agent)

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
            "evidence_refs": [
                e.get("evidence_source")
                for e in (getattr(run_scope, "evidence_chain", ()) or ())
                if isinstance(e, Mapping)
            ],
        }
        verdict = runner(context)
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
