"""RuntimeNativeRunner: mysql CLI + dbt execution seam (agno Phase 2).

This is the native_runner implementation behind the agno target's governed
tools (technical-design-agno-phase2 §3.2, Q1/Q4 rulings):

- ``chatbi-bootstrap/run_mysql`` — CREATE DATABASE IF NOT EXISTS
  ``<warehouse_db>`` + INFORMATION_SCHEMA introspection (4 columns only:
  table_name / column_name / data_type / COLUMN_KEY) -> atomic
  ``.chatbi/bootstrap/source_inventory.json`` write;
- ``chatbi-bootstrap/scaffold`` — dbt_project.yml (name/profile from
  ``deployment.warehouse_db``) + ``models/{ods,dwd,dws,ads}`` +
  ``docs/org/data-warehouse-blueprint.md`` stub;
- ``chatbi-maintain-model/dbt_run|dbt_test`` — dbt argv assembly (reused by
  the ``chatbi_dbt_execute`` domain hook);
- :meth:`run_mysql_query` — read-only SELECT execution via the mysql CLI.

CLI discipline (kernel ``adapters``-同构, technical-design §8.2):
- every command is an argv ARRAY with ``shell=False``;
- cwd is fixed to the workspace root;
- env is built by the kernel :func:`build_cli_env` — only locale, a safe
  PATH and DECLARED credential env var NAMES (SEC-003: no password values
  ever appear in argv or env);
- stdout is untrusted data (truncated + ``untrusted`` marker), never
  spliced into a prompt.

Judgment (invariant 2): this module carries NO governance decisions —
it executes and reports; the tool_hooks chain (hooks.py) decides.

``cli_runner`` is the subprocess seam for offline tests (fake injection);
None = real :func:`subprocess.run`.

Applicable rules: SEC-003, PORT-001, HOOK-001/004, invariant 2/5.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from chatbi_governance.adapters import (
    build_cli_env,
    validate_cli_argv,
)
from chatbi_governance.bootstrap import (
    SourceColumn,
    SourceInventory,
    SourceTable,
)

#: Source database identifier pattern (introspection statement embedding).
_SOURCE_DB_RE = re.compile(r"^[a-zA-Z0-9_]+$")

#: Read-only query result row cap (untrusted stdout size guard).
_QUERY_ROW_CAP = 2000
#: dbt log tail cap (2 KiB, design §6.2 step 5).
_LOG_TAIL_BYTES = 2048
#: mysql query timeout.
_MYSQL_TIMEOUT_SECONDS = 60
#: dbt run/test timeout (design §6.2 step 5: 300s).
_DBT_TIMEOUT_SECONDS = 300


def _tail(text: str, limit: int = _LOG_TAIL_BYTES) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


class RuntimeNativeRunner:
    """native_runner implementation (mysql introspection/scaffold/query/dbt).

    ``cli_runner`` is the subprocess seam: a callable shaped like
    ``subprocess.run(argv, *, cwd, env, timeout, ...)`` returning a
    CompletedProcess-like object (``returncode`` / ``stdout`` / ``stderr``).
    Tests inject a fake; None = real subprocess.
    """

    def __init__(
        self,
        *,
        deployment: Any,
        config: Any | None,
        workspace_root: Path,
        harness_release: str = "dev",
        cli_runner: Callable[..., Any] | None = None,
    ) -> None:
        self._deployment = deployment
        self._config = config
        self._workspace_root = Path(workspace_root)
        self._harness_release = harness_release
        self._cli_runner = cli_runner

    # -- subprocess seam ---------------------------------------------------
    def _run_cli(self, argv: list[str], *, timeout: int) -> Any:
        env = build_cli_env()
        if self._cli_runner is not None:
            return self._cli_runner(
                argv=argv, cwd=str(self._workspace_root), env=env,
                timeout=timeout)
        return subprocess.run(
            argv, shell=False, cwd=str(self._workspace_root), env=env,
            capture_output=True, timeout=timeout, check=False)

    def _run_cli_with_env(
        self, argv: list[str], *, credential_env_names: tuple[str, ...],
        timeout: int,
    ) -> Any:
        env = build_cli_env(credential_env_names)
        if self._cli_runner is not None:
            return self._cli_runner(
                argv=argv, cwd=str(self._workspace_root), env=env,
                timeout=timeout)
        return subprocess.run(
            argv, shell=False, cwd=str(self._workspace_root), env=env,
            capture_output=True, timeout=timeout, check=False)

    @staticmethod
    def _stdout_text(result: Any) -> str:
        raw = getattr(result, "stdout", b"")
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    @staticmethod
    def _stderr_text(result: Any) -> str:
        raw = getattr(result, "stderr", b"")
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    # -- entry point -------------------------------------------------------
    def __call__(self, workflow_id: str, step_id: str,
                 request: Mapping[str, Any]) -> Mapping[str, Any]:
        if step_id == "run_mysql":
            return self._run_mysql(request)
        if step_id == "scaffold":
            return self._scaffold()
        if step_id in ("dbt_run", "dbt_test"):
            return self._run_dbt(step_id, request)
        raise ValueError(
            f"unknown native step {workflow_id}/{step_id} (fail-closed)")

    # -- run_mysql ---------------------------------------------------------
    def _run_mysql(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        spec = request.get("spec")
        executable = request.get("executable")
        if not isinstance(spec, Mapping) or not isinstance(executable, str):
            return {"status": "error", "error_category": "missing_spec"}
        argv_base = tuple(spec.get("argv") or ())
        if not argv_base:
            return {"status": "error", "error_category": "missing_argv"}
        credential_env_names = tuple(spec.get("credential_env_names") or ())
        source_db = request.get("database")
        if not isinstance(source_db, str) or not _SOURCE_DB_RE.fullmatch(
                source_db):
            return {"status": "error",
                    "error_category": "invalid_source_database"}
        warehouse_db = getattr(self._deployment, "warehouse_db", "dw_agno")

        # 1) CREATE DATABASE IF NOT EXISTS <warehouse_db> (argv array).
        create_argv = [
            executable, *argv_base[1:], "--batch", "--skip-column-names",
            "-e", f"CREATE DATABASE IF NOT EXISTS {warehouse_db}",
        ]
        illegal = validate_cli_argv(create_argv)
        if illegal is not None:
            return {"status": "error", "error_category": f"argv:{illegal}"}
        create_result = self._run_cli_with_env(
            create_argv, credential_env_names=credential_env_names,
            timeout=_MYSQL_TIMEOUT_SECONDS)
        if getattr(create_result, "returncode", -1) != 0:
            return {"status": "error",
                    "error_category": "create_warehouse_db_failed",
                    "returncode": getattr(create_result, "returncode", -1),
                    "log_tail": _tail(self._stderr_text(create_result))}

        # 2) INFORMATION_SCHEMA introspection (4 columns, TABLE_SCHEMA=source).
        introspect_sql = (
            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{source_db}' "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        introspect_argv = [
            executable, *argv_base[1:], "--batch", "--skip-column-names",
            "-e", introspect_sql,
        ]
        illegal = validate_cli_argv(introspect_argv)
        if illegal is not None:
            return {"status": "error", "error_category": f"argv:{illegal}"}
        try:
            introspect_result = self._run_cli_with_env(
                introspect_argv, credential_env_names=credential_env_names,
                timeout=_MYSQL_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"status": "error",
                    "error_category": f"run_failure:{type(error).__name__}"}
        if getattr(introspect_result, "returncode", -1) != 0:
            return {"status": "error",
                    "error_category": "introspection_failed",
                    "returncode": getattr(introspect_result, "returncode", -1),
                    "log_tail": _tail(self._stderr_text(introspect_result))}

        # Parse batch rows (tab-separated, skip-column-names).
        tables: dict[str, list[SourceColumn]] = {}
        for line in self._stdout_text(introspect_result).splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue  # malformed batch row: skip (counted below)
            table_name, column_name, data_type, column_key = parts[:4]
            if not table_name or not column_name:
                continue
            tables.setdefault(table_name, []).append(SourceColumn(
                name=column_name,
                data_type=data_type,
                is_primary_key=column_key == "PRI",
            ))
        inventory = SourceInventory(
            source_database=source_db,
            tables=tuple(
                SourceTable(name=name, columns=tuple(columns))
                for name, columns in tables.items()
            ),
        )
        # Atomic write (same-dir temp + os.replace).
        target = self._workspace_root / ".chatbi" / "bootstrap" \
            / "source_inventory.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target.with_name(target.name + ".tmp")
        tmp_target.write_text(
            json_dumps(inventory.to_dict()), encoding="utf-8")
        os.replace(tmp_target, target)
        return {
            "status": "ok",
            "inventory_path": str(target),
            "source_database": source_db,
            "table_count": len(inventory.tables),
        }

    # -- scaffold ----------------------------------------------------------
    def _scaffold(self) -> Mapping[str, Any]:
        warehouse_db = getattr(self._deployment, "warehouse_db", "dw_agno")
        project_file = self._workspace_root / "dbt_project.yml"
        project_file.write_text(
            f"name: {warehouse_db}\n"
            "version: '1.0.0'\n"
            "config-version: 2\n"
            f"profile: {warehouse_db}\n"
            'model-paths: ["models"]\n',
            encoding="utf-8")
        for layer in ("ods", "dwd", "dws", "ads"):
            (self._workspace_root / "models" / layer).mkdir(
                parents=True, exist_ok=True)
        blueprint = (self._workspace_root / "docs" / "org"
                     / "data-warehouse-blueprint.md")
        blueprint.parent.mkdir(parents=True, exist_ok=True)
        blueprint.write_text(
            "# Data Warehouse Blueprint (demo scaffold)\n\n"
            f"Project: {warehouse_db} (dbt profile: {warehouse_db})\n"
            "Layers: ods -> dwd -> dws -> ads\n",
            encoding="utf-8")
        return {"status": "ok",
                "scaffold": {"project": warehouse_db, "models_dir": "models"}}

    # -- dbt run/test ------------------------------------------------------
    def _run_dbt(self, step_id: str,
                 request: Mapping[str, Any]) -> Mapping[str, Any]:
        operation = "run" if step_id == "dbt_run" else "test"
        select = request.get("select")
        if not isinstance(select, str) or not select:
            return {"status": "error", "error_category": "missing_select"}
        dbt_bin = request.get("dbt_bin") or getattr(
            self._deployment, "dbt_bin", "")
        if not dbt_bin:
            return {"status": "error", "error_category": "dbt_bin_unset"}
        profiles_dir = request.get("profiles_dir") or getattr(
            self._deployment, "dbt_profiles_dir", "")
        argv: list[str] = [
            str(dbt_bin), operation, "--select", select,
            "--project-dir", str(self._workspace_root),
        ]
        if profiles_dir:
            argv += ["--profiles-dir", str(profiles_dir)]
        illegal = validate_cli_argv(argv)
        if illegal is not None:
            return {"status": "error", "error_category": f"argv:{illegal}"}
        try:
            result = self._run_cli(argv, timeout=_DBT_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"status": "error",
                    "error_category": f"run_failure:{type(error).__name__}"}
        returncode = getattr(result, "returncode", -1)
        log_tail = _tail(
            self._stdout_text(result) + self._stderr_text(result))
        if returncode != 0:
            return {"status": "error", "error_category": "nonzero_exit",
                    "returncode": returncode, "log_tail": log_tail}
        return {"status": "ok", "returncode": 0, "log_tail": log_tail}

    # -- read-only query ---------------------------------------------------
    def run_mysql_query(
        self, *, statement: str, spec: Mapping[str, Any],
        executable: Path,
    ) -> Mapping[str, Any]:
        """Execute one read-only SELECT via the mysql CLI (argv array).

        argv = ``[executable, *spec.argv[1:], --batch, --skip-column-names,
        -e, statement]``. Returns ``{"status": "ok", "rows": [...],
        "row_count": n, "truncated": bool, "returncode": 0,
        "untrusted": True}``; nonzero exit -> ``{"status": "error",
        "error_category": ...}`` (the hook turns it into a deny, HOOK-004).
        """
        argv_base = tuple(spec.get("argv") or ())
        if not argv_base:
            return {"status": "error", "error_category": "missing_argv"}
        credential_env_names = tuple(spec.get("credential_env_names") or ())
        argv = [
            str(executable), *argv_base[1:], "--batch", "--skip-column-names",
            "-e", statement,
        ]
        illegal = validate_cli_argv(argv)
        if illegal is not None:
            return {"status": "error", "error_category": f"argv:{illegal}"}
        try:
            result = self._run_cli_with_env(
                argv, credential_env_names=credential_env_names,
                timeout=_MYSQL_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"status": "error",
                    "error_category": f"run_failure:{type(error).__name__}"}
        returncode = getattr(result, "returncode", -1)
        if returncode != 0:
            return {"status": "error", "error_category": "nonzero_exit",
                    "returncode": returncode,
                    "log_tail": _tail(self._stderr_text(result))}
        rows: list[list[str]] = []
        for line in self._stdout_text(result).splitlines():
            if not line:
                continue
            rows.append(line.split("\t"))
        truncated = len(rows) > _QUERY_ROW_CAP
        if truncated:
            rows = rows[:_QUERY_ROW_CAP]
        return {
            "status": "ok",
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "returncode": 0,
            "untrusted": True,
        }


def json_dumps(payload: Mapping[str, Any]) -> str:
    """Deterministic JSON serialization (no machine paths by construction)."""
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = ["RuntimeNativeRunner"]
