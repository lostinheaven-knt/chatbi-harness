# Feature Flow v6 - Cycle 5 (评测、纠正闭环、全量兼容演练与交付收敛)

> Code-as-read on 2026-07-23 from `chatbi-cc-dev/`. Version v6 (Cycle 4 took v5;
> `dev-cycles.md`'s "v5" label for Cycle 5 is stale - see `dev-cycle-5.md` top).
> Final cycle. Each entry cites real file:line. Not-yet-exercised runtime is
> marked; the real Claude E2E (Task 06) is a human-environment gate.

Cycle 5 adds six flows on top of Cycle 1-4:

1. **Flow A** - evaluator + ground-truth isolation (`lib/chatbi_harness/evaluator.py`)
2. **Flow B** - `/chatbi-evaluate` + evaluation SKILL
3. **Flow C** - `/chatbi-correction` (dual-candidate)
4. **Flow D** - evaluation suite + six-Command routing + production-no-connection STOP
5. **Flow E** - AS_BUILT + 46/46 final audit (Task 07)
6. **Flow F** - real Claude Code 2.1.216 E2E (Task 06, human gate)

## 1. Flow A: evaluator + ground-truth isolation

Entry: `evaluator.py:116 GroundTruthVault` holds ground-truth answers;
`evaluator.py:143 score(assertion_id, actual)` returns
`evaluator.py:49 AssertionResult` (passed + `expected_hash`/`actual_hash`,
never the raw answer). There is **no method that returns the raw expected
answer** - the isolation invariant (EVAL-001/002). `evaluator.py:71 EvaluationRun`
records run_id/skill_version/content_hash/model_id/assertions/tokens/latency_ms/
seen/threshold_owner_confirmed (EVAL-003). `evaluator.py:167 build_evaluation_run`
scores actuals against the vault; `content_hash` via `evidence.compute_candidate_sha`
(no Git -> content hash). `evaluator.py:32 FBK_003_STATEMENT` is carried on every
run + correction (pass != absolute correctness).

`evaluator.py:218 build_correction_record` produces BOTH `fix_candidate`
(kind ∈ reference/Skill/model, `_FIX_KINDS:37`) AND `eval_case_candidate`;
`owner_approved` defaults False (FBK-002, SEM-003). Sanitizes all text fields via
`gates._sanitize_text` (SEC-003/PORT-001).

Validators: `evaluator.py:208 validate_evaluation`, `:213 validate_correction`
(against `schemas/{evaluation,correction}.schema.json` via `evidence` schema
loader).

**Rules:** EVAL-001..005, ABL-001/002, FBK-001/002/003, HOOK-001/004, SEC-003,
PORT-001, SEM-003.

## 2. Flow B: /chatbi-evaluate + evaluation SKILL

Entry: `commands/chatbi-evaluate.md` (57) + `skills/chatbi-evaluation/SKILL.md`
(55). Routes the fixed suite (high-freq + long-tail + 5 stress) with
ground-truth isolation; owner-confirmed threshold (EVAL-004, never hard-coded
90%); semantic-covered cases assert semantic-layer use (EVAL-005); records run
(EVAL-003); carries FBK-003 (pass != absolute correctness).

**Rules:** AC-07, EVAL-001..005, FBK-003, SEM-003, PORT-001.

## 3. Flow C: /chatbi-correction (dual-candidate)

Entry: `commands/chatbi-correction.md` (57). Each valid correction produces a
fix candidate (reference/Skill/model) AND an eval-case candidate (FBK-002);
`owner_approved` defaults False; never auto-approves a canonical metric
(SEM-003). Structured collection tracks semantic-layer resolution ratio +
corrective-language ratio (FBK-001). ABL-001: one component at a time.

**Rules:** AC-09, FBK-001/002/003, SEM-003, ABL-001/002, SEC-003, PORT-001.

## 4. Flow D: evaluation suite + six-Command routing + production STOP

Entry: `fixtures/evaluations/suite/{high-freq,long-tail}.json` (synthetic ground
truth + inputs, no org real facts/secrets/paths). `test_e2e.py
EvaluationE2ETests`: loads suite -> `GroundTruthVault` -> `build_evaluation_run`
-> asserts pass/fail + isolation (no raw answer in run) + FBK-003. Six Commands
routing: `chatbi-init`/`analyze`/`maintain-model`/`maintain-knowledge`/`evaluate`
/`correction` all present. Production-no-connection STOP: `select_adapter` with
no configured adapters -> `status="stopped"` (no silent Fixture fallback).

**Rules:** AC-01..09, EVAL/ABL/FBK, HOOK-001/004, SEM-001, PORT-001.

## 5. Flow E: AS_BUILT + 46/46 final audit (Task 07)

Entry: `docs/technical-design.md` updated to `STATUS: AS_BUILT` by plan-agent
after real E2E passes. Final audit: file inventory, 46/46 rule evidence
(`docs/harness/rule-traceability.md`), report/feature-flow-v6/AS_BUILT
consistency, native-command evidence. Mandatory completion checklist all ticked
before claiming COMPLETE.

**Rules:** AC-01..09, all 46 rules final review, FBK-003.

## 6. Flow F: real Claude Code 2.1.216 E2E (Task 06, human gate)

Entry: human-environment-gated. Agent prepares E2E procedure + evidence-recording
template; the user runs the logged-in Claude Code 2.1.216 (Darwin arm64), registers
live Hooks **in the E2E environment only** (not the dev session - deadlock lesson),
and triggers real SessionStart/PreToolUse/PostToolUse/SubagentStop/Stop/ConfigChange
+ the isolated `adversarial-reviewer`. Records exact commands/exit/output/model to
`docs/harness/compatibility.md`. Verifies production-no-connection STOP.

**Gap (hard gate):** without triggering every P0 event, Cycle 5 cannot exit
(`dev-cycle-5.md` 退出门). Agent cannot self-login; login/keychain must be
resolved by the user. OS sandbox runtime evidence is a BLOCKING GAP unless
exercised here.

## 7. Known gaps (final hard-gates)

1. Real CC Hook process E2E + live `settings.json` registration - NOT YET
   EXERCISED until Task 06 (human gate). HOOK-003/005 remain PARTIAL until then.
2. OS sandbox deny runtime evidence - BLOCKING GAP (unless exercised in Task 06).
3. Real managed/CLI adapter + real reviewer process runtime - Cycle 5 Task 06.
4. Organizational PII policy / real owner / real connection / release gate not
   provided -> "cannot production-certify"; synthetic correctness验收 stands but
   production-use claims are prohibited.
5. PII redaction email-only (Cycle 3 carry-forward; broader PII = owner policy).

Evaluation success is evidence, not a guarantee silent failure is eliminated
(FBK-003). AS_BUILT reflects real code; completeness requires the real E2E.
