# Optimization Checklist: `/chatbi-bootstrap` v1 (design vs as-built)

> Step 7.c evaluation. Compares `docs/technical-design-bootstrap.md` +
> `docs/modification-bootstrap.md` (design) against the as-built implementation
> (`harness/.claude/lib/chatbi_harness/bootstrap.py`, `__init__.py`,
> `commands/chatbi-bootstrap.md`, `skills/chatbi-bootstrap/SKILL.md`,
> `tests/harness/test_bootstrap.py`, `build-product.sh`, and the 4 updated docs).
>
> Method: static design review. The `grill-with-docs` / `writing-plans` skills
> target requirement-grilling and TDD implementation plans respectively; this
> legacy-flow step is a design-vs-as-built evaluation, so standard design review
> was applied per the task's skill-fallback note.

## Verification runs

| Check | Method | Result |
| --- | --- | --- |
| 46-rule count unchanged | `Grep` unique rule IDs in `harness/docs/chatbi-harness-domain-model.md` | **46 unique** (SCOPE-001..003, SEC-001..003, REQ-001..004, SEM-001..003, RAW-001..003, SRC-001..002, DOC-001..005, PORT-001, QLT-001, REV-001..003, ANS-001..003, EVAL-001..005, ABL-001..002, FBK-001..003, HOOK-001..005). No rule added/renamed/reworded. |
| test_bootstrap.py case count | `Grep "def test_"` | **30 methods** (matches design §5 test plan + feature-flow §10 claim). |
| Unittest suite execution | **NOT RUN** | This agent has no shell-execution tool. Test method bodies were statically verified to cover every case in design §5.1-5.5; imports resolve to real symbols (`SourceColumn`/`SourceInventory`/`SourceTable`/`build_mysql_adapter_spec`/`merge_local_config` in `bootstrap.py`; `load_effective_config`/`_SECRET_ARG` in `config.py`; `GateError` in `gates.py`). |
| `./build-product.sh` execution | **NOT RUN** | Statically verified: command loop (`build-product.sh:36-38`) includes `chatbi-bootstrap`; comment (`:35`) says "7 chatbi commands"; import canary (`:60-63`) includes `chatbi_harness.bootstrap`; dev-only leak sweep (`:68-75`) correctly excludes `chatbi-bootstrap` (it is a product command). |
| feature-flow AS_BUILT | Read `docs/feature-flow-bootstrap-v1.md` header | Header says `Status: AS_BUILT (2026-07-27)`. |

## 1. Lib API match (design §3 vs `bootstrap.py`)

### 1.1 `build_mysql_adapter_spec` - MATCH

| Design contract (§3.1) | As-built (`bootstrap.py:67-157`) | Verdict |
| --- | --- | --- |
| Signature `(host, port, user, *, database, credential_env_name=None) -> dict` | Identical (`:67-74`) | MATCH |
| Returns `{"argv": [...], "credential_env_names": [...]}` only | Returns exactly those two keys (`:147-157`); test asserts `set(spec) == {"argv","credential_env_names"}` | MATCH |
| `argv == ["mysql","--host",h,"--port",str(p),"--user",u,"--database="+d]` | Identical (`:147-153`) | MATCH |
| `credential_env_names == [name]` or `[]` | Identical (`:154-156`) | MATCH |
| host non-empty str -> GateError HOOK-004 | `:88-94` | MATCH |
| port int (not bool), 1-65535 -> GateError HOOK-004 | `:97-110` (explicit `isinstance(port, bool)` rejection) | MATCH |
| user non-empty str -> GateError HOOK-004 | `:112-118` | MATCH |
| database non-empty str -> GateError HOOK-004 | `:120-126` | MATCH |
| credential_env_name `^[A-Z_][A-Z0-9_]*$` -> GateError SEC-003+HOOK-004 | `:131-146` (uses `_CREDENTIAL_NAME.fullmatch`) | MATCH |
| Never a password value | Only env var NAME carried; tests assert no `password=`/`secret=`/`token=`/`api_key=`/`sk-` in serialized spec | MATCH |
| Error style mirrors `config._config_gate_error` | `_bootstrap_gate_error` (`:44-64`) builds `GateDecision.block` wrapped in `GateError` | MATCH |
| `from __future__ import annotations` | Present (`:24`) | MATCH |

