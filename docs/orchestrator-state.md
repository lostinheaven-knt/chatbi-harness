# Orchestrator State

- Workflow type: legacy
- Modification: 补齐 harness 缺口 - 新增 `/chatbi-bootstrap` command (MySQL-only v1, dbt-mysql layout)
- Current phase: complete (legacy flow done; AS_BUILT)
- Current iteration: 1
- Plan (human-verified, serves as requirements): `.scratch/chatbi-bootstrap-plan.md`
- 介入点① decision: feature-flow accepted; gap (CliAdapter JSON-stdin vs mysql SQL-stdin) resolved via **option (a)** = per-operation CliAdapter with `--execute=<SQL>` (single-statement, no semicolon; passes validate_cli_argv + _contains_secret_argv).
- Completed nodes:
  - [x] requirements_drafted (.scratch/chatbi-bootstrap-plan.md)
  - [x] feature_flow_scanned        (legacy step 2 -> docs/feature-flow-bootstrap-v1.md)
  - [x] modification_doc            (legacy step 4 -> docs/modification-bootstrap.md)
  - [x] technical_design            (legacy step 5 -> docs/technical-design-bootstrap.md)
  - [x] modification_confirmed      (介入点② confirmed)
  - [ ] dev_cycle                   <- in progress (legacy step 7.a)
  - [x] implementation_converged    (legacy step 7.c CONVERGED; 0 BLOCKER/MAJOR)
  - [x] tests_passed                (legacy step 7.d ALL_PASSED; live smoke done: dw created, public=125 tables introspected, risk#4 pre-check=0)
  - [x] technical_design_as_built   (legacy step 10-11 done; technical-design-bootstrap.md + technical-design.md §20 AS_BUILT)
- Latest artifact: docs/technical-design-bootstrap.md (AS_BUILT)
- Waiting for human: none (flow complete; pending user decision on commit+push)

## Prior flow (superseded)
harness v1 AS_BUILT (46/46 rules IMPLEMENTED). This legacy flow adds /chatbi-bootstrap
on top, WITHOUT adding new rules.

## Context
- Dev workspace (source of truth): /Users/admin/Downloads/workspace/chatbi-cc-dev
- Deployed product: /Users/admin/Downloads/workspace/chatbi (rebuilt via build-product.sh)
- E2E test dir: /tmp/chatbi-e2e

## Key design boundary (from plan)
bootstrap = INFRA SETUP only: write local config (cli_adapters.mysql + path_bindings) +
create `dw` DB + scaffold project dirs + introspect `public` -> source_inventory.json +
hand off to /chatbi-maintain-model. Does NOT create governed models, NOT approve metrics,
NOT touch production, NOT destructive migration. Reuses config._contains_secret_argv +
adapters.CliAdapter + select_adapter. Password = env var NAME only (SEC-003); no machine
paths in shared config (PORT-001).
