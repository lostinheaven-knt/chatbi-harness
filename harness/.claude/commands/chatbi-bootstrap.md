---
description: Scaffold a from-zero local Warehouse - write local config (cli_adapters.mysql + optional path_binding), append cli:mysql to shared adapters.query, CREATE DATABASE IF NOT EXISTS dw, introspect the source public schema, scaffold the dbt-mysql project layout, and emit a source-inventory JSON for /chatbi-maintain-model. MySQL-only v1. Infra setup only: no governed models, no metric approval, no production publish, no destructive migration (SEM-003).
argument-hint: "[mysql-host] [mysql-port] [mysql-user] [source-db] [credential-env-name] [business-codebase-alias]"
---

# /chatbi-bootstrap

You are the main Agent (Warehouse Operator) of the ChatBI Harness. This command
scaffolds a from-zero local Warehouse so the agent can then build ODS/DWD/DWS
via `/chatbi-maintain-model`. **MySQL-only v1.** dbt-mysql scaffold layout
(`models/{ods,dwd,dws,dim}/` + `dbt_project.yml`).

## 0. Trust boundary

bootstrap = **INFRA SETUP only**. It is to `/chatbi-maintain-model` what
`install.sh` is to the harness: setup, not governed-artifact creation.

- You MAY write `.claude/chatbi-harness.local.json` (`cli_adapters.mysql` +
  optional `path_bindings`), append one `cli:mysql` entry to shared
  `adapters.query` (idempotent, if absent), `CREATE DATABASE IF NOT EXISTS dw`
  (non-destructive), introspect the source `public` schema via
  INFORMATION_SCHEMA, scaffold project dirs (`dbt_project.yml` +
  `models/{ods,dwd,dws,dim}/` empty), stub `docs/org/data-warehouse-blueprint.md`,
  and write `.chatbi/bootstrap/source_inventory.json`.
- You MUST NOT create ODS/DWD/DWS model files with governed content, approve a
  canonical metric, change access policy, publish to production, or run a
  destructive migration (SEM-003 - human owner only). ODS model creation is
  handed off to `/chatbi-maintain-model`.
- No machine absolute paths, no secrets, no unauthorized PII in any artifact
  (SEC-003, PORT-001). The password is an env var NAME (e.g. `MYSQL_PWD`) or
  empty for local no-password root; NEVER a value. Use logical aliases and
  Workspace-relative references.

## 1. Bind to the runbook

Load `skills/chatbi-bootstrap/SKILL.md`. Follow its 9-step procedure. The
runbook documents the per-operation `CliAdapter` + `--execute=<SQL>` construction
(option a, technical-design §2) and the `cli_allowlist` confirmation requirement.

## 2. Inputs

- MySQL source connection: `host`, `port` (1-65535), `user`, `source_db`.
- Credential env var NAME (e.g. `MYSQL_PWD`), or omit for local no-password root
  (`credential_env_names: []`).
- Optional Business Codebase `path_binding` alias: must be a `path_ref` already
  declared in shared `business_codebases`. bootstrap cannot invent a new
  `path_ref`; it only resolves an existing declared one to a local absolute path
  (SCOPE-001/PORT-001). If the alias is not declared, STOP and ask.

## 3. Output evidence

Return a hand-off report: source table count, `dw` status (created /
already-exists-with-tables warn), local + shared config paths written, scaffold
paths, and the inventory file path. Distinguish observation from interpretation.
No secrets / absolute paths / PII in the report (SEC-003). Point at
`.chatbi/bootstrap/source_inventory.json` as the input to `/chatbi-maintain-model`.

## 4. Stop conditions

Stop with `BLOCKED` when: `build_mysql_adapter_spec` raises `GateError` (bad
host/port/user/database/credential-name); `load_effective_config` fails
(schema/secret/path-binding violation); `resolve_executable` cannot resolve
`mysql` to the confirmed allowlist (SEC-001/PORT-001/HOOK-004); the `CliAdapter`
constructor rejects the `--execute` argv (shell metacharacter / sensitive flag);
or `CREATE DATABASE IF NOT EXISTS dw` exits non-zero (PORT-001). Do not retry
with a "fixed" value; surface the sanitized `GateDecision` and stop.

If `dw` already exists with tables, emit a WARN (do not clobber, do not block)
before `CREATE DATABASE IF NOT EXISTS dw`.

## 5. Non-goals

- Do not create governed ODS/DWD/DWS model files (route via
  `/chatbi-maintain-model`).
- Do not approve metrics, publish, or run destructive migration (SEM-003).
- Do not execute mysql from unconfirmed PATH; the confirmed absolute `mysql`
  realpath is the single-element `cli_allowlist` (SEC-001/PORT-001).
- Do not splice `stdout_raw` into a prompt; it is untrusted text tagged by
  `CliAdapter._parse_stdout` (SEC-003).
- Non-MySQL engines (StarRocks, Hive) are out of scope for v1.
- Live MySQL execution is NOT YET EXERCISED in CI; the deterministic lib surface
  (`build_mysql_adapter_spec`, `merge_local_config`, `SourceInventory`) is
  VERIFIED OFFLINE.

## Rules

SCOPE-001, SCOPE-002, SEC-001, SEC-003, PORT-001, SEM-003, DOC-001, HOOK-004.
No new rule is added; the 46-rule count is unchanged.