### 1.2 `merge_local_config` - MATCH (signature widened)

| Design contract (§3.2) | As-built (`bootstrap.py:160-197`) | Verdict |
| --- | --- | --- |
| Signature `(existing: dict, *, path_bindings=None, cli_adapters=None) -> dict` | `(existing: dict \| None, *, ...)` (`:160-165`) | Widened to `dict \| None` - consistent with design §3.2 semantics "or `{}` if `existing` is `None`". Improvement, not regression. |
| Preserves existing path_bindings / cli_adapters | `:182-189` copies only the two permitted keys | MATCH |
| Adds/overwrites supplied entries | `:190-193` | MATCH |
| Drops non-local top-level keys (SEM-003/HOOK-004) | `:178-189` only reads `path_bindings` + `cli_adapters` from existing | MATCH |
| No mutation of `existing` | Uses `dict(...)` copies, never touches input | MATCH |
| Output limited to `path_bindings` + `cli_adapters` | `:194-197` returns exactly those two keys | MATCH |

### 1.3 `SourceInventory` (+ nested) - MATCH

| Design contract (§3.3) | As-built (`bootstrap.py:200-250`) | Verdict |
| --- | --- | --- |
| `@dataclass(frozen=True, slots=True)` on all three | `:200, 209, 217` | MATCH |
| `SourceColumn{name, data_type, is_primary_key}` | `:201-206` | MATCH |
| `SourceTable{name, columns: tuple}` | `:210-214` | MATCH |
| `SourceInventory{source_database, tables: tuple}` | `:218-230` | MATCH |
| `to_dict()` shape with `schema_version: 1` | `:232-250` produces `schema_version`, `source_database`, `tables[].columns[]{name,data_type,is_primary_key}` | MATCH |
| Mirrors `CapabilitySnapshot`/`DiagnosticResult` pattern | frozen-slots + `to_dict` | MATCH |
| Only `SourceInventory` exported via `__init__.py` | `__init__.py:3-7` re-exports only `SourceInventory` (not `SourceTable`/`SourceColumn`) | MATCH |

Note: `bootstrap.py:253-259` `__all__` includes all three classes. This is correct - the runbook/tests must construct `SourceTable`/`SourceColumn` to build an inventory. The package-level `__init__.py` is the export boundary the design constrains, and it matches.

### 1.4 `__init__.py` exports - MATCH

`__init__.py:3-7` adds `from .bootstrap import SourceInventory, build_mysql_adapter_spec, merge_local_config`. `__all__` (`:19-36`) includes `"SourceInventory"`, `"build_mysql_adapter_spec"`, `"merge_local_config"` in alphabetic order. Matches modification doc §1.

### 1.5 No `adapters` import - MATCH

`bootstrap.py` imports only `re`, `dataclass`, and `from .gates import GateDecision, GateError` (`:24-29`). It does NOT import `adapters`. The `_CREDENTIAL_NAME` regex is re-declared locally (`:36`) with a comment explaining why. Matches design §3 ("It does NOT import adapters").

## 2. Gap resolution - option (a): per-operation CliAdapter with `--execute=<SQL>`

| Design §2 requirement | As-built SKILL location | Verdict |
| --- | --- | --- |
| Per-operation CliAdapter, `--execute=<SQL>` single statement | SKILL §6 (Step 6.2, 6.6) + §7 (Step 7.1) | MATCH |
| No trailing `;` | SKILL §6.2 lists `;` in the prohibited metachar set | MATCH |
| No shell metacharacters | SKILL §6.2 cites `_SHELL_METACHARACTERS = frozenset("\|;&\`$<>\\\n\r")` | MATCH |
| Re-validates via CliAdapter ctor (`validate_cli_argv`) | SKILL §6.2 cites `adapters/__init__.py:352-354` | MATCH |
| `resolve_executable` with confirmed allowlist | SKILL §6.3 (`cli_allowlist=(confirmed_mysql_executable,)`) | MATCH |
| `build_cli_env(credential_env_names)` | SKILL §6.3 | MATCH |
| `stdout_raw` treated as untrusted text | SKILL §6.4 + command doc §5 | MATCH |
| `--execute` is runtime argv (not stored config) | Base argv from `build_mysql_adapter_spec` has no `--execute`; appended at runtime | MATCH |

