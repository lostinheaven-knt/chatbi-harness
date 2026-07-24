# Cycle 2 Test Checklist (v3)

> Provenance: This checklist was produced by the **main agent as a fallback**
> after the `test-agent` dispatch failed twice on transient API errors (HTTP
> 429 quota-exceeded, then HTTP 500 server-side `sensitive_words_detected`).
> It is a deterministic reconciliation of `docs/feature-flow-v3.md` flows and
> `docs/dev-cycle-2.md` §8 test matrix against the actual test files. It is
> **not** an independent adversarial review (REV-001/REV-002); a true
> independent re-review should run when the API is stable. See
> `docs/optimization-checklist-v3.md` §6.

## 1. Scope

Cycle 2 delivers six code-grounded flows (`docs/feature-flow-v3.md`):

| Flow | Artifact | Tested in |
| --- | --- | --- |
| A. `policy.decide` | `.claude/lib/chatbi_harness/policy.py` | `tests/harness/test_security.py` (policy) |
| B. Adapter selection chain `select_adapter` | `adapters/__init__.py:495-718` | `tests/harness/test_adapters.py` (selection) |
| C. `FixtureAdapter` (Ticket 03) | `adapters/fixture.py` | `tests/harness/test_adapters.py` (FixtureAdapter) |
| D. `CodebaseReader` (Ticket 04) | `adapters/codebase_reader.py` | `tests/harness/test_adapters.py` (CodebaseReader) |
| E. PreToolUse gate (Ticket 05) | `.claude/hooks/pretool_guard.py` | `tests/harness/test_security.py` (PreToolUse) |
| F. ConfigChange gate (Ticket 06) | `.claude/hooks/config_change_gate.py` | `tests/harness/test_security.py` (ConfigChange) |

Plus Cycle 1 regression: `test_config`, `test_paths`, `test_hooks`,
`test_gates`, `test_diagnostics`, `test_contract`.

## 2. Required coverage (dev-cycle-2.md §8 test matrix)

### 2.1 policy unit (§8 row 1)

- [x] access/PII/approval/tool-group/risk judgments are deterministic
      (`test_security.py`, 22 policy tests).
- [x] insufficient authorization / missing PII / protected action blocked.
- [x] high-risk requires sign-off; protected action cannot be self-approved.
- [x] no canary secret leak in any decision output.

### 2.2 adapter protocol (§8 row 2)

- [x] selection chain three branches managed->CLI->STOP
      (`test_adapters.py:761` `# select_adapter`).
- [x] CLI argv validation: shell metacharacters / newlines / sensitive flags
      rejected (`validate_cli_argv`).
- [x] evidence schema includes hashes; no machine path / shell fallback.

### 2.3 Fixture (§8 row 3)

- [x] test mode returns stable evidence; production mode blocks (PORT-001).
- [x] catalog contains metrics/dimensions/segments.
- [x] **Known gap (documented, not blocking):** `FixtureAdapter` is tested by
      direct construction only; `select_adapter` STOPs at `fixture_pending`
      (`adapters/__init__.py:696-706`, `test_adapters.py:1235-1237`). Wiring
      into the selection chain is a deferred, later-cycle scope item; §10
      line 491 only requires the three branches + Fixture production-fallback
      block, both satisfied.

### 2.4 codebase_reader (§8 row 4)

- [x] read-only: no execute/write/install/commit
      (`CodebaseReaderScopeBlockTests`).
- [x] external content wrapped `untrusted=true`; portable reference
      alias+relative_path+revision.
- [x] prompt-injection instruction candidates ignored and logged (SCOPE-003);
      SRC-002 conflicts disclosed.
- [x] symlink escape blocked (runs on Darwin; conditional skip only if a
      symlink cannot be created).
- [x] **Known gap (documented, not blocking):** `CodebaseReader` is a read
      accessor for Business Codebases, not a `semantic`/`query` adapter; it is
      not routed through `select_adapter` by design (`feature-flow-v3.md`
      §4 "Known gap"). No `codebase:` branch exists or is required.

### 2.5 PreToolUse contract (§8 row 5)

- [x] valid/malicious/oversized/malformed JSON, cwd mismatch -> exit 0/2 per
      contract (17 PreToolUse tests).
- [x] rule_ids / evidence / recovery complete on block; no leak.
- [x] continuous TOCTOU re-validation (point-time -> continuous gate).
- [x] unknown event fields ignored; only cwd/tool_name/tool_input/tool_use_id
      validated.

### 2.6 permission-layer deny (§8 row 6)

- [x] offline deterministic block: settings deny + PreToolUse external
      write/execute. Exact command recorded in `test-report-v3.md`.

