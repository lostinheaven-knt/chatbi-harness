# Cycle 3 Test Report (v4)

> Provenance: produced by the **main agent** (direct-implementation mode, user-
> authorized 2026-07-23 for speed after subagent dispatch latency/hangs). Real
> test-run output, not narration. **Not** an independent adversarial review
> (REV-001/REV-002); an independent `plan-agent`/`test-agent` re-review can run
> now that the API quota has reset - see `optimization-checklist-v4.md` §6.

## 1. Environment

- Date: 2026-07-23. Platform: Darwin 24.6.0 (offline unit-test env). Python 3.14.
- Workspace: `chatbi-cc-dev/` (dev, source of truth). Runner: `python3 -B`.

## 2. Canonical command and result

```text
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
```

```text
Ran 429 tests in 22.5s
OK (skipped=1)
```

Cycle 3 target trio:

```text
python3 -B -m unittest tests.harness.test_review_gate tests.harness.test_analysis tests.harness.test_e2e
Ran 106 tests in 4.5s
OK
```

**STATUS: ALL_PASSED** (428 passed; 1 skipped = documented BLOCKING GAP).
Cycle 1 + 2 + 3 regression green. Growth: 323 (Cycle 2 close) -> 429 = +106.

## 3. Per-module breakdown

| Test file | Tests | Status |
| --- | ---: | --- |
| `test_adapters.py` | 183 | passed (Cycle 2) |
| `test_security.py` | 55 | passed (Cycle 2; 1 skip = sandbox) |
| `test_analysis.py` | 59 | passed (54 Task 01 + 5 Task 06 integration) |
| `test_config.py` | 34 | passed (Cycle 1) |
| `test_paths.py` | 21 | passed (Cycle 1) |
| `test_review_gate.py` | 32 | passed (Task 03) |
| `test_e2e.py` | 15 | passed (Task 06) |
| `test_diagnostics.py` | 8 | passed (Cycle 1) |
| `test_hooks.py` | 9 | passed (Cycle 1) |
| `test_contract.py` | 7 | passed (Cycle 1) |
| `test_gates.py` | 6 | passed (Cycle 1) |
| **Total** | **429** | **428 passed, 1 skipped** |

## 4. The single skip (BLOCKING GAP, continues from Cycle 2)

- Test: `test_security.py:1139` (OS sandbox deny-write/deny-execute).
- Reason: real CC sandbox is a runtime feature of a logged-in Claude process
  with no offline invocation surface; Darwin `sandbox-exec` is not a valid proxy.
- Classification: HIGH deviation, AC-03 BLOCKING GAP. Deferred to Cycle 5. Not
  faked; not downgraded. Recorded in `docs/harness/compatibility.md`.

## 5. Per-flow coverage (feature-flow-v4.md)

| Flow | Covered | Notes |
| --- | --- | --- |
| A. evidence + schemas | yes (59) | SHA binding, sanitization, schema validation, degradation chain |
| B. reviewer contract | yes (via C) | 11 coverage, PASS/BLOCKED/ERROR, SHA binding (tested in test_review_gate) |
| C. SubagentStop gate | yes (32) | PASS+SHA exit 0; stale/missing/block/recursion exit 2; field tolerance |
| D. Stop gate | yes (in 32) | open block finding exit 2; warn/info-only exit 0 |
| E. /chatbi-analyze 5-layer | yes (15) | T1-hit / T1->T2->T3 / clarify / block; 5 stress scenarios |
| F. footer + offline E2E | yes (15) | full loop; footer 17 fields; no-evidence bypass; canary no-leak |

## 6. Canary / disclosure sweep

`rg -n '/Users/admin|BEGIN .*PRIVATE KEY|api[_-]?key|token\s*[:=]' .claude docs/harness`
-> no real secret/machine-path hit. Deliberate canary fixtures
(`sk-secret-canary`, `/home/canary/...`, `canary@example.com`) appear only in
test assertions (`assertNotIn`) and reviewer prohibition examples; non-real.

## 7. settings.json

Untouched (mtime Jul 22 18:45:00 2026). Still SessionStart-only. The
`subagent_review_gate.py` / `stop_gate.py` hooks are delivered as scripts +
offline tests but NOT registered (dev-safety; live registration = Cycle 5).

## 8. Gaps and limitations

1. OS sandbox deny - BLOCKING GAP, skipped, Cycle 5.
2. Reviewer is a SYNTHETIC contract producer in `test_e2e.py`; real Claude
   reviewer process = Cycle 5.
3. Hook registration in `settings.json` - deferred to Cycle 5.
4. PII redaction is email-only in `evidence.py` (phone/SSN deferred).
5. Independent convergence review - this report is main-agent direct, not
   independent.

## 9. Verdict

Cycle 3 target tests + Cycle 1/2 regression: **ALL_PASSED** (429 tests, 1
documented BLOCKING-GAP skip). No silent failure. No canary leak. Acceptance
for the offline-exercisable scope is met; deferred items are explicit.
