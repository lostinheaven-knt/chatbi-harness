# ChatBI Harness

A governed, multi-runtime harness for an **Agent-operated Warehouse**. One
governance contract, two deployment targets: **Claude Code** (commands +
skills + deterministic fail-closed hooks + independent adversarial reviewer)
and **Agno** (agno 2.6.22 - the same governance kernel as a single governed
agent: 19 `chatbi_*` governance tools, a six-layer tool-hook chain, run-level
guardrails, and a delivery gate; served through the agno backend
`/agents/chatbi-agno/runs` and the existing agent-ui frontend).

> **Status:** AS_BUILT (2026-08-13). Conformance 26/26 golden scenarios
> byte-identical on both targets. Offline regression: 761 harness tests +
> 337 agno-runtime tests. Live acceptance with real MySQL + real dbt: from-zero
> bootstrap 19/19, crosscheck 5/5, BFR/EXT 43/43, full journey 41/41; real-model
> adherence measured 15/15. Known seams (mid-run HITL approval resume fail-closed,
> real-reviewer verdict variance) are registered in `docs/agno-acceptance-manual.md`.

## Install

```sh
./install.sh <workspace-root>                # Claude Code target (default, full install)
./install.sh <workspace-root> --target agno  # agno-only trimmed install
```

- `--target all` (default) installs the full harness: `.claude/` (hooks,
  commands, skills, rules, settings), both runtimes, workflows, prompts,
  conformance, docs, `launch_agno.py`. It then verifies a Python 3.10+
  binding outside the workspace boundary and prints the `CHATBI_PYTHON` to
  set (SessionStart re-validates it every session).
- `--target agno` installs the trimmed agno-only surface: skips the CC
  execution surface (`.claude/{commands,hooks,lib,schedules,settings.json}`,
  `runtimes/claude_code`, `CLAUDE.md`, `CONTEXT.md`, `e2e-state.py`), keeps
  `.claude/{agents,fixtures,rules,schemas,skills,chatbi-harness.json}`,
  `packages/`, `runtimes/agno`, `workflows/`, `prompts/`, `conformance/`,
  `docs/`, `launch_agno.py`. No CHATBI_PYTHON binding is required.

### Claude Code target

1. Set a Python 3.10+ binding (the harness uses `@dataclass(slots=True)`):
   ```sh
   export CHATBI_PYTHON=/opt/homebrew/bin/python3   # homebrew macOS; or /usr/bin/python3 if 3.10+
   ```
2. (Live gating) Register the deterministic hooks in `.claude/settings.json`.
   The shipped `settings.json` is SessionStart-only (safe default). For live
   PreToolUse/PostToolUse/SubagentStop/Stop/ConfigChange gating, see
   `docs/harness/e2e-checklist.md`. **Register live hooks only in a throwaway
   E2E workspace** - a blocking hook hot-reloads `settings.json` and can
   deadlock a dev session.
3. Run `/chatbi-init` to produce capability + production-readiness evidence.
4. Run `/chatbi-bootstrap` to scaffold a from-zero local Warehouse (MySQL-only
   v1): it writes local config (`cli_adapters.mysql` + optional
   `path_binding`), appends `cli:mysql` to shared `adapters.query`, creates the
   `dw` database (non-destructive), introspects the source schema, and emits a
   `.chatbi/bootstrap/source_inventory.json` hand-off for
   `/chatbi-maintain-model`.

### Agno target

1. Provide the deployment-boundary config `<ws>/deployment.json` (machine
   paths only here, PORT-001): `agno_main` (agno installation root),
   `cli_allowlist` (absolute mysql/dbt binaries), `warehouse_db`. Env
   overrides: `CHATBI_AGNO_MAIN` (also `CHATBI_AGNO_PORT`, default 7778).
   Missing bindings are fail-closed - no silent defaults.
2. Launch the governed service with your agno venv python:
   ```sh
   CHATBI_AGNO_MAIN=<agno-main-root> <agno-venv>/bin/python launch_agno.py
   ```
3. Drive it by conversation through the existing agent-ui frontend (or the
   `/agents/chatbi-agno/runs` API) - natural utterances trigger the runbooks
   via the skills' when-to-use metadata; the request-first precondition, the
   delivery-gate candidate contract, and the GOVERNANCE_PROTOCOL preamble
   keep the model on the governed rails deterministically.
4. Full operator runbook + from-zero reset steps: `docs/agno-acceptance-manual.md`.

## Commands

| Command | Purpose |
| --- | --- |
| `/chatbi-init` | install / diagnose |
| `/chatbi-bootstrap` | scaffold local Warehouse (config + dw + inventory, MySQL-only v1) |
| `/chatbi-build-from-requirement` | derive a build plan from a requirement + chain /chatbi-maintain-model (orchestrator; no governed authoring) |
| `/chatbi-analyze` | governed 5-layer analysis -> provenance footer |
| `/chatbi-maintain-model` | model change + impact sync gate |
| `/chatbi-maintain-knowledge` | knowledge-reference authoring + lint |
| `/chatbi-evaluate` | fixed-suite evaluation (ground-truth isolated) |
| `/chatbi-correction` | dual-candidate correction (fix + eval case, owner-approved) |
| `/chatbi-audit-drift` | triage drift candidates + route to the target maintenance command |

## Governance (brief)

- **T1 -> T2 -> T3**: semantic layer first; degrade only with recorded evidence.
- **Independent adversarial review**: every candidate answer passes an isolated,
  read-only reviewer (11 coverage dimensions, SHA-bound) before delivery.
- **Deterministic gates**: on the CC target, fail-closed hooks with rule_ids +
  sanitized evidence + recovery (first defense = PreToolUse + OS sandbox). On
  the agno target, the six-layer tool-hook chain (realpath -> sanitize ->
  allowlist -> approval_verify -> domain -> event) + run-level guardrails +
  the delivery gate (final candidate object contract, C002). Analyze-scoped
  tools deny until a request is recorded (request-first, REQ-001/HOOK-004).
- **Human-gated**: canonical metrics, access policy, production publish,
  destructive migration require human sign-off (SEM-003).

## Where to look

- `docs/chatbi-harness-domain-model.md` - the governed rules (authoritative).
- `docs/harness/` - security, compatibility, rule-traceability, analysis,
  maintenance, knowledge-authoring, evaluation, troubleshooting,
  negative-experiments, e2e-checklist.
- `docs/agno-acceptance-manual.md` - the agno target operator runbook
  (from-zero reset, conversation usage, L1-L4 + BFR/EXT + full journey,
  measured model adherence, registered seams).
- `CLAUDE.md` / `CONTEXT.md` - the harness contract + vocabulary (CC target).
- `launch_agno.py` / `runtimes/agno/` - the agno service launcher + runtime
  adapter (governed tools, hooks, guardrails, deployment_bindings).

## Honest reporting

Fixture/offline behavior is test evidence, never a silent production fallback.
`docs/harness/compatibility.md` distinguishes VERIFIED LIVE / VERIFIED OFFLINE /
NOT YET EXERCISED / BLOCKING GAP. Real-model adherence is measured (15/15 on
the bootstrap instruction) - not assumed - and every known seam is registered
in the acceptance manual. Evaluation success is evidence, not a guarantee
silent failure is eliminated (FBK-003).
