"""Explicit-test Fixture adapter (technical-design section 8, Ticket 03).

The Fixture adapter is an explicit :class:`Adapter` protocol implementation used
only in test/example mode. It returns deterministic synthetic evidence loaded
from ``.claude/fixtures/semantic-catalog.json`` and
``.claude/fixtures/warehouse.json``.

Production mode (no test/example flag) deterministically blocks every operation
with rule ID ``PORT-001``; the recovery action explicitly states "enable test
mode or configure a real adapter". The Fixture adapter never silently acts as a
production fallback and never labels its evidence ``local_probe`` (PORT-001,
SEM-002, SEC-003).

Fixture data contains no organizational facts, no real credentials, no PII, no
machine absolute paths and no secret canary. Warehouse numbers are anchored to a
fixed snapshot date and do not drift with the current date.

Design gap: ``adapters/__init__.py`` still STOPs at ``fixture_pending`` in the
selection chain. Wiring :class:`FixtureAdapter` into ``select_adapter`` requires
modifying ``adapters/__init__.py`` (Ticket 02 territory) and is out of scope for
Ticket 03 per the file-ownership constraint. The adapter is tested by direct
construction until that integration ticket lands.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..resources import get_fixtures_root
from .base import (
    AdapterCapabilities,
    AdapterEvidence,
    validate_adapter_id,
    # Private helpers reused to keep the content hash and timestamp canonical
    # with the AdapterEvidence.ok/error/unavailable factories. base.py does not
    # expose a ``blocked`` factory (Ticket 02 territory), so the production-mode
    # block constructs AdapterEvidence directly and must compute these fields
    # with the same algorithm to remain hash-consistent.
    _content_sha256,
    _utc_now_iso,
)


_RUN_MODES = frozenset({"production", "test", "example"})
_FIXTURE_RUN_MODES = frozenset({"test", "example"})
_ADAPTER_KINDS = frozenset({"semantic", "query"})

# Asset root resolution (multi-runtime module 2): replaces the historical
# parents[3] depth derivation (feature-flow §3.1.2); behavior unchanged.
_DEFAULT_FIXTURES_ROOT = get_fixtures_root()

_CATALOG_FILENAME = "semantic-catalog.json"
_WAREHOUSE_FILENAME = "warehouse.json"

_PRODUCTION_BLOCK_REASON = (
    "Fixture adapter is not available outside explicit test/example mode"
)
_PRODUCTION_BLOCK_RECOVERY = (
    "Enable fixture mode and run with a test/example flag, "
    "or configure a real adapter"
)

# Fixed synthetic compile/quality/lineage payloads. Deterministic, no
# organizational facts, no credentials, no PII, no machine paths.
_COMPILED_PAYLOAD: dict[str, Any] = {
    "fixture": True,
    "compiled_sql": (
        "SELECT SUM(amount) AS revenue "
        "FROM fixture_orders "
        "WHERE ds BETWEEN '2024-01-01' AND '2024-01-15'"
    ),
    "source_refs": ["fixture:metric:revenue"],
    "disclosure": "sql_only",
}

_QUALITY_PAYLOAD: dict[str, Any] = {
    "fixture": True,
    "freshness": "2024-01-15",
    "completeness": 1.0,
    "issues": [],
    "notes": "Synthetic quality evidence; all checks pass by construction",
}

_LINEAGE_PAYLOAD: dict[str, Any] = {
    "fixture": True,
    "upstream": ["fixture:table:orders"],
    "downstream": [],
    "notes": "Synthetic lineage evidence; single upstream table",
}


class FixtureAdapter:
    """Explicit-test Fixture adapter (Ticket 03, technical-design section 8).

    Implements the :class:`~chatbi_governance.adapters.base.Adapter` protocol.
    Only available when ``fixture_enabled`` is true and ``run_mode`` is
    ``test`` or ``example``. In production mode every operation deterministically
    blocks with ``PORT-001``; the Fixture adapter never silently acts as a
    production fallback.

    ``discover`` returns the synthetic semantic catalog (SEM-002: must contain
    metrics/dimensions/segments). ``query`` returns the fixed synthetic
    warehouse snapshot (numbers anchored, no date drift). ``quality`` and
    ``lineage`` return fixed synthetic evidence. All evidence is tagged
    ``evidence_source=fixture`` and never ``local_probe``.
    """

    __slots__ = (
        "_adapter_id",
        "_kind",
        "_run_mode",
        "_fixture_enabled",
        "_fixtures_root",
    )

    def __init__(
        self,
        adapter_id: str,
        kind: str,
        run_mode: str,
        *,
        fixture_enabled: bool = True,
        fixtures_root: Path | None = None,
    ) -> None:
        if not validate_adapter_id(adapter_id) or not adapter_id.startswith("fixture:"):
            raise ValueError(f"Invalid fixture adapter ID: {adapter_id}")
        if kind not in _ADAPTER_KINDS:
            raise ValueError(f"Unknown adapter kind: {kind}")
        if run_mode not in _RUN_MODES:
            raise ValueError(f"Unknown run mode: {run_mode}")
        self._adapter_id = adapter_id
        self._kind = kind
        self._run_mode = run_mode
        self._fixture_enabled = bool(fixture_enabled)
        self._fixtures_root = (
            Path(fixtures_root) if fixtures_root is not None else _DEFAULT_FIXTURES_ROOT
        )

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def _is_available(self) -> bool:
        """True only when fixture_enabled and run_mode is test/example."""
        return self._fixture_enabled and self._run_mode in _FIXTURE_RUN_MODES

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            discover=True, query=True, quality=True, lineage=True, mutate=False
        )

    # -- evidence factories --------------------------------------------------

    def _blocked(self, operation: str) -> AdapterEvidence:
        """Deterministic block evidence for production mode (PORT-001)."""
        payload: dict[str, Any] = {"operation": operation, "fixture_blocked": True}
        return AdapterEvidence(
            adapter_id=self._adapter_id,
            produced_at=_utc_now_iso(),
            evidence_source="fixture",
            status="blocked",
            content_sha256=_content_sha256(payload),
            rule_ids=("PORT-001",),
            error_category="fixture_not_test_mode",
            payload=payload,
            reason=_PRODUCTION_BLOCK_REASON,
            recovery=_PRODUCTION_BLOCK_RECOVERY,
        )

    def _ok(self, payload: Any, *, operation: str) -> AdapterEvidence:
        return AdapterEvidence.ok(
            adapter_id=self._adapter_id,
            evidence_source="fixture",
            payload=payload,
            rule_ids=("PORT-001",),
            reason=f"Fixture adapter returned synthetic {operation} evidence",
            recovery="No action required",
        )

    def _error(self, operation: str, reason: str) -> AdapterEvidence:
        payload: dict[str, Any] = {"operation": operation, "fixture_error": True}
        return AdapterEvidence.error(
            adapter_id=self._adapter_id,
            evidence_source="fixture",
            error_category="fixture_load_failure",
            reason=reason,
            recovery="Verify the fixture data files exist and are valid JSON",
            rule_ids=("PORT-001",),
            payload=payload,
        )

    def _load_fixture(self, filename: str) -> Any | None:
        """Load a fixture JSON file; return None on missing/invalid file."""
        path = self._fixtures_root / filename
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    # -- Adapter protocol methods -------------------------------------------

    def healthcheck(
        self, context: Mapping[str, Any] | None = None
    ) -> AdapterEvidence:
        if not self._is_available():
            return self._blocked("healthcheck")
        payload: dict[str, Any] = {
            "fixture": True,
            "available": True,
            "run_mode": self._run_mode,
        }
        return self._ok(payload, operation="healthcheck")

    def discover(self, request: Mapping[str, Any]) -> AdapterEvidence:
        if not self._is_available():
            return self._blocked("discover")
        catalog = self._load_fixture(_CATALOG_FILENAME)
        if catalog is None:
            return self._error("discover", f"Could not load {_CATALOG_FILENAME}")
        return self._ok(catalog, operation="discover")

    def compile(self, query_spec: Mapping[str, Any]) -> AdapterEvidence:
        if not self._is_available():
            return self._blocked("compile")
        return self._ok(_COMPILED_PAYLOAD, operation="compile")

    def query(
        self,
        compiled: Mapping[str, Any],
        disclosure_policy: Mapping[str, Any] | None = None,
    ) -> AdapterEvidence:
        if not self._is_available():
            return self._blocked("query")
        snapshot = self._load_fixture(_WAREHOUSE_FILENAME)
        if snapshot is None:
            return self._error("query", f"Could not load {_WAREHOUSE_FILENAME}")
        return self._ok(snapshot, operation="query")

    def quality(self, source_refs: tuple[str, ...]) -> AdapterEvidence:
        if not self._is_available():
            return self._blocked("quality")
        payload = dict(_QUALITY_PAYLOAD)
        payload["source_refs"] = list(source_refs)
        return self._ok(payload, operation="quality")

    def lineage(self, source_refs: tuple[str, ...]) -> AdapterEvidence:
        if not self._is_available():
            return self._blocked("lineage")
        payload = dict(_LINEAGE_PAYLOAD)
        payload["source_refs"] = list(source_refs)
        return self._ok(payload, operation="lineage")
