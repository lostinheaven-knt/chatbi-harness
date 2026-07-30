from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / "harness" / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.bootstrap import (  # noqa: E402
    SourceColumn,
    SourceInventory,
    SourceTable,
    build_mysql_adapter_spec,
    merge_local_config,
    merge_source_inventories,
    read_source_inventory,
)
from chatbi_harness.config import load_effective_config  # noqa: E402
from chatbi_harness.gates import GateError  # noqa: E402


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def install_domain_contract(workspace: Path) -> None:
    for relative in (
        "CLAUDE.md",
        "CONTEXT.md",
        "docs/chatbi-harness-domain-model.md",
        ".claude/rules/00-domain-contract.md",
        ".claude/rules/10-security.md",
        ".claude/rules/20-completion.md",
    ):
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WORKSPACE_ROOT / "harness" / relative, destination)


def write_ready_shared_config(workspace: Path) -> Path:
    """Mirror test_diagnostics.write_ready_config but keep adapters.query empty.

    bootstrap appends ``cli:mysql`` to ``adapters.query`` at runtime; the
    round-trip test only needs a schema-valid shared config so
    ``load_effective_config`` can validate the local ``cli_adapters.mysql`` spec.
    """
    config = json.loads(
        (WORKSPACE_ROOT / "harness" / ".claude" / "chatbi-harness.json").read_text(
            encoding="utf-8"
        )
    )
    config["adapters"] = {
        "semantic": ["managed:semantic"],
        "query": [],
        "fixture_enabled": False,
    }
    config["governance"].update(
        {
            "pii_policy_ref": "synthetic-pii-policy",
            "restricted_disclosure": "sql_only",
            "owners": {
                "default_domain_owner": "role:synthetic-domain-owner",
                "metrics": {},
            },
        }
    )
    config["evaluation"].update(
        {
            "release_threshold": 0.9,
            "threshold_owner": "role:synthetic-evaluation-owner",
        }
    )
    shared_config = workspace / ".claude" / "chatbi-harness.json"
    shared_config.write_text(json.dumps(config), encoding="utf-8")
    return shared_config


