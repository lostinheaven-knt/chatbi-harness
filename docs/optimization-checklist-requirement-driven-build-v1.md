# Optimization Checklist: requirement-driven build v1

> Status: legacy step 7.c evaluation. Compares the as-built implementation
> (coder-agent 7.a-7.b) against `docs/technical-design-requirement-driven-build.md`
> (the design蓝本). Evidence cites `file:line` against the as-built harness source.
>
> Verification method: full static read of every implementation file + every test
> file + every product-doc file, cross-referenced against the technical-design
> section contracts. The test suite could not be re-executed by plan-agent (no
> shell tool available); see §3 for the static test-count reconciliation + the
> coder-agent/orchestrator-state reported result.

## 1. Evaluation summary (12 dimensions)

| # | Dimension | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | API contract: dataclass fields | PASS | `build_plan.py:64-156` - `HumanApproval`/`CrossLayerException`/`ModelEntry`/`LayerRule`/`BuildPlan` all `@dataclass(frozen=True, slots=True)`, field names + defaults + types match design §2.2-2.4 exactly. `to_dict()` shapes (`:73-78`,`:89-90`,`:110-124`,`:148-156`) match `build-plan.schema.json` required/properties. |
| 2 | Function signatures | PASS | `validate_build_plan(plan, layer_rules, known_models=frozenset())` `build_plan.py:413-417` matches design §2.7 incl. open point 6. `validate_layer_dependency(plan, layer_rules)` `:529-532`. `read_model_registry(path)` `:341`. `append_model_registry(path, entry)` `:585`. `build_model_entry(*, ...)` `:159-172`. `read_source_inventory`/`merge_source_inventories` in `bootstrap.py:256,422`. |
| 3 | Q1-Q6 landed | PASS | Q1 `HumanApproval(approved=False)` default `:69` + extend-source gate `:507-520`; Q2 `cross_layer_exception` non-raise `:564-569`; Q3 no registry schema file (reader is validator `:341-410`); Q4 reader+merge in `bootstrap.py:256,422`; Q5 `_sanitize_text` at construction `:183,223,257,268-273,306-310`; Q6 topology (`validate_build_plan:446-476`) vs layer-matrix (`validate_layer_dependency:529-582`) independent. |
| 4 | SCOPE-001 cross-plan-boundary (open point 6) | PASS | `validate_build_plan` `:460-475`: dep in neither `plan.models` nor `known_models` -> `GateError` rule_ids `("SCOPE-001","HOOK-004")`, evidence_ref `build-plan:scope:<name>:<dep>`. SKILL Step 2.6 `chatbi-build/SKILL.md:89-94` passes `known_models = {m.name for m in read_model_registry(...)}`. |
| 5 | Governance constraints | PASS | `validate_domain_contract` PASS (test_contract.py:41-45 asserts `status=="pass"`; 46 rules via `:75`). `chatbi-harness.schema.json` not modified (protected_actions enum stays 4; no `models` field - registry is derived evidence). `harness/CLAUDE.md` is 113 content lines (last `:113`) < 200. No derivation lib (build_plan.py only reads+validates+appends). Cross-layer declarative in blueprint `## Layers` (`chatbi-bootstrap/SKILL.md:202-219`), not SKILL-hardcoded. fail-closed throughout (`_build_plan_gate_error` `:46-61`). |
| 6 | 7 modules all landed | PASS | M1 command `commands/chatbi-build-from-requirement.md` + `skills/chatbi-build/SKILL.md`; M2 `build_plan.py` + `build-plan.schema.json` + `__init__.py:10-18`; M3 `bootstrap.py:256,422`; M4 `chatbi-maintenance/SKILL.md:54-86`; M5 `chatbi-bootstrap/SKILL.md:202-219`; M6 `build-product.sh:35-39,60-63` + `CLAUDE.md:76` + `product-README.md:3,45` + `installation.md:97` + `README.md:137`; M7 `test_build_plan.py`(49) + `test_bootstrap.py`(+11) + `test_maintenance.py`(+3) + `test_contract.py:64-73` + `test_e2e.py:597-604`. |
| 7 | Test coverage | PASS | 49 new in test_build_plan.py (grep count confirmed): frozen-slots(`:103-124`), to_dict round-trip schema(`:126-132`), factory reject bad alias/layer/change_kind/flags/empty-reason(`:166-205`), sanitize(`:207-214`), read_model_registry absent+parse+malformed+wrong-ver+tampered(`:258-320`), read_source_inventory round-trip+absent+malformed(`:323-373`), validate_build_plan PASS+topology+SCOPE-001+SEM-003+Q1(`:376-474`), validate_layer_dependency PASS+3 cross-layer reject+exception non-raise+pre-existing skip(`:477-563`), append create+idempotent+history+atomic 0o600+no-tmp+no-mutate(`:566-650`). test_bootstrap +11 (`:533-646`). test_maintenance +3 (`:318-350`). test_contract required_routes +bootstrap +build-from-requirement (`:64-73`). test_e2e six->eight (`:597-604`). |
| 8 | Code style consistency | PASS | frozen-slots dataclass (aligns `SourceInventory` `bootstrap.py:220`). `_build_plan_gate_error` mirrors `_bootstrap_gate_error` (`bootstrap.py:47-67`) + `_impact_gate_error`. `_sanitize_text` imported from gates (`:27`, sanctioned reuse). `_validate_against_schema` + `_get_schema` imported from evidence (`:33`, mirrors `impact.py:25-29`). `_CHANGE_KINDS` re-imported from impact (`:34`, not re-declared). Atomic temp+rename 0o600 (`:661-672`, mirrors `harness_state.write_state`). |
| 9 | Absent policy asymmetry | PASS | `read_model_registry` absent -> `()` (`build_plan.py:353-354`, first build empty registry). `read_source_inventory` absent -> `GateError` (`bootstrap.py:281-287`, bootstrap prerequisite missing). Both fail-closed on malformed/tampered. |
| 10 | (duplicate of 9) | PASS | See row 9. |
| 11 | append_model_registry atomic + idempotent | PASS | Atomic: `os.open(tmp, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` + `os.replace` (`:662-666`), unlink tmp on exception (`:667-672`). Idempotent on `(name, created_rev)` (`:644-650`). v1 append-with-history (`:651-655`). Only after sync gate + stop_gate pass: documented in `chatbi-maintenance/SKILL.md:72-86` + tested `test_maintenance.py:318-350` (pass->append, fail->no append, protected->no append). |
| 12 | Product integration | PASS | `build-product.sh:35` comment "the 8 chatbi commands"; `:36-38` loop includes `chatbi-build-from-requirement`; `:60-63` import canary includes `chatbi_harness.build_plan`. `harness/CLAUDE.md:76` routing row (+1, 113 lines < 200). `product-README.md:3` "Eight slash commands"; `:45` table row. `installation.md:97-115` "Build from a requirement" VERIFIED OFFLINE framing. `README.md:137-166` §2.5 VERIFIED OFFLINE / NOT YET EXERCISED framing. |

