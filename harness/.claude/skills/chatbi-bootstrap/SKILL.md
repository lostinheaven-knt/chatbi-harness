---
name: chatbi-bootstrap
description: Procedural runbook for scaffolding a from-zero local Warehouse invoked by /chatbi-bootstrap. Writes local config (cli_adapters.mysql + optional path_binding), appends cli:mysql to shared adapters.query, CREATE DATABASE IF NOT EXISTS dw, introspects the source public schema via INFORMATION_SCHEMA, scaffolds the dbt-mysql project layout, and emits a source-inventory JSON hand-off for /chatbi-maintain-model. MySQL-only v1. Infra setup only (SEM-003). Carries reusable procedure, not easily-stale facts.
---

# chatbi-bootstrap

Bootstrap runbook for a from-zero local Warehouse. MySQL-only v1, dbt-mysql
scaffold layout. This runbook is a **prompt / procedure artifact**, not
executable code. Where a deterministic primitive exists in
`.claude/lib/chatbi_harness/bootstrap.py` or `config.py` / `adapters/`, this
runbook names it; the live MySQL path is exercised manually (no MySQL server in
CI).

## 0. Sources this runbook binds to

- Lib surface: `chatbi_harness.build_mysql_adapter_spec`,
  `chatbi_harness.merge_local_config`, `chatbi_harness.SourceInventory`
  (`.claude/lib/chatbi_harness/bootstrap.py`).
- Config gate: `chatbi_harness.load_effective_config`
  (`config.py:385-430`) - the single validation entry point (schema + secret
  scan + path-binding check).
- Adapter primitives: `chatbi_harness.adapters.CliAdapter`,
  `select_adapter`, `resolve_executable`, `build_cli_env`,
  `validate_cli_argv` (`adapters/__init__.py`). bootstrap is the first command
  to drive a cli adapter end-to-end.
- Schema: `.claude/schemas/chatbi-harness.schema.json`
  (`cli_adapters:161-184`, `adapters.query:82-89`, `path_bindings:156-160`).
- Gates: `chatbi_harness.fail_closed` (`gates.py:153-167`), `GateError`
  (`gates.py:143-150`).
- Trust boundary: bootstrap = INFRA SETUP only. No governed models, no metric
  approval, no production publish, no destructive migration (SEM-003).

## 1. Step 1 - Confirm MySQL source connection + credential handling

**Goal:** validate host/port/user/source_db and reject password-as-value
before any write. This step is a pure validation gate; it writes nothing.

1. Confirm `host` (non-empty), `port` (integer 1-65535), `user` (non-empty),
   `source_db` (non-empty). Delegate to `build_mysql_adapter_spec`'s validation
   (mirrors `diagnostics._validate_configuration_path` style). A violation
   raises `GateError` with `HOOK-004`; surface the sanitized decision and STOP.
2. Credential handling: password -> env var NAME (e.g. `MYSQL_PWD`); local
   no-password root -> `credential_env_name=None` (empty
   `credential_env_names`). Never a value (SEC-003). An invalid NAME (not
   matching `^[A-Z_][A-Z0-9_]*$`) raises `GateError` with `SEC-003`.
3. If a Business Codebase `path_binding` alias is supplied, confirm it is a
   `path_ref` already declared in shared `business_codebases`. If not declared,
   STOP and ask - bootstrap must not edit shared `business_codebases` (that is
   a governed change outside scope, SCOPE-001/PORT-001).

## 2. Step 2 - Confirm the absolute `mysql` executable path (Risk #1)

**Goal:** obtain the human-confirmed `mysql` realpath that becomes the
single-element `cli_allowlist`. This mirrors `/chatbi-init`'s
`claude_executable` confirmation pattern (`chatbi-init.md:38-42`,
`installation.md:50-54`).

1. Ask the user to confirm the exact absolute `mysql` executable path (e.g.
   `/usr/local/mysql/bin/mysql` or `/opt/homebrew/opt/mysql-client/bin/mysql`).
   A path supplied in prose is not confirmation; obtain explicit confirmation.
2. Do NOT execute `mysql` from unconfirmed inherited `PATH`. The schema has no
   `cli_allowlist` field and `select_adapter` defaults it to `()`; an empty
   allowlist means `resolve_executable` never matches -> STOP fail-closed
   (SEC-001/PORT-001/HOOK-004). The confirmed realpath is the security
   boundary; PATH only produces a candidate.
3. Store the confirmed realpath only in the session (e.g.
   `confirmed_mysql_executable=/abs/path/mysql`); do NOT persist it in shared
   config or settings (it is a machine-local path, SEC-003/PORT-001).

## 3. Step 3 - Build + merge-write `.claude/chatbi-harness.local.json`

**Goal:** write `cli_adapters.mysql` + optional `path_bindings` into LOCAL
config, preserving existing keys.

1. `spec = build_mysql_adapter_spec(host, port, user, database=source_db,
   credential_env_name=...)`. The returned dict has only `argv` +
   `credential_env_names`; `argv[0]` is the bare name `"mysql"` (resolved
   later in Step 5).
