# ChatBI Harness

A governed Claude Code harness for an **Agent-operated Warehouse**. It binds one
Workspace, enforces 46 executable rules across 14 families (scope, security,
request, semantic, raw, source, doc, portability, quality, review, answer,
evaluation, ablation, feedback), and routes six slash commands through a
deterministic, fail-closed gate layer with independent adversarial review.

> Status: **AS_BUILT (2026-07-24)**. 46/46 rules IMPLEMENTED. 533 offline tests
> green (1 skip = OS-sandbox BLOCKING GAP). 5/6 deterministic hooks live-confirmed
> in Claude Code 2.1.217. OS-sandbox runtime, real-reviewer-verdict-production
> loop, and production certification remain human/environment hard-gates (not
> faked, FBK-003). See `docs/technical-design.md` (STATUS: AS_BUILT) and
> `docs/harness/rule-traceability.md` §9.9.

## What's in this repo

This repo is the **dev source of truth**. It contains two things:

1. **The harness** (the product) - `.claude/{lib,hooks,commands,skills,schemas,
   fixtures,rules}`, `CLAUDE.md`, `CONTEXT.md`, `docs/harness/`,
   `docs/chatbi-harness-domain-model.md`, `e2e-state.py`.
2. **The orchestrate dev-workflow** that built it - `.claude/agents/{plan,coder,
   test}-agent.md`, `.claude/commands/orchestrate.md`, `tests/`, `.scratch/`
   (cycle tickets), and the dev docs: `docs/{technical-design,requirements,
   dev-cycles,dev-cycle-1..5,orchestrator-state,feature-flow-v1..6,test-*,
   optimization-*}.md`.

The **clean installable product** is a build artifact, produced by
`./build-product.sh` into `../chatbi` (sibling, outside this repo). The build
excludes the dev-workflow framework, tests, scratch, and dev docs.

## Build the product

```sh
./build-product.sh              # builds ../chatbi (clean installable snapshot)
./build-product.sh /tmp/chatbi  # or a custom destination
```

The script rsyncs the harness subset, excludes dev-only files, and runs an
import + canary check. Re-run anytime; it rebuilds from this source.

## Install the harness (into a Warehouse Workspace)

1. Copy the built product's `.claude/` + `docs/` + `CLAUDE.md` + `CONTEXT.md` +
   `e2e-state.py` into your Workspace root.
2. Set a Python 3.10+ binding (the harness uses `@dataclass(slots=True)`):
   `export CHATBI_PYTHON=/opt/homebrew/bin/python3` (homebrew) - needed by the
   SessionStart diagnostic.
3. Register the deterministic hooks in `.claude/settings.json` for live gating
   (see `docs/harness/e2e-checklist.md` for the exact 6-hook block + the live
   E2E procedure). **In a dev session, keep `settings.json` SessionStart-only**
   - a blocking PreToolUse/SubagentStop/Stop hook hot-reloads `settings.json`
   and can deadlock the session; register live hooks only in a throwaway E2E
   workspace.
4. Run `/chatbi-init` to produce capability + production-readiness evidence.

## The six commands

| Command | Route |
| --- | --- |
| `/chatbi-init` | install / diagnose - capability + production-readiness evidence |
| `/chatbi-analyze` | governed 5-layer analysis (clarify -> T1 -> T2 -> T3 -> independent PASS -> footer) |
| `/chatbi-maintain-model` | model/semantic maintenance + change-impact sync gate |
| `/chatbi-maintain-knowledge` | knowledge-reference authoring + lint |
| `/chatbi-evaluate` | fixed-suite evaluation (ground-truth isolated, FBK-003) |
| `/chatbi-correction` | dual-candidate correction (fix + eval case, owner-approved) |

## Governance model (brief)

- **T1 -> T2 -> T3 source tiers**: try the human-governed semantic layer first;
  degrade only with recorded evidence; historical SQL is a clue only.
- **Independent adversarial review**: every candidate data conclusion passes an
  isolated, read-only `adversarial-reviewer` (11 coverage dimensions, SHA-bound)
  before delivery. The main agent cannot self-certify (REV-001).
- **Deterministic gates**: thin Claude Code hooks (PreToolUse/PostToolUse/
  SubagentStop/Stop/ConfigChange) enforce scope/security/semantic boundaries
  fail-closed with rule_ids + sanitized evidence + recovery. First defense =
  PreToolUse + OS sandbox; PostToolUse is record-only, not undo.
- **Human-gated protected actions**: canonical metric definitions, access
  policy, production publication, destructive migration require human sign-off
  (SEM-003). The agent may draft, never approve.

See `docs/chatbi-harness-domain-model.md` (the 46 rules) and `docs/harness/`
for the full picture.

## How this harness was developed

Via the `/orchestrate` command + plan/coder/test-agent workflow over 5 cycles
(`docs/dev-cycle-1..5.md`, `docs/orchestrator-state.md`):

1. skeleton / config / paths / diagnosis
2. security depth / read-only codebase / adapter selection
3. governed analysis / independent adversarial review
4. model/knowledge maintenance / change-impact gating
5. evaluation / correction / live E2E / AS_BUILT

Each cycle: plan -> to-tickets -> implement (TDD) -> test -> convergence review.
`docs/feature-flow-v6.md` is the code-grounded flow reference.

## Honest capability reporting

Fixture/offline behavior is test evidence, never a silent production fallback.
`docs/harness/compatibility.md` distinguishes VERIFIED LIVE / VERIFIED OFFLINE /
NOT YET EXERCISED / BLOCKING GAP for every capability. Evaluation success is
evidence, not a guarantee silent failure is eliminated (FBK-003).