## 2. Issues by severity

### BLOCKER (must fix, blocks delivery)

None.

### MAJOR (should fix)

None.

### MINOR (optional, non-blocking)

1. **`validate_layer_dependency` skips pre-existing deps (no `known_models` param).**
   - Location: `build_plan.py:556-562` (`dep_layer is None -> continue`).
   - Observation: `validate_layer_dependency(plan, layer_rules)` does not accept
     `known_models`. A dep to a pre-existing registry model (not in `plan.models`)
     is skipped rather than cross-layer-checked. The design §2.8 offered two
     sanctioned v1 options ("plan-internal names only" OR "passed via known_models
     arg"); the implementation chose the first. This means a NEW plan entry
     depending on a pre-existing model that crosses a layer (e.g. a new ADS
     depending on a pre-existing ODS) is not layer-checked here.
   - Risk: low. `validate_build_plan` already confirms such a dep is in
     `known_models` (SCOPE-001), and the pre-existing model's own cross-layer
     deps were checked when it was built. The behavior is tested
     (`test_build_plan.py:550-563` `test_pre_existing_dep_skipped`) and documented
     in the docstring (`build_plan.py:540-544`).
   - Suggestion (future, not v1): if cross-layer validation of pre-existing deps
     becomes desired, pass `known_models` + a `layer_of` lookup for registry
     models into `validate_layer_dependency`. No v1 action required.

2. **`build_model_entry` sanitizes `upstream_deps` (beyond the design's explicit
   field list).**
   - Location: `build_plan.py:231-243`.
   - Observation: the design §2.2 lists the sanitized fields as name/owner/
     join_or_aggregate_summary/human_approval.approver/cross_layer_exception.
     reason+approved_by. The implementation additionally sanitizes each
     `upstream_dep`. This is strictly defense-in-depth (dep names are aliases that
     could carry a path/secret), not a deviation that weakens anything.
   - Suggestion: none. Documented here only for traceability.

3. **`read_model_registry` / `append_model_registry` enforce a 256 KiB size cap.**
   - Location: `build_plan.py:357-358`, `:605-606`.
   - Observation: not in the design contract; mirrors `read_source_inventory`'s
     `bootstrap.py:290-291` discipline. Reasonable DoS guard for derived evidence.
   - Suggestion: none.

4. **`validate_build_plan` rejects duplicate model names.**
   - Location: `build_plan.py:438-444`.
   - Observation: not explicitly in design §2.7, but a natural extension of the
     topology check (a duplicate name breaks the name->index map). Enhances
     fail-closed behavior.
   - Suggestion: none.

## 3. Test results

plan-agent has no shell-execution tool, so the suite was not re-run in this
session. Reconciliation by static analysis:

- `test_build_plan.py`: 49 test methods (grep `def test_` count = 49). Confirmed.
- `test_bootstrap.py` new classes: `ReadSourceInventoryTests` (4) +
  `MergeSourceInventoriesTests` (7) = 11 new. Confirmed.
- `test_maintenance.py` new class: `AppendModelRegistryAfterSyncGateTests` (3).
  Confirmed.
- `test_contract.py`: `required_routes` extended (no new test method). Confirmed.
- `test_e2e.py`: `test_six_commands` -> `test_eight_commands_exist_and_route`
  (renamed, tuple extended; no new test method). Confirmed.

New tests total: 49 + 11 + 3 = 63. Baseline 566 + 63 = 629. Matches the
orchestrator-state + feature-flow AS_BUILT claim of "629 tests (628 pass + 1
skip)".

