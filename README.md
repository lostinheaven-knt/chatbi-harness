# ChatBI Harness

A governed, multi-runtime harness for an **Agent-operated Warehouse**. One
governance contract, two deployment targets:

- **Claude Code target** - governed commands + skills with deterministic
  fail-closed hooks and an independent adversarial reviewer.
- **Agno target** (agno 2.6.22) - the same governance kernel + workflows
  re-expressed as a single governed agent: 19 `chatbi_*` governance tools, a
  six-layer tool-hook chain, run-level guardrails, and a delivery gate,
  served through the existing agno backend (`/agents/chatbi-agno/runs`) and
  the existing agent-ui frontend - no new API surface, no workflow router.

> **Status: AS_BUILT** (2026-08-13). Conformance 26/26 golden scenarios
> byte-identical on both targets. Offline regression: 761 harness tests
> (system python) + 337 agno-runtime tests (agno venv). Live acceptance with
> real MySQL 125-table source + real dbt: from-zero bootstrap 19/19,
> Business Codebase crosscheck 5/5, BFR/EXT 43/43, full journey (需求→初始化→
> 扩源→ODS→DWD→DWS→ADS) 41/41. Real-model adherence measured 15/15 (not
> assumed). Known seams are registered honestly in the acceptance manual
> (`harness/docs/agno-acceptance-manual.md`): AgentOS 2.6.22 cannot resolve
> mid-run HITL approvals on resume (SEC-003 fail-closed), and the real
> adversarial reviewer shows verdict variance on long chains.

## What's in this repo

This repo is the **dev source of truth** for the harness. The installable
product lives under `harness/`:

- `harness/.claude/{lib,hooks,commands,skills,schemas,fixtures,rules}` - the
  harness code + governed contract (the governed executable rules across the
  14 families, plus the multi-runtime families MR/META; aggregated in
  `rules/00-domain-contract.md`, `rules/10-security.md`,
  `rules/20-completion.md` - the authoritative rule-to-code map is
  `harness/docs/harness/rule-traceability.md`).
- `harness/packages/` - the governance kernel (evidence, impact, evaluator,
  bootstrap, build-plan, drift, policy, adapters).
- `harness/runtimes/` - runtime adapters: `claude_code/` (probe/adapter/
  manifest/reconcile) + `agno/` (agent builder, 19 governance tools, hooks,
  guardrails, reviewer, deployment_bindings).
- `harness/workflows/` + `harness/prompts/` - the IR workflow definitions +
  sha256-pinned prompt assets (9 runbooks in `.claude/skills/chatbi-*`).
- `harness/conformance/` - frozen golden/expected baselines (26 scenarios).
- `harness/install.sh`, `harness/launch_agno.py` - the installer (both
  targets) + the agno service launcher.
- `harness/CLAUDE.md`, `harness/CONTEXT.md`, `harness/e2e-state.py` - contract
  + vocabulary + e2e state.
- `harness/docs/` - domain model, harness docs, and the agno acceptance
  manual.
- `harness/product-README.md` - the product README (renamed to `README.md`
  in the built product).

Dev-only files (orchestrate workflow `.claude/`, `tests/`, dev `docs/`,
`.scratch/`, `AGENTS.md`) are gitignored - they live locally during
development but are NOT tracked, so they do not appear in a fresh clone. The
harness product (`harness/`) is the only tracked deployable.

## Deploy the harness (into a Warehouse Workspace)

**Self-use (recommended): run `install.sh` directly from `harness/`** - no
build step needed:

```sh
git clone <repo> chatbi-cc-dev
cd chatbi-cc-dev
./harness/install.sh /path/to/my-workspace                # Claude Code target (default)
./harness/install.sh /path/to/agno-workspace --target agno  # agno-only target
```

- `--target all` (default) installs the full harness: `.claude/` (hooks,
  commands, skills, rules, settings), both runtimes, workflows, prompts,
  conformance, docs, `launch_agno.py`. The installer verifies a Python 3.10+
  binding outside the workspace boundary and prints the `CHATBI_PYTHON` to
  set (SessionStart re-validates it every session).
- `--target agno` installs the trimmed agno-only surface: skips the CC
  execution surface (`.claude/{commands,hooks,lib,schedules,settings.json}`,
  `runtimes/claude_code`, `CLAUDE.md`, `CONTEXT.md`, `e2e-state.py`), keeps
  `.claude/{agents,fixtures,rules,schemas,skills,chatbi-harness.json}`,
  `packages/`, `runtimes/agno`, `workflows/`, `prompts/`, `conformance/`,
  `docs/`, `launch_agno.py`.

### Claude Code target