class BuildMysqlAdapterSpecTests(unittest.TestCase):
    def test_correct_argv_shape_and_credential_env_name(self) -> None:
        spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="public",
            credential_env_name="MYSQL_PWD",
        )
        self.assertEqual(
            spec["argv"],
            [
                "mysql",
                "--host", "127.0.0.1",
                "--port", "3306",
                "--user", "root",
                "--database=public",
            ],
        )
        self.assertEqual(spec["credential_env_names"], ["MYSQL_PWD"])
        # Only the two schema-permitted keys.
        self.assertEqual(set(spec), {"argv", "credential_env_names"})

    def test_credential_env_name_none_yields_empty_list(self) -> None:
        spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="public",
        )
        self.assertEqual(spec["credential_env_names"], [])
        # argv still contains the base connection elements.
        self.assertEqual(spec["argv"][0], "mysql")
        self.assertIn("--database=public", spec["argv"])

    def test_no_password_value_anywhere_in_spec(self) -> None:
        spec = build_mysql_adapter_spec(
            "db.example.internal", 3306, "warehouse_agent",
            database="public", credential_env_name="MYSQL_PWD",
        )
        serialized = json.dumps(spec)
        # No secret-value pattern (api_key=..., token=..., password=..., sk-...).
        self.assertNotIn("password=", serialized.lower())
        self.assertNotIn("secret=", serialized.lower())
        self.assertNotIn("token=", serialized.lower())
        self.assertNotIn("api_key=", serialized.lower())
        self.assertNotIn("sk-", serialized.lower())
        # No argv element matches the sensitive-flag pattern.
        from chatbi_harness.config import _SECRET_ARG
        for element in spec["argv"]:
            self.assertIsNone(_SECRET_ARG.match(element))
        # Credential is carried as a NAME only, never a value.
        self.assertEqual(spec["credential_env_names"], ["MYSQL_PWD"])

    def test_empty_host_raises_gate_error_with_hook_004(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_mysql_adapter_spec("", 3306, "root", database="public")
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)
        self.assertEqual("block", ctx.exception.decision.status)

    def test_empty_user_raises_gate_error(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_mysql_adapter_spec("127.0.0.1", 3306, "", database="public")
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_empty_database_raises_gate_error(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_mysql_adapter_spec("127.0.0.1", 3306, "root", database="")
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_port_below_one_raises_gate_error(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_mysql_adapter_spec("127.0.0.1", 0, "root", database="public")
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_port_above_65535_raises_gate_error(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_mysql_adapter_spec("127.0.0.1", 65536, "root", database="public")
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_non_integer_port_raises_gate_error(self) -> None:
        with self.assertRaises(GateError) as ctx:
            build_mysql_adapter_spec(
                "127.0.0.1", "3306", "root", database="public",  # type: ignore[arg-type]
            )
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_bool_port_raises_gate_error(self) -> None:
        # bool is a subclass of int; the spec rejects it explicitly.
        with self.assertRaises(GateError) as ctx:
            build_mysql_adapter_spec(
                "127.0.0.1", True, "root", database="public",  # type: ignore[arg-type]
            )
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_bad_credential_env_name_lowercase_raises_gate_error_with_sec_003(
        self,
    ) -> None:
        with self.assertRaises(GateError) as ctx:
            build_mysql_adapter_spec(
                "127.0.0.1", 3306, "root", database="public",
                credential_env_name="mysql_pwd",
            )
        self.assertIn("SEC-003", ctx.exception.decision.rule_ids)
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_bad_credential_env_name_dash_raises_gate_error(self) -> None:
        with self.assertRaises(GateError):
            build_mysql_adapter_spec(
                "127.0.0.1", 3306, "root", database="public",
                credential_env_name="MYSQL-PWD",
            )

    def test_bad_credential_env_name_leading_digit_raises_gate_error(self) -> None:
        with self.assertRaises(GateError):
            build_mysql_adapter_spec(
                "127.0.0.1", 3306, "root", database="public",
                credential_env_name="1PWD",
            )

    def test_non_string_host_raises_gate_error(self) -> None:
        with self.assertRaises(GateError):
            build_mysql_adapter_spec(
                127001, 3306, "root", database="public",  # type: ignore[arg-type]
            )


class MergeLocalConfigTests(unittest.TestCase):
    def test_preserves_existing_adapters_and_bindings(self) -> None:
        spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="public",
        )
        existing = {
            "path_bindings": {"billing_root": "/abs/billing"},
            "cli_adapters": {"semantic": {"argv": ["x"], "credential_env_names": []}},
        }
        merged = merge_local_config(existing, cli_adapters={"mysql": spec})
        # Existing binding preserved.
        self.assertEqual(merged["path_bindings"]["billing_root"], "/abs/billing")
        # Existing semantic adapter preserved.
        self.assertIn("semantic", merged["cli_adapters"])
        # New mysql adapter added.
        self.assertIn("mysql", merged["cli_adapters"])
        self.assertEqual(merged["cli_adapters"]["mysql"], spec)

    def test_adds_new_bindings_and_adapters_to_empty_existing(self) -> None:
        spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="public",
        )
        merged = merge_local_config(
            {},
            path_bindings={"events_root": "/abs/events"},
            cli_adapters={"mysql": spec},
        )
        self.assertEqual(merged["path_bindings"], {"events_root": "/abs/events"})
        self.assertEqual(merged["cli_adapters"], {"mysql": spec})

    def test_does_not_clobber_unrelated_adapters(self) -> None:
        first_spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="public",
        )
        second_spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="analytics",
        )
        existing = {"cli_adapters": {"mysql": first_spec}}
        # Supplying a different adapter key does not touch mysql.
        merged = merge_local_config(
            existing, cli_adapters={"postgres": second_spec}
        )
        self.assertEqual(merged["cli_adapters"]["mysql"], first_spec)
        self.assertEqual(merged["cli_adapters"]["postgres"], second_spec)

    def test_overwrites_only_supplied_adapter(self) -> None:
        old_spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="public",
        )
        new_spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="analytics",
        )
        existing = {"cli_adapters": {"mysql": old_spec}}
        merged = merge_local_config(existing, cli_adapters={"mysql": new_spec})
        self.assertEqual(merged["cli_adapters"]["mysql"], new_spec)

    def test_drops_non_local_top_level_keys(self) -> None:
        spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="public",
        )
        existing = {
            "path_bindings": {"events_root": "/abs/events"},
            "cli_adapters": {"mysql": spec},
            # Smuggled shared/protected policy - must not survive the merge.
            "governance": {"owners": {"default_domain_owner": "attacker"}},
            "adapters": {"query": ["fixture:query"]},
        }
        merged = merge_local_config(existing)
        self.assertEqual(set(merged), {"path_bindings", "cli_adapters"})
        self.assertNotIn("governance", merged)
        self.assertNotIn("adapters", merged)

    def test_does_not_mutate_existing(self) -> None:
        spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="public",
        )
        existing = {
            "path_bindings": {"events_root": "/abs/events"},
            "cli_adapters": {"semantic": {"argv": ["x"], "credential_env_names": []}},
        }
        existing_snapshot = json.loads(json.dumps(existing))
        merge_local_config(existing, cli_adapters={"mysql": spec})
        self.assertEqual(existing, existing_snapshot)

    def test_none_existing_treated_as_empty(self) -> None:
        spec = build_mysql_adapter_spec(
            "127.0.0.1", 3306, "root", database="public",
        )
        merged = merge_local_config(None, cli_adapters={"mysql": spec})
        self.assertEqual(merged["cli_adapters"], {"mysql": spec})
        self.assertEqual(merged["path_bindings"], {})

    def test_none_supplied_sections_yield_empty_dicts(self) -> None:
        # If existing is None and nothing is supplied, result has empty
        # path_bindings + cli_adapters (the two permitted local keys).
        merged = merge_local_config(None)
        self.assertEqual(merged, {"path_bindings": {}, "cli_adapters": {}})


