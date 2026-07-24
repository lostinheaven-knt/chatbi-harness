# Troubleshooting

Diagnosis and recovery for the ChatBI Harness. The deterministic gates emit
rule_ids + sanitized evidence + a recovery action on every block; start there.

## 1. `/chatbi-init` / SessionStart

- **Symptom**: init reports a governed-model conflict or overlapping roots.
- **Cause**: `docs/chatbi-harness-domain-model.md` missing/unreadable, or
  overlapping Workspace roots (P0-05).
- **Recovery**: restore the domain model; correct the configured roots; re-run
  `/chatbi-init`. The SessionStart hook (`session_diagnose`) reports the specific
  rule_id.

## 2. Hook does not fire

- **Symptom**: a PreToolUse/PostToolUse/SubagentStop/Stop/ConfigChange gate never
  runs.
- **Cause**: the hook is not registered in `settings.json`. In the dev session
  hooks are intentionally NOT registered (a blocking hook hot-reloads
  `settings.json` and can deadlock the session - learned constraint).
- **Recovery**: live registration is a Cycle 5 E2E step, performed in the E2E
  environment (not the dev session). Run the Task 06 real E2E procedure.

## 3. OS sandbox unavailable

- **Symptom**: the sandbox deny test is skipped; `compatibility.md` records a
  PRODUCTION BLOCKER.
- **Cause**: the CC sandbox is a runtime feature of a logged-in Claude process
  with no offline invocation surface; Darwin `sandbox-exec` is not a valid proxy.
- **Recovery**: exercise the sandbox in the Task 06 real E2E. Do not fake it;
  do not downgrade to warn.

## 4. Production no-connection STOP

- **Symptom**: analysis/maintenance stops with "no usable adapter" (SEM-001,
  PORT-001).
- **Cause**: no managed or approved-CLI adapter configured/authorized; Fixture is
  test/example-only.
- **Recovery**: configure + authorize a real managed or approved-CLI adapter. Do
  not enable Fixture as a production fallback.

## 5. Reviewer isolation / SubagentStop

- **Symptom**: delivery blocked with REV-001/002/003; stale SHA forces a new
  round.
- **Cause**: the candidate changed (new SHA) after the last review, or a coverage
  item failed / a block finding is open.
- **Recovery**: re-run the `adversarial-reviewer` on the current candidate; do
  not reuse a stale PASS. The reviewer is read-only; it never mutates.

## 6. Login / keychain (Cycle 5 real E2E)

- **Symptom**: the real Claude Code 2.1.216 E2E cannot trigger events.
- **Cause**: not logged in; keychain/auth prerequisites unresolved.
- **Recovery**: the user resolves login/keychain in the E2E environment. The
  agent cannot self-login. Until resolved, Cycle 5 cannot exit (Task 06 hard
  gate).

## 7. PII / secret leak in output

- **Symptom**: a canary appears in gate stdout/stderr/evidence.
- **Recovery**: this should never happen - gates sanitize via `gates._sanitize_text`
  and emit leak-safe summaries. If it does, treat as a SEC-003/PORT-001 defect;
  do not ship. (Note: `evidence.py` PII redaction is email-only; broader PII
  patterns are owner-policy.)

## 8. Honest status

If a capability is not yet exercised, the docs say NOT YET EXERCISED / BLOCKING
GAP, never `verified`. Fixture behavior is test evidence, never a production
certification.
