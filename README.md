# ChatBI Harness

A governed Claude Code harness for an **Agent-operated Warehouse**. It binds one
Workspace, enforces 46 executable rules across 14 families (scope, security,
request, semantic, raw, source, doc, portability, quality, review, answer,
evaluation, ablation, feedback), and routes **eight slash commands** through a
deterministic, fail-closed gate layer with independent adversarial review.

> Status: **AS_BUILT**. 46/46 rules IMPLEMENTED. 634 offline tests green
> (1 skip = OS-sandbox BLOCKING GAP). 5/6 deterministic hooks live-confirmed
> in Claude Code 2.1.217. OS-sandbox runtime, real-reviewer-verdict-production
> loop, and production certification remain human/environment hard-gates (not
> faked, FBK-003). See `harness/docs/harness/compatibility.md`.

## What's in this repo

This repo is the **dev source of truth** for the harness. The installable
product lives under `harness/`:

- `harness/.claude/{lib,hooks,commands,skills,schemas,fixtures,rules}` - the
  harness code + governed contract.
- `harness/CLAUDE.md`, `harness/CONTEXT.md`, `harness/e2e-state.py` - contract
  + e2e state.
- `harness/docs/chatbi-harness-domain-model.md`, `harness/docs/harness/` - the
  46 rules + harness docs.
- `harness/install.sh` - the installer (deploys from `harness/`).
- `harness/product-README.md` - the product README (renamed to `README.md` in
  the built product).

Dev-only files (orchestrate workflow `.claude/`, `tests/`, dev `docs/`,
`AGENTS.md`, dev `CLAUDE.md`) are gitignored - they live locally during
development but are NOT tracked, so they do not appear in a fresh clone. The
harness product (`harness/`) is the only tracked deployable.

## Deploy the harness (into a Warehouse Workspace)

**Self-use (recommended): run `install.sh` directly from `harness/`** - no build
step needed:

```sh
git clone <repo> chatbi-cc-dev
cd chatbi-cc-dev
./harness/install.sh /path/to/my-workspace
```

`install.sh` copies `harness/.claude/` + `harness/docs/` + `harness/CLAUDE.md` +
`harness/CONTEXT.md` + `harness/e2e-state.py` into your Workspace root, verifies
a Python 3.10+ binding outside the boundary, and prints the `CHATBI_PYTHON` to
set. Then:

1. `export CHATBI_PYTHON=/opt/homebrew/bin/python3` (homebrew macOS; or any 3.10+
   outside the workspace) - needed by the SessionStart diagnostic.
2. (Live gating) Register the deterministic hooks in `.claude/settings.json`.
   The shipped `settings.json` is SessionStart-only (safe default). For live
   PreToolUse/PostToolUse/SubagentStop/Stop/ConfigChange gating, see
   `harness/docs/harness/e2e-checklist.md` for the exact 6-hook block. **Register
   live hooks only in a throwaway E2E workspace** - a blocking hook hot-reloads
   `settings.json` and can deadlock a dev session.
3. Run `/chatbi-init` to produce capability + production-readiness evidence.

## `build-product.sh` - build verification + distribution artifact (NOT required for deploy)

`./build-product.sh` builds a clean installable snapshot into `../chatbi`
(sibling, outside this repo). It is **optional for deployment** - `install.sh`
deploys directly from `harness/` without it. Use it when you want to:

- **Verify the build** before deploying: the script runs an `import` canary
  (catches broken `harness/.claude/lib` imports), a leak sweep (no machine
  absolute paths / secrets), and a dev-only-absent check (no dev files leaked
  into the product).
- **Produce a distributable artifact**: zip `../chatbi/` for users who should
  not clone the dev repo. `../chatbi/install.sh` deploys from `../chatbi/`.

```sh
./build-product.sh              # builds ../chatbi (clean installable snapshot)
./build-product.sh /tmp/chatbi  # or a custom destination
```

If you only want to deploy the harness to your own workspace, skip
`build-product.sh` and use `harness/install.sh` directly.

## The eight commands

| Command | Route |
| --- | --- |
| `/chatbi-init` | install / diagnose - capability + production-readiness evidence |
| `/chatbi-bootstrap` | from-zero local Warehouse scaffold (MySQL-only v1, dbt-mysql) |
| `/chatbi-build-from-requirement` | derive a build plan from a requirement + chain `/chatbi-maintain-model` (orchestrator; bridges analyze and maintain-model) |
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

See `harness/docs/chatbi-harness-domain-model.md` (the 46 rules) and
`harness/docs/harness/` for the full picture.

## Honest capability reporting

Fixture/offline behavior is test evidence, never a silent production fallback.
`harness/docs/harness/compatibility.md` distinguishes VERIFIED LIVE / VERIFIED
OFFLINE / NOT YET EXERCISED / BLOCKING GAP for every capability. Evaluation
success is evidence, not a guarantee silent failure is eliminated (FBK-003).
