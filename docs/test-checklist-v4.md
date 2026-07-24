# Cycle 3 Test Checklist (v4)

> Provenance: main-agent direct (user-authorized 2026-07-23). Deterministic
> reconciliation of `feature-flow-v4.md` flows and `dev-cycle-3.md` §8 matrix
> against the actual test files. **Not** an independent review (REV-001/002);
> see `optimization-checklist-v4.md` §6.

## 1. Scope

Six Cycle 3 flows (`feature-flow-v4.md`):

| Flow | Artifact | Tested in |
| --- | --- | --- |
| A. evidence + schemas | `evidence.py`, `schemas/{request,review,provenance}.schema.json` | `test_analysis.py` |
| B. reviewer contract | `agents/adversarial-reviewer.md` | `test_review_gate.py` (contract) |
| C. SubagentStop gate | `hooks/subagent_review_gate.py` | `test_review_gate.py` |
| D. Stop gate | `hooks/stop_gate.py` | `test_review_gate.py` |
| E. /chatbi-analyze 5-layer | `commands/chatbi-analyze.md`, `skills/chatbi-runbook/SKILL.md` | `test_e2e.py` |
| F. footer + offline E2E | full loop | `test_e2e.py` + `test_analysis.py` integration |

Plus Cycle 1/2 regression (`test_config`, `test_paths`, `test_hooks`,
`test_gates`, `test_diagnostics`, `test_contract`, `test_adapters`,
`test_security`).

## 2. Required coverage (dev-cycle-3.md §8 matrix)

### 2.1 evidence unit (§8 row 1) - MET
- [x] SHA binding / sanitization / schema validation / degradation chain
      (`test_analysis.py`: ComputeCandidateShaTests, EvidenceEntrySanitizationTests,
      DegradationChainTests, Request/Review/ProvenanceSchemaTests, CanarySweepTests,
      EvidenceIntegrationTests).
- [x] fail-closed on missing payload / non-serializable / non-idempotent sanitization.

### 2.2 reviewer contract (§8 row "reviewer contract") - MET
- [x] 11 coverage / finding structure / PASS-BLOCKED-ERROR / SHA binding /
      sanitization / missing-evidence block / recursion stop
      (`test_review_gate.py`, 32 tests).
- [x] `adversarial-reviewer.md` read-only, no mutating tools (verified: `tools:
      Read, Grep, Glob`).

### 2.3 review/stop gates (§8 row "review/stop 门控") - MET
- [x] SubagentStop PASS+SHA exit 0; stale/missing/block/recursion exit 2
      (`test_review_gate.py`).
- [x] Stop gate open-block-finding exit 2; warn/info-only exit 0.
- [x] unknown-field tolerance; fail-closed.

### 2.4 analyze 5-layer (§8 row "analyze 五层") - MET
- [x] T1-hit / T1->T2 / T2->T3 / no-evidence-bypass-fails
      (`test_e2e.py test_historical_sql_degrades_to_t3`,
      `test_no_evidence_bypass_fails`).
- [x] footer full fields (`test_delivered_footer_carries_all_required_fields`).

### 2.5 five stress scenarios (§8 row "五压力场景") - MET
- [x] ambiguity -> clarify, no fabrication.
- [x] stale -> freshness warning + signoff, BLOCKED.
- [x] historical-sql -> T1->T2->T3, PASS, SQL-not-canonical.
- [x] prompt-injection -> T1-hit, PASS, instructions logged not executed.
- [x] pii-permission -> block pre-T1, min_auth, BLOCKED.

### 2.6 offline vertical (§8 row "离线纵向") - MET
- [x] policy -> adapter -> evidence -> reviewer -> gate -> footer runs end-to-end
      (`test_e2e.py _run_flow`).

### 2.7 compatibility (§8 row "兼容性") - MET
- [x] reviewer offline-contract VERIFIED; real Claude reviewer NOT YET EXERCISED
      (Cycle 5). Sandbox BLOCKING GAP continues (1 skip).

## 3. §10 acceptance mapping

| §10 item | Status |
| --- | --- |
| Tickets approved, executing-plans loaded | MET |
| Task 1-7 files exist, non-empty, owned, no unplanned prod files | MET |
| `evidence.py` atomic/sanitized/SHA/fail-closed; 3 schemas full | MET |
| `adversarial-reviewer.md` self-contained, no mutating tools | MET |
| review/stop gates PASS+SHA, stale/missing/block/recursion exit 2, field tolerance | MET |
| `/chatbi-analyze` + runbook 5-layer, degradation, no-bypass, footer full | MET |
| 5 stress scenario fixtures green; no org facts/secrets/paths | MET |
| test_review_gate/test_analysis/test_e2e + Cycle 1/2 regression green | MET (429 OK, 1 skip) |
| no canary secret/PII/path in reviewer/evidence/gate stdout | MET |
| rule-traceability REQ/SEM/RAW/SRC/QLT/REV/ANS per-rule Cycle 3 | MET (17 upgraded) |
| analysis/security/compatibility distinguish offline vs real reviewer; sandbox gap | MET |
| feature-flow-v4 from code, line refs/branches/dataflow/gaps | MET |
| test-agent report + plan-agent CONVERGED | FALLBACK (main-agent direct) |
| inventory/feature-flow/test-report/technical-design consistent | MET |

## 4. Gaps carried forward (non-blocking, documented)

1. OS sandbox deny - BLOCKING GAP, Cycle 5.
2. Real Claude reviewer process - Cycle 5 (synthetic producer offline).
3. Live hook registration in settings.json - Cycle 5.
4. PII redaction email-only - P2 (broader PII = owner policy).
5. Independent convergence review - main-agent direct; optional re-review.

## 5. Verdict

All Cycle 3 acceptance tests exercisable offline are green (428 passed). The
single skip is a documented BLOCKING GAP, not a silent pass. No blocking test
gap for Cycle 3 scope. Deferred items are documented and escalated.
