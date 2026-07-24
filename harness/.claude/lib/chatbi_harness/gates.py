"""Stable, machine-readable decisions for deterministic Harness gates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_URL_QUERY = re.compile(r"(?P<base>https?://[^\s?\"]+)\?[^\s\"]+", re.IGNORECASE)
_NAMED_SECRET = re.compile(
    r"\b(?P<name>api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_BEARER_SECRET = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_PREFIXED_SECRET = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{8,}")
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.:-])/(?:Users|private|tmp|var|home|opt|etc|root)"
    r"(?:/[^\s,;)\]}]+)+"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"\b[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s,;]+"
)
_RULE_ID = re.compile(
    r"\b(?:SCOPE|SEC|REQ|SEM|RAW|SRC|DOC|PORT|QLT|REV|ANS|EVAL|ABL|FBK|HOOK)"
    r"-\d{3}\b"
)
_CONTRACT_ARTIFACTS = (
    "CLAUDE.md",
    "CONTEXT.md",
    ".claude/rules/00-domain-contract.md",
    ".claude/rules/10-security.md",
    ".claude/rules/20-completion.md",
)


def _sanitize_text(value: str) -> str:
    sanitized = _URL_QUERY.sub(r"\g<base>?[REDACTED_QUERY]", value)
    sanitized = _NAMED_SECRET.sub(r"\g<name>=[REDACTED_SECRET]", sanitized)
    sanitized = _BEARER_SECRET.sub("Bearer [REDACTED_SECRET]", sanitized)
    sanitized = _PREFIXED_SECRET.sub("[REDACTED_SECRET]", sanitized)
    sanitized = _POSIX_ABSOLUTE_PATH.sub("[REDACTED_PATH]", sanitized)
    return _WINDOWS_ABSOLUTE_PATH.sub("[REDACTED_PATH]", sanitized)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True, slots=True)
class GateDecision:
    """A deterministic gate outcome exposed to Hooks and commands."""

    status: str
    rule_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str
    recovery: str

    def __post_init__(self) -> None:
        if self.status not in {"pass", "warn", "block"}:
            raise ValueError("GateDecision status must be pass, warn, or block")
        object.__setattr__(self, "rule_ids", _unique(self.rule_ids))
        object.__setattr__(
            self,
            "evidence_refs",
            _unique(_sanitize_text(value) for value in self.evidence_refs),
        )
        object.__setattr__(self, "reason", _sanitize_text(self.reason))
        object.__setattr__(self, "recovery", _sanitize_text(self.recovery))

    @classmethod
    def pass_(
        cls,
        *,
        rule_ids: Iterable[str],
        evidence_refs: Iterable[str],
        reason: str,
        recovery: str,
    ) -> "GateDecision":
        return cls(
            status="pass",
            rule_ids=_unique(rule_ids),
            evidence_refs=_unique(evidence_refs),
            reason=reason,
            recovery=recovery,
        )

    @classmethod
    def warn(
        cls,
        *,
        rule_ids: Iterable[str],
        evidence_refs: Iterable[str],
        reason: str,
        recovery: str,
    ) -> "GateDecision":
        return cls(
            status="warn",
            rule_ids=_unique(rule_ids),
            evidence_refs=_unique(evidence_refs),
            reason=reason,
            recovery=recovery,
        )

    @classmethod
    def block(
        cls,
        *,
        rule_ids: Iterable[str],
        evidence_refs: Iterable[str],
        reason: str,
        recovery: str,
    ) -> "GateDecision":
        return cls(
            status="block",
            rule_ids=_unique(rule_ids),
            evidence_refs=_unique(evidence_refs),
            reason=reason,
            recovery=recovery,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "rule_ids": list(self.rule_ids),
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "recovery": self.recovery,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class GateError(Exception):
    """An exception boundary that carries a sanitized blocking decision."""

    def __init__(self, decision: GateDecision) -> None:
        if decision.status != "block":
            raise ValueError("GateError requires a blocking decision")
        self.decision = decision
        super().__init__(decision.to_json())


def fail_closed(
    error: BaseException,
    *,
    rule_ids: Iterable[str] = ("HOOK-004",),
    evidence_refs: Iterable[str] = (),
    recovery: str = "Inspect the sanitized evidence and correct the gate input",
) -> GateDecision:
    """Convert an unexpected failure into a deterministic blocking decision."""

    return GateDecision.block(
        rule_ids=rule_ids,
        evidence_refs=evidence_refs,
        reason=f"Unexpected gate failure: {type(error).__name__}",
        recovery=recovery,
    )


def validate_domain_contract(workspace_root: Path) -> GateDecision:
    """Validate the static, governed contract exposed by a Harness workspace."""

    domain_model = workspace_root / "docs" / "chatbi-harness-domain-model.md"
    if not domain_model.is_file():
        return GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("contract:domain-model",),
            reason="Governed domain model is missing",
            recovery="Restore docs/chatbi-harness-domain-model.md before using Harness artifacts",
        )
    try:
        governed_rule_ids = set(_RULE_ID.findall(domain_model.read_text(encoding="utf-8")))
        referenced_rule_ids: set[str] = set()
        for relative_path in _CONTRACT_ARTIFACTS:
            artifact = workspace_root / relative_path
            if not artifact.is_file():
                return GateDecision.block(
                    rule_ids=("HOOK-004",),
                    evidence_refs=(f"contract:{relative_path}",),
                    reason=f"Required Harness contract artifact is missing: {relative_path}",
                    recovery="Restore the missing contract artifact before using the Harness",
                )
            artifact_text = artifact.read_text(encoding="utf-8")
            if relative_path == "CLAUDE.md" and len(artifact_text.splitlines()) > 200:
                return GateDecision.block(
                    rule_ids=("HOOK-004",),
                    evidence_refs=("contract:CLAUDE.md",),
                    reason="Root Harness contract exceeds the 200-line budget",
                    recovery="Move conditional details into scoped rules",
                )
            if _sanitize_text(artifact_text) != artifact_text:
                return GateDecision.block(
                    rule_ids=("HOOK-004",),
                    evidence_refs=(f"contract:{relative_path}",),
                    reason="Harness contract contains sensitive or machine-local content",
                    recovery="Replace secrets, query values, and absolute paths with portable references",
                )
            referenced_rule_ids.update(_RULE_ID.findall(artifact_text))
    except Exception as error:
        return fail_closed(error, evidence_refs=("contract:static-files",))

    unknown_rule_ids = sorted(referenced_rule_ids - governed_rule_ids)
    if unknown_rule_ids:
        return GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("contract:rule-references",),
            reason=f"Unknown governed rule references: {', '.join(unknown_rule_ids)}",
            recovery="Remove the unknown references or update the domain model through governance",
        )
    missing_rule_ids = sorted(governed_rule_ids - referenced_rule_ids)
    if missing_rule_ids:
        return GateDecision.block(
            rule_ids=("HOOK-004",),
            evidence_refs=("contract:rule-coverage",),
            reason=f"Missing governed rule coverage: {', '.join(missing_rule_ids)}",
            recovery="Add every governed rule ID to the applicable Harness contract artifact",
        )
    return GateDecision.pass_(
        rule_ids=("HOOK-004",),
        evidence_refs=("contract:domain-model",),
        reason="Governed domain model is available",
        recovery="No action required",
    )
