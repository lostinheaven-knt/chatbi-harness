# Feature Flow: `/chatbi-bootstrap` (MySQL-only v1)

> Status: **AS_BUILT** (2026-07-27). The bootstrap lib surface, command, SKILL,
> tests, build wiring, and doc updates are implemented. Line references below
> point to the **as-built bootstrap code** under `harness/.claude/lib/
> chatbi_harness/bootstrap.py` plus the **existing primitives bootstrap reuses**.
> The deterministic lib surface is VERIFIED OFFLINE (30 cases in
> `tests/harness/test_bootstrap.py`); live MySQL execution is NOT YET EXERCISED
> in CI. The CliAdapter JSON-stdin vs mysql SQL-stdin gap is resolved via
> option (a) (per-operation `CliAdapter` with `--execute=<SQL>`); see
> `docs/technical-design-bootstrap.md` §2 and the SKILL at
> `harness/.claude/skills/chatbi-bootstrap/SKILL.md`.

## 0. Project shape

**Monolith, not a microservice.** Confirmed by reading the source:

- One lib package: `harness/.claude/lib/chatbi_harness/` (`__init__.py`,
  `config.py`, `diagnostics.py`, `gates.py`, `paths.py`, `policy.py`,
  `evidence.py`, `impact.py`, `evaluator.py`, `knowledge.py`,
  `harness_state.py`, `adapters/`).
- One shared config + one local config per Workspace
  (`.claude/chatbi-harness.json` + `.claude/chatbi-harness.local.json`).
- One Workspace binding (`workspace.root == "."`, `chatbi-harness.schema.json:36`).
- Six slash commands today (`build-product.sh:36-38` lists `chatbi-init`,
  `chatbi-analyze`, `chatbi-maintain-model`, `chatbi-maintain-knowledge`,
  `chatbi-evaluate`, `chatbi-correction`). `product-README.md:3` says
  "Six slash commands, 46 enforced rules".

`/chatbi-bootstrap` adds a 7th command. It does not add a service.

## 1. Goal (from plan)

Close the harness "from-zero" gap with a governed command that scaffolds a
local Warehouse - config + `dw` DB + project structure + source schema
inventory - so the agent can then build ODS/DWD/DWS via
`/chatbi-maintain-model`. **MySQL-only v1**, dbt-mysql scaffold layout.

## 2. Trust boundary (verbatim from plan, §"Key design decision")

> **bootstrap = INFRA SETUP only.** It does NOT create governed models, NOT
> approve metrics, NOT touch production, NOT run destructive migrations.
> Model creation stays in `/chatbi-maintain-model` (impact + sync + review +
> approval). bootstrap is to `/chatbi-maintain-model` what `install.sh` is to
> the harness: setup, not governed-artifact creation. This keeps SEM-003/REV
> clean and the 46-rule count unchanged (no new rule).

Concretely, bootstrap MAY: write local config, append one `cli:mysql` entry
to shared `adapters.query`, create the `dw` database (non-destructive
`CREATE DATABASE IF NOT EXISTS`), introspect the source `public` schema,
scaffold project dirs, and write a source-inventory JSON. bootstrap MUST NOT:
create ODS/DWD/DWS model files with governed content, approve metrics,
publish, or run destructive migration. ODS model creation is handed off to
`/chatbi-maintain-model`.

## 3. Inputs

| Input | Source | Example | Handling |
| --- | --- | --- | --- |
| MySQL host | user/agent | `127.0.0.1` | Becomes `--host` element in `cli_adapters.mysql.argv` |
| MySQL port | user/agent | `3306` | Becomes `--port` element |
| MySQL user | user/agent | `root` | Becomes `--user` element |
| Source DB name | user/agent | `public` | Becomes `--database=public` element |
| Password handling | user/agent | `MYSQL_PWD` env var NAME, or empty for local no-password root | Stored as `credential_env_names: ["MYSQL_PWD"]` (or `[]`). **Never a value.** SEC-003. |
| Target `dw` DB name | fixed by plan | `dw` | `CREATE DATABASE IF NOT EXISTS dw` |
| Optional Business Codebase path_binding | user/agent (confirmed) | fypro docs dir absolute path | Only binds a `path_ref` already declared in shared `business_codebases` |

**Credential rule (SEC-003, enforced by existing primitives):**

- `chatbi-harness.schema.json:173-180` requires each
  `credential_env_names` item to match `^[A-Z_][A-Z0-9_]*$` - so only
  env-var NAMES pass schema.
