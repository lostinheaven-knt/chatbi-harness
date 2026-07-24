# Cycle 2 Test Report (v3)

> Provenance: Produced by the **main agent as a fallback** after the
> `test-agent` dispatch failed twice on transient API errors (HTTP 429
> quota-exceeded resetting 2026-07-22 22:01 +0800; then HTTP 500 server-side
> `sensitive_words_detected`). Results below are real test-run output, not
> agent narration. **Not** an independent review (REV-001/REV-002); re-run
> `test-agent` when the API is stable.

## 1. Environment

- Date: 2026-07-22
- Platform: Darwin 24.6.0 (offline unit-test environment)
- Python: 3.14 (cpython-314 `.pyc` artifacts)
- Workspace: `/Users/admin/Downloads/workspace/chatbi-cc-dev` (dev, source of truth)
- Runner: `python3 -B` (bytecode-suppressed, canonical per dev-cycle-2 §10)

## 2. Canonical command and result

Command (dev-cycle-2.md §10 line 502):

```text
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
```

Result:

```text
..................................................................................................................................................................................................................................................................................................................................s
----------------------------------------------------------------------
Ran 323 tests in 17.668s

OK (skipped=1)
```

Cross-check with pytest:

```text
python -m pytest tests/harness/ -q --no-header
322 passed, 1 skipped, 162 subtests passed in 18.02s
```

**STATUS: ALL_PASSED** (322 passed; 1 skipped = documented BLOCKING GAP, not a
failure). Cycle 1 + Cycle 2 regression green.

## 3. Per-module breakdown

| Test file | Tests | Status |
| --- | ---: | --- |
| `tests/harness/test_adapters.py` | 183 | passed (selection 77, fixture 54, codebase 52) |
| `tests/harness/test_security.py` | 55 | 54 passed + 1 skipped (policy 22, PreToolUse 17, ConfigChange 16) |
| `tests/harness/test_config.py` | 34 | passed |
| `tests/harness/test_paths.py` | 21 | passed |
| `tests/harness/test_diagnostics.py` | 8 | passed |
| `tests/harness/test_contract.py` | 7 | passed |
| `tests/harness/test_hooks.py` | 9 | passed |
| `tests/harness/test_gates.py` | 6 | passed |
| **Total** | **323** | **322 passed, 1 skipped** |

(162 subtests across the suite.)

## 4. The single skip (BLOCKING GAP)

- Test: `test_security.py:1139` (sandbox deny-write/deny-execute).
- Reason (verbatim from skip message): real Claude Code sandbox
  deny-write/deny-execute cannot be exercised in this offline unit-test
  environment. The CC sandbox is a runtime feature of a logged-in Claude
  process with no offline invocation surface; Darwin `sandbox-exec` is a
  different mechanism and is not a valid proxy.
- Classification: **HIGH deviation, AC-03 BLOCKING GAP**.
- Disposition: runtime evidence deferred to Cycle 5 real E2E; recorded in
  `docs/harness/compatibility.md` PRODUCTION BLOCKER. **Not faked with a
  Prompt test; not downgraded to warn.**
- Rule refs: SEC-001/SEC-003, PORT-001, AC-03, FBK-003.

The conditional symlink skips in `test_adapters.py:2128/2151/2170/2209` did
**not** fire on this Darwin host (symlinks creatable), so they ran as real
tests - only the sandbox test skipped.

## 5. Per-flow coverage (feature-flow-v3.md)

| Flow | Covered | Notes |
| --- | --- | --- |
| A. `policy.decide` | yes (22) | access/PII/approval/tool-group/risk; protected action not self-approvable |
| B. `select_adapter` | yes (77) | managed->CLI->STOP; argv; allowlist; env; cwd; Fixture production-block |
| C. `FixtureAdapter` | yes (54) | direct construction; test-mode ok / production block PORT-001 |
| D. `CodebaseReader` | yes (52) | read/search/stat/git-metadata; scope-block; injection-ignore; symlink |
| E. PreToolUse gate | yes (17) | valid/malicious/oversized/malformed; cwd mismatch; TOCTOU; unknown-field ignore |
| F. ConfigChange gate | yes (16) | revalidate; downgrade/secret block; managed-policy feedback |

## 6. Canary / disclosure sweep

```text
rg -n '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token\s*[:=]' .claude docs/harness
```

No real secret, machine path, or private key in any Hook stdout/stderr or
evidence. Legitimate rule-ID patterns (`[A-Z]{2,5}-[0-9]{3}`) and documented
credential-env-name examples are distinguishable and expected.

## 7. File inventory (dev-cycle-2.md §10 line 489, no unplanned prod files)

Lib + hooks + fixtures (dev):

```text
.claude/lib/chatbi_harness/__init__.py
.claude/lib/chatbi_harness/config.py
.claude/lib/chatbi_harness/diagnostics.py
.claude/lib/chatbi_harness/gates.py
.claude/lib/chatbi_harness/paths.py
.claude/lib/chatbi_harness/policy.py
.claude/lib/chatbi_harness/adapters/__init__.py
.claude/lib/chatbi_harness/adapters/base.py
.claude/lib/chatbi_harness/adapters/codebase_reader.py
.claude/lib/chatbi_harness/adapters/fixture.py
.claude/hooks/config_change_gate.py
.claude/hooks/pretool_guard.py
.claude/hooks/python_binding_launcher.py
.claude/hooks/session_diagnose(.py)
.claude/fixtures/semantic-catalog.json
.claude/fixtures/warehouse.json
.claude/fixtures/config/*.json (8 negative/positive config fixtures)
.claude/fixtures/codebases/billing_app/** (Ticket 04 fixture codebase)
```

All Task 1-6 files present, non-empty, owned. No unplanned production file.
(`__pycache__/*.pyc` are build artifacts, not source.)

## 8. Gaps and limitations

1. **OS sandbox deny** - BLOCKING GAP, skipped, deferred to Cycle 5 (§4 above).
2. **FixtureAdapter not wired into `select_adapter`** - tested by direct
   construction only; documented deferral, not a Cycle 2 AC.
3. **Hook registration in settings.json** - dev + product settings.json are
   SessionStart-only; live registration deferred to Cycle 5 E2E (dev-safety).
4. **Independent review** - this report is a main-agent fallback; not an
   independent adversarial review.

## 9. Verdict

Cycle 2 target tests + Cycle 1 regression: **ALL_PASSED** (323 tests, 1
documented BLOCKING-GAP skip). No silent failure. No canary leak. Acceptance
for the offline-exercisable scope is met; the deferred items (sandbox,
hook registration, independent review) are explicit and escalated.
