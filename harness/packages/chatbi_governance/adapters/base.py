"""Platform-neutral adapter protocol and evidence schema (technical-design §8.1).

Defines the :class:`Adapter` protocol and the JSON evidence model every adapter
result must carry. Evidence is structured data with a content SHA-256; adapter
tool stdout is captured as a data payload and never spliced into a Shell or
system prompt (SEC-003, SCOPE-003, technical-design §8.1).

Adapter IDs have the form ``<family>:<name>`` where ``<family>`` is ``managed``,
``cli`` or ``fixture`` and ``<name>`` is a lowercase identifier. This mirrors
``diagnostics._ADAPTER_ID`` from Cycle 1 (PORT-001: adapter identities carry no
machine absolute paths). The pattern is re-declared here rather than imported
from a private name, and is kept in sync by contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol


ADAPTER_ID_PATTERN = re.compile(r"^(?:managed|cli|fixture):[a-z][a-z0-9_-]{1,62}$")
# Mirrors diagnostics._ADAPTER_ID (Cycle 1). Not imported from diagnostics to
# avoid coupling the adapter layer to a private name; kept in sync by contract.

_EVIDENCE_SOURCES = frozenset({"managed", "cli", "fixture", "local_probe"})
_EVIDENCE_STATUSES = frozenset({"ok", "unavailable", "error", "blocked"})


def validate_adapter_id(adapter_id: str) -> bool:
    """Return True if ``adapter_id`` matches the governed adapter ID shape."""
    return ADAPTER_ID_PATTERN.fullmatch(adapter_id) is not None


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _content_sha256(payload: Any) -> str:
    """Stable SHA-256 over the canonical JSON encoding of ``payload``."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """Declares which operations an adapter provides (technical-design §8.1).

    ``mutate`` is optional and defaults to disabled; v1 adapters never expose
    mutation (SEC-001, technical-design §8.3).
    """

    discover: bool
    query: bool
    quality: bool
    lineage: bool
    mutate: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "discover": self.discover,
            "query": self.query,
            "quality": self.quality,
            "lineage": self.lineage,
            "mutate": self.mutate,
        }


@dataclass(frozen=True, slots=True)
class AdapterEvidence:
    """JSON evidence carried by every adapter result (technical-design §8.1).

    Fields: adapter ID, UTC production time, evidence source, status, error
    category, content SHA-256, rule IDs, payload, reason and recovery. The
    payload is untrusted data captured from the adapter tool; it is hashed and
    structured, and is never spliced into a Shell or system prompt. Use the
    ``ok``/``unavailable``/``error`` classmethod factories so the content hash
    and timestamp are computed deterministically.
    """

    adapter_id: str
    produced_at: str
    evidence_source: str
    status: str
    content_sha256: str
    rule_ids: tuple[str, ...] = ()
    error_category: str | None = None
    payload: Any = None
    reason: str = ""
    recovery: str = ""

    def __post_init__(self) -> None:
        if not validate_adapter_id(self.adapter_id):
            raise ValueError(f"Invalid adapter ID: {self.adapter_id}")
        if self.evidence_source not in _EVIDENCE_SOURCES:
            raise ValueError(f"Invalid evidence source: {self.evidence_source}")
        if self.status not in _EVIDENCE_STATUSES:
            raise ValueError(f"Invalid evidence status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "produced_at": self.produced_at,
            "evidence_source": self.evidence_source,
            "status": self.status,
            "error_category": self.error_category,
            "content_sha256": self.content_sha256,
            "rule_ids": list(self.rule_ids),
            "payload": self.payload,
            "reason": self.reason,
            "recovery": self.recovery,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def ok(
        cls,
        *,
        adapter_id: str,
        evidence_source: str,
        payload: Any,
        rule_ids: tuple[str, ...] = (),
        reason: str = "",
        recovery: str = "",
    ) -> "AdapterEvidence":
        return cls(
            adapter_id=adapter_id,
            produced_at=_utc_now_iso(),
            evidence_source=evidence_source,
            status="ok",
            content_sha256=_content_sha256(payload),
            rule_ids=rule_ids,
            payload=payload,
            reason=reason,
            recovery=recovery,
        )

    @classmethod
    def unavailable(
        cls,
        *,
        adapter_id: str,
        evidence_source: str,
        rule_ids: tuple[str, ...] = (),
        error_category: str = "unavailable",
        reason: str = "",
        recovery: str = "",
        payload: Any = None,
    ) -> "AdapterEvidence":
        return cls(
            adapter_id=adapter_id,
            produced_at=_utc_now_iso(),
            evidence_source=evidence_source,
            status="unavailable",
            content_sha256=_content_sha256(payload),
            rule_ids=rule_ids,
            error_category=error_category,
            payload=payload,
            reason=reason,
            recovery=recovery,
        )

    @classmethod
    def error(
        cls,
        *,
        adapter_id: str,
        evidence_source: str,
        error_category: str,
        reason: str,
        recovery: str = "",
        rule_ids: tuple[str, ...] = (),
        payload: Any = None,
    ) -> "AdapterEvidence":
        return cls(
            adapter_id=adapter_id,
            produced_at=_utc_now_iso(),
            evidence_source=evidence_source,
            status="error",
            content_sha256=_content_sha256(payload),
            rule_ids=rule_ids,
            error_category=error_category,
            payload=payload,
            reason=reason,
            recovery=recovery,
        )


class Adapter(Protocol):
    """Platform-neutral adapter contract (technical-design §8.1).

    Implementations include managed, approved CLI and explicit-test fixture
    adapters. Every method returns :class:`AdapterEvidence`; results are
    structured JSON with a content hash and never spliced into a prompt.
    """

    @property
    def adapter_id(self) -> str: ...

    def capabilities(self) -> AdapterCapabilities: ...

    def healthcheck(
        self, context: Mapping[str, Any] | None = None
    ) -> AdapterEvidence: ...

    def discover(self, request: Mapping[str, Any]) -> AdapterEvidence: ...

    def compile(self, query_spec: Mapping[str, Any]) -> AdapterEvidence: ...

    def query(
        self,
        compiled: Mapping[str, Any],
        disclosure_policy: Mapping[str, Any] | None = None,
    ) -> AdapterEvidence: ...

    def quality(self, source_refs: tuple[str, ...]) -> AdapterEvidence: ...

    def lineage(self, source_refs: tuple[str, ...]) -> AdapterEvidence: ...
