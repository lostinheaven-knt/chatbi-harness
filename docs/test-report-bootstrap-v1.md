# Test Report: `/chatbi-bootstrap` (MySQL-only v1)

> Status: **ALL_PASSED**. Authored by the orchestrator from the test-agent's
> inline output (the test-agent's operational guardrail returns its report as
> text rather than writing a `.md`). Test run occurred after step 7.b
> implementation, before the 3 doc-nit fixes (which are doc-only and did not
> affect any test). Live smoke ran against the user's real local MySQL.
> Date: 2026-07-27.

## 0. Scope

- Offline test suite (primary signal).
- Product build (primary signal).
- Lib smoke (deterministic, no DB).
- Live smoke (non-destructive, against 127.0.0.1:3306 `public`).

## 1. Offline test suite

- Command: `PYTHONPATH=harness/.claude/lib python3 -B -m unittest discover -s tests/harness`
- Expected: 563 passed, 1 skipped (OS-sandbox BLOCKING GAP skip, baseline).
- Actual: `Ran 563 tests in 25.334s` / `OK (skipped=1)`.
- Result: **PASS** (533 baseline + 30 new `test_bootstrap.py` cases).

The 30 new cases statically verified to cover `technical-design-bootstrap.md`
§5.1-5.5: `build_mysql_adapter_spec` shape + validation (non-empty
host/user/database, port 1-65535, env-name `^[A-Z_][A-Z0-9_]*$`, no password
value, `GateError` on violation); `merge_local_config` preserve/add/no-clobber;
secret rejection; `SourceInventory` shape; spec round-trip through
`load_effective_config`. No live MySQL in unit tests.

## 2. Product build

- Command: `./build-product.sh`
- Expected: import OK, canary sweep clean, no dev-only leak, `chatbi-bootstrap`
  present in `../chatbi`.
- Actual:
  - `import OK` (canary imports `chatbi_harness.bootstrap` alongside the
    existing modules).
  - Canary sweep (no machine path / secret): no output = clean.
  - Dev-only files absent: no `LEAK` lines printed = clean.
  - `chatbi-bootstrap.md` present at
    `/Users/admin/Downloads/workspace/chatbi/.claude/commands/chatbi-bootstrap.md`.
  - `e2e-state.py` appears in `../chatbi/` but is **intentionally shipped**
    (`build-product.sh:53` explicitly `cp`s it with `CLAUDE.md`/`CONTEXT.md`),
    not a dev-only leak.
- Result: **PASS** (clean, no leak).

## 3. Lib smoke (deterministic, no DB)

- Command: `PYTHONPATH=harness/.claude/lib python3 -B -c "..."` invoking
  `build_mysql_adapter_spec` + `merge_local_config`.
- Expected: argv includes `--database=public`; `credential_env_names=[]`; merge
  preserves `existing` and adds `fypro` + `mysql`.
- Actual:
  - `argv: ['mysql', '--host', '127.0.0.1', '--port', '3306', '--user', 'root', '--database=public']` - includes `--database=public`. PASS.
  - `credential_env_names: []` - no password value anywhere. PASS.
  - `merged path_bindings: {'existing': '/x', 'fypro': '/y'}` - preserves
    `existing`, adds `fypro`. PASS.
  - `merged cli_adapters keys: ['mysql']` - adds `mysql`. PASS.
- Result: **PASS**.
- Note: the smoke snippet in the test task used a camelCase `credentialEnvName`
  typo, which raised `TypeError`. The actual API is snake_case
  `credential_env_name` (matches `technical-design-bootstrap.md` §3.1 and
  `bootstrap.py:67-74`). Re-ran with the correct parameter and it passed. This
  was a typo in the task prompt, **not** a code bug.

## 4. Live smoke (non-destructive, mysql CLI reachable)

- mysql CLI: `/opt/homebrew/bin/mysql`, `Ver 9.7.1 for macos15.7 on arm64 (Homebrew)`.
- Connection: `mysql -h 127.0.0.1 -P 3306 -u root` (no password; read-only on
  `public`; non-destructive on `dw`).
- Mirrors bootstrap steps 5-6 (option a: `--execute`).

| Step | Command | Expected | Actual | Result |
| --- | --- | --- | --- | --- |
| 4a | `CREATE DATABASE IF NOT EXISTS dw` | exit 0, non-destructive | `exit=0` | PASS |
| 4b | `SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='public'` | 125 (per context) | `n=125` | PASS |
| 4c | `SELECT TABLE_NAME FROM ... WHERE TABLE_SCHEMA='public' LIMIT 3` | 3 sample names | `t_mq_msg_receive_record`, `t_mq_msg_send_record`, `t_plg_account_credits_account` | PASS |
| 4d | `SELECT COUNT(*) FROM ... WHERE TABLE_SCHEMA='dw'` (risk #4 pre-check) | 0 after create | `n=0` | PASS |

- No `DROP` executed. No writes to `public`. `dw` left with 0 tables (ready for
  bootstrap).
- Result: **PASS**.

The live smoke confirms the option-(a) gap resolution works on a real mysql
CLI: `--execute=<SQL>` (single statement, no `;`) drives `CREATE DATABASE` and
`INFORMATION_SCHEMA` reads end-to-end, and risk #4's pre-check query returns
the expected 0-table state for a fresh `dw`.

## 5. Doc nits (carried from `optimization-checklist-bootstrap-v1.md`)

These 3 findings are documentation-level (no code impact). They were carried
forward from the step 7.c eval and **subsequently fixed** in a follow-up
coder-agent pass (tests unaffected):

1. **[MINOR-1]** `harness/.claude/commands/chatbi-bootstrap.md:36` said
   "8-step procedure" but the bound SKILL has 9 steps (Step 2 elevated
   `cli_allowlist`/mysql-path confirmation). Fixed: "8-step" -> "9-step".
2. **[NIT-1]** `docs/feature-flow-bootstrap-v1.md:395-397` cross-referenced the
   step count stalely. Fixed: notes design §4 specified 8 steps, SKILL refined
   to 9 by elevating mysql-path/cli_allowlist confirmation into Step 2.
3. **[NIT-2]** `harness/.claude/skills/chatbi-bootstrap/SKILL.md:166` hardcoded
   `TABLE_SCHEMA='public'` in Step 7 introspection SQL. Fixed: ->
   `TABLE_SCHEMA='<source_db>'` placeholder. (Step 6.2's `TABLE_SCHEMA='dw'`
   unchanged - `dw` is the fixed target.)

## 6. Status

- **STATUS: ALL_PASSED** (offline suite green + build clean + lib smoke ok +
  live smoke done).
- 0 failures, 0 blockers.
- 3 doc nits: fixed after this test run (doc-only, no test impact).
- 5 open design risks: all handled in implementation (see
  `technical-design-bootstrap.md` §10.1).

## 7. Out of scope (not covered by this report)

- Live MySQL unit tests (no DB server in CI; covered by this manual live smoke).
- Full `/chatbi-bootstrap` command invocation inside a real Claude Code session
  (sessionB E2E - the user runs `/chatbi-bootstrap` in
  `/Users/admin/Downloads/workspace/chatbi` per the sessionB runbook; this
  report covers the lib + adapter + mysql-CLI path, not the command routing).
- Non-MySQL engines (v1 is MySQL-only).