Verified against `adapters/__init__.py`: `_SHELL_METACHARACTERS` (`:63`), `validate_cli_argv` (`:89-106`), `CliAdapter.__init__` re-runs `validate_cli_argv` (`:352-354`), `resolve_executable` (`:109-143`), `build_cli_env` (`:146-162`), `_parse_stdout` tags `untrusted: True` (`:422-432`). All claims in the design are accurate.

Verified against `chatbi-harness.schema.json`: stored `cli_adapters.*.argv` items require pattern `\S` (`:171`) - the base argv from `build_mysql_adapter_spec` has no whitespace in any element. The runtime `--execute=<SQL>` element (with spaces) is NOT a stored config element; it is validated by `validate_cli_argv` only. Consistent with design §2.4.

## 3. Five open risks (design §6) - all handled

| Risk | Design resolution | As-built handling | Verdict |
| --- | --- | --- | --- |
| #1 `cli_allowlist` not a config field | SKILL obtains user-confirmed absolute `mysql` realpath, passes as single-element allowlist | SKILL Step 2 (`:52-69`): asks user to confirm exact absolute path; stores in session only (not config); cites `chatbi-init.md:38-42` + `installation.md:50-54` pattern. Command doc §5 (`:76-77`): "Do not execute mysql from unconfirmed PATH". | HANDLED |
| #2 `stdout_raw` untrusted | Documented; never spliced into prompt | SKILL Step 6.4 (`:143-149`); command doc §5 (`:78-79`); `_parse_stdout` tags `untrusted: True` (`adapters/__init__.py:422-432`) | HANDLED |
| #3 shared-config append discipline | UTF-8 + no dup keys + deterministic separators | SKILL Step 4 (`:99-103`): `json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=False)` to match existing file style; references `_load_json` enforcement. Note: design §6 recommended `sort_keys=True, separators=(",",":")` (matching `EffectiveConfig.to_json`), but the SKILL uses `indent=2, sort_keys=False` (matching the existing `.claude/chatbi-harness.json` file style). The SKILL's choice is correct for an idempotent append to a human-edited file (minimal diff, preserves readability). Design §4 Step 3 itself says "matching the existing file style", so the SKILL follows §4. | HANDLED (deviation from §6 recommendation is justified) |
| #4 `dw` exists-with-tables pre-check | Non-blocking WARN | SKILL Step 6.2-6.5 (`:128-152`): pre-check `SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='dw'`; if count > 0 emit WARN, proceed with non-destructive DDL. Command doc §4 (`:68-69`). | HANDLED |
| #5 StarRocks unverified | Noted as MySQL-only | SKILL §10 (`:210-213`); command doc §5 (`:80`); installation.md (`:95`); README.md §2.4 (`:134-135`) | HANDLED |

## 4. Trust boundary (design §1.2) - MATCH

- bootstrap = INFRA SETUP only: command doc §0 (`:13-32`), SKILL §0 (`:31-32`).
- MAY: write local config, append `cli:mysql` to shared `adapters.query`, `CREATE DATABASE IF NOT EXISTS dw`, introspect source schema, scaffold dirs, write inventory JSON. All listed in command doc §0.
- MUST NOT: create governed models, approve metrics, publish, run destructive migration (SEM-003). Listed in command doc §0 (`:25-28`).
- Hand-off to `/chatbi-maintain-model` via `.chatbi/bootstrap/source_inventory.json`: SKILL Step 9 (`:194-206`), command doc §3 (`:50-56`).
- No `production_ready` change; no new hook; no schema change.

## 5. Rules (design §7) - MATCH

- 46-rule count: verified via Grep, 46 unique rule IDs in `harness/docs/chatbi-harness-domain-model.md`. No rule added/renamed/reworded.
- 8 cited rules: SCOPE-001, SCOPE-002, SEC-001, SEC-003, PORT-001, SEM-003, DOC-001, HOOK-004. Present in command doc §Rules (`:87`), SKILL §11 (`:226`).
- `validate_domain_contract` (`gates.py:170-233`) continues to pass: contract artifacts (`CLAUDE.md`, `CONTEXT.md`, 3 rule files, domain model) not modified by bootstrap. `harness/CLAUDE.md` is 113 lines (under 200-line budget, `gates.py:194-200`).