The `validate_domain_contract` gate is statically confirmed to pass:
`test_contract.py:41-45` asserts `status == "pass"` against the checked-in
contract at `WORKSPACE_ROOT / "harness"`, and `:75` asserts exactly 46 rule IDs.
The schema import path resolves (`evidence.py:44` `_SCHEMAS_DIR =
parents[2] / "schemas"` -> finds `build-plan.schema.json`). All test imports
resolve against the as-built `harness/.claude/lib`.

Recommendation: the test-agent (legacy step 7.d) should re-run
`python3 -B -m unittest discover -s tests/harness` independently to confirm the
628 pass + 1 skip number before marking `tests_passed`.

## 4. Convergence judgment

The implementation faithfully follows the technical design across all 12
evaluated dimensions. The 7 modules are all landed with correct content. The 6
adopted decisions (Q1-Q6) and open point 6 (`known_models` SCOPE-001
cross-plan-boundary check) are correctly implemented in the lib + exercised by
the SKILL procedure. Governance constraints hold (46 rules unchanged,
`chatbi-harness.schema.json` unmodified, CLAUDE.md 113 < 200, fail-closed
throughout, no derivation lib, cross-layer declarative in blueprint). The 4
MINOR notes are sanctioned v1 choices or defense-in-depth enhancements - none
require coder-agent action.

No BLOCKER. No MAJOR. The implementation is ready to enter the test-agent
independent verification cycle (legacy step 7.d).

STATUS: CONVERGED
