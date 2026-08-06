"""Deterministic primitives for the ``/chatbi-bootstrap`` command (MySQL-only v1).

This module is a thin layer over the existing :mod:`chatbi_governance.config` and
:mod:`chatbi_governance.gates` primitives. It does NOT import ``adapters`` -
adapter construction (``CliAdapter``, ``select_adapter``, ``resolve_executable``,
``build_cli_env``, ``validate_cli_argv``) is a runbook concern, not a lib
concern. It does NOT duplicate secret/argv validation: the spec round-trips
through :func:`chatbi_governance.config.load_effective_config` (which runs
``_contains_secret_argv`` + ``_contains_matching_string`` + the schema) and the
``CliAdapter`` constructor re-runs ``validate_cli_argv`` at construction time.

Public surface (technical-design-bootstrap.md §3):

- :func:`build_mysql_adapter_spec` - builds the ``cli_adapters.mysql`` spec
  (``argv`` + ``credential_env_names``). Never includes a password value.
- :func:`merge_local_config` - merges ``path_bindings`` / ``cli_adapters`` into
  an existing local config, preserving unrelated keys and dropping smuggled
  shared/protected policy.
- :class:`SourceInventory` (plus nested :class:`SourceTable` /
  :class:`SourceColumn`) - frozen-slots dataclass capturing the source schema
  inventory written to ``.chatbi/bootstrap/source_inventory.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gates import GateDecision, GateError


# Same regex as adapters._CREDENTIAL_NAME (adapters/__init__.py:86) and the
# schema's credential_env_names pattern (chatbi-harness.schema.json:178).
# Re-declared locally because bootstrap.py does not import adapters (the
# adapter layer is a runbook concern, not a lib concern).
_CREDENTIAL_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# The two top-level keys load_effective_config permits in local config
# (config.py:410-417). Any other key is rejected as a smuggled shared/protected
# policy override (SEM-003/HOOK-004).
_LOCAL_PERMITTED_KEYS = ("path_bindings", "cli_adapters")


def _bootstrap_gate_error(
    *,
    rule_ids: tuple[str, ...],
    evidence_ref: str,
    reason: str,
    recovery: str,
) -> GateError:
    """Build a fail-closed ``GateError`` mirroring ``config._config_gate_error``.

    The decision is sanitized by ``GateDecision.__post_init__``
    (``gates.py:62-72``); no secret/absolute-path value can leak through
    ``evidence_ref`` / ``reason`` / ``recovery``.
    """
    return GateError(
        GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=(evidence_ref,),
            reason=reason,
            recovery=recovery,
        )
    )


def build_mysql_adapter_spec(
    host: str,
    port: int,
    user: str,
    *,
    database: str,
    credential_env_name: str | None = None,
) -> dict:
    """Build the ``cli_adapters.mysql`` spec (``argv`` + ``credential_env_names``).

    Returns ``{"argv": [...], "credential_env_names": [...]}``. Never includes a
    password value: the password is carried only as an env var NAME in
    ``credential_env_names`` (SEC-003). Raises :class:`GateError` on any
    validation violation (HOOK-004); the runbook surfaces the sanitized
    decision and stops without retrying.

    The returned ``argv[0]`` is the bare name ``"mysql"``; the runbook resolves
    it to an allowlisted absolute path later via
    :func:`chatbi_governance.adapters.resolve_executable`.
    """
    # host: non-empty str.
    if not isinstance(host, str) or not host:
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:mysql-spec:host",
            reason="MySQL host must be a non-empty string",
            recovery="Provide a non-empty MySQL host",
        )
    # port: int (not bool), 1 <= port <= 65535. bool is a subclass of int and
    # is rejected explicitly to avoid silent truthy coercion.
    if not isinstance(port, int) or isinstance(port, bool):
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:mysql-spec:port",
            reason="MySQL port must be an integer",
            recovery="Provide an integer MySQL port between 1 and 65535",
        )
    if port < 1 or port > 65535:
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:mysql-spec:port",
            reason=f"MySQL port out of range: {port}",
            recovery="Provide an integer MySQL port between 1 and 65535",
        )
    # user: non-empty str.
    if not isinstance(user, str) or not user:
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:mysql-spec:user",
            reason="MySQL user must be a non-empty string",
            recovery="Provide a non-empty MySQL user",
        )
    # database: non-empty str.
    if not isinstance(database, str) or not database:
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:mysql-spec:database",
            reason="MySQL source database must be a non-empty string",
            recovery="Provide a non-empty MySQL source database name",
        )
    # credential_env_name: if not None, must match ^[A-Z_][A-Z0-9_]*$ (same
    # regex as adapters._CREDENTIAL_NAME and the schema). A mismatch is a
    # secret-handling violation (SEC-003) because an invalid name cannot be
    # safely sourced from the process environment.
    if credential_env_name is not None:
        if not isinstance(credential_env_name, str) or not _CREDENTIAL_NAME.fullmatch(
            credential_env_name
        ):
            raise _bootstrap_gate_error(
                rule_ids=("SEC-003", "HOOK-004"),
                evidence_ref="bootstrap:mysql-spec:credential-name",
                reason=(
                    "credential_env_name must match ^[A-Z_][A-Z0-9_]*$ or be "
                    "omitted for local no-password root"
                ),
                recovery=(
                    "Use an uppercase env var NAME (e.g. MYSQL_PWD) or omit "
                    "credential_env_name for local no-password root"
                ),
            )
    argv = [
        "mysql",
        "--host", host,
        "--port", str(port),
        "--user", user,
        f"--database={database}",
    ]
    credential_env_names = (
        [credential_env_name] if credential_env_name is not None else []
    )
    return {"argv": argv, "credential_env_names": credential_env_names}


def merge_local_config(
    existing: dict | None,
    *,
    path_bindings: dict | None = None,
    cli_adapters: dict | None = None,
) -> dict:
    """Merge ``path_bindings`` / ``cli_adapters`` into an existing local config.

    Preserves existing keys; only adds/overwrites the supplied entries. Returns
    a new dict (does not mutate ``existing``). The result is limited to
    ``path_bindings`` + ``cli_adapters`` - the two keys
    :func:`chatbi_governance.config.load_effective_config` permits in local config
    (``config.py:410-417``). Any other top-level key in ``existing`` is dropped
    (local config may not override shared/protected policy - SEM-003/HOOK-004).
    """
    # Start from empty permitted sections; only copy the two permitted keys from
    # existing. This drops smuggled shared/protected policy (governance,
    # adapters, workspace, ...) and avoids mutating the input.
    if existing is None:
        result_path_bindings: dict = {}
        result_cli_adapters: dict = {}
    else:
        existing_bindings = existing.get("path_bindings")
        result_path_bindings = (
            dict(existing_bindings) if isinstance(existing_bindings, dict) else {}
        )
        existing_adapters = existing.get("cli_adapters")
        result_cli_adapters = (
            dict(existing_adapters) if isinstance(existing_adapters, dict) else {}
        )
    if path_bindings:
        result_path_bindings.update(path_bindings)
    if cli_adapters:
        result_cli_adapters.update(cli_adapters)
    return {
        "path_bindings": result_path_bindings,
        "cli_adapters": result_cli_adapters,
    }


@dataclass(frozen=True, slots=True)
class SourceColumn:
    """One column in a source schema inventory table."""

    name: str
    data_type: str
    is_primary_key: bool


@dataclass(frozen=True, slots=True)
class SourceTable:
    """One table in a source schema inventory."""

    name: str
    columns: tuple[SourceColumn, ...]


@dataclass(frozen=True, slots=True)
class SourceInventory:
    """Source schema inventory handed off to ``/chatbi-maintain-model``.

    Captures the four aspects required by the plan: tables (``SourceTable.name``),
    columns (``SourceColumn.name``), PKs (``SourceColumn.is_primary_key``), and
    types (``SourceColumn.data_type``). The ``to_dict()`` shape is written to
    ``.chatbi/bootstrap/source_inventory.json`` and is the contract surface to
    ``/chatbi-maintain-model``; the ``schema_version: 1`` field makes the shape
    self-describing for forward compatibility.
    """

    source_database: str
    tables: tuple[SourceTable, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "source_database": self.source_database,
            "tables": [
                {
                    "name": table.name,
                    "columns": [
                        {
                            "name": column.name,
                            "data_type": column.data_type,
                            "is_primary_key": column.is_primary_key,
                        }
                        for column in table.columns
                    ],
                }
                for table in self.tables
            ],
        }


def read_source_inventory(path: Path) -> SourceInventory:
    """Inverse of :meth:`SourceInventory.to_dict`.

    Parse ``.chatbi/bootstrap/source_inventory.json`` into a
    :class:`SourceInventory` / :class:`SourceTable` / :class:`SourceColumn`.
    Absent file -> :class:`GateError` (HOOK-004): the build-from-requirement
    flow requires a bootstrapped Workspace; an absent inventory is a missing
    prerequisite, not an empty registry. Malformed JSON / ``schema_version !=
    1`` / unknown column shape -> :class:`GateError` (fail-closed on tampered
    evidence).

    The absent-policy asymmetry vs :func:`chatbi_governance.build_plan.
    read_model_registry` is intentional: the registry starts empty (first
    build legitimately has no models); the source inventory is a bootstrap
    prerequisite - its absence means bootstrap has not run, which is a hard
    STOP for build-from-requirement Step 1. Both are fail-closed on
    malformed/tampered; they differ only on absent.
    """
    if not isinstance(path, Path):
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:source-inventory:path",
            reason="source_inventory path must be a Path",
            recovery="Provide a Path to .chatbi/bootstrap/source_inventory.json",
        )
    if not path.is_file():
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:source-inventory:absent",
            reason="source_inventory.json is absent; bootstrap has not run",
            recovery="Run /chatbi-bootstrap to introspect the source schema first",
        )
    try:
        raw = path.read_bytes()
        if len(raw) > 256 * 1024:
            raise ValueError("source_inventory.json exceeds 256 KiB")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:source-inventory:malformed",
            reason=f"source_inventory.json is malformed: {type(error).__name__}",
            recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
        ) from error
    if not isinstance(data, dict):
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:source-inventory:shape",
            reason="source_inventory.json must be a JSON object",
            recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
        )
    version = data.get("schema_version")
    if version != 1:
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:source-inventory:schema-version",
            reason=f"source_inventory schema_version must be 1; got {version!r}",
            recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
        )
    source_database = data.get("source_database")
    if not isinstance(source_database, str) or not source_database:
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:source-inventory:source-database",
            reason="source_inventory source_database must be a non-empty string",
            recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
        )
    raw_tables = data.get("tables")
    if not isinstance(raw_tables, list):
        raise _bootstrap_gate_error(
            rule_ids=("HOOK-004",),
            evidence_ref="bootstrap:source-inventory:tables",
            reason="source_inventory tables must be an array",
            recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
        )
    tables: list[SourceTable] = []
    for index, raw_table in enumerate(raw_tables):
        if not isinstance(raw_table, dict):
            raise _bootstrap_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref=f"bootstrap:source-inventory:table[{index}]",
                reason=f"table entry {index} must be a JSON object",
                recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
            )
        table_name = raw_table.get("name")
        if not isinstance(table_name, str) or not table_name:
            raise _bootstrap_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref=f"bootstrap:source-inventory:table[{index}]:name",
                reason=f"table entry {index} name must be a non-empty string",
                recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
            )
        raw_columns = raw_table.get("columns")
        if not isinstance(raw_columns, list):
            raise _bootstrap_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref=f"bootstrap:source-inventory:table[{index}]:columns",
                reason=f"table {table_name} columns must be an array",
                recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
            )
        columns: list[SourceColumn] = []
        for col_index, raw_col in enumerate(raw_columns):
            if not isinstance(raw_col, dict):
                raise _bootstrap_gate_error(
                    rule_ids=("HOOK-004",),
                    evidence_ref=(
                        f"bootstrap:source-inventory:table[{index}]"
                        f":column[{col_index}]"
                    ),
                    reason=(
                        f"column {col_index} of table {table_name} must be a "
                        "JSON object"
                    ),
                    recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
                )
            col_name = raw_col.get("name")
            col_type = raw_col.get("data_type")
            col_pk = raw_col.get("is_primary_key")
            if not isinstance(col_name, str) or not col_name:
                raise _bootstrap_gate_error(
                    rule_ids=("HOOK-004",),
                    evidence_ref=(
                        f"bootstrap:source-inventory:table[{index}]"
                        f":column[{col_index}]:name"
                    ),
                    reason=(
                        f"column {col_index} of table {table_name} name must "
                        "be a non-empty string"
                    ),
                    recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
                )
            if not isinstance(col_type, str) or not col_type:
                raise _bootstrap_gate_error(
                    rule_ids=("HOOK-004",),
                    evidence_ref=(
                        f"bootstrap:source-inventory:table[{index}]"
                        f":column[{col_index}]:data-type"
                    ),
                    reason=(
                        f"column {col_name} of table {table_name} data_type "
                        "must be a non-empty string"
                    ),
                    recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
                )
            if not isinstance(col_pk, bool):
                raise _bootstrap_gate_error(
                    rule_ids=("HOOK-004",),
                    evidence_ref=(
                        f"bootstrap:source-inventory:table[{index}]"
                        f":column[{col_index}]:is-primary-key"
                    ),
                    reason=(
                        f"column {col_name} of table {table_name} "
                        "is_primary_key must be a boolean"
                    ),
                    recovery="Re-run /chatbi-bootstrap to regenerate the inventory",
                )
            columns.append(SourceColumn(
                name=col_name,
                data_type=col_type,
                is_primary_key=col_pk,
            ))
        tables.append(SourceTable(name=table_name, columns=tuple(columns)))
    return SourceInventory(source_database=source_database, tables=tuple(tables))


def merge_source_inventories(
    base: SourceInventory, extra: SourceInventory,
) -> SourceInventory:
    """Union two source inventories by table name.

    On name collision -> :class:`GateError` (HOOK-004): do NOT silently
    overwrite an already-inventoried table (v1 = fail-closed; a future
    overwrite-with-human-approval path is out of scope). Returns a new frozen
    :class:`SourceInventory` (does not mutate inputs). ``schema_version``
    stays 1 (inventory shape unchanged, only adds tables).

    ``base`` = on-disk inventory (read via :func:`read_source_inventory`);
    ``extra`` = the scoped incremental introspect result (newly-approved
    tables only). The result ``source_database`` = ``base.source_database``
    (the incremental introspect is against the same source DB).
    """
    existing_names = {table.name for table in base.tables}
    for extra_table in extra.tables:
        if extra_table.name in existing_names:
            raise _bootstrap_gate_error(
                rule_ids=("HOOK-004",),
                evidence_ref=f"bootstrap:merge:collision:{extra_table.name}",
                reason=(
                    f"source table {extra_table.name} is already inventoried; "
                    "incremental merge does not overwrite existing tables"
                ),
                recovery=(
                    "Remove the duplicate table from the incremental introspect "
                    "or refresh the full inventory via /chatbi-bootstrap"
                ),
            )
    # Build a new tuple: base tables first, then extra tables. The frozen
    # dataclass is immutable so we construct a fresh SourceInventory.
    combined_tables = tuple(base.tables) + tuple(extra.tables)
    return SourceInventory(
        source_database=base.source_database,
        tables=combined_tables,
    )


__all__ = [
    "SourceColumn",
    "SourceInventory",
    "SourceTable",
    "build_mysql_adapter_spec",
    "merge_local_config",
    "merge_source_inventories",
    "read_source_inventory",
]
