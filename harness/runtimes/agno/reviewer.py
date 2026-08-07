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

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from chatbi_governance.evidence import GateError, validate_review
from chatbi_runtime_contract.types import ReviewResult, ReviewVerdict

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


def build_reviewer_agent(
    deployment: Any,
    model_config: Any,
) -> Any:
    """Build the independent reviewer Agent (agno 2.6.22).

    ``deployment`` is the resolved runtimes.agno.config.DeploymentConfig and
    ``model_config`` the resolved ModelConfig. Returns an ``agno.agent.Agent``
    configured with read-only tools, an explicit distinct id, and no shared
    memory wiring.
    """
    from . import ensure_agno_unshadowed

    ensure_agno_unshadowed()
    from agno.agent import Agent
    from agno.tools.file import FileTools

    tools = [
        FileTools(
            base_dir=None,
            enable_save_file=False,
            enable_delete_file=False,
            enable_read_file=True,
            enable_list_files=True,
            enable_search_files=True,
            enable_search_content=True,
            enable_read_file_chunk=False,
            enable_replace_file_chunk=False,
        )
    ]
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
        response = agent.run(
            json.dumps(review_context, ensure_ascii=False, sort_keys=True)
        )
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
                "reviewer_context_hash": reviewer_context_hash or candidate_sha,
                "run_id": run_record.run_id,
                "round": run_record.round,
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