class SpecRoundTripsLoadEffectiveConfigTests(unittest.TestCase):
    def test_spec_round_trips_through_load_effective_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            shared_path = write_ready_shared_config(workspace)

            spec = build_mysql_adapter_spec(
                "127.0.0.1", 3306, "root", database="public",
                credential_env_name="MYSQL_PWD",
            )
            local_data = merge_local_config(
                {}, cli_adapters={"mysql": spec}
            )
            local_path = workspace / ".claude" / "chatbi-harness.local.json"
            local_path.write_text(
                json.dumps(local_data, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )

            with working_directory(workspace):
                config = load_effective_config(
                    Path(".claude/chatbi-harness.json"),
                    Path(".claude/chatbi-harness.local.json"),
                )

            mysql = config["cli_adapters"]["mysql"]
            self.assertEqual(
                list(mysql["argv"]),
                [
                    "mysql",
                    "--host", "127.0.0.1",
                    "--port", "3306",
                    "--user", "root",
                    "--database=public",
                ],
            )
            self.assertEqual(list(mysql["credential_env_names"]), ["MYSQL_PWD"])

    def test_local_no_password_root_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            shared_path = write_ready_shared_config(workspace)

            spec = build_mysql_adapter_spec(
                "127.0.0.1", 3306, "root", database="public",
            )
            local_data = merge_local_config(
                {}, cli_adapters={"mysql": spec}
            )
            local_path = workspace / ".claude" / "chatbi-harness.local.json"
            local_path.write_text(json.dumps(local_data), encoding="utf-8")

            with working_directory(workspace):
                config = load_effective_config(
                    Path(".claude/chatbi-harness.json"),
                    Path(".claude/chatbi-harness.local.json"),
                )

            self.assertEqual(
                list(config["cli_adapters"]["mysql"]["credential_env_names"]),
                [],
            )


class SecretRejectionTests(unittest.TestCase):
    def test_password_flag_in_argv_raises_gate_error_with_sec_003(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            shared_path = write_ready_shared_config(workspace)

            local_data = {
                "path_bindings": {},
                "cli_adapters": {
                    "mysql": {
                        "argv": [
                            "mysql",
                            "--host", "127.0.0.1",
                            "--port", "3306",
                            "--user", "root",
                            "--password=secret-value",
                        ],
                        "credential_env_names": [],
                    }
                },
            }
            local_path = workspace / ".claude" / "chatbi-harness.local.json"
            local_path.write_text(json.dumps(local_data), encoding="utf-8")

            with working_directory(workspace):
                with self.assertRaises(GateError) as ctx:
                    load_effective_config(
                        Path(".claude/chatbi-harness.json"),
                        Path(".claude/chatbi-harness.local.json"),
                    )
            self.assertIn("SEC-003", ctx.exception.decision.rule_ids)
            self.assertEqual("block", ctx.exception.decision.status)

    def test_secret_value_in_local_config_raises_gate_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            install_domain_contract(workspace)
            shared_path = write_ready_shared_config(workspace)

            # The secret-value scan (config.py:418-426) is recursive over the
            # whole local config; placing a leaked secret inside a PERMITTED
            # top-level key (path_bindings) ensures the SEC-003 secret scan
            # fires rather than the unknown-local-key check (SEM-003) which
            # runs first for smuggled top-level keys.
            local_data = {
                "path_bindings": {"events_root": "sk-leakedsecretvalue1234"},
                "cli_adapters": {
                    "mysql": {
                        "argv": ["mysql", "--host", "127.0.0.1"],
                        "credential_env_names": [],
                    }
                },
            }
            local_path = workspace / ".claude" / "chatbi-harness.local.json"
            local_path.write_text(json.dumps(local_data), encoding="utf-8")

            with working_directory(workspace):
                with self.assertRaises(GateError) as ctx:
                    load_effective_config(
                        Path(".claude/chatbi-harness.json"),
                        Path(".claude/chatbi-harness.local.json"),
                    )
            self.assertIn("SEC-003", ctx.exception.decision.rule_ids)


class SourceInventoryTests(unittest.TestCase):
    def test_to_dict_shape_is_self_describing_and_versioned(self) -> None:
        inventory = SourceInventory(
            source_database="public",
            tables=(
                SourceTable(
                    name="orders",
                    columns=(
                        SourceColumn(
                            name="id",
                            data_type="bigint",
                            is_primary_key=True,
                        ),
                        SourceColumn(
                            name="amount",
                            data_type="decimal(18,2)",
                            is_primary_key=False,
                        ),
                    ),
                ),
                SourceTable(
                    name="customers",
                    columns=(
                        SourceColumn(
                            name="customer_id",
                            data_type="int",
                            is_primary_key=True,
                        ),
                    ),
                ),
            ),
        )
        rendered = inventory.to_dict()
        self.assertEqual(rendered["schema_version"], 1)
        self.assertEqual(rendered["source_database"], "public")
        self.assertEqual(len(rendered["tables"]), 2)
        first_table = rendered["tables"][0]
        self.assertEqual(first_table["name"], "orders")
        self.assertEqual(len(first_table["columns"]), 2)
        self.assertEqual(
            first_table["columns"][0],
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
        )
        self.assertEqual(
            first_table["columns"][1],
            {"name": "amount", "data_type": "decimal(18,2)", "is_primary_key": False},
        )

    def test_to_dict_is_json_serializable_without_nan(self) -> None:
        inventory = SourceInventory(
            source_database="public",
            tables=(
                SourceTable(
                    name="orders",
                    columns=(
                        SourceColumn(
                            name="id", data_type="bigint", is_primary_key=True
                        ),
                    ),
                ),
            ),
        )
        # allow_nan=False mirrors EffectiveConfig.to_json / DiagnosticResult.to_json.
        rendered = json.dumps(
            inventory.to_dict(), ensure_ascii=False, allow_nan=False,
        )
        self.assertIn("orders", rendered)
        self.assertIn("schema_version", rendered)

    def test_source_inventory_is_frozen(self) -> None:
        inventory = SourceInventory(
            source_database="public",
            tables=(),
        )
        with self.assertRaises(Exception):
            inventory.source_database = "other"  # type: ignore[misc]

    def test_empty_inventory_round_trips(self) -> None:
        inventory = SourceInventory(source_database="public", tables=())
        rendered = inventory.to_dict()
        self.assertEqual(rendered["tables"], [])
        self.assertEqual(rendered["source_database"], "public")
        json.dumps(rendered, allow_nan=False)


class ReadSourceInventoryTests(unittest.TestCase):
    def _sample_inventory_dict(self) -> dict:
        return {
            "schema_version": 1,
            "source_database": "public",
            "tables": [
                {
                    "name": "orders",
                    "columns": [
                        {"name": "id", "data_type": "bigint", "is_primary_key": True},
                        {"name": "amount", "data_type": "decimal", "is_primary_key": False},
                    ],
                },
                {
                    "name": "customers",
                    "columns": [
                        {"name": "cid", "data_type": "int", "is_primary_key": True},
                    ],
                },
            ],
        }

    def test_round_trips_with_to_dict(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source_inventory.json"
            path.write_text(json.dumps(self._sample_inventory_dict()), encoding="utf-8")
            inv = read_source_inventory(path)
            self.assertEqual(inv.source_database, "public")
            self.assertEqual(len(inv.tables), 2)
            self.assertEqual(inv.tables[0].name, "orders")
            self.assertEqual(len(inv.tables[0].columns), 2)
            self.assertTrue(inv.tables[0].columns[0].is_primary_key)
            # Round-trip: read -> to_dict == original dict
            self.assertEqual(inv.to_dict(), self._sample_inventory_dict())

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
            data = self._sample_inventory_dict()
            data["schema_version"] = 99
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(GateError):
                read_source_inventory(path)


class MergeSourceInventoriesTests(unittest.TestCase):
    def _table(self, name: str) -> SourceTable:
        return SourceTable(
            name=name,
            columns=(SourceColumn(name="id", data_type="int", is_primary_key=True),),
        )

    def _inventory(self, tables: tuple[SourceTable, ...]) -> SourceInventory:
        return SourceInventory(source_database="public", tables=tables)

    def test_union_by_name(self) -> None:
        base = self._inventory((self._table("orders"),))
        extra = self._inventory((self._table("customers"),))
        merged = merge_source_inventories(base, extra)
        self.assertEqual(len(merged.tables), 2)
        self.assertEqual(merged.tables[0].name, "orders")
        self.assertEqual(merged.tables[1].name, "customers")

    def test_collision_raises_gate_error(self) -> None:
        base = self._inventory((self._table("orders"),))
        extra = self._inventory((self._table("orders"),))
        with self.assertRaises(GateError) as ctx:
            merge_source_inventories(base, extra)
        self.assertIn("HOOK-004", ctx.exception.decision.rule_ids)

    def test_result_is_new_frozen_inventory(self) -> None:
        base = self._inventory((self._table("orders"),))
        extra = self._inventory((self._table("customers"),))
        merged = merge_source_inventories(base, extra)
        # Result is a new frozen SourceInventory (not base or extra).
        self.assertIsNot(merged, base)
        self.assertIsNot(merged, extra)
        # Inputs not mutated.
        self.assertEqual(len(base.tables), 1)
        self.assertEqual(len(extra.tables), 1)
        # Result has both.
        self.assertEqual(len(merged.tables), 2)

    def test_source_database_from_base(self) -> None:
        base = SourceInventory(source_database="production", tables=(self._table("orders"),))
        extra = SourceInventory(source_database="staging", tables=(self._table("customers"),))
        merged = merge_source_inventories(base, extra)
        self.assertEqual(merged.source_database, "production")

    def test_schema_version_stays_one(self) -> None:
        base = self._inventory((self._table("orders"),))
        extra = self._inventory((self._table("customers"),))
        merged = merge_source_inventories(base, extra)
        self.assertEqual(merged.to_dict()["schema_version"], 1)

    def test_empty_extra_returns_base_tables(self) -> None:
        base = self._inventory((self._table("orders"),))
        extra = self._inventory(())
        merged = merge_source_inventories(base, extra)
        self.assertEqual(len(merged.tables), 1)
        self.assertEqual(merged.tables[0].name, "orders")

    def test_incremental_path_round_trip(self) -> None:
        """Simulate the incremental introspect: read base -> merge extra -> rewrite."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source_inventory.json"
            # Write base inventory (from original bootstrap).
            base = self._inventory((self._table("orders"),))
            path.write_text(json.dumps(base.to_dict()), encoding="utf-8")
            # Simulate incremental introspect of newly-approved tables.
            extra = self._inventory((self._table("events"), self._table("users")))
            base_read = read_source_inventory(path)
            merged = merge_source_inventories(base_read, extra)
            # Rewrite.
            path.write_text(json.dumps(merged.to_dict()), encoding="utf-8")
            # Verify round-trip.
            final = read_source_inventory(path)
            self.assertEqual(len(final.tables), 3)
            names = {t.name for t in final.tables}
            self.assertEqual(names, {"orders", "events", "users"})


if __name__ == "__main__":
    unittest.main()
