"""Tests for chatbi_harness.build_plan (requirement-driven build lib).

Covers (per technical-design-requirement-driven-build.md §9.1):
- ModelEntry / HumanApproval / CrossLayerException: frozen-slots, to_dict
  round-trip against build-plan.schema.json, build_model_entry factory
  validation + sanitization (Q5).
- read_model_registry: absent -> (), parse round-trip, reject malformed /
  wrong schema_version / tampered entry (Q3, HOOK-004).
- read_source_inventory (bootstrap.py, Q4): round-trip, absent/malformed ->
  GateError.
- validate_build_plan: PASS on well-ordered plan; raise on topology out of
  order, SEM-003 consistency, Q1 extend-source gate, SCOPE-001 cross-plan-
  boundary (open point 6: known_models).
- validate_layer_dependency: PASS on ADS->DWS->DWD->ODS + DIM; raise on
  cross-layer violation; cross_layer_exception does NOT raise (Q2).
- append_model_registry: create if absent, idempotent on (name, created_rev),
  append-with-history, atomic write (0o600, no .tmp left), does not mutate
  entry.

Applicable rules: SCOPE-001, SEC-001/003, RAW-003, SEM-003, PORT-001,
DOC-002, META-003, HOOK-001/004.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / "harness" / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.bootstrap import (  # noqa: E402
    SourceColumn,
    SourceInventory,
    SourceTable,
    read_source_inventory,
)
from chatbi_harness.build_plan import (  # noqa: E402
    BuildPlan,
    CrossLayerException,
    HumanApproval,
    LayerRule,
    ModelEntry,
    append_model_registry,
    build_model_entry,
    collect_known_models,
    read_model_registry,
    validate_build_plan,
    validate_layer_dependency,
)
from chatbi_harness.evidence import _get_schema, _validate_against_schema  # noqa: E402
from chatbi_harness.gates import GateError  # noqa: E402


# v1 default layer-permission matrix (matches bootstrap ## Layers stub skeleton).
DEFAULT_LAYER_RULES = (
    LayerRule(layer="ods", may_depend_on=frozenset()),
    LayerRule(layer="dwd", may_depend_on=frozenset({"ods", "dim"})),
    LayerRule(layer="dws", may_depend_on=frozenset({"dwd", "dim"})),
    LayerRule(layer="ads", may_depend_on=frozenset({"dws", "dim"})),
    LayerRule(layer="dim", may_depend_on=frozenset()),
)


def _valid_plan() -> BuildPlan:
    """A well-ordered plan: dim, ods -> dwd -> dws -> ads (all deps before dependents)."""
    dim = build_model_entry(
        name="dim_date", layer="dim", change_kind="model",
        created_rev="r1", owner="op",
    )
    ods = build_model_entry(
        name="ods_orders", layer="ods", change_kind="model",
        created_rev="r1", owner="op",
    )
    dwd = build_model_entry(
        name="dwd_orders", layer="dwd", change_kind="model",
        created_rev="r1", owner="op",
        upstream_deps=("ods_orders", "dim_date"),
        join_or_aggregate_summary="join ods_orders on order_id",
    )
    dws = build_model_entry(
        name="dws_orders_daily", layer="dws", change_kind="model",
        created_rev="r1", owner="op",
        upstream_deps=("dwd_orders", "dim_date"),
    )
    ads = build_model_entry(
        name="ads_revenue_summary", layer="ads", change_kind="model",
        created_rev="r1", owner="op",
        upstream_deps=("dws_orders_daily", "dim_date"),
    )
    return BuildPlan(
        schema_version=1, session_id="sess-1",
        models=(dim, ods, dwd, dws, ads),
    )


class ModelEntryDataclassTests(unittest.TestCase):
    def test_model_entry_is_frozen(self) -> None:
        entry = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
        )
        with self.assertRaises(Exception):
            entry.name = "other"  # type: ignore[misc]

    def test_human_approval_is_frozen(self) -> None:
        ha = HumanApproval()
        with self.assertRaises(Exception):
            ha.approved = True  # type: ignore[misc]

    def test_cross_layer_exception_is_frozen(self) -> None:
        cle = CrossLayerException(reason="legacy", approved_by="op")
        with self.assertRaises(Exception):
            cle.reason = "other"  # type: ignore[misc]

    def test_build_plan_is_frozen(self) -> None:
        plan = _valid_plan()
        with self.assertRaises(Exception):
            plan.session_id = "other"  # type: ignore[misc]

    def test_to_dict_round_trips_schema(self) -> None:
        plan = _valid_plan()
        payload = plan.to_dict()
        _validate_against_schema(
            payload, _get_schema("build-plan.schema.json"),
            "build-plan.schema.json",
        )

    def test_to_dict_shape(self) -> None:
        entry = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
            upstream_deps=("ods_orders",),
            join_or_aggregate_summary="join on id",
        )
        d = entry.to_dict()
        self.assertEqual(d["name"], "dwd_orders")
        self.assertEqual(d["layer"], "dwd")
        self.assertEqual(d["upstream_deps"], ["ods_orders"])
        self.assertEqual(d["change_kind"], "model")
        self.assertEqual(d["created_rev"], "r1")
        self.assertEqual(d["owner"], "op")
        self.assertIsNone(d["cross_layer_exception"])
        self.assertEqual(d["join_or_aggregate_summary"], "join on id")
        self.assertEqual(d["protected_action_flags"], [])
        self.assertFalse(d["requires_human_approval"])
        self.assertEqual(d["human_approval"]["approved"], False)
        self.assertIsNone(d["human_approval"]["approver"])
        self.assertEqual(d["human_approval"]["rule_ids"], [])


class BuildModelEntryFactoryTests(unittest.TestCase):
    def test_valid_entry_constructed(self) -> None:
        entry = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
        )
        self.assertEqual(entry.name, "dwd_orders")
        self.assertEqual(entry.layer, "dwd")

    def test_bad_alias_name_rejected_port_001(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_model_entry(
                name="Bad Alias!", layer="dwd", change_kind="model",
                created_rev="r1", owner="op",
            )
        self.assertIn("PORT-001", ctx.exception.decision.rule_ids)

    def test_unknown_layer_rejected(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_model_entry(
                name="dwd_orders", layer="bogus", change_kind="model",
                created_rev="r1", owner="op",
            )
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_unknown_change_kind_rejected(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_model_entry(
                name="dwd_orders", layer="dwd", change_kind="bogus",
                created_rev="r1", owner="op",
            )
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_bad_protected_action_flag_rejected_sem_003(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_model_entry(
                name="dwd_orders", layer="dwd", change_kind="model",
                created_rev="r1", owner="op",
                protected_action_flags=("bogus_action",),
            )
        self.assertIn("SEM-003", ctx.exception.decision.rule_ids)

    def test_empty_reason_cross_layer_exception_rejected(self) -> None:
        with self.assertRaises(GateError):
            build_model_entry(
                name="ads_revenue", layer="ads", change_kind="model",
                created_rev="r1", owner="op",
                cross_layer_exception={"reason": "", "approved_by": "op"},
            )

    def test_sanitizes_join_summary_with_absolute_path(self) -> None:
        entry = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
            join_or_aggregate_summary="join on /Users/admin/secret_path",
        )
        self.assertNotIn("/Users/admin", entry.join_or_aggregate_summary)
        self.assertIn("[REDACTED_PATH]", entry.join_or_aggregate_summary)

    def test_name_with_secret_rejected(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_model_entry(
                name="dwd_api_key=secret123", layer="dwd", change_kind="model",
                created_rev="r1", owner="op",
            )
        self.assertIn("PORT-001", ctx.exception.decision.rule_ids)

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(GateError):
            build_model_entry(
                name="", layer="dwd", change_kind="model",
                created_rev="r1", owner="op",
            )

    def test_empty_owner_rejected(self) -> None:
        with self.assertRaises(GateError):
            build_model_entry(
                name="dwd_orders", layer="dwd", change_kind="model",
                created_rev="r1", owner="",
            )

    def test_cross_layer_exception_from_mapping(self) -> None:
        entry = build_model_entry(
            name="ads_revenue", layer="ads", change_kind="model",
            created_rev="r1", owner="op",
            cross_layer_exception={"reason": "legacy skip", "approved_by": "op"},
        )
        self.assertIsNotNone(entry.cross_layer_exception)
        self.assertEqual(entry.cross_layer_exception.reason, "legacy skip")

    def test_human_approval_from_mapping(self) -> None:
        entry = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
            human_approval={"approved": True, "approver": "owner1", "rule_ids": ["SCOPE-001"]},
        )
        self.assertTrue(entry.human_approval.approved)
        self.assertEqual(entry.human_approval.approver, "owner1")


class ReadModelRegistryTests(unittest.TestCase):
    def test_absent_returns_empty_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            entries = read_model_registry(Path(d) / "nonexistent.json")
            self.assertEqual(entries, ())

    def test_parses_valid_registry(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            entry = build_model_entry(
                name="dwd_orders", layer="dwd", change_kind="model",
                created_rev="r1", owner="op",
                upstream_deps=("ods_orders",),
            )
            registry = {"schema_version": 1, "models": [entry.to_dict()]}
            path.write_text(json.dumps(registry), encoding="utf-8")
            entries = read_model_registry(path)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].name, "dwd_orders")
            self.assertEqual(entries[0].upstream_deps, ("ods_orders",))

    def test_malformed_json_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(GateError) as ctx:
                read_model_registry(path)
            self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_wrong_schema_version_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            path.write_text(
                json.dumps({"schema_version": 99, "models": []}),
                encoding="utf-8",
            )
            with self.assertRaises(GateError):
                read_model_registry(path)

    def test_tampered_entry_bad_alias_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            entry_dict = build_model_entry(
                name="dwd_orders", layer="dwd", change_kind="model",
                created_rev="r1", owner="op",
            ).to_dict()
            entry_dict["name"] = "Bad Alias!"
            registry = {"schema_version": 1, "models": [entry_dict]}
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaises(GateError):
                read_model_registry(path)

    def test_tampered_entry_extra_field_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            entry_dict = build_model_entry(
                name="dwd_orders", layer="dwd", change_kind="model",
                created_rev="r1", owner="op",
            ).to_dict()
            entry_dict["extra_field"] = "smuggled"
            registry = {"schema_version": 1, "models": [entry_dict]}
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaises(GateError):
                read_model_registry(path)


class ReadSourceInventoryTests(unittest.TestCase):
    def _sample_inventory(self) -> SourceInventory:
        return SourceInventory(
            source_database="public",
            tables=(
                SourceTable(
                    name="orders",
                    columns=(
                        SourceColumn(name="id", data_type="bigint", is_primary_key=True),
                        SourceColumn(name="amount", data_type="decimal", is_primary_key=False),
                    ),
                ),
            ),
        )

    def test_round_trips_with_to_dict(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source_inventory.json"
            inv = self._sample_inventory()
            path.write_text(json.dumps(inv.to_dict()), encoding="utf-8")
            parsed = read_source_inventory(path)
            self.assertEqual(parsed.source_database, "public")
            self.assertEqual(len(parsed.tables), 1)
            self.assertEqual(parsed.tables[0].name, "orders")
            self.assertEqual(len(parsed.tables[0].columns), 2)
            self.assertTrue(parsed.tables[0].columns[0].is_primary_key)
            # Round-trip: parsed.to_dict() == original.to_dict()
            self.assertEqual(parsed.to_dict(), inv.to_dict())

    def test_absent_raises_gate_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(GateError) as ctx:
                read_source_inventory(Path(d) / "nonexistent.json")
            self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_malformed_raises_gate_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source_inventory.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(GateError):
                read_source_inventory(path)

    def test_wrong_schema_version_raises_gate_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source_inventory.json"
            path.write_text(
                json.dumps({"schema_version": 99, "source_database": "public", "tables": []}),
                encoding="utf-8",
            )
            with self.assertRaises(GateError):
                read_source_inventory(path)


class ValidateBuildPlanTests(unittest.TestCase):
    def test_pass_on_well_ordered_plan(self) -> None:
        plan = _valid_plan()
        validate_build_plan(plan, DEFAULT_LAYER_RULES)
        # No exception raised.

    def test_pass_with_known_models_pre_existing_dep(self) -> None:
        # dwd depends on ods_orders which is NOT in the plan but IS in
        # known_models (pre-existing model in the registry). Open point 6.
        dwd = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
            upstream_deps=("ods_orders",),
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(dwd,),
        )
        validate_build_plan(
            plan, DEFAULT_LAYER_RULES,
            known_models=frozenset({"ods_orders"}),
        )

    def test_raises_topology_out_of_order(self) -> None:
        # dwd appears BEFORE ods, but dwd depends on ods -> topology violation.
        dwd = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
            upstream_deps=("ods_orders",),
        )
        ods = build_model_entry(
            name="ods_orders", layer="ods", change_kind="model",
            created_rev="r1", owner="op",
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(dwd, ods),
        )
        with self.assertRaises(GateError) as ctx:
            validate_build_plan(plan, DEFAULT_LAYER_RULES)
        self.assertIn("DOC-002", ctx.exception.decision.rule_ids)
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_raises_scope_001_cross_plan_boundary(self) -> None:
        # dwd depends on unknown_model which is neither in the plan nor in
        # known_models. Open point 6: SCOPE-001 cross-plan-boundary.
        dwd = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
            upstream_deps=("unknown_model",),
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(dwd,),
        )
        with self.assertRaises(GateError) as ctx:
            validate_build_plan(plan, DEFAULT_LAYER_RULES, known_models=frozenset())
        self.assertIn("SCOPE-001", ctx.exception.decision.rule_ids)
        self.assertIn("build-plan:scope:dwd_orders:unknown_model",
                      ctx.exception.decision.evidence_refs)

    def test_raises_sem_003_protected_flags_without_approval(self) -> None:
        entry = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
            protected_action_flags=("approve_metric",),
            requires_human_approval=False,
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(entry,),
        )
        with self.assertRaises(GateError) as ctx:
            validate_build_plan(plan, DEFAULT_LAYER_RULES)
        self.assertIn("SEM-003", ctx.exception.decision.rule_ids)

    def test_raises_q1_extend_source_unapproved(self) -> None:
        entry = build_model_entry(
            name="ods_new_source", layer="ods", change_kind="model",
            created_rev="r1", owner="op",
            requires_human_approval=True,
            human_approval=HumanApproval(approved=False),
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(entry,),
        )
        with self.assertRaises(GateError) as ctx:
            validate_build_plan(plan, DEFAULT_LAYER_RULES)
        self.assertIn("SCOPE-001", ctx.exception.decision.rule_ids)
        self.assertIn("SEC-001", ctx.exception.decision.rule_ids)
        self.assertIn("RAW-003", ctx.exception.decision.rule_ids)

    def test_pass_q1_extend_source_approved(self) -> None:
        entry = build_model_entry(
            name="ods_new_source", layer="ods", change_kind="model",
            created_rev="r1", owner="op",
            requires_human_approval=True,
            human_approval=HumanApproval(approved=True, approver="op"),
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(entry,),
        )
        validate_build_plan(plan, DEFAULT_LAYER_RULES)


class ValidateLayerDependencyTests(unittest.TestCase):
    def test_pass_on_well_ordered_plan(self) -> None:
        plan = _valid_plan()
        validate_layer_dependency(plan, DEFAULT_LAYER_RULES)

    def test_raises_ads_reads_dwd_directly(self) -> None:
        dwd = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
        )
        ads = build_model_entry(
            name="ads_revenue", layer="ads", change_kind="model",
            created_rev="r1", owner="op",
            upstream_deps=("dwd_orders",),
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(dwd, ads),
        )
        with self.assertRaises(GateError) as ctx:
            validate_layer_dependency(plan, DEFAULT_LAYER_RULES)
        self.assertIn("DOC-002", ctx.exception.decision.rule_ids)

    def test_raises_dws_reads_ods_directly(self) -> None:
        ods = build_model_entry(
            name="ods_orders", layer="ods", change_kind="model",
            created_rev="r1", owner="op",
        )
        dws = build_model_entry(
            name="dws_summary", layer="dws", change_kind="model",
            created_rev="r1", owner="op",
            upstream_deps=("ods_orders",),
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(ods, dws),
        )
        with self.assertRaises(GateError):
            validate_layer_dependency(plan, DEFAULT_LAYER_RULES)

    def test_raises_dwd_reads_dws_reverse(self) -> None:
        dws = build_model_entry(
            name="dws_summary", layer="dws", change_kind="model",
            created_rev="r1", owner="op",
        )
        dwd = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
            upstream_deps=("dws_summary",),
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(dws, dwd),
        )
        with self.assertRaises(GateError):
            validate_layer_dependency(plan, DEFAULT_LAYER_RULES)

    def test_cross_layer_exception_does_not_raise(self) -> None:
        dwd = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
        )
        ads = build_model_entry(
            name="ads_revenue", layer="ads", change_kind="model",
            created_rev="r1", owner="op",
            upstream_deps=("dwd_orders",),
            cross_layer_exception=CrossLayerException(
                reason="legacy ADS reads DWD directly", approved_by="op",
            ),
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(dwd, ads),
        )
        validate_layer_dependency(plan, DEFAULT_LAYER_RULES)
        # No exception raised.

    def test_pre_existing_dep_skipped(self) -> None:
        # dwd depends on ods_preexisting which is not in the plan (pre-existing
        # model in known_models, validated by validate_build_plan). The layer
        # check skips it (layer unknown, already validated when built).
        dwd = build_model_entry(
            name="dwd_orders", layer="dwd", change_kind="model",
            created_rev="r1", owner="op",
            upstream_deps=("ods_preexisting",),
        )
        plan = BuildPlan(
            schema_version=1, session_id="sess-1", models=(dwd,),
        )
        validate_layer_dependency(plan, DEFAULT_LAYER_RULES)
        # No exception raised.


class AppendModelRegistryTests(unittest.TestCase):
    def _entry(self, name: str = "dwd_orders", rev: str = "r1") -> ModelEntry:
        return build_model_entry(
            name=name, layer="dwd", change_kind="model",
            created_rev=rev, owner="op",
        )

    def test_creates_file_if_absent(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            entry = self._entry()
            result = append_model_registry(path, entry)
            self.assertEqual(result, path)
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(len(data["models"]), 1)
            self.assertEqual(data["models"][0]["name"], "dwd_orders")

    def test_appends_without_duplicating_same_name_and_rev(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            entry = self._entry()
            append_model_registry(path, entry)
            append_model_registry(path, entry)  # idempotent
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["models"]), 1)

    def test_rebuild_at_new_rev_appends_second_entry(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            append_model_registry(path, self._entry(rev="r1"))
            append_model_registry(path, self._entry(rev="r2"))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["models"]), 2)

    def test_atomic_write_no_tmp_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            append_model_registry(path, self._entry())
            # No .tmp file should remain after a successful write.
            tmp = path.with_suffix(path.suffix + ".tmp")
            self.assertFalse(tmp.exists())

    def test_tmp_cleaned_up_on_mid_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            entry = self._entry()
            # Simulate a mid-write failure: os.replace raises.
            with patch("chatbi_harness.build_plan.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    append_model_registry(path, entry)
            # The .tmp file should have been cleaned up.
            tmp = path.with_suffix(path.suffix + ".tmp")
            self.assertFalse(tmp.exists())

    def test_does_not_mutate_entry(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            entry = self._entry()
            original_dict = entry.to_dict()
            append_model_registry(path, entry)
            self.assertEqual(entry.to_dict(), original_dict)

    def test_file_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            append_model_registry(path, self._entry())
            mode = os.stat(path).st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_appends_to_existing_registry(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "model_registry.json"
            first = build_model_entry(
                name="ods_orders", layer="ods", change_kind="model",
                created_rev="r1", owner="op",
            )
            append_model_registry(path, first)
            second = self._entry(name="dwd_orders")
            append_model_registry(path, second)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["models"]), 2)
            self.assertEqual(data["models"][0]["name"], "ods_orders")
            self.assertEqual(data["models"][1]["name"], "dwd_orders")


class CollectKnownModelsTests(unittest.TestCase):
    """collect_known_models = registry names ∪ on-disk models/*.sql stems.

    The registry may lag actual models (built before the registry feature);
    the directory scan closes that gap. Used as ``known_models`` for
    validate_build_plan (SCOPE-001 cross-plan-boundary check).
    """

    def test_both_absent_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(collect_known_models(Path(d)), frozenset())

    def test_registry_absent_models_present_returns_stems(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "models" / "ods").mkdir(parents=True)
            (ws / "models" / "ods" / "ods_agent_session.sql").write_text("SELECT 1")
            (ws / "models" / "dwd").mkdir(parents=True)
            (ws / "models" / "dwd" / "dwd_session_creator_detail.sql").write_text("SELECT 1")
            # non-.sql files (schema.yml) are ignored
            (ws / "models" / "ods" / "schema.yml").write_text("version: 2")
            self.assertEqual(
                collect_known_models(ws),
                frozenset({"ods_agent_session", "dwd_session_creator_detail"}),
            )

    def test_registry_and_models_union_with_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "models" / "ods").mkdir(parents=True)
            (ws / "models" / "ods" / "ods_agent_session.sql").write_text("SELECT 1")
            # registry has one model on disk (dedup) + one NOT on disk (union adds)
            on_disk = build_model_entry(
                name="ods_agent_session", layer="ods", change_kind="model",
                created_rev="r1", owner="op",
            )
            not_on_disk = build_model_entry(
                name="dws_function_usage_daily", layer="dws", change_kind="model",
                created_rev="r1", owner="op",
            )
            reg = ws / ".chatbi" / "model_registry.json"
            append_model_registry(reg, on_disk)
            append_model_registry(reg, not_on_disk)
            self.assertEqual(
                collect_known_models(ws),
                frozenset({"ods_agent_session", "dws_function_usage_daily"}),
            )

    def test_registry_present_models_absent_returns_registry_names(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            entry = build_model_entry(
                name="dwd_session_creator_detail", layer="dwd", change_kind="model",
                created_rev="r1", owner="op",
            )
            append_model_registry(ws / ".chatbi" / "model_registry.json", entry)
            self.assertEqual(
                collect_known_models(ws),
                frozenset({"dwd_session_creator_detail"}),
            )

    def test_malformed_registry_raises_gate_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            reg = ws / ".chatbi" / "model_registry.json"
            reg.parent.mkdir(parents=True)
            reg.write_text("{not json")
            with self.assertRaises(GateError):
                collect_known_models(ws)


if __name__ == "__main__":
    unittest.main()
