# Modification: `/chatbi-bootstrap` (MySQL-only v1)

> Status: DESIGN. Per-file change list for the `/chatbi-bootstrap` command.
> Grounded in `.scratch/chatbi-bootstrap-plan.md` (approved) and
> `docs/feature-flow-bootstrap-v1.md` (as-built scan of the harness source under
> `harness/`). No bootstrap code exists yet; this doc describes **what changes**
> per file. For **how** (API contracts, gap resolution, test plan) see the
> companion `docs/technical-design-bootstrap.md`.

## 0. Context

`/chatbi-bootstrap` adds a 7th slash command to the harness. It is INFRA SETUP
only (config + `dw` DB + dbt-mysql scaffold + source schema inventory), NOT
governed model creation. Model creation stays in `/chatbi-maintain-model`. The
46-rule count is unchanged; bootstrap cites 8 existing rules and adds none.

The one design gap left open by the feature-flow scan (CliAdapter JSON-stdin vs
mysql SQL-stdin) is resolved upstream of this doc by **decision 介入点① option
(a)**: bootstrap constructs a per-operation `CliAdapter` whose argv embeds
`--execute=<SQL>` (single statement, no trailing `;`). See
`docs/technical-design-bootstrap.md` §2 for the full resolution.

## 1. Lib module — `harness/.claude/lib/chatbi_harness/`

### ADD `harness/.claude/lib/chatbi_harness/bootstrap.py`
- New small deterministic module. Thin wrappers over existing `config` /
  `adapters` primitives; does NOT duplicate secret/argv validation (delegates to
  `config._contains_secret_argv` / `adapters.validate_cli_argv` via
  `load_effective_config` and the `CliAdapter` constructor).
- Public surface (see `docs/technical-design-bootstrap.md` §3 for full contracts):
  - `build_mysql_adapter_spec(host, port, user, *, database, credential_env_name=None) -> dict`
    — returns `{"argv": [...], "credential_env_names": [...]}`. Validates
    host/user/database non-empty, port 1-65535, `credential_env_name` matches
    `^[A-Z_][A-Z0-9_]*$` if given. Never includes a password value. Raises
    `GateError` on violation (mirrors `config._config_gate_error` style,
    `config.py:60-74`).
  - `merge_local_config(existing, *, path_bindings=None, cli_adapters=None) -> dict`
    — merges preserving existing keys; only adds/overwrites supplied entries.
    Output stays limited to `path_bindings` + `cli_adapters` (the only keys
    `load_effective_config` permits in local config, `config.py:410-417`).
  - `SourceInventory` — `@dataclass(frozen=True, slots=True)` (plus nested
    frozen-slots `SourceTable` / `SourceColumn` helpers) capturing tables,
    columns, PKs, types. `to_dict()` produces the versioned, self-describing
    shape written to `.chatbi/bootstrap/source_inventory.json` (the hand-off
    contract surface to `/chatbi-maintain-model`). Mirrors the
    `CapabilitySnapshot`/`DiagnosticResult` frozen-slots + `to_dict` pattern
    (`diagnostics.py:44-88`, `312-382`).

### MODIFY `harness/.claude/lib/chatbi_harness/__init__.py`
- Add `from .bootstrap import SourceInventory, build_mysql_adapter_spec, merge_local_config`.
- Add `"SourceInventory"`, `"build_mysql_adapter_spec"`, `"merge_local_config"`
  to `__all__` (keeps alphabetic-ish ordering consistent with the existing list,
  `__init__.py:14-28`).
- No other import changes; `bootstrap.py` itself imports from `.config` /
  `.gates` only (no new third-party deps).

## 2. Commands — `harness/.claude/commands/`

### ADD `harness/.claude/commands/chatbi-bootstrap.md`
- New slash command. Mirrors the structure of
  `harness/.claude/commands/chatbi-maintain-model.md`: YAML frontmatter
  (`description`, `argument-hint`), `## 0. Trust boundary`, `## 1. Bind to the
  runbook`, procedure sections, footer, and a `## Rules` / non-goals section.
- Frontmatter `argument-hint` accepts the MySQL source connection
  (host/port/user/source_db) + optional credential env var NAME + optional
  Business Codebase path_binding alias.
- Trust boundary section states: MAY write local config, append one `cli:mysql`
  to shared `adapters.query`, `CREATE DATABASE IF NOT EXISTS dw`, introspect
  source schema, scaffold project dirs, write source-inventory JSON; MUST NOT
  create governed models, approve metrics, publish, or run destructive migration
  (SEM-003).
- Binds the agent to `skills/chatbi-bootstrap/SKILL.md` (mirrors
  `chatbi-maintain-model.md:20-22`).
- Cites rules SCOPE-001, SCOPE-002, SEC-001, SEC-003, PORT-001, SEM-003,
  DOC-001, HOOK-004 (no new rule).

