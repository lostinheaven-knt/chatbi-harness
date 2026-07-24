# Cycle 2 Optimization Checklist (v3) - Convergence Evaluation

> Provenance: This convergence evaluation was produced by the **main agent as
> a fallback** after the `plan-agent` dispatch could not complete (the
> `test-agent` dispatch that precedes it failed twice on transient API
> errors: HTTP 429 quota-exceeded, then HTTP 500 `sensitive_words_detected`).
>
> **Independence caveat (REV-001/REV-002):** the completion contract requires
> an independent, least-privileged adversarial reviewer; the main agent cannot
> certify its own work. This document is a deterministic reconciliation of
> `docs/dev-cycle-2.md` (technical design) against `docs/feature-flow-v3.md`
> (code-as-read) and the §10 completion checklist. It is evidence for, but not
> a substitute for, an independent re-review when the API is stable. A
> blocking finding here would require a new candidate + new review round; no
> such blocking finding is raised.

## 1. Method

1. Re-read `select_adapter` (`adapters/__init__.py:495-718`) to resolve the
   open design gap flagged in `orchestrator-state.md`: "FixtureAdapter +
   CodebaseReader not wired into `select_adapter`."
2. Reconcile `dev-cycle-2.md` §7 (expected data flow) and §10 (completion
   checklist) against `feature-flow-v3.md` (code-as-read) and the test suite.
3. Verify every §10 item with real evidence (test counts, file inventory,
   settings.json, skip reason).
4. Classify findings P0/P1/P2 and assign STATUS.

## 2. The flagged gap: fixture/codebase not wired into select_adapter

`orchestrator-state.md` listed as an open design gap:
> (1) FixtureAdapter + CodebaseReader not wired into `select_adapter` (both
> stop at pending).

**Resolution: documented deliberate deferral, NOT a P1.**

Evidence:

1. `select_adapter` `fixture:` branch (`adapters/__init__.py:675-706`) enforces
   the mode gate correctly: fixture is rejected unless `fixture_enabled` is
   true **and** `run_mode` is `test`/`example` (PORT-001). Past the gate it
   appends `fixture_pending` and continues rather than constructing the
   adapter.
2. The test suite explicitly frames this as scoped deferral, not a bug:
   `test_adapters.py:1235-1237` - "`select_adapter` still STOPs at
   `fixture_pending` (Ticket 02 territory). Wiring the adapter into the
   selection chain is a later step."
3. `feature-flow-v3.md` §3 and §4 both label it "Known gap: not wired into
   selection chain" and treat it as accepted.
4. `dev-cycle-2.md` §9 (lines 481-482) directs that non-Cycle-2 gaps be
   recorded to the feature-flow/design-gap doc, "不提前实现造成依赖倒置"
   (do not pre-implement and invert dependencies).
5. `dev-cycle-2.md` §10 line 491 - the acceptance criterion is the
   managed->CLI->STOP **three branches** + CLI argv/allowlist/no-shell/env/cwd
   + **Fixture production-fallback block**. It does **not** require fixture to
   be selectable. All three are satisfied with test evidence.
6. `CodebaseReader` is a read-only accessor for Business Codebases, not a
   `semantic`/`query` adapter; `select_adapter` only handles `semantic`/`query`
   kinds (`_ADAPTER_KINDS`). No `codebase:` branch exists or is required; this
   is by design, not a gap.

**Conclusion:** the previous session's "expected P1" does not survive the
code read. No convergence loop (code change) is required for Cycle 2.

### 2.1 Residual P2 (documentation accuracy)

`dev-cycle-2.md` §7 data-flow diagram (lines 415-427) draws the chain as
managed -> CLI -> **fixture (test only)** -> STOP, i.e. it shows fixture being
*selected*. The code STOPs at `fixture_pending`. This is a doc-vs-code
mismatch, not an implementation defect. Two acceptable resolutions:

- (a) Annotate §7 to mark the fixture-select edge as "deferred - STOPs at
  `fixture_pending` in Cycle 2", matching `feature-flow-v3.md`; or
- (b) Wire `FixtureAdapter` into `select_adapter` (low-risk: the mode gate is
  already correct) in a later cycle or a Cycle 2.5 patch, then update
  `feature-flow-v4` + the test comment.

Either resolves the P2 without blocking Cycle 2 convergence. Recommended:
(a) now (doc-only), track (b) as an early Cycle 3 candidate.

## 3. dev-cycle-2.md §10 completion checklist - verification