## 6. Security - MATCH

- Password = env var NAME or empty array, NEVER a value: `build_mysql_adapter_spec` only carries `credential_env_name` (NAME) in `credential_env_names`; `argv` never contains `--password`. Enforced by `_contains_secret_argv` (`config.py:174-186`) + `_SECRET_VALUE` (`config.py:26-29`) + schema `^[A-Z_][A-Z0-9_]*$` (`:178`). Tests `test_no_password_value_anywhere_in_spec`, `test_password_flag_in_argv_raises_gate_error_with_sec_003`, `test_secret_value_in_local_config_raises_gate_error` confirm.
- No machine absolute paths in shared config: confirmed `mysql` realpath stored in session only (SKILL §2.3, `:67-69`); local config `argv[0]` is bare `"mysql"` (resolved later by `resolve_executable`).
- Reuses `config._contains_secret_argv` + `_contains_matching_string` + `_SECRET_VALUE` + adapters primitives: `bootstrap.py` delegates all validation to `load_effective_config` and `CliAdapter` ctor; no reinvention. `_CREDENTIAL_NAME` re-declared locally (not imported from `adapters`) with explanatory comment - accepted trade-off per design §3.

## 7. Tests (design §5 vs `test_bootstrap.py`) - MATCH

30 test methods (Grep confirmed). Coverage map:

| Design §5 case | Test method | Verdict |
| --- | --- | --- |
| §5.1 correct argv + env name | `test_correct_argv_shape_and_credential_env_name` | COVERED |
| §5.1 credential_env_name=None | `test_credential_env_name_none_yields_empty_list` | COVERED |
| §5.1 no password value | `test_no_password_value_anywhere_in_spec` | COVERED |
| §5.1 empty host/user/database | `test_empty_host_raises_gate_error_with_hook_004`, `test_empty_user_raises_gate_error`, `test_empty_database_raises_gate_error` | COVERED |
| §5.1 port <1 / >65535 / non-int / bool | `test_port_below_one_raises_gate_error`, `test_port_above_65535_raises_gate_error`, `test_non_integer_port_raises_gate_error`, `test_bool_port_raises_gate_error` | COVERED |
| §5.1 bad credential_env_name | `test_bad_credential_env_name_lowercase_raises_gate_error_with_sec_003`, `test_bad_credential_env_name_dash_raises_gate_error`, `test_bad_credential_env_name_leading_digit_raises_gate_error` | COVERED |
| §5.1 non-string host | `test_non_string_host_raises_gate_error` | COVERED |
| §5.2 preserve existing | `test_preserves_existing_adapters_and_bindings` | COVERED |
| §5.2 add new | `test_adds_new_bindings_and_adapters_to_empty_existing` | COVERED |
| §5.2 no clobber | `test_does_not_clobber_unrelated_adapters`, `test_overwrites_only_supplied_adapter` | COVERED |
| §5.2 drops non-local keys | `test_drops_non_local_top_level_keys` | COVERED |
| §5.2 no mutation | `test_does_not_mutate_existing` | COVERED |
| §5.3 spec round-trips load_effective_config | `test_spec_round_trips_through_load_effective_config`, `test_local_no_password_root_round_trips` | COVERED |
| §5.4 password-in-argv -> GateError SEC-003 | `test_password_flag_in_argv_raises_gate_error_with_sec_003` | COVERED |
| §5.4 secret value -> GateError | `test_secret_value_in_local_config_raises_gate_error` | COVERED |
| §5.5 SourceInventory shape | `test_to_dict_shape_is_self_describing_and_versioned` | COVERED |
| §5.5 JSON-serializable (allow_nan=False) | `test_to_dict_is_json_serializable_without_nan` | COVERED |
| §5.5 frozen | `test_source_inventory_is_frozen` | COVERED |
| §5.5 empty inventory | `test_empty_inventory_round_trips` | COVERED |
| §5.6 NO live MySQL | No MySQL connection in any test; `write_ready_shared_config` builds a synthetic ready config | CONFIRMED |

## 8. Wiring + docs - MATCH

