# Test Report: `/chatbi-build-from-requirement` v1

> Status: **ALL_PASSED** (legacy step 7.d). Independent re-run by test-agent (the
> plan-agent eval at step 7.c had no shell and could not run tests live; this
> report is the live verification). All numbers below are from a fresh shell run,
> not inferred.

## §1 Full-suite result

- Command: `python3 -B -m unittest discover -s tests/harness`
- Result: **Ran 629 tests in 26.365s - OK (skipped=1)**
- Total 629 / pass 628 / fail 0 / error 0 / skip 1
- Matches coder-agent's report (629 = 628 pass + 1 skip): **independently confirmed**.

## §2 Per-module result

| Module | Cases | Result | Notes |
| --- | --- | --- | --- |
| test_build_plan | 49 | OK (0.011s) | new module, 49 green |
| test_bootstrap | 41 | OK (0.017s) | +11 new (ReadSourceInventoryTests 4 + MergeSourceInventoriesTests 7) |
| test_maintenance | 34 | OK (0.842s) | +3 new (AppendModelRegistryAfterSyncGateTests) |
| test_contract | 7 | OK (0.014s) | validate_domain_contract PASS + required_routes + 46 rules |
| test_e2e | 30 | OK (3.127s) | test_eight_commands_exist_and_route (six->eight, incl chatbi-build-from-requirement.md) |
| rest | 468 | OK | covered by full 629 |
| total | 629 | 628 pass + 1 skip | |

## §3 build-product.sh result

- Build: clean, no error; `import OK` canary includes `chatbi_harness.build_plan` (`build-product.sh:62`).
- All 8 commands copied to `../chatbi/.claude/commands/`: chatbi-init / chatbi-analyze / chatbi-maintain-model / chatbi-maintain-knowledge / chatbi-evaluate / chatbi-correction / chatbi-bootstrap / **chatbi-build-from-requirement** (verified by ls).
- Deployed product includes `build_plan.py` (lib), `build-plan.schema.json` + `chatbi-harness.schema.json` (schemas).
- Dev-only leak check: tests / .scratch / AGENTS.md / orchestrate.md / plan|coder|test-agent.md / technical-design|requirements|orchestrator-state|dev-cycles|dev-cycle-1.md - **all absent, no leak**.
- Canary sweep: build script self-reports clean. One heuristic match at `docs/harness/knowledge-authoring.md:31` (``(`/Users/...`, `/home/...`)``) - this is a **placeholder describing the forbidden pattern** (ellipses, not a real path/secret), last changed in commit `1650a4d` (precedes this feature). **NON-ISSUE**.

## §4 validate_domain_contract result

- `validate_domain_contract(harness/)`: **status=pass**, rule_ids=`('HOOK-004',)`.
- 46 rules: `test_contract.py:75` asserts `len(domain_rule_ids)==46`, passes (rule set unchanged).
- required_routes: CLAUDE.md has all 8 routes incl `/chatbi-bootstrap` and `/chatbi-build-from-requirement` (both independently asserted `in root`).
- CLAUDE.md lines: source 114 / deployed 114, **< 200**.

## §5 4 MINOR verification (all genuinely harmless, no regression)

1. **`validate_layer_dependency` takes no `known_models`** - signature `(plan, layer_rules) -> None`; pre-existing dep skipped. Covered by `test_pre_existing_dep_skipped` PASS. A sanctioned v1 option per design §2.8.
2. **`build_model_entry` extra-sanitizes `upstream_deps`** - verified: `/Users/admin/secret.sql` -> `('[REDACTED_PATH]',)`. Pure defensive enhancement, weakens no contract.
3. **`read/append_model_registry` 256 KiB size cap** (`build_plan.py:357-358, 605-606`) - verified normal append+read flow passes (entries=1); mirrors `read_source_inventory` discipline.
4. **`validate_build_plan` rejects duplicate model names** - verified: duplicate triggers `GateError` rule_ids `('DOC-002','HOOK-004')` evidence `build-plan:topology:duplicate:<name>`; non-duplicate plan still PASS. fail-closed enhancement.

(All 4 are also covered by the full 629 green, confirming no regression.)

## §6 Convergence

- Full 629 (628 pass + 1 skip) independently run-confirmed, matches coder-agent.
- New test_build_plan 49 green; bootstrap +11, maintenance +3, contract required_routes, e2e six->eight all landed and passing.
- build-product.sh clean: import canary includes build_plan, 8 commands copied, no dev-only leak.
- validate_domain_contract PASS: 46 rules, required_routes incl build-from-requirement + bootstrap, CLAUDE.md 114<200.
- 4 MINOR verified by direct behavior to be defensive enhancements / sanctioned v1 choices, genuinely harmless, no coder-agent action needed.
- No BLOCKER, no MAJOR, no failing case. Implementation clears `tests_passed`.

STATUS: ALL_PASSED