### 2.7 sandbox-layer deny (§8 row 7) — BLOCKING GAP

- [ ] real OS sandbox deny-write/deny-execute **cannot be exercised** in this
      offline unit-test environment. The single skipped test
      (`test_security.py:1139`) records this as a HIGH deviation / AC-03
      BLOCKING GAP, deferred to Cycle 5 real E2E. **Not faked with a Prompt
      test; not downgraded to warn.** Recorded in
      `docs/harness/compatibility.md` PRODUCTION BLOCKER.

### 2.8 ConfigChange contract (§8 row 8)

- [x] valid/downgrade/secret/managed-policy -> revalidate pass/block (16
      ConfigChange tests).
- [x] managed policy source produces notified feedback (exit 0), not assumed
      blocking.
- [x] no leak.

### 2.9 offline vertical + compatibility (§8 rows 9-10)

- [x] synthetic Workspace + codebase + Fixture runs policy->adapter->
      codebase->PreToolUse end-to-end offline.
- [x] version/doctor/platform probes record facts; sandbox/managed marked
      NOT YET EXERCISED / PRODUCTION BLOCKER (no `verified` claim).

## 3. Acceptance mapping (dev-cycle-2.md §10)

| §10 item | Status | Evidence |
| --- | --- | --- |
| Tickets approved, executing-plans loaded | MET | orchestrator-state |
| Task 1-6 files exist, non-empty, owned, no unplanned prod files | MET | inventory §4 of test-report |
| `policy.py` deterministic; protected action not self-approvable | MET | 22 policy tests |
| selection chain 3 branches + CLI argv + Fixture prod-block | MET | 77 selection + 54 fixture tests |
| codebase_reader read-only + untrusted + injection-ignored + portable ref | MET | 52 codebase tests |
| PreToolUse continuous TOCTOU + external write/exec blocked | MET | 17 PreToolUse tests |
| ConfigChange revalidate schema/path/sandbox/permissions | MET | 16 ConfigChange tests |
| `.claude/settings.json` maps PreToolUse/ConfigChange/permissions/sandbox | **DEFERRED** | dev + product settings.json are SessionStart-only; hook registration deferred to Cycle 5 E2E (dev-safety: a blocking PreToolUse hook hot-reloads and bricks the dev session — see memory `pretool-use-hook-dev-deadlock`). Hooks exist and are unit-tested; live registration is Cycle 5. |
| Claude permission deny + OS sandbox deny separate command evidence | **PARTIAL / DEFERRED** | permission-layer deny = MET (offline deterministic); OS sandbox deny = BLOCKING GAP deferred to Cycle 5 (§9). |
| managed runtime = official-only / NOT YET EXERCISED | MET | `ManagedAdapter._unavailable` |
| no canary secret/PII/absolute Workspace path in any hook output | MET | test assertions + `rg` sweep |
| `unittest discover` all green | MET | 323 tests, OK (skipped=1) |
| rule-traceability per-rule Cycle 2 evidence | MET | `docs/harness/rule-traceability.md` |
| security.md / compatibility.md distinguish verified/official-only/blocked | MET | `docs/harness/{security,compatibility}.md` |
| feature-flow-v3 from code with line refs/branches/dataflow/gaps | MET | `docs/feature-flow-v3.md` (185 file:line refs) |
| test-agent report + plan-agent checklist no CRITICAL/HIGH + CONVERGED | **FALLBACK** | produced by main agent (this file + `optimization-checklist-v3.md`); independent re-review pending API stability |
| inventory / feature-flow / test-report / technical-design consistent | MET | test-report §4-5 |

## 4. Gaps carried forward (non-blocking, documented)

1. **FixtureAdapter not wired into `select_adapter`** — deferred scope; P2
   doc-accuracy: `dev-cycle-2.md` §7 data-flow diagram shows fixture being
   selected, but the code STOPs at `fixture_pending`. Reconcile by annotating
   §7 or wiring in a later cycle (low-risk, mode-gated).
2. **CodebaseReader not in selection chain** — by design (read accessor, not a
   semantic/query adapter); no action.
3. **OS sandbox deny** — BLOCKING GAP, Cycle 5.
4. **Hook registration in settings.json** — deferred to Cycle 5 E2E
   (dev-safety).
5. **Independent convergence review** — main-agent fallback; re-run when API
   stable.

## 5. Verdict

All Cycle 2 acceptance tests that can be exercised offline are green (322
passed). The single skip is an explicitly documented BLOCKING GAP, not a
silent pass. No blocking test gap exists for Cycle 2 scope. The deferred items
(sandbox, hook registration, independent review) are documented and escalated,
not hidden.