1. `export CHATBI_PYTHON=/opt/homebrew/bin/python3` (homebrew macOS; or any
   3.10+ outside the workspace) - needed by the SessionStart diagnostic.
2. (Live gating) Register the deterministic hooks in `.claude/settings.json`.
   The shipped `settings.json` is SessionStart-only (safe default). For live
   PreToolUse/PostToolUse/SubagentStop/Stop/ConfigChange gating, see
   `harness/docs/harness/e2e-checklist.md`. **Register live hooks only in a
   throwaway E2E workspace** - a blocking hook hot-reloads `settings.json`
   and can deadlock a dev session.
3. Run `/chatbi-init` to produce capability + production-readiness evidence.

### Agno target

1. Provide the deployment-boundary config `<ws>/deployment.json` (machine
   paths only here, PORT-001): `agno_main` (agno installation root),
   `cli_allowlist` (absolute mysql/dbt binaries), `warehouse_db`. Env
   overrides: `CHATBI_AGNO_MAIN` (also `CHATBI_AGNO_PORT`, default 7778).
   Missing bindings are fail-closed - no silent defaults.
2. Launch the governed service with your agno venv python:
   `CHATBI_AGNO_MAIN=<agno-main-root> <agno-venv>/bin/python launch_agno.py`
3. Drive it by conversation through the existing agent-ui frontend (or the
   `/agents/chatbi-agno/runs` API) - natural utterances trigger the runbooks
   via the skills' when-to-use metadata; the request-first precondition, the
   delivery-gate candidate contract, and the GOVERNANCE_PROTOCOL preamble
   keep the model on the governed rails deterministically.
4. Full operator runbook + from-zero reset steps: `docs/agno-acceptance-manual.md`.

## `build-product.sh` - build verification + distribution artifact (NOT required for deploy)

`./build-product.sh` builds a clean installable snapshot into `../chatbi`
(sibling, outside this repo). It is **optional for deployment** - `install.sh`
deploys directly from `harness/` without it. Use it when you want to:

- **Verify the build** before deploying: the script runs an `import` canary
  (catches broken kernel/runtime imports), a leak sweep (no machine absolute
  paths / secrets in the shipped tree), `py_compile` on `launch_agno.py`, and
  a dev-only-absent check (no dev files leaked into the product).
- **Produce a distributable artifact**: zip `../chatbi/` for users who should
  not clone the dev repo. `../chatbi/install.sh` deploys from `../chatbi/`
  (same `--target` flag).

```sh
./build-product.sh              # builds ../chatbi (clean installable snapshot)
./build-product.sh /tmp/chatbi  # or a custom destination
```

## The nine commands

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
| `/chatbi-audit-drift` | triage drift candidates + route to the target maintenance command |

## Governance model (brief)

- **T1 -> T2 -> T3 source tiers**: try the human-governed semantic layer first;
  degrade only with recorded evidence; historical SQL is a clue only.
- **Independent adversarial review**: every candidate data conclusion passes an
  isolated, read-only `adversarial-reviewer` (11 coverage dimensions, SHA-bound)
  before delivery. The main agent cannot self-certify (REV-001).
- **Deterministic gates**: on the CC target, thin hooks (PreToolUse/
  PostToolUse/SubagentStop/Stop/ConfigChange) enforce scope/security/semantic
  boundaries fail-closed with rule_ids + sanitized evidence + recovery. On the
  agno target, the same rules are enforced by the six-layer tool-hook chain
  (realpath -> sanitize -> allowlist -> approval_verify -> domain -> event)
  plus run-level guardrails and the delivery gate (a final candidate object
  contract; prose-only endings are conversational hand-offs, C002). Request-
  first precondition: analyze-scoped tools deny until a request is recorded.
- **Human-gated protected actions**: canonical metric definitions, access
  policy, production publication, destructive migration require human sign-off
  (SEM-003). The agent may draft, never approve.

See `harness/docs/chatbi-harness-domain-model.md` (the governed rules),
`harness/docs/harness/rule-traceability.md` (rule-to-code map), and
`harness/docs/agno-acceptance-manual.md` (agno target acceptance) for the
full picture.

## Honest capability reporting

Fixture/offline behavior is test evidence, never a silent production fallback.
`harness/docs/harness/compatibility.md` distinguishes VERIFIED LIVE / VERIFIED
OFFLINE / NOT YET EXERCISED / BLOCKING GAP for every capability. Real-model
adherence is measured (15/15 on the bootstrap instruction) - not assumed -
and every known seam (approval resume, reviewer variance) is registered in
the acceptance manual. Evaluation success is evidence, not a guarantee silent
failure is eliminated (FBK-003).
