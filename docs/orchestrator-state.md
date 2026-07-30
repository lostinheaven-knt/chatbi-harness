# Orchestrator State

- Workflow type: legacy
- Modification: 新增"需求驱动建造工作流" `/chatbi-build-from-requirement`。
- Current phase: complete (legacy flow done; AS_BUILT)
- Current iteration: 1
- 介入点① (CONFIRMED): 方案A / `/chatbi-build-from-requirement` / 扩源=bootstrap增量 / Registry=`.chatbi/model_registry.json` / 计划不过独立审查 / 跨层规则进蓝图Layers / 不新增governed rule。
- 6 design decisions (ADOPTED): Q1-Q6 全落地。
- 介入点② (CONFIRMED): modification + technical-design 接受。
- Open point 6 (CONFIRMED): validate_build_plan v1 加 known_models:frozenset[str]。
- Final verification:
  - 629 tests (628 pass + 1 skip), +63 additive, test-agent independently run-confirmed.
  - validate_domain_contract PASS (46 rules unchanged, required_routes incl build-from-requirement + bootstrap, CLAUDE.md 114 < 200).
  - build-product.sh clean (8 commands, import canary incl build_plan, no dev-only leak).
  - Evaluation CONVERGED (12 dims PASS, 0 BLOCKER/MAJOR, 4 MINOR defensive enhancements).
- Completed nodes:
  - [x] requirements_provided
  - [x] feature_flow_scanned        (docs/feature-flow-requirement-driven-build-v1.md, AS_BUILT)
  - [x] 介入点① confirmed
  - [x] modification_doc            (docs/modification-requirement-driven-build.md)
  - [x] technical_design            (docs/technical-design-requirement-driven-build.md, AS_BUILT)
  - [x] modification_confirmed      (介入点②)
  - [x] dev_cycle                   (docs/dev-cycle-requirement-driven-build.md)
  - [x] implementation_done         (7.b, 629 green)
  - [x] implementation_converged    (7.c CONVERGED)
  - [x] tests_passed                (7.d ALL_PASSED, docs/test-report-requirement-driven-build-v1.md)
  - [x] technical_design_as_built   (step 10-11 done; technical-design-requirement-driven-build.md AS_BUILT + technical-design.md §21)
- Latest artifact: docs/technical-design.md §21 (AS_BUILT)
- Waiting for human: commit+push decision (and optional live smoke in chatbi-ws)

## Deliverables
- New command: harness/.claude/commands/chatbi-build-from-requirement.md + skills/chatbi-build/SKILL.md
- Thin lib: harness/.claude/lib/chatbi_harness/build_plan.py (BuildPlan/ModelEntry/HumanApproval/CrossLayerException/LayerRule + build_model_entry + read_model_registry + validate_build_plan[known_models] + validate_layer_dependency + append_model_registry)
- Schema: harness/.claude/schemas/build-plan.schema.json
- bootstrap incremental: bootstrap.py + read_source_inventory + merge_source_inventories (Q4)
- maintain-model: chatbi-maintenance/SKILL.md (read § Layers + append_model_registry after sync gate)
- blueprint stub: chatbi-bootstrap/SKILL.md Step 8 + ## Layers
- product: build-product.sh (8 cmds), CLAUDE.md (+1 route), product-README (Seven->Eight), installation.md, README.md
- tests: test_build_plan.py (49), test_bootstrap (+11), test_maintenance (+3), test_contract (required_routes), test_e2e (eight_commands)
- docs: feature-flow (AS_BUILT), modification, technical-design (AS_BUILT), dev-cycle, optimization-checklist (CONVERGED), test-report (ALL_PASSED), technical-design.md §21

## 4 implementation enhancements (MINOR, recorded in AS_BUILT §12)
1. validate_layer_dependency takes no known_models (pre-existing dep skipped) - sanctioned v1 per §2.8.
2. build_model_entry extra-sanitizes upstream_deps (defensive).
3. read/append_model_registry 256 KiB size cap (mirrors read_source_inventory DoS guard).
4. validate_build_plan rejects duplicate model names (topology extension, fail-closed).

## Context
- Dev workspace: /Users/admin/Downloads/workspace/chatbi-cc-dev
- Deployed product: /Users/admin/Downloads/workspace/chatbi (rebuilt, 8 commands)
- Demo workspace: /Users/admin/Downloads/workspace/chatbi-ws (NOT yet live-smoke'd for this command)
- E2E test dir: /tmp/chatbi-e2e
- Baseline: 566 tests. Final: 629 (628 pass + 1 skip). +63 additive.

## Note
Verification is unit-test + build level only. No live end-to-end exercise of /chatbi-build-from-requirement in chatbi-ws yet (unlike /chatbi-bootstrap which had a live smoke). The deterministic lib surface (build_plan.py) is VERIFIED OFFLINE; live derivation + maintain-model chaining are NOT YET EXERCISED.