- `config._contains_secret_argv` (`config.py:174-186`) rejects any argv
  element matching `^--?(?:api[-_]?key|token|password|secret)(?:[-_]file)?(?:=|$)`
  - so `--password=secret` or `--password secret` in argv is fail-closed.
- `config._contains_matching_string` + `_SECRET_VALUE` (`config.py:26-29`,
  `163-171`, applied at `config.py:402-408` and `418-426`) rejects secret
  values anywhere in local config.
- Local no-password root -> `credential_env_names: []` (schema allows empty
  array; `minItems` is not set on `credential_env_names`).

**Path-binding constraint (SCOPE-001, PORT-001, enforced at
`config.py:334-350`):** a `path_bindings` entry may only bind a `path_ref`
name that is already declared in shared `business_codebases`, and the value
must be an absolute path. bootstrap cannot invent a new `path_ref`; it can
only resolve an existing declared one to a local absolute path. If the user
supplies a Business Codebase path whose alias is not declared in shared
config, bootstrap must stop and ask (it must not edit shared
`business_codebases` - that is a governed change outside bootstrap's scope).

## 4. Step-by-step procedure (the 8 plan steps)

Each step names the existing primitive it reuses. No primitive is reinvented.

### Step 1 - Confirm MySQL source connection + credential handling

**Goal:** validate host/port/user/source_db and reject password-as-value
before any write.

- Confirm `host` non-empty, `port` is an integer 1-65535, `user` non-empty,
  `source_db` non-empty. (The plan's `build_mysql_adapter_spec` will enforce
  this; it mirrors the validation style of `diagnostics._validate_configuration_path`
  `diagnostics.py:250-309`.)
- Credential handling: password -> env var NAME (e.g. `MYSQL_PWD`); local
  no-password root -> empty `credential_env_names`. Never a value (SEC-003).
- This step is a pure validation gate; it writes nothing.

### Step 2 - Build + merge-write `.claude/chatbi-harness.local.json`

**Goal:** write `cli_adapters.mysql` + `path_bindings` into LOCAL config,
preserving existing keys.

- Target file: `.claude/chatbi-harness.local.json` (Workspace-relative; the
  local-config layer, see `config.load_effective_config` `config.py:409`).
- Shape written (matches `chatbi-harness.local.example.json` and schema
  `chatbi-harness.schema.json:156-184`):
  ```json
  {
    "path_bindings": { "<declared_path_ref>": "<absolute local path>" },
    "cli_adapters": {
      "mysql": {
        "argv": ["mysql", "--host", "127.0.0.1", "--port", "3306",
                 "--user", "root", "--database=public"],
        "credential_env_names": ["MYSQL_PWD"]
      }
    }
  }
  ```
- `argv` elements must each match `\S` (non-whitespace), `minItems: 1`
  (`chatbi-harness.schema.json:168-172`). Shell metacharacters are rejected
  later by `adapters.validate_cli_argv` (`adapters/__init__.py:89-106`,
  charset `_SHELL_METACHARACTERS = frozenset("|;&\`$<>\\\n\r")` at line 63).
- Merge semantics: preserve existing `path_bindings` and `cli_adapters` keys;
  add/overwrite only `cli_adapters.mysql` and the supplied `path_bindings`
  entry. No clobber of unrelated adapters/bindings. (This is the
  `merge_local_config(existing, *, path_bindings=None, cli_adapters=None)`
  primitive the plan adds.)
- Local config may ONLY contain `path_bindings` + `cli_adapters`
  (`config.py:410-417` rejects any other top-level key with SEM-003/HOOK-004).
  bootstrap must not smuggle shared-policy overrides here.

### Step 3 - Register `cli:mysql` in shared config `adapters.query` (if absent)

**Goal:** make the mysql adapter selectable for the `query` capability kind.

- Target file: `.claude/chatbi-harness.json` (the SHARED config).
- Field: `adapters.query` (`chatbi-harness.schema.json:82-89`), an array of
  adapter IDs matching `^(?:managed|cli|fixture):[a-z][a-z0-9_-]{1,62}$`.
- Today `adapters.query` is `[]` in the real shared config
  (`harness/.claude/chatbi-harness.json`). bootstrap appends `"cli:mysql"` if
  it is not already present (idempotent).
- This is the one shared-config write bootstrap performs. It does NOT touch
  `adapters.semantic`, `governance`, `evaluation`, `runtime`, `workspace`, or
  `business_codebases` - those are governed and out of scope.
- Note: writing shared config means bootstrap must round-trip the file
  preserving duplicate-key-free UTF-8 JSON (`config._load_json`
  `config.py:86-144` enforces these on read; the writer must preserve them).

### Step 4 - Validate via `load_effective_config` (fail-closed)

**Goal:** prove the merged config is schema-valid and secret-free before any
DB action.

- Call `chatbi_harness.config.load_effective_config(shared_path, local_path)`
  (`config.py:385-430`).
- This re-runs every gate: schema validation (`_validate_effective_data`
  `config.py:266-350`), protected-actions presence (`config.py:278-287`,
  SEM-003), sandbox-fail-closed (`config.py:288-294`, SEC-001), fixture-mode
  isolation (`config.py:309-322`, PORT-001), path_ref/path_binding uniqueness
  and absoluteness (`config.py:324-350`, SCOPE-001/PORT-001), and the
  secret-value/secret-argv scans (`config.py:402-426`, SEC-003).
- On any violation -> `GateError` (`gates.py:143-150`) carrying a sanitized
  blocking `GateDecision` (`gates.py:52-140`). bootstrap must surface that
  decision and stop; it must not retry with a "fixed" value.
- On success -> immutable `EffectiveConfig` (`config.py:353-382`). bootstrap
  uses this view for all subsequent reads.

### Step 5 - Create target `dw` database via `CliAdapter`

**Goal:** non-destructively ensure the `dw` database exists.

- Select the adapter via `chatbi_harness.adapters.select_adapter(
  config, kind="query", run_mode=<run_mode>, workspace_root=<cwd>,
  cli_allowlist=<allowlist>)` (`adapters/__init__.py:495-718`).
  - The chain walks `adapters.query` in order: `managed:` -> `cli:` ->
    `fixture:`. With only `cli:mysql` registered, and `ManagedAdapter`
    deterministically unavailable (`adapters/__init__.py:248-315`,
    `not_yet_exercised`), the chain selects `cli:mysql`
    (`adapters/__init__.py:583-673`).
  - `resolve_executable(argv[0], cli_allowlist)` (`adapters/__init__.py:109-143`)
    resolves `mysql` to an allowlisted absolute path. If not in the allowlist
    -> STOP fail-closed (SEC-003/PORT-001/HOOK-004). The allowlist is the
    security boundary; PATH only produces a candidate.
  - `validate_cli_argv(argv)` (`adapters/__init__.py:89-106`) re-checks no
    shell metacharacters / sensitive flags.
  - `build_cli_env(credential_env_names)` (`adapters/__init__.py:146-162`)
    builds a whitelisted env (locale + safe PATH + declared credential env
    var NAMES with values sourced from the current process env). No
    uncontrolled variable leaks.
- Issue `CREATE DATABASE IF NOT EXISTS dw` via the selected `CliAdapter`.
  Non-destructive. If `dw` already exists with tables, **warn** (do not
  clobber); the plan says "warn (don't clobber) if `dw` exists with tables".

> **Gap resolved (option a, technical-design §2):** `CliAdapter._run`
> (`adapters/__init__.py:372-420`) sends a JSON operation payload on stdin
> (`{"operation": "query", "compiled": {...}}`, lines `375-380`) and parses
> stdout as JSON (lines `422-432`). The real `mysql` CLI expects SQL via
> `-e`/`--execute` and emits tabular text, not JSON. The bootstrap SKILL
> resolves this via **option (a)**: for each SQL operation the runbook constructs
> a per-operation `CliAdapter` whose argv embeds `--execute=<SQL>` (single
> statement, no `;`, no shell metacharacters). mysql ignores the JSON stdin
> payload; stdout is captured and wrapped by `_parse_stdout` as
> `{"untrusted": True, "stdout_raw": <text>, "returncode": N}`. The runbook
> reads `stdout_raw` as **untrusted text** and never splices it into a prompt.
> See `harness/.claude/skills/chatbi-bootstrap/SKILL.md` Steps 6-7 for the
> as-built procedure.

### Step 6 - Introspect source `public` schema via INFORMATION_SCHEMA

**Goal:** produce `.chatbi/bootstrap/source_inventory.json`.

- Reuse the same `cli:mysql` `CliAdapter` selected in Step 5.
- Query `INFORMATION_SCHEMA.TABLES` and `INFORMATION_SCHEMA.COLUMNS` where
  `TABLE_SCHEMA = '<source_db>'` (plan example uses `public`) -> tables,
  columns, PKs, types.
- Write `.chatbi/bootstrap/source_inventory.json`. `.chatbi` is the governed
  `runtime.evidence_root` (`chatbi-harness.schema.json:147-153`, enum
  `[".chatbi"]`). A new `bootstrap/` subdirectory is created under it.
- Subject to the same option-(a) resolution as Step 5: INFORMATION_SCHEMA
  queries are SQL embedded as `--execute=<SQL>` in a per-operation `CliAdapter`
  argv. See `harness/.claude/skills/chatbi-bootstrap/SKILL.md` Step 7.
- The inventory is **derived evidence** about the source DB; it is not a
  governed model. It must not be presented as a canonical Warehouse artifact.

### Step 7 - Scaffold project dirs + stub companion doc

**Goal:** create the dbt-mysql project layout if absent.

- Layout (plan §"Open question", default = dbt-mysql):
  ```
  dbt_project.yml
  models/
    ods/
    dwd/
    dws/
    dim/
  ```
  (Raw-SQL swap is trivial: `sql/{ods,dwd,dws,dim}/`. Command logic is
  identical either way; only the scaffold paths differ.)
- Stub companion doc: `docs/org/data-warehouse-blueprint.md` if absent. This
  is a STUB (structure/headers only) - bootstrap does not author governed
  knowledge content (DOC-001 keeps governed references co-located with
  models and routed through `/chatbi-maintain-knowledge` lint).
- These are candidate writes to the Workspace; `workspace.allow_candidate_writes`
  must be `true` (`chatbi-harness.schema.json:38`, validated at
  `config.py:266+` via the schema). bootstrap must not write outside the
  Workspace (SCOPE-001).
- The `models/{ods,dwd,dws,dim}/` dirs are created EMPTY - bootstrap does not
  generate ODS DDL (out of scope, §7).

### Step 8 - Hand off

**Goal:** report inventory summary + next step.

- Report: number of source tables introspected, the `dw` DB status
  (created / already-exists-with-tables warning), the local+shared config
  paths written, the project scaffold paths, and the inventory file path.
- Next step: invoke `/chatbi-maintain-model` per ODS model, governed. The
  source_inventory.json is the input to ODS model creation (see §8 Hand-off).
- bootstrap does NOT call `/chatbi-maintain-model` itself; it reports the
  hand-off and stops. The user/operator decides which tables to promote.

## 5. Artifacts produced/modified

| Artifact | Path | Action | Layer |
| --- | --- | --- | --- |
| Local config | `.claude/chatbi-harness.local.json` | merge-write `cli_adapters.mysql` + `path_bindings` | local |
| Shared config | `.claude/chatbi-harness.json` | append `cli:mysql` to `adapters.query` if absent | shared (one governed field) |
| Target DB | MySQL `dw` | `CREATE DATABASE IF NOT EXISTS dw` (non-destructive) | external MySQL |
| Source inventory | `.chatbi/bootstrap/source_inventory.json` | create (tables, columns, PKs, types) | `runtime.evidence_root` |
| Project scaffold | `dbt_project.yml`, `models/{ods,dwd,dws,dim}/` | create if absent | Workspace |
| Companion doc stub | `docs/org/data-warehouse-blueprint.md` | create stub if absent | Workspace |

bootstrap does NOT create: ODS/DWD/DWS model files, semantic-layer objects,
knowledge references, evaluation cases, or correction records. Those are
governed and belong to `/chatbi-maintain-model`, `/chatbi-maintain-knowledge`,
`/chatbi-evaluate`, `/chatbi-correction` respectively.

## 6. Reused primitives (do NOT reinvent)

| Primitive | Location | Why bootstrap reuses it |
| --- | --- | --- |
| `config._contains_secret_argv` | `config.py:174-186` | Rejects `--password`/`--token`/`--api-key`/`--secret` in argv (SEC-003). Already applied by `load_effective_config`. |
| `config._contains_matching_string` + `_SECRET_VALUE` | `config.py:26-29`, `163-171`, `402-426` | Rejects secret values anywhere in local/shared config (SEC-003). |
| `config.load_effective_config` | `config.py:385-430` | Validates the merged shared+local config (schema + policy + secrets). The single validation entry point. |
| `config.EffectiveConfig` | `config.py:353-382` | Immutable read-only view passed to adapter selection. |
| `adapters.select_adapter` | `adapters/__init__.py:495-718` | managed->cli->fixture chain. Picks `cli:mysql` for `kind="query"`. |
| `adapters.CliAdapter` | `adapters/__init__.py:317-463` | `shell=False`, fixed cwd=Workspace, whitelisted env, allowlisted executable. The only sanctioned subprocess launcher. |
| `adapters.build_cli_env` | `adapters/__init__.py:146-162` | Env whitelist: locale + safe PATH + declared credential env var NAMES. No leaks. |
| `adapters.validate_cli_argv` | `adapters/__init__.py:89-106` | Rejects shell metacharacters + sensitive flags before launch. |
| `adapters.resolve_executable` | `adapters/__init__.py:109-143` | Allowlist resolution; PATH only produces a candidate. |
| `chatbi-harness.schema.json` | `cli_adapters:161-184`, `adapters.query:82-89`, `path_bindings:156-160` | The schema that gates `argv`/`credential_env_names`/adapter-ID shape. |
| `gates.fail_closed` | `gates.py:153-167` | Convert unexpected failures into deterministic blocking decisions (HOOK-004). |
| `gates.GateError` / `gates.GateDecision` | `gates.py:143-150`, `52-140` | Sanitized exception boundary + pass/warn/block decision. |
| Test helpers | `tests/harness/test_diagnostics.py:26-47` (`working_directory`, `install_domain_contract`) | The new `tests/harness/test_bootstrap.py` mirrors these helpers (plan §"Files to add"). |

**bootstrap's own lib surface (AS_BUILT, `harness/.claude/lib/chatbi_harness/bootstrap.py`):**
`build_mysql_adapter_spec(host, port, user, *, database, credential_env_name=None)`,
`merge_local_config(existing, *, path_bindings=None, cli_adapters=None)`, and a
frozen-slots `SourceInventory` dataclass (plus nested `SourceTable` /
`SourceColumn`). These are thin deterministic wrappers over the primitives
above; they do not duplicate secret/argv validation (delegate to `config` /
`adapters`). Exported via `chatbi_harness.__init__`. Verified offline by
`tests/harness/test_bootstrap.py` (30 cases).

## 7. Rules cited (NO new rule; 46 stays 46)

The domain model `harness/docs/chatbi-harness-domain-model.md` defines 46
unique rule IDs (verified: `grep -oE` over the file yields exactly 46 unique
matches). bootstrap cites 8 existing rules; it adds none.

| Rule | Where bootstrap touches it |
| --- | --- |
| SCOPE-001 | One Workspace; candidate writes limited to it (Steps 2, 5, 7). Local config + scaffold + inventory stay inside the Workspace. |
| SCOPE-002 | If a Business Codebase path_binding is supplied, it must use the configured read-only alias; bootstrap never executes/edits the external root. |
| SEC-001 | Sandbox must fail-closed (`config.py:288-294`); bootstrap does not elevate access and requests minimum authorization for the mysql CLI. |
| SEC-003 | No credentials/PII/absolute paths in output; password = env var NAME, never value. Enforced by `_contains_secret_argv` + `_SECRET_VALUE` + schema `^[A-Z_][A-Z0-9_]*$`. |
| PORT-001 | `cli:mysql` must resolve to the allowlist; no fixture as production fallback; portable references in any report. |
| SEM-003 | bootstrap does NOT create governed models or approve metrics; protected actions stay with the human owner. This is the boundary that keeps bootstrap out of `/chatbi-maintain-model` territory. |
| DOC-001 | bootstrap stubs the companion doc but does not author governed knowledge; governed references stay co-located with models and routed through `/chatbi-maintain-knowledge`. |
| HOOK-004 | Deterministic fail-closed gates; `load_effective_config` schema validation is the gate. bootstrap does not bypass it. |

**46-rule count unchanged.** No rule is added, renamed, or reworded. The
`validate_domain_contract` gate (`gates.py:170-233`) will continue to pass
because the contract artifacts (`CLAUDE.md`, `CONTEXT.md`, the three rule
files, the domain model) are not modified by bootstrap.

## 8. MySQL-only v1 scope + out-of-scope

**In scope (v1):**
- MySQL source connection (host/port/user/source_db).
- dbt-mysql scaffold layout (`models/{ods,dwd,dws,dim}/` + `dbt_project.yml`).
- Password = env var NAME or empty array for local no-password root.
- Single `cli:mysql` adapter registered in `adapters.query`.
- `CREATE DATABASE IF NOT EXISTS dw` (non-destructive).
- INFORMATION_SCHEMA introspection of the source DB.
- source_inventory.json + companion doc stub.

**Out of scope (v1), per plan §"Out of scope":**
- **Bulk ODS DDL generation.** bootstrap produces inventory only; ODS model
  creation is governed via `/chatbi-maintain-model`. Future: a `--generate-ods`
  flag.
- **Non-MySQL engines.** StarRocks is MySQL-protocol-compatible so `cli:mysql`
  largely works unmodified; Hive and other engines need their own adapter
  (future).
- **Live MySQL unit tests.** No MySQL server in CI; the runbook + manual E2E
  cover the live path. Unit tests cover the deterministic lib surface
  (`build_mysql_adapter_spec`, `merge_local_config`) only.
- **Live/deterministic hook registration.** bootstrap is invoked via
  SessionStart-only command routing; it does not register new PreToolUse /
  PostToolUse hooks (HOOK-001/003/004 unchanged).

## 9. Hand-off to `/chatbi-maintain-model`

bootstrap ends by pointing at `.chatbi/bootstrap/source_inventory.json`.
`/chatbi-maintain-model` (command at `harness/.claude/commands/chatbi-maintain-model.md`,
runbook at `harness/.claude/skills/chatbi-maintenance/SKILL.md`) picks up
from there:

1. For each table in `source_inventory.json`, the operator invokes
   `/chatbi-maintain-model` with a model-change request targeting that table.
2. `/chatbi-maintain-model` classifies the change (`change_kind = model`),
   builds an `ImpactManifest` (`chatbi_harness.impact.build_impact_manifest`),
   produces the candidate ODS model under `models/ods/`, and drives the
   model-metadata-semantic-reference-Skill-tests-downstream-eval sync gate
   (DOC-004).
3. Protected actions (metric approval, production publish, destructive
   migration) remain human-owner-only (SEM-003); bootstrap never reaches
   this layer.
4. The ODS model is delivered only after the sync gate passes and the Cycle 3
   `stop_gate` admits it.

The inventory JSON is therefore the **contract surface** between bootstrap
(infra setup) and maintain-model (governed model creation). Its shape must be
stable and self-describing (table names, column names, types, PKs) so
maintain-model can consume it without re-introspecting the source DB.

## 10. Files added / updated (AS_BUILT 2026-07-27)

**Added:**
- `harness/.claude/commands/chatbi-bootstrap.md` - command; mirrors
  `chatbi-maintain-model.md` structure (trust boundary, bind skill,
  procedure, footer, rules). States INFRA SETUP trust boundary explicitly.
- `harness/.claude/skills/chatbi-bootstrap/SKILL.md` - procedural runbook;
  mirrors `chatbi-maintenance/SKILL.md` shape. The 9 steps (design §4
  specified 8; the SKILL elevated the mysql-path / `cli_allowlist`
  confirmation into its own Step 2 as a security-prominence refinement). Handles all 5 open risks (cli_allowlist confirmation,
  stdout_raw untrusted, shared-config append discipline, dw pre-check,
  StarRocks unverified).
- `harness/.claude/lib/chatbi_harness/bootstrap.py` - deterministic module
  (`build_mysql_adapter_spec`, `merge_local_config`, frozen-slots
  `SourceInventory`/`SourceTable`/`SourceColumn`; fail-closed `GateError` on
  bad input). `from __future__ import annotations`, `@dataclass(frozen=True,
  slots=True)`.
- `tests/harness/test_bootstrap.py` - unittest (30 cases); mirrors
  `tests/harness/test_diagnostics.py` helpers (`install_domain_contract`,
  `working_directory`, `WORKSPACE_ROOT`/`HARNESS_LIB`/`sys.path.insert`).
  Live MySQL NOT unit-tested.

**Updated:**
- `harness/.claude/lib/chatbi_harness/__init__.py` - exports
  `build_mysql_adapter_spec`, `merge_local_config`, `SourceInventory`.
- `build-product.sh:35-39` - `chatbi-bootstrap` added to the command loop
  (comment updated "6" -> "7 chatbi commands"); `chatbi_harness.bootstrap`
  added to the import canary at `:59-62`.
- `harness/CLAUDE.md` request-routing table - +1 row for bootstrap (after
  `/chatbi-init`). 113 lines (under the 200-line budget).
- `harness/product-README.md:3` - "Six" -> "Seven" commands + table row
  (after `/chatbi-init`) + Install step 5.
- `docs/harness/installation.md` - "Bootstrap a from-zero Warehouse" subsection
  after "Install and run" (MySQL-only v1, evidence status).
- `docs/harness/README.md` - §2.4 `/chatbi-bootstrap` command entry point
  (lib surface VERIFIED OFFLINE, live MySQL NOT YET EXERCISED).