## 3. Skills — `harness/.claude/skills/`

### ADD `harness/.claude/skills/chatbi-bootstrap/SKILL.md`
- New procedural runbook. Mirrors the shape of
  `harness/.claude/skills/chatbi-maintenance/SKILL.md` (frontmatter `name` +
  `description`, numbered procedure sections, footer). Carries reusable
  procedure, not easily-stale facts.
- Documents the 8-step procedure (see `docs/technical-design-bootstrap.md` §4).
  Steps 5-6 use **option (a)**: for each SQL operation the runbook appends
  `--execute=<SQL>` to the base `cli_adapters.mysql` argv and constructs a
  per-operation `CliAdapter` (single statement, no `;`, no shell metacharacters).
- Cites the primitives it reuses: `config.load_effective_config`
  (`config.py:385-430`), `adapters.select_adapter` / `CliAdapter` /
  `build_cli_env` / `validate_cli_argv` / `resolve_executable`
  (`adapters/__init__.py`), `chatbi-harness.schema.json`, and
  `gates.fail_closed` (`gates.py:153-167`).
- Notes the runtime-constructed argv still goes through the `CliAdapter`
  constructor's `validate_cli_argv` re-check (`adapters/__init__.py:352-354`).

## 4. Tests — `tests/harness/`

### ADD `tests/harness/test_bootstrap.py`
- New `unittest` module. Mirrors `tests/harness/test_diagnostics.py` helpers:
  `working_directory` contextmanager (`test_diagnostics.py:26-33`),
  `install_domain_contract` (`:36-47`), and the `WORKSPACE_ROOT` / `HARNESS_LIB`
  / `sys.path.insert` bootstrap block (`:15-17`).
- Cases (see `docs/technical-design-bootstrap.md` §5 for full test plan):
  - `build_mysql_adapter_spec`: correct argv shape + env name; no password value
    anywhere; rejects empty host/user/database; rejects port <1 / >65535 /
    non-int; rejects `credential_env_name` not matching `^[A-Z_][A-Z0-9_]*$`.
  - `merge_local_config`: preserves existing keys, adds new, does not clobber
    unrelated adapters/bindings.
  - Spec round-trips through `load_effective_config` (writes local config from
    the spec + a ready shared config, then loads and asserts `cli_adapters.mysql`
    is present and validated).
  - Password-in-argv / password-value -> `GateError` (SEC-003).
- NO live MySQL connection in unit tests (no DB in CI); the live path is covered
  by the runbook + manual E2E (out of scope, §7 of the technical-design doc).

## 5. Build — `build-product.sh`

### MODIFY `build-product.sh`
- **Command loop** (`:36-38`): add `chatbi-bootstrap` to the `for c in ...` list.
  Update the comment on `:35` from "the 6 chatbi commands" to "the 7 chatbi
  commands".
- **Import canary** (`:59-62`): add `chatbi_harness.bootstrap` to the
  `PYTHONPATH=.claude/lib python3 -B -c "import ...; print('import OK')"` line
  so a broken `bootstrap.py` (e.g. bad import) fails the build.
- No change needed for skills/lib copy: `rsync -a .../skills/` (`:45`) and
  `rsync -a .../lib/` (`:25`) already pick up the new `chatbi-bootstrap/SKILL.md`
  and `bootstrap.py` automatically.
- No change to the dev-only leak sweep (`:64-74`): `chatbi-bootstrap` is a
  product command, not dev-only, so it must NOT appear in the leak list.

## 6. Contract & product docs

### MODIFY `harness/CLAUDE.md`
- **Request-routing table** (`:72-79`): add one row. The table columns are
  `Request | Expected route | Mandatory outcome`. New row:
  `| Bootstrap a Warehouse | \`/chatbi-bootstrap\` | local config, dw DB, source inventory, project scaffold |`.
- Place the row after `/chatbi-init` (logical ordering: install -> bootstrap ->
  analyze -> maintain) or at the end; either is acceptable as long as the table
  stays well-formed.
- **200-line budget**: `validate_domain_contract` (`gates.py:194-200`) rejects
  `CLAUDE.md` > 200 lines. Adding one table row (~1 line) is within budget;
  verify line count after edit.
- No rule-count change; the 46 governed rule IDs in
  `docs/chatbi-harness-domain-model.md` are untouched.

### MODIFY `harness/product-README.md`
- **Command count** (`:3`): "Six slash commands, 46 enforced rules" ->
  "Seven slash commands, 46 enforced rules" (rule count unchanged).
- **Commands table** (`:33-40`): add a row for `/chatbi-bootstrap`:
  `| \`/chatbi-bootstrap\` | scaffold local Warehouse (config + dw + inventory, MySQL-only v1) |`.
- **Install section** (`:13-29`): after the `/chatbi-init` step (`:29`), add a
  brief note that a from-zero Warehouse is then scaffolded via
  `/chatbi-bootstrap` (MySQL-only v1), which writes local config, creates the
  `dw` database, and produces a source inventory for `/chatbi-maintain-model`.
