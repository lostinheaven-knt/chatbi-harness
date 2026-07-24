"""Cycle 5 Task 01: offline evaluator + ground-truth isolation runner.

``EvaluationRun`` records a reproducible evaluation: skill version, content
hash (no Git -> content hash, EVAL-003), model id, per-assertion results
(seen/unseen separated), tokens, latency. ``GroundTruthVault`` physically
isolates ground-truth answers from the session under test: it exposes only
``AssertionResult`` (pass/fail + hashes of expected/actual), never the raw
expected answer, so a tested session cannot read the answers (EVAL-001/002,
ABL isolation).

Evaluation success is evidence, NOT a guarantee that silent failure is
eliminated (FBK-003). Hashing + sanitization reuse ``evidence``/``gates``.

Applicable rules: EVAL-001/002/003/004/005, ABL-001/002, FBK-001/002/003,
HOOK-001/004, SEC-003, PORT-001.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .gates import GateError, _sanitize_text  # noqa: E402 (sanctioned reuse)
from .evidence import (  # noqa: E402 (sanctioned reuse)
    _get_schema,
    _validate_against_schema,
    compute_candidate_sha,
)

# FBK-003 mandatory disclosure: evaluation pass != absolute correctness.
FBK_003_STATEMENT = (
    "Evaluation success is evidence; it does NOT prove silent failure is "
    "eliminated and is NOT a guarantee of absolute correctness (FBK-003)."
)

_FIX_KINDS = frozenset({"reference", "Skill", "model"})


def _eval_gate_error(*, rule_ids: tuple[str, ...], evidence_ref: str,
                     reason: str, recovery: str) -> GateError:
    from .gates import GateDecision
    return GateError(
        GateDecision.block(rule_ids=rule_ids, evidence_refs=(evidence_ref,),
                           reason=reason, recovery=recovery))


@dataclass(frozen=True, slots=True)
class AssertionResult:
    """One assertion outcome. ``expected_hash``/``actual_hash`` are SHA-256 of
    the expected/actual answers; the raw expected answer is NEVER stored here
    (ground-truth isolation)."""

    assertion_id: str
    passed: bool
    expected_hash: str
    actual_hash: str
    rule_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "passed": self.passed,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """A reproducible evaluation run record (EVAL-003)."""

    run_id: str
    skill_version: str
    content_hash: str
    model_id: str
    assertions: tuple[AssertionResult, ...]
    tokens: int
    latency_ms: int
    seen: bool
    threshold_owner_confirmed: bool

    @property
    def passed_count(self) -> int:
        return sum(1 for a in self.assertions if a.passed)

    @property
    def all_passed(self) -> bool:
        return bool(self.assertions) and all(a.passed for a in self.assertions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "skill_version": self.skill_version,
            "content_hash": self.content_hash,
            "model_id": self.model_id,
            "assertions": [a.to_dict() for a in self.assertions],
            "tokens": self.tokens,
            "latency_ms": self.latency_ms,
            "seen": self.seen,
            "threshold_owner_confirmed": self.threshold_owner_confirmed,
            "passed_count": self.passed_count,
            "total_count": len(self.assertions),
            "fbk_003_statement": FBK_003_STATEMENT,
        }


def _sha(value: Any) -> str:
    return hashlib.sha256(
        repr(sorted(value.items()) if isinstance(value, Mapping) else value)
        .encode("utf-8")
    ).hexdigest()


class GroundTruthVault:
    """Physically isolates ground-truth answers from the session under test.

    Answers are held inside the vault; the only exposed outcome is
    :class:`AssertionResult` (pass/fail + hashes). The raw expected answer is
    never returned to the caller, so a tested session cannot read it. A scorer
    may be supplied for non-exact scoring (entity selection / query scoring);
    default is exact equality on canonicalized values.
    """

    __slots__ = ("_answers", "_scorers")

    def __init__(self, answers: Mapping[str, Any],
                 scorers: Mapping[str, Callable[[Any, Any], bool]] | None = None) -> None:
        if not answers:
            raise _eval_gate_error(
                rule_ids=("EVAL-001", "HOOK-004"),
                evidence_ref="evaluator:empty-ground-truth",
                reason="ground-truth vault is empty",
                recovery="Provide at least one assertion answer",
            )
        self._answers = dict(answers)
        self._scorers = dict(scorers) if scorers else {}

    def assertion_ids(self) -> tuple[str, ...]:
        return tuple(self._answers)

    def score(self, assertion_id: str, actual: Any,
              rule_ids: tuple[str, ...] = ()) -> AssertionResult:
        if assertion_id not in self._answers:
            raise _eval_gate_error(
                rule_ids=("EVAL-002", "HOOK-004"),
                evidence_ref=f"evaluator:unknown-assertion:{assertion_id}",
                reason=f"assertion_id {assertion_id!r} has no ground truth",
                recovery="Use an assertion_id present in the vault",
            )
        expected = self._answers[assertion_id]
        scorer = self._scorers.get(assertion_id)
        passed = bool(scorer(expected, actual)) if scorer else (expected == actual)
        return AssertionResult(
            assertion_id=assertion_id,
            passed=passed,
            expected_hash=_sha(expected),
            actual_hash=_sha(actual),
            rule_ids=tuple(rule_ids),
        )

    # NOTE: there is intentionally NO method that returns the raw expected
    # answer. This is the ground-truth isolation invariant (EVAL-001/002).


def build_evaluation_run(
    *, run_id: str, skill_version: str, model_id: str,
    vault: GroundTruthVault, actuals: Mapping[str, Any],
    tokens: int, latency_ms: int, seen: bool,
    threshold_owner_confirmed: bool,
    rule_ids_by_assertion: Mapping[str, tuple[str, ...]] | None = None,
    content_payload: Any,
) -> EvaluationRun:
    """Build an EvaluationRun by scoring ``actuals`` against the vault.

    Fail-closed: a missing assertion_id in the vault raises ``GateError``.
    ``content_hash`` is computed from ``content_payload`` (no Git -> content
    hash, EVAL-003). The run always carries the FBK-003 statement.
    """
    if not run_id or not model_id:
        raise _eval_gate_error(
            rule_ids=("EVAL-003", "HOOK-004"),
            evidence_ref="evaluator:run-fields",
            reason="run_id and model_id are required",
            recovery="Provide a non-empty run_id and model_id",
        )
    rids = rule_ids_by_assertion or {}
    assertions = tuple(
        vault.score(aid, actuals[aid], rids.get(aid, ()))
        for aid in vault.assertion_ids()
    )
    run = EvaluationRun(
        run_id=run_id,
        skill_version=_sanitize_text(skill_version) if skill_version else "",
        content_hash=compute_candidate_sha(content_payload),
        model_id=model_id,
        assertions=assertions,
        tokens=int(tokens),
        latency_ms=int(latency_ms),
        seen=bool(seen),
        threshold_owner_confirmed=bool(threshold_owner_confirmed),
    )
    validate_evaluation(run.to_dict())
    return run


def validate_evaluation(payload: Mapping[str, Any]) -> None:
    _validate_against_schema(
        payload, _get_schema("evaluation.schema.json"), "evaluation.schema.json")


def validate_correction(payload: Mapping[str, Any]) -> None:
    _validate_against_schema(
        payload, _get_schema("correction.schema.json"), "correction.schema.json")


def build_correction_record(
    *, correction_id: str, fix_kind: str, fix_target: str,
    fix_change_summary: str, eval_case_assertion_id: str,
    eval_case_expected_hash: str, rule_ids: tuple[str, ...],
    owner_approved: bool = False, description: str = "",
) -> dict[str, Any]:
    """Build a correction.schema.json-conformant record. A correction produces
    BOTH a fix candidate AND an eval-case candidate (FBK-002); ``owner_approved``
    defaults to False (no auto-merge; SEM-003)."""
    if fix_kind not in _FIX_KINDS:
        raise _eval_gate_error(
            rule_ids=("FBK-002", "HOOK-004"),
            evidence_ref="evaluator:fix-kind",
            reason=f"Unknown fix_kind: {fix_kind}",
            recovery=f"Use one of {sorted(_FIX_KINDS)}",
        )
    if not correction_id:
        raise _eval_gate_error(
            rule_ids=("FBK-002", "HOOK-004"),
            evidence_ref="evaluator:correction-id",
            reason="correction_id is required",
            recovery="Provide a non-empty correction_id",
        )
    record = {
        "correction_id": correction_id,
        "description": _sanitize_text(description) if description else "",
        "fix_candidate": {
            "kind": fix_kind,
            "target": _sanitize_text(fix_target) if fix_target else "",
            "change_summary": _sanitize_text(fix_change_summary) if fix_change_summary else "",
        },
        "eval_case_candidate": {
            "assertion_id": _sanitize_text(eval_case_assertion_id) if eval_case_assertion_id else "",
            "expected_hash": eval_case_expected_hash,
        },
        "owner_approved": bool(owner_approved),
        "rule_ids": list(rule_ids),
        "fbk_003_statement": FBK_003_STATEMENT,
    }
    validate_correction(record)
    return record


__all__ = [
    "AssertionResult",
    "EvaluationRun",
    "FBK_003_STATEMENT",
    "GroundTruthVault",
    "build_correction_record",
    "build_evaluation_run",
    "validate_correction",
    "validate_evaluation",
]
