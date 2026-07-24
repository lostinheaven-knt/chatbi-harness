# Real Claude Code E2E Runbook (Cycle 5 Task 06 - human gate)

This is the **human-environment gate** for Cycle 5. The agent cannot self-login
or trigger real Claude Code hook events. The user runs this procedure in a
logged-in Claude Code 2.1.216 session (Darwin arm64) and records evidence. Until
it passes, Cycle 5 cannot exit and HOOK-003/005 remain PARTIAL.

## 0. Why a human gate

The deterministic hook contracts are verified OFFLINE (Cycle 2-5 unit tests).
The退出门 additionally requires a REAL Claude Code process to trigger the six
P0 hook events and the isolated `adversarial-reviewer`. That requires a logged-in
session the agent cannot create. Live hook registration is done in the E2E
environment ONLY (not the dev session - a blocking hook hot-reloads
`settings.json` and can deadlock the dev session; learned constraint).

## 1. Prerequisites

- Claude Code 2.1.216 (or the version in use), logged in, Darwin arm64.
- Keychain / auth resolved (the agent cannot resolve these).
- The harness product installed (the `chatbi/` output dir, or this dev workspace
  with `.claude/`).
- Record the exact CC version + model id (`claude --version` and the model in
  use) for the evidence log.

## 2. Register live hooks (E2E environment only)

Create an E2E `settings.json` that registers the hooks (do NOT use the dev
session's SessionStart-only `settings.json`). Register:

```json
{
  "hooks": {
    "SessionStart": [{"matcher": "startup|resume|clear|compact",
      "hooks": [{"type": "command", "command": ".claude/hooks/session_diagnose"}]}],
    "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|Bash|Read|Grep|Glob",
      "hooks": [{"type": "command", "command": "python3 -B -I .claude/hooks/pretool_guard.py"}]}],
    "PostToolUse": [{"matcher": "Edit|Write|MultiEdit|Bash",
      "hooks": [{"type": "command", "command": "python3 -B -I .claude/hooks/posttool_impact.py"}]}],
    "SubagentStop": [{"matcher": "*",
      "hooks": [{"type": "command", "command": "python3 -B -I .claude/hooks/subagent_review_gate.py"}]}],
    "Stop": [{"matcher": "*",
      "hooks": [{"type": "command", "command": "python3 -B -I .claude/hooks/stop_gate.py"}]}],
    "ConfigChange": [{"matcher": "*",
      "hooks": [{"type": "command", "command": "python3 -B -I .claude/hooks/config_change_gate.py"}]}]
  }
}
```

## 3. Trigger the six P0 events + isolated reviewer

Run a logged-in Claude Code session with the E2E `settings.json` and exercise
each event. For each, record: the exact command, the hook exit code, the stdout/
stderr (sanitized), and the model id.

1. **SessionStart**: start/resume the session; capture `session_diagnose` output.
2. **PreToolUse**: attempt an Edit/Write to an external (non-Workspace) path;
   expect exit 2 (SCOPE/SEC). Attempt a Workspace edit; expect exit 0.
3. **PostToolUse**: after a model change, feed an impact manifest; expect exit 0
   (synced) or exit 2 (blocking drift). Confirm `undo=false` in the record.
4. **SubagentStop**: run the `adversarial-reviewer` subagent on a candidate;
   confirm it is read-only (no mutating tools) and emits a `review.schema.json`
   verdict; feed a PASS+matching-SHA -> exit 0; a stale SHA -> exit 2.
5. **Stop**: end the workflow with an open block finding -> exit 2; clean -> 0.
6. **ConfigChange**: edit `chatbi-harness.json` (a blockable downgrade) -> exit 2;
   a valid change -> exit 0.

## 4. Production-no-connection STOP

With no managed/CLI adapter configured, run `/chatbi-analyze`; confirm it STOPs
fail-closed (no Fixture fallback) with SEM-001/PORT-001.

## 5. Sandbox (if the environment supports it)

If the CC sandbox is available, exercise deny-write/deny-execute; record the
real deny. If not available, record the BLOCKING GAP unchanged (do not fake).

## 6. Evidence log

Record results into `docs/harness/compatibility.md` (E2E evidence section): per
event - exact command, exit code, sanitized output, model id, CC version. State
whether each P0 event was triggered. If any P0 event cannot be triggered, Cycle 5
does NOT exit (record the blocker).

## 7. After Task 06 passes

- Upgrade HOOK-003/005 to IMPLEMENTED in `rule-traceability.md` (46/46).
- Task 07: plan-agent updates `docs/technical-design.md` to `STATUS: AS_BUILT`;
  final audit (inventory / 46-46 / AS_BUILT consistency / native-command
  evidence); claim Harness v1 COMPLETE.
- Re-sync the product to `chatbi/` with the E2E evidence.

## 8. Honest reporting

Real E2E evidence is the only acceptable substitute for the NOT YET EXERCISED /
BLOCKING GAP labels. Do not fake, do not downgrade, do not mock (FBK-003).