- **Where to look** (`:53-58`): optionally mention the bootstrap runbook at
  `.claude/skills/chatbi-bootstrap/SKILL.md`; keep it to one bullet to avoid
  bloat.

### MODIFY `harness/docs/harness/installation.md`
- Add a short subsection (e.g. after the "Install and run" steps, `:39-57`)
  noting that `/chatbi-bootstrap` is the post-init step for a from-zero
  Warehouse: it is MySQL-only v1, writes `.claude/chatbi-harness.local.json`
  (mysql adapter + optional path_binding), appends `cli:mysql` to shared
  `adapters.query`, and requires the user to confirm the exact absolute `mysql`
  executable path (mirroring the `CHATBI_PYTHON` / `claude_executable`
  confirmation pattern, `installation.md:22-29`, `:50-54`).
- Keep the evidence-status framing: live MySQL execution is NOT YET EXERCISED in
  CI; the deterministic lib surface is VERIFIED OFFLINE.

### MODIFY `harness/docs/harness/README.md`
- This README is `CODE_AS_READ on 2026-07-22` and lists Cycle 1 entry points +
  later-cycle capabilities (`:97-150`). Add `/chatbi-bootstrap` as a new entry
  point (a 7th command), with a one-paragraph note that it is MySQL-only v1 and
  that its deterministic lib surface (`build_mysql_adapter_spec`,
  `merge_local_config`, `SourceInventory`) is VERIFIED OFFLINE while live MySQL
  execution is NOT YET EXERCISED.
- Update the "Cycle 1 always reports `production_ready=false`" boundary note
  (`:175-182`) only if needed; bootstrap does not change `production_ready`
  semantics, so no edit required there.
- The document map (`:200-208`) may gain a row for the bootstrap runbook if a
  dedicated doc is added later; for v1 the SKILL is the doc, so no new map row is
  required.

## 7. Files NOT changed (and why)

These are intentionally untouched to keep the 46-rule contract and the
deterministic gate surface stable:

- `harness/.claude/schemas/chatbi-harness.schema.json` — the existing schema
  already permits `cli_adapters.mysql` + `path_bindings` + `adapters.query`
  entries (`:82-89`, `:156-184`); no new field is needed.
- `harness/.claude/lib/chatbi_harness/config.py`, `gates.py`, `diagnostics.py`,
  `adapters/__init__.py` — reused as-is; bootstrap delegates to them.
- `harness/.claude/hooks/*`, `harness/.claude/settings.json` — bootstrap is
  SessionStart-only command routing; no new hook (HOOK-001/003/004 unchanged).
- `harness/.claude/rules/*`, `harness/docs/chatbi-harness-domain-model.md` — no
  rule added/renamed/reworded (46 stays 46).
- `harness/.claude/chatbi-harness.json` (shared config) — bootstrap appends
  `cli:mysql` to `adapters.query` at runtime if absent; the shipped shared
  config stays `adapters.query: []` (idempotent runtime write, not a source
  edit).
- `harness/CONTEXT.md`, `harness/e2e-state.py`, `harness/install.sh` —
  unchanged.

## 8. Open points for the implementer (non-blocking)

1. **`cli_allowlist` sourcing.** `resolve_executable(argv[0], cli_allowlist)`
   (`adapters/__init__.py:109-143,615`) requires a non-empty allowlist, but the
   schema has no `cli_allowlist` field and `select_adapter` defaults it to `()`
   (`:501`). bootstrap is the first command to drive a cli adapter end-to-end.
   The SKILL must obtain the user-confirmed absolute `mysql` realpath and pass it
   as a single-element allowlist — mirror `/chatbi-init`'s `claude_executable`
   confirmation (`chatbi-init.md:38-42`, `installation.md:50-54`). Flagged in
   `docs/technical-design-bootstrap.md` §6.
2. **Shared-config append discipline.** The plan lists `merge_local_config` for
   the LOCAL layer only; the shared `adapters.query` idempotent append (Step 3)
   is performed by the SKILL. The writer must round-trip the shared JSON
   preserving UTF-8 + no duplicate keys + deterministic separators (the reader
   `_load_json` enforces these on read, `config.py:86-144`). Recommend the
   implementer reuse `json.dumps(..., ensure_ascii=False, sort_keys=True,
   allow_nan=False, separators=(",", ":"))` to match `EffectiveConfig.to_json`
   (`config.py:375-382`).
3. **`dw` exists-with-tables warning.** Step 5 says "warn (don't clobber) if
   `dw` exists with tables". The SKILL must run a pre-check
   (`INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='dw'`) and emit a warn (not
   block) if the table count > 0 before issuing `CREATE DATABASE IF NOT EXISTS
   dw`.

STATUS: MOD_DESIGN_DONE