| # | §10 item | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Tickets approved; executing-plans loaded | MET | orchestrator-state; Tickets 01-07 resolved |
| 2 | Task 1-6 files exist, non-empty, owned, no unplanned prod files | MET | test-report-v3 §7 inventory |
| 3 | `policy.py` deterministic; protected action not self-approvable | MET | 22 policy tests (`test_security.py`) |
| 4 | selection chain 3 branches + CLI argv + Fixture prod-block | MET | 77 selection + 54 fixture tests |
| 5 | codebase_reader read-only + untrusted + injection-ignored + portable ref | MET | 52 codebase tests |
| 6 | PreToolUse continuous TOCTOU + external write/exec blocked | MET | 17 PreToolUse tests |
| 7 | ConfigChange revalidate schema/path/sandbox/permissions; managed policy not assumed blocking | MET | 16 ConfigChange tests |
| 8 | `.claude/settings.json` maps PreToolUse/ConfigChange/permissions/sandbox, no machine path/secret | **DEFERRED** | dev + product settings.json are SessionStart-only. Hook **registration** deferred to Cycle 5 E2E: a blocking PreToolUse hook hot-reloads `settings.json` and bricks the dev session (memory `pretool-use-hook-dev-deadlock`, 2026-07-22). Hooks exist and are unit-tested; live wiring is Cycle 5. |
| 9 | Claude permission deny + OS sandbox deny separate command evidence; sandbox unavailable = BLOCKING GAP, not Prompt-test | **PARTIAL / DEFERRED** | permission-layer deny = MET (offline deterministic); OS sandbox deny = BLOCKING GAP, skipped test `test_security.py:1139`, deferred to Cycle 5 (§9). |
| 10 | managed runtime = official-only / NOT YET EXERCISED | MET | `ManagedAdapter._unavailable` (`adapters/__init__.py:278-292`) |
| 11 | no canary secret/PII/absolute Workspace path in any hook output | MET | test assertions + `rg` sweep (test-report §6) |
| 12 | `unittest discover` all green | MET | 323 tests, OK (skipped=1) |
| 13 | rule-traceability per-rule Cycle 2 evidence | MET | `docs/harness/rule-traceability.md` (46 governed rules, CODE_AS_READ 2026-07-22) |
| 14 | security.md / compatibility.md distinguish verified/official-only/blocked | MET | `docs/harness/{security,compatibility}.md` |
| 15 | feature-flow-v3 from code, line refs/branches/dataflow/gaps | MET | `docs/feature-flow-v3.md` (185 file:line refs) |
| 16 | test-agent report + plan-agent checklist no CRITICAL/HIGH + CONVERGED | **FALLBACK** | produced by main agent (test-checklist-v3 / test-report-v3 / this file); independent re-review pending API stability |
| 17 | inventory / feature-flow / test-report / technical-design consistent | MET | test-report §4-5; this §2-3 |

## 4. Findings

- **P0 (blocking, must fix before convergence):** 0.
- **P1 (blocking, requires new candidate + review round):** 0.
  - The fixture/codebase not-wired item was the only candidate; resolved as a
    documented deferral (§2), not a defect.
- **P2 (non-blocking):** 2.
  - P2-1: `dev-cycle-2.md` §7 data-flow diagram shows fixture selected; code
    STOPs at `fixture_pending`. Doc-accuracy. Fix: annotate §7 (recommended)
    or wire later.
  - P2-2: `.claude/settings.json` does not yet map PreToolUse/ConfigChange/
    permissions/sandbox defaults (item 8 DEFERRED). Track as a Cycle 5 E2E
    prerequisite; ensure the product settings.json is assembled then.
- **DEFERRED (Cycle 5, require human sign-off for Cycle 2 partial exit per §9):**
  - OS sandbox deny-write/deny-execute runtime evidence (BLOCKING GAP, AC-03).
  - Hook registration in `settings.json` (dev-safety + Cycle 5 real E2E).
  - Independent adversarial convergence review (this is a main-agent fallback).

## 5. Rule coverage (rule-traceability.md headline)

- 46 governed executable rules, CODE_AS_READ 2026-07-22.
- Cycle 2 upgraded a cohort from PLANNED/PARTIAL to IMPLEMENTED (Cycle 2) for
  the security/scope/semantic/source/portability/hook families; analysis
  (REQ/SEM analyze), model/knowledge maintenance, and evaluation rules remain
  PLANNED: Cycle 3/4/5 (out of Cycle 2 scope, correctly not pre-implemented).
- No rule is falsely claimed `verified`; sandbox/managed are marked
  NOT YET EXERCISED / PRODUCTION BLOCKER.

## 6. Independence and honesty disclosure (FBK-003, ANS-003)

- This evaluation is a **main-agent fallback**, not an independent adversarial
  review. The `plan-agent`/`test-agent` dispatches did not complete due to
  transient API errors (429 then 500), not logic failures.
- "CONVERGED" below means: *the main agent's deterministic reconciliation
  finds no blocking technical gap for Cycle 2 scope.* It does **not** mean
  silent failure is eliminated, and it does **not** substitute for the
  independent review the completion contract requires.
- The Cycle-5-deferred items (sandbox BLOCKING GAP, hook registration,
  independent review) are explicit and must be resolved before Harness v1
  COMPLETE; they are not hidden by this convergence status.
- Per `dev-cycle-2.md` §9, the sandbox BLOCKING GAP means Cycle 2 exits
  **partially**; a human decides whether to allow the partial exit.

## 7. STATUS

```
STATUS: CONVERGED (main-agent fallback; pending independent re-review)
P0: 0   P1: 0   P2: 2 (doc-accuracy + deferred settings.json mapping)
DEFERRED to Cycle 5 (human sign-off for partial exit):
  - OS sandbox deny runtime evidence (BLOCKING GAP)
  - Hook registration in settings.json
  - Independent convergence review
```

Cycle 2 may enter **CONVERGED** for its offline-exercisable scope. The two P2
findings do not block. The deferred items are escalated, not concealed. No
new implementation round (NEEDS_ITERATION) is required.

## 8. Recommended next actions (non-blocking, for the human)

1. Decide whether to accept this main-agent fallback convergence or wait for
   an independent `plan-agent` re-review once the API is stable.
2. Decide P2-1 resolution: annotate §7 now (doc-only) vs. wire
   `FixtureAdapter` into `select_adapter` as an early Cycle 3 item.
3. On Cycle 2 partial-exit acceptance: record `cycle_2_converged` in
   `orchestrator-state.md` and sync the product snapshot to the `chatbi`
   output directory.
4. Carry the Cycle-5-deferred BLOCKING GAPS forward explicitly into the
   Cycle 3 plan.