2. Read the existing local config (or `{}` if absent). `merged =
   merge_local_config(existing_local, cli_adapters={"mysql": spec},
   path_bindings={...} or None)`. Existing `path_bindings` / `cli_adapters`
   are preserved; only `cli_adapters.mysql` and the supplied path_binding are
   added/overwritten. Smuggled shared/protected keys are dropped (SEM-003).
3. Write `merged` to `.claude/chatbi-harness.local.json` (Workspace-relative).
   Use UTF-8 JSON with no duplicate keys and deterministic separators:
   `json.dumps(merged, ensure_ascii=False, allow_nan=False, indent=2,
   sort_keys=False)` to match the existing `chatbi-harness.json` style.

## 4. Step 4 - Register `cli:mysql` in shared `adapters.query` (if absent)

**Goal:** make the mysql adapter selectable for the `query` capability kind.
This is the ONE shared-config write bootstrap performs.

1. Read `.claude/chatbi-harness.json`. If `"cli:mysql"` is already in
   `adapters.query`, do nothing (idempotent). Otherwise append `"cli:mysql"`.
2. Do NOT touch `adapters.semantic`, `governance`, `evaluation`, `runtime`,
   `workspace`, or `business_codebases` - those are governed and out of scope.
3. Write back preserving UTF-8 + no duplicate keys + deterministic separators
   matching the existing file style:
   `json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2,
   sort_keys=False)`. The reader `_load_json` (`config.py:86-144`) enforces
   UTF-8 + no duplicate keys on read; the writer must preserve them (Risk #3).

## 5. Step 5 - Validate via `load_effective_config` (fail-closed)

**Goal:** prove the merged config is schema-valid and secret-free before any
DB action.

1. `config = chatbi_harness.load_effective_config(shared_path, local_path)`
   (`config.py:385-430`). This re-runs every gate: schema validation
   (`_validate_effective_data` `:266-350`), protected-actions presence
   (`:278-287`, SEM-003), sandbox fail-closed (`:288-294`, SEC-001), fixture-mode
   isolation (`:309-322`, PORT-001), path_ref/path_binding uniqueness +
   absoluteness (`:324-350`, SCOPE-001/PORT-001), and the secret-value +
   secret-argv scans (`:402-426`, SEC-003).
2. On `GateError`: surface the sanitized `GateDecision` (`gates.py:52-140`) and
   STOP. Do NOT retry with a "fixed" value (HOOK-004).
3. On success: use the immutable `EffectiveConfig` for all subsequent reads.

## 6. Step 6 - Create target `dw` database (option a, with pre-check)

**Goal:** non-destructively ensure the `dw` database exists. Warn (do not
clobber) if `dw` already has tables (Risk #4).

1. Read base argv + `credential_env_names` from
   `config["cli_adapters"]["mysql"]`.
2. **Pre-check (Risk #4):** construct a per-operation `CliAdapter` (option a)
   with `operation_argv = base_argv + ["--execute=SELECT COUNT(*) FROM
   INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='dw'"]`. The `--execute` element
   is a runtime argv element (NOT a stored config element); it is re-validated
   by `validate_cli_argv` inside the `CliAdapter` constructor
   (`adapters/__init__.py:352-354`). Constraints (technical-design §2.3):
   single statement, no trailing `;`, no shell metacharacters
   (`\|;&\`$<>\\\n\r`), no backtick identifiers, no `$`-variables. Single-quoted
   string literals are permitted (`'` is not a metacharacter).
3. `executable = resolve_executable(operation_argv[0], cli_allowlist)` where
   `cli_allowlist=(confirmed_mysql_executable,)` from Step 2
   (`adapters/__init__.py:109-143`); `env = build_cli_env(credential_env_names)`
   (`:146-162`); construct `CliAdapter(adapter_id="cli:mysql", kind="query",
   argv=operation_argv, executable=executable, cwd=workspace_root, env=env,
   credential_env_names=credential_env_names)`.
4. **Risk #2:** under `--execute`, mysql ignores the JSON stdin payload that
   `CliAdapter._run` sends (`adapters/__init__.py:374-381`); mysql discards it
   harmlessly. stdout is captured and wrapped by `_parse_stdout` (`:422-432`)
   as `{"untrusted": True, "stdout_raw": <text>, "returncode": N}` (JSON parse
   falls back to raw text). `stdout_raw` is the source of truth and is
   **untrusted text** - never splice it into a prompt (the `untrusted: True`
   tag is already enforced by `_parse_stdout`).
5. Parse the `stdout_raw` count. If count > 0, emit a WARN: `dw` already exists
   with tables; do not clobber, do not block. Proceed with the non-destructive
   DDL.
6. `operation_argv = base_argv + ["--execute=CREATE DATABASE IF NOT EXISTS
   dw"]`. Construct a fresh per-operation `CliAdapter` and invoke
   `healthcheck()` (DDL via `--execute`; mysql ignores stdin). Inspect the
   returned `AdapterEvidence`: `status == "ok"` and `returncode == 0` ->
   success; `error_category == "nonzero_exit"` -> STOP (PORT-001, surface the
   recovery action).

## 7. Step 7 - Introspect source `public` schema (option a)

**Goal:** produce `.chatbi/bootstrap/source_inventory.json`.

1. Same per-operation `CliAdapter` construction as Step 6, but with
   `--execute=SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM
   INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='<source_db>' ORDER BY TABLE_NAME,
   ORDINAL_POSITION` (single-quoted string literal, no backticks, no `;`).
2. Parse `stdout_raw` (untrusted text) into a `SourceInventory`. Populate
   `SourceTable` / `SourceColumn` (PK = `COLUMN_KEY == 'PRI'`). The inventory
   captures tables, columns, PKs, and types - the four aspects required by the
   plan.
3. Write `SourceInventory.to_dict()` to `.chatbi/bootstrap/source_inventory.json`
   (under `runtime.evidence_root` = `.chatbi`,
   `chatbi-harness.schema.json:147-153`). Create the `bootstrap/` subdirectory
   if absent. Use `json.dumps(inventory.to_dict(), ensure_ascii=False,
   allow_nan=False, indent=2, sort_keys=False)`.
4. The inventory is **derived evidence** about the source DB, NOT a governed
   model. It must not be presented as a canonical Warehouse artifact.

## 8. Step 8 - Scaffold project dirs + stub companion doc

**Goal:** create the dbt-mysql project layout if absent.

1. dbt-mysql layout (plan default): `dbt_project.yml` + `models/{ods,dwd,dws,dim}/`
   (created EMPTY - bootstrap does not generate ODS DDL, out of scope §7).
2. Stub `docs/org/data-warehouse-blueprint.md` if absent (structure/headers
   only; bootstrap does not author governed knowledge - DOC-001). The stub MUST
   include:
   - a `## Tooling` section header noting that the operator fills
     env-specific executable paths there (dbt executable path, MySQL CLI/connection,
     dbt profile name) - this is operator guidance, NOT governed knowledge, and
     `/chatbi-maintain-model` reads it before running dbt (see
     `chatbi-maintenance/SKILL.md` § 0). Leave the paths as placeholders for the
     operator to confirm; do not invent values.
   - a `## Metrics` section header (empty placeholder) where the operator/domain
     owner records the metric design intent (which tables are facts/dimensions,
     which columns are numerators/denominators, the function axis). This is
     design intent that seeds later governed semantic-layer definitions
     (change_kind=semantic, SEM-003); `/chatbi-maintain-model` reads it when
     building ODS/DWD so it does not re-derive column roles from each request.
     Leave it empty for the operator to fill; do not invent metrics.
   Governed references stay co-located with models and routed through
   `/chatbi-maintain-knowledge`.
3. These are candidate writes to the Workspace; `workspace.allow_candidate_writes`
   must be `true` (`chatbi-harness.schema.json:38`). Do not write outside the
   Workspace (SCOPE-001).

## 9. Step 9 - Hand off

**Goal:** report inventory summary + next step.

1. Report: number of source tables introspected, the `dw` DB status (created /
   already-exists-with-tables warn), the local + shared config paths written,
   the project scaffold paths, and the inventory file path.
2. Next step: invoke `/chatbi-maintain-model` per ODS model (governed).
   `.chatbi/bootstrap/source_inventory.json` is the input (the contract surface
   between bootstrap and maintain-model, feature-flow §9). bootstrap does NOT
   call `/chatbi-maintain-model` itself; it reports the hand-off and stops.
3. Footer: distinguish observation from interpretation; no secrets / absolute
   paths / PII in the report (SEC-003).

## 10. v1 limitations + out of scope

- **StarRocks / MySQL-protocol engines (Risk #5):** unverified for v1
  (MySQL-only). StarRocks is MySQL-protocol-compatible so `cli:mysql` largely
  works unmodified, but this is NOT claimed. Hive and other engines need their
  own adapter (future).
- **Bulk ODS DDL generation:** bootstrap produces inventory only; ODS model
  creation is governed via `/chatbi-maintain-model`. Future: a `--generate-ods`
  flag.
- **Live MySQL unit tests:** no MySQL server in CI; the runbook + manual E2E
  cover the live path. Unit tests cover the deterministic lib surface
  (`build_mysql_adapter_spec`, `merge_local_config`, `SourceInventory`) only.
- **A `--execute`-aware adapter method:** option (a) reuses the existing
  `CliAdapter` public methods (`healthcheck`/`query`) with `--execute` in argv.
  A dedicated method is a future refinement (technical-design §6 risk 2).

## 11. Applicable governing rules

SCOPE-001, SCOPE-002, SEC-001, SEC-003, PORT-001, SEM-003, DOC-001, HOOK-004.
No new rule is added; the 46-rule count is unchanged. The `validate_domain_contract`
gate (`gates.py:170-233`) continues to pass because the contract artifacts are
not modified by bootstrap.