| Item | Location | Verdict |
| --- | --- | --- |
| `build-product.sh` command loop includes `chatbi-bootstrap` | `build-product.sh:36-38` | MATCH |
| Comment "7 chatbi commands" | `build-product.sh:35` | MATCH |
| Import canary includes `chatbi_harness.bootstrap` | `build-product.sh:60-63` | MATCH |
| Dev-only leak sweep excludes `chatbi-bootstrap` | `build-product.sh:68-75` (not listed; correct - it is a product command) | MATCH |
| `harness/CLAUDE.md` request-routing table +1 row | `:75` (after `/chatbi-init`): "Bootstrap a Warehouse \| `/chatbi-bootstrap` \| local config, dw DB, source inventory, project scaffold" | MATCH |
| `harness/product-README.md` "Seven slash commands" | `:3` | MATCH |
| `product-README.md` commands table +1 row | `:41` | MATCH |
| `product-README.md` install step 5 | `:30-34` | MATCH |
| `docs/harness/installation.md` bootstrap subsection | `:59-95` ("Bootstrap a from-zero Warehouse") | MATCH |
| `docs/harness/README.md` §2.4 entry point | `:97-135` | MATCH |
| feature-flow AS_BUILT | `docs/feature-flow-bootstrap-v1.md` header: `Status: AS_BUILT (2026-07-27)` | MATCH |

## 9. Findings

### BLOCKER
(none)

### MAJOR
(none)

### MINOR
- [ ] **MINOR-1: Command doc step count mismatch.** `harness/.claude/commands/chatbi-bootstrap.md:36` says "Follow its 8-step procedure" but the bound SKILL (`harness/.claude/skills/chatbi-bootstrap/SKILL.md`) has 9 steps (Step 1 through Step 9). The SKILL elevated the `cli_allowlist` confirmation (design §4 Step 5 sub-item) into its own Step 2, which is a good security-prominence refinement but makes the command doc's "8-step" reference stale.
  - **Fix:** Change `chatbi-bootstrap.md:36` from "Follow its 8-step procedure" to "Follow its 9-step procedure". (One-word edit; no functional impact - the command doc only points to the SKILL, which is authoritative.)

### NIT
- [ ] **NIT-1: feature-flow step count reference.** `docs/feature-flow-bootstrap-v1.md:396` says "The 9 steps per technical-design §4 + skill procedure" but `docs/technical-design-bootstrap.md §4` says "The 8 steps". The feature-flow should note the SKILL refined the design's 8 steps into 9 (by splitting cli_allowlist confirmation into Step 2).
  - **Fix:** Update the feature-flow sentence to: "The 9 steps (design §4 specified 8; the SKILL elevated the cli_allowlist confirmation into its own Step 2)."
- [ ] **NIT-2: SKILL hardcodes `TABLE_SCHEMA='public'` in the INFORMATION_SCHEMA query.** `SKILL.md:166` uses `WHERE TABLE_SCHEMA='public'` in the Step 7 introspection SQL. While `public` is the running example throughout the design (consistent with design §4 Step 6 and feature-flow §3), the runbook would be clearer with a placeholder (`<source_db>`) to remind the agent to substitute the actual source database name confirmed in Step 1. An intelligent agent will substitute, but a literal reading could introspect the wrong schema if the source DB is not named `public`.
  - **Fix:** Change `TABLE_SCHEMA='public'` to `TABLE_SCHEMA='<source_db>'` in SKILL Step 7.1 (and optionally in Step 6.2's `TABLE_SCHEMA='dw'` is correct since `dw` is the fixed target). This is a runbook clarity improvement, not a code change.

## 10. Summary

The `/chatbi-bootstrap` implementation matches the approved technical design across all 8 verification axes: lib API (signatures, validation, frozen-slots, `from __future__ import annotations`), gap resolution (option a with per-operation CliAdapter + `--execute=<SQL>`), all 5 open risks handled, trust boundary (INFRA SETUP only), 46-rule count unchanged with 8 cited, security (password = env var NAME, no machine paths in config, primitives reused not reinvented), 30-test plan fully covered with no live MySQL, and build/doc wiring complete. The 3 findings are all documentation-level (one stale step count in the command doc, one stale cross-reference in feature-flow, one example-vs-placeholder clarity issue in the SKILL SQL). No code changes are required.

STATUS: CONVERGED
