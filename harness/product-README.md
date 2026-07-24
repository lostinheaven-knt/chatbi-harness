# ChatBI Harness

A governed Claude Code harness for an **Agent-operated Warehouse**. Six slash
commands, 46 enforced rules, deterministic fail-closed gates, and an independent
adversarial reviewer - so an agent can draft data answers but cannot self-certify,
fabricate, or cross the Workspace boundary.

> **Status:** AS_BUILT (2026-07-24). 46/46 rules IMPLEMENTED. Offline tests green.
> Deterministic hooks live-confirmed in Claude Code 2.1.217 (5/6 events + production-
> no-connection STOP). OS-sandbox runtime + production certification are
> human/environment hard-gates (not faked, FBK-003). See `docs/harness/compatibility.md`.

## Install

1. Copy this directory's `.claude/`, `docs/`, `CLAUDE.md`, `CONTEXT.md`, and
   `e2e-state.py` into your Warehouse Workspace root.
2. Set a Python 3.10+ binding (the harness uses `@dataclass(slots=True)`):
   ```sh
   export CHATBI_PYTHON=/opt/homebrew/bin/python3   # homebrew macOS; or /usr/bin/python3 if 3.10+
   ```
3. (Live gating) Register the deterministic hooks in `.claude/settings.json`.
   The shipped `settings.json` is SessionStart-only (safe default). For live
   PreToolUse/PostToolUse/SubagentStop/Stop/ConfigChange gating, see
   `docs/harness/e2e-checklist.md` for the exact 6-hook block. **Register live
   hooks only in a throwaway E2E workspace** - a blocking hook hot-reloads
   `settings.json` and can deadlock a dev session.
4. Run `/chatbi-init` to produce capability + production-readiness evidence.

## Commands

| Command | Purpose |
| --- | --- |
| `/chatbi-init` | install / diagnose |
| `/chatbi-analyze` | governed 5-layer analysis -> provenance footer |
| `/chatbi-maintain-model` | model change + impact sync gate |
| `/chatbi-maintain-knowledge` | knowledge-reference authoring + lint |
| `/chatbi-evaluate` | fixed-suite evaluation (ground-truth isolated) |
| `/chatbi-correction` | dual-candidate correction (fix + eval case, owner-approved) |

## Governance (brief)

- **T1 -> T2 -> T3**: semantic layer first; degrade only with recorded evidence.
- **Independent adversarial review**: every candidate answer passes an isolated,
  read-only reviewer (11 coverage dimensions, SHA-bound) before delivery.
- **Deterministic gates**: fail-closed with rule_ids + sanitized evidence +
  recovery. PostToolUse records impact, never undoes (first defense = PreToolUse
  + OS sandbox).
- **Human-gated**: canonical metrics, access policy, production publish,
  destructive migration require human sign-off (SEM-003).

## Where to look

- `docs/chatbi-harness-domain-model.md` - the 46 governed rules (authoritative).
- `docs/harness/` - security, compatibility, rule-traceability, analysis,
  maintenance, knowledge-authoring, evaluation, troubleshooting,
  negative-experiments, e2e-checklist.
- `CLAUDE.md` / `CONTEXT.md` - the harness contract + vocabulary.

## Honest reporting

Fixture/offline behavior is test evidence, never a silent production fallback.
`docs/harness/compatibility.md` distinguishes VERIFIED LIVE / VERIFIED OFFLINE /
NOT YET EXERCISED / BLOCKING GAP. Evaluation success is evidence, not a guarantee
silent failure is eliminated (FBK-003).
