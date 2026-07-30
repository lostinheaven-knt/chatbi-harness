# Modification: 需求驱动建造工作流 (`/chatbi-build-from-requirement`)

> Status: DESIGN. Per-module change list for the new 8th command and its
> supporting thin lib / bootstrap / maintain-model / product-doc changes.
> Grounded in `docs/feature-flow-requirement-driven-build-v1.md` (SCANNED) +
> `docs/orchestrator-state.md` 介入点① (4 confirmed decisions). No implementation
> code yet; this doc describes **what changes** per module. For **how** (API
> contracts, dataclass fields, test plan) see the companion
> `docs/technical-design-requirement-driven-build.md` (legacy step 5).

## 0. Context

New command `/chatbi-build-from-requirement` is an **orchestrator trust layer**
bridging `/chatbi-analyze` (query, T1->T2->T3 degrade, stops on "needs new
model") and `/chatbi-maintain-model` (single-model build). It derives a DWD/DWS/
ADS build plan from a requirement + DW state + blueprint, chains
`/chatbi-maintain-model` per model in dependency order, routes protected points
to the human, and hands off to `/chatbi-analyze` once models are in place. It
does NOT write governed model content (maintain-model does), NOT answer
(analyze does), NOT approve metrics (human does) -- the same "narrow trust
layer" shape as `/chatbi-bootstrap` (INFRA SETUP only).

Confirmed decisions (orchestrator-state.md:7-12, do not re-ask):
- 方案 A: new 8th command (orchestrator); name = `/chatbi-build-from-requirement`.
- 扩源闭环 = bootstrap **incremental** introspect (add incremental capability to
  bootstrap; ODS-missing-table -> human approves -> bootstrap introspects new
  source tables, appends to source_inventory).
- Registry 落点 = `.chatbi/model_registry.json` (derived evidence, appended by
  maintain-model after a successful build, NOT in config schema).
- Build plan itself does NOT pass independent review (REV-001 applies only to the
  final answer in Step 4); per-model sync gate (DOC-004) + answer review gate
  carry the review burden. Cross-layer rules go in blueprint `## Layers`
  (declarative, agent reads). No new governed rule (46 stays 46).

### Discrepancy flagged against the feature-flow scan

feature-flow §4 / :259 claims "bootstrap stub 已创建 `## Layers` header
(`chatbi-bootstrap/SKILL.md:191-203`)". **Verified false against as-built code**:
`chatbi-bootstrap/SKILL.md:186-203` Step 8 creates only `## Tooling` (`:189`) and
`## Metrics` (`:195`) headers. There is NO `## Layers` header. Module 5 below is
therefore an ADD (new header + skeleton in Step 8), not a "fill existing
placeholder" edit. See Module 5.

## 1. Module: new command `/chatbi-build-from-requirement`

### ADD `harness/.claude/commands/chatbi-build-from-requirement.md`
Mirror the structure of `harness/.claude/commands/chatbi-bootstrap.md`
(frontmatter `:1-4` -> `## 0. Trust boundary` `:13` -> `## 1. Bind to the
runbook` `:34` -> `## 2. Inputs` `:40` -> `## 3. Output evidence` `:50` ->
`## 4. Stop conditions` `:58` -> `## 5. Non-goals` `:71` -> `## Rules` `:85`).

- Frontmatter `description`: orchestrator that derives a build plan from a
  requirement + DW state + blueprint, chains `/chatbi-maintain-model` in
  dependency order, routes protected points (source boundary / metric approval /
  access policy / production publish / destructive migration) to the human, and
  hands off to `/chatbi-analyze` once models exist. Does NOT write governed
  content, answer, or approve (SEM-003 / META-008).
- `argument-hint`: `[requirement-text]` + optional `[granularity] [segment]`
  (requirement is wider than analyze's 7-field request: "build the metric system
  that can answer X", not "answer X").
- `## 0. Trust boundary`: orchestrator = derive + chain + human-in-loop +
  handoff. MAY read blueprint/source_inventory/model_registry/semantic layer;
  MAY call `/chatbi-maintain-model` per plan entry; MAY persist the build plan
  via `harness_state.write_state` (`harness_state.py:100-123`). MUST NOT author
  governed model content, approve a canonical metric, change access policy,
  publish, or run destructive migration (SEM-003), MUST NOT answer the business
  question (that is `/chatbi-analyze`), MUST NOT self-certify (META-008), MUST
  NOT extend the source boundary without human approval (SCOPE-001/SEC-001/
  RAW-003). No machine absolute paths / secrets / PII (SEC-003/PORT-001).
- `## 1. Bind to the runbook`: load `skills/chatbi-build/SKILL.md`.
- `## 2. Inputs`: requirement text + optional granularity/dimension/segment;
  reads blueprint (`docs/org/data-warehouse-blueprint.md` § Source / § Metrics /
  § Layers / § Tooling), `.chatbi/bootstrap/source_inventory.json`,
  `.chatbi/model_registry.json`, existing semantic layer via `select_adapter`
  (`adapters/__init__.py` select_adapter).
- `## 3. Output evidence`: build-plan summary (ordered model list with
  change_kind / target / upstream deps / join-or-aggregate summary / protected
  flags), per-model maintain-model footer aggregation, final hand-off to
  `/chatbi-analyze`. Distinguish observation from interpretation (FBK-003).
- `## 4. Stop conditions`: requirement ambiguity (REQ-001/002); source-boundary
  extend (ODS missing table -> STOP for human, SCOPE-001/SEC-001); metric
  definition (SEM-003 `approve_metric`); `validate_build_plan` /
  `validate_layer_dependency` raise `GateError` (HOOK-004); any maintain-model
  sync gate fails (DOC-004). Surface sanitized `GateDecision`, do not retry with
  a "fixed" value.
- `## 5. Non-goals`: no governed model authoring (route via maintain-model); no
  answer (route via analyze); no build-plan independent review (REV-001 is the
  answer gate only); no derivation lib (join/aggregate logic is agent reasoning,
  not a deterministic lib); no new governed rule.
- `## Rules`: REQ-001/002/003/004, RAW-003, SEM-001/003, SCOPE-001, SEC-001/003,
  PORT-001, META-003/008, DOC-001/002/004, HOOK-004, REV-001, ANS-003, FBK-003.
  No new rule; 46 stays 46.

### ADD `harness/.claude/skills/chatbi-build/SKILL.md`
Mirror the shape of `chatbi-bootstrap/SKILL.md` (frontmatter `name` +
`description`, `## 0. Sources this runbook binds to`, numbered procedure
sections, `## N. Applicable governing rules`). The 4-step flow
(feature-flow §3) maps to SKILL steps:

- `## 0. Sources`: lib surface `chatbi_harness.build_plan` (BuildPlan, ModelEntry,
  LayerRule, read_model_registry, read_source_inventory, validate_build_plan,
  validate_layer_dependency, append_model_registry); reused primitives
  `load_effective_config` (`config.py`), `select_adapter` (`adapters/`), `policy.
  decide` (`policy.py`), `harness_state.write_state/read_state`
  (`harness_state.py:63,100`), `fail_closed`/`GateError` (`gates.py:143-167`);
  schema `build-plan.schema.json`. Trust boundary restated (orchestrator).
- `## 1. Step 1 - Read requirement + DW state (pure read)`: parse requirement
  (multi-sense terms explicit, REQ-002; multi-team definitions listed not
  merged, REQ-004); read blueprint § Source/§ Metrics/§ Layers/§ Tooling;
  `read_source_inventory` + `read_model_registry`; discover T1 coverage via
  `select_adapter`. STOP on ambiguity (REQ-001/002). Cites RAW-003, SEM-001,
  SCOPE-001, SEC-003, PORT-001, DOC-001.
- `## 2. Step 2 - Derive build plan (agent reasoning)`: reuse existing DWD/DWS
  if T1 covers; else derive ODS->DWD (join keys/grain) ->DWS (aggregate dims/
  measure formula) ->ADS from blueprint § Metrics `DWD needed`/`DWS needed` +
  § Layers cross-layer rules + source_inventory existence. ODS missing table ->
  mark `requires_human_approval` extend-source, STOP (SCOPE-001/SEC-001/
  RAW-003). Metric definitions -> STOP for owner (SEM-003). Call
  `validate_build_plan` + `validate_layer_dependency` (HOOK-004 fail-closed).
  Cites SEM-003, SCOPE-001/SEC-001, RAW-003, DOC-002, PORT-001, META-003.
  **Explicit**: derivation is agent reasoning; the lib only reads + validates
  plan shape (no derivation lib).
- `## 3. Step 3 - Chain /chatbi-maintain-model per model (human at protected
  points)`: for each plan entry in dependency order (ODS->DWD->DWS->ADS), invoke
  `/chatbi-maintain-model` with the derived join/aggregate summary as
  change-request input. Each call runs maintain-model's full flow (classify ->
  read § Tooling/§ Metrics/§ Layers -> build_impact_manifest -> candidates ->
  sync gate DOC-004 -> stop_gate -> footer). Human at each protected point
  (SEM-003: approve_metric / change_access_policy / production_publish /
  destructive_migration; SEC-001/ANS-003 high-risk). Routine build + join/
  aggregate derivation = agent (no per-model human sign-off). Persist plan +
  progress via `harness_state.write_state` to `.chatbi/runs/<sid>/build_plan.json`
  (resumable). On maintain-model success, append ModelEntry to
  `.chatbi/model_registry.json` (Module 4).
- `## 4. Step 4 - Build ADS + hand off to /chatbi-analyze`: if the requirement
  needs an ADS layer, chain one final `/chatbi-maintain-model` (ADS depends on
  DWS, cross-layer per § Layers). Once models exist, hand off to
  `/chatbi-analyze` -- T1 now covers (new metric/dimension landed via
  maintain-model's semantic change_kind), so analyze no longer STOPs on
  "needs new model" (`chatbi-analyze.md:206-207` gap closed). Answer passes
  analyze's independent review gate (adversarial-reviewer +
  subagent_review_gate + stop_gate, REV-001/002/003; high-risk sign-off
  ANS-003). Cites SEM-001, REV-001/002/003, ANS-001/002/003, QLT-001, FBK-003.
- `## 5. Applicable governing rules`: the union above; no new rule. State
  `validate_domain_contract` (`gates.py:170-233`) still passes (contract
  artifacts unchanged).

**Dependency**: depends on Module 2 (lib) for the named primitives; on Module 4
(registry) for Step 3 append; on Module 5 (blueprint § Layers) for Step 2
cross-layer input. The command/SKILL can be authored in parallel but cannot be
exercised end-to-end until Modules 2/4/5 land.

**Protected points** (must match feature-flow §6 table; no enum change):
metric approval / access policy / production publish / destructive migration
(SEM-003, the 4 existing `protected_actions`, schema `:44-49`); source-boundary
extend (`requires_human_approval` flag, NOT in enum, SCOPE-001/SEC-001/RAW-003);
high-risk sign-off (SEC-001/ANS-003); answer independent review (REV-001/002/003).

## 2. Module: thin lib `build_plan.py`

### ADD `harness/.claude/lib/chatbi_harness/build_plan.py`
A thin deterministic module over existing `config`/`gates`/`bootstrap`
primitives. Mirrors `bootstrap.py` discipline: does NOT duplicate secret/path
validation (delegates to `load_effective_config`); raises `GateError`
(HOOK-004) on validation violation, mirroring `_bootstrap_gate_error`
(`bootstrap.py:44-64`). **Does NOT derive** join/aggregate logic (agent
reasoning); only reads + validates plan shape.

Public surface (full contracts in technical-design doc):
- `ModelEntry` - `@dataclass(frozen=True, slots=True)` (mirrors `SourceTable`,
  `bootstrap.py:209-214`): model name, layer (`ods`/`dwd`/`dws`/`ads`/`dim`),
  upstream deps (tuple of model names), change_kind (reuse `impact.py:31-33`
  `_CHANGE_KINDS`), created_rev, owner. `to_dict()` for registry write.
- `LayerRule` - `@dataclass(frozen=True, slots=True)`: layer + the set of layers
  it may depend on (e.g. ads -> {dws, dim}). Parsed from blueprint § Layers by
  the agent and passed in (lib does not parse markdown; declarative knowledge
  stays in blueprint, META-003/PORT-001).
- `BuildPlan` - `@dataclass(frozen=True, slots=True)` (mirrors `SourceInventory`,
  `bootstrap.py:217-250`): `schema_version: 1`, session_id, ordered
  `models: tuple[ModelEntry, ...]`, each entry carrying join/aggregate summary
  (text), upstream deps, protected-action flags, `requires_human_approval`
  (for extend-source). `to_dict()` produces the shape validated by
  `build-plan.schema.json` and persisted to
  `.chatbi/runs/<sid>/build_plan.json`.
- `read_model_registry(path) -> tuple[ModelEntry, ...]`: read
  `.chatbi/model_registry.json` (derived evidence under `runtime.evidence_root`
  = `.chatbi`, schema `:147-153`). Returns `()` if absent (first build). Does
  not fail-closed on absent file (absence = empty registry, not an error).
- `read_source_inventory(path) -> SourceInventory`: **wraps** bootstrap's
  `SourceInventory` (`bootstrap.py:217`). bootstrap.py has a `to_dict()` writer
  but NO reader; add the inverse parser here (JSON -> `SourceInventory` /
  `SourceTable` / `SourceColumn`). Reuses the bootstrap dataclasses (no
  redefinition) -- import `SourceInventory` etc. from `.bootstrap`.
- `validate_build_plan(plan, layer_rules) -> None`: raises `GateError` if (a)
  cross-layer dependency not ordered / not in `layer_rules`, (b) any `target`
  is not a logical alias (PORT-001: matches `^[a-z][a-z0-9_-]{1,62}$`, same
  pattern as schema `:36`), (c) plan references assets outside the Workspace
  (SCOPE-001), (d) a protected-action flag is missing on an entry that needs
  one (SEM-003). Pure shape check; no reasoning.
- `validate_layer_dependency(plan, layer_rules) -> None`: raises `GateError`
  if any model depends on a layer not permitted by `layer_rules` (ADS->DWS->
  DWD->ODS, no skip; DIM referenceable by DWD/DWS/ADS). Deterministic
  (HOOK-001 category). Exceptions (a model that legitimately crosses a layer)
  must be explicit in the plan entry's metadata (DOC-002), not silent.
- `append_model_registry(path, entry: ModelEntry) -> Path`: append one
  ModelEntry to `.chatbi/model_registry.json` (create if absent). Atomic
  temp+rename mirroring `harness_state.write_state` (`harness_state.py:104-122`,
  `0o600`). Idempotent on (name, created_rev) -- re-running maintain-model on
  the same model does not duplicate. **Lives in build_plan.py** (registry is
  one cohesive concern: ModelEntry + read + append); maintain-model's flow
  calls it. `impact.py` is NOT the home (impact = change blast radius, not
  registry bookkeeping) -- see Module 4.

### ADD `harness/.claude/schemas/build-plan.schema.json`
Mirror `impact-manifest.schema.json` (`:1-34`): `$schema` draft-07, `$id`,
`x-implemented-keywords`, `additionalProperties: false`, `required` +
`properties` with enum/pattern. Enforces: `schema_version` enum `[1]`; `models`
array of objects with `layer` enum `[ods,dwd,dws,ads,dim]`, `change_kind` enum
(reuse `impact.py:31-33`), `target` pattern `^[a-z][a-z0-9_-]{1,62}$` (PORT-001),
`requires_human_approval` boolean. Schema is the single contract for
`validate_build_plan` (mirror impact.py's `validate_impact_manifest` pattern,
`impact.py:229`).

### MODIFY `harness/.claude/lib/chatbi_harness/__init__.py`
- Add `from .build_plan import (BuildPlan, LayerRule, ModelEntry,
  append_model_registry, read_model_registry, read_source_inventory,
  validate_build_plan, validate_layer_dependency)` (alongside the `.bootstrap`
  import block, `__init__.py:3-7`).
- Add all eight names to `__all__` (`__init__.py:19-36`), preserving alphabetic
  ordering.
- `build_plan.py` imports only from `.bootstrap` / `.gates` / `.harness_state`
  (no new third-party deps).

**Dependency**: `read_source_inventory` depends on `bootstrap.SourceInventory`
(existing). `append_model_registry` depends on `harness_state`'s atomic-write
discipline (existing). No dependency on `impact.py`.

**Protected points**: `validate_build_plan` enforces SEM-003 flag completeness +
PORT-001 alias + SCOPE-001 boundary; `validate_layer_dependency` enforces the
declarative cross-layer rule. Both are fail-closed (HOOK-004).

## 3. Module: bootstrap incremental introspect

### MODIFY `harness/.claude/lib/chatbi_harness/bootstrap.py`
Add an incremental introspect capability so the new workflow's Step 2
extend-source path can introspect ONLY the newly-approved source tables and
append to the existing `source_inventory.json`, instead of redoing the full
one-shot introspect (Step 7, `chatbi-bootstrap/SKILL.md:160-178`).

- ADD a helper (e.g. `merge_source_inventories(base: SourceInventory, extra:
  SourceInventory) -> SourceInventory`): union tables by name; on name collision,
  keep `base` (do not silently overwrite an already-inventoried table -- surface
  a `GateError` or WARN, HOOK-004 spirit). Returns a new frozen `SourceInventory`
  (does not mutate inputs).
- The incremental path reuses the same `--execute=SELECT ... FROM
  INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=...` SQL but scoped to the
  approved table set (Step 7 option-a pattern, `chatbi-bootstrap/SKILL.md:164-
  167`), parses into a `SourceInventory`, then `merge_source_inventories` with
  the on-disk inventory, and rewrites `.chatbi/bootstrap/source_inventory.json`.
- `schema_version` stays `1` (`bootstrap.py:234`): the inventory shape is
  unchanged (tables/columns/PK/type); incremental only adds tables. No schema
  bump, no migration. Forward-compat field already self-describes.
- Export `merge_source_inventories` from `__init__.py` (Module 2 edit bundles
  this).

### MODIFY `harness/.claude/skills/chatbi-bootstrap/SKILL.md`
- Add a step (or a subsection of Step 7, `:160-178`) for the **incremental**
  mode: triggered when `/chatbi-build-from-requirement` flagged
  extend-source AND the human approved the new source. Documents: read existing
  inventory, introspect only the approved new tables, `merge_source_inventories`,
  rewrite. Same untrusted-`stdout_raw` discipline (Step 7 `:168-171`), same
  fail-closed on parse error.
- Keep the existing full introspect (Step 7) unchanged as the default; the
  incremental mode is an additional entry point, not a replacement. Do NOT
  break the one-shot path or its tests (`tests/harness/test_bootstrap.py`).

**Dependency**: none new; reuses existing `SourceInventory` + adapter
`--execute` pattern.

**Protected points**: extend-source still requires human approval BEFORE this
incremental introspect runs (SCOPE-001/SEC-001/RAW-003). The introspect itself
is a read+append; it does not invent tables -- it only inventories what the human
authorized.

## 4. Module: maintain-model writes `model_registry.json` + reads § Layers

### MODIFY `harness/.claude/skills/chatbi-maintenance/SKILL.md`
Two additions, both following the existing "read blueprint section" pattern
(§0 Tooling `:12-24`, §3 Metrics `:46-52`):
- **Read § Layers before drafting** (extend the §3 read block, `:46-52`): before
  drafting ODS/DWD/DWS/ADS, also read blueprint `## Layers` for the cross-layer
  dependency rule (which layers a model may depend on). Same posture as §
  Metrics: if § Layers is absent or has no rule for the model's layer, ask the
  operator -- do not invent cross-layer rules (META-003: declarative knowledge
  lives in blueprint, not SKILL; PORT-001).
- **Append ModelEntry after a successful build** (new step, after the §4 sync
  gate passes, `:54-60`): on sync-gate pass + `stop_gate` pass, call
  `chatbi_harness.build_plan.append_model_registry(
  .chatbi/model_registry.json, ModelEntry(...))` to record the built model
  (name, layer, upstream deps, change_kind, created_rev, owner). This is the
  contract surface the new workflow's Step 1 reads. It is **derived evidence**
  (a maintain-model byproduct, like source_inventory is bootstrap's byproduct),
  NOT governed config -- does not enter `chatbi-harness.schema.json`.

### Lib placement (decision)
`append_model_registry` + `ModelEntry` live in `build_plan.py` (Module 2), NOT
`impact.py`. Rationale: registry bookkeeping (model name/layer/deps) is one
cohesive concern co-located with its reader (`read_model_registry`); `impact.py`
(`impact.py:1-17`) is scoped to change blast radius + evidence state + SHA
binding. maintain-model's flow imports `build_plan` for the append (cross-module
import is already the norm -- `impact.py:24-29` imports from `gates`/`evidence`).
No re-export through `impact.py` unless the technical-design doc decides
otherwise; prefer the direct import to keep `impact.py` focused.

**Dependency**: depends on Module 2 (`ModelEntry`, `append_model_registry`).
**Protected points**: the append happens ONLY after sync gate + stop_gate pass
(DOC-004/HOOK-001); a model that failed sync is NOT recorded in the registry
(fail-closed -- the registry is a record of built models, not attempted ones).

## 5. Module: blueprint stub `## Layers` section

### MODIFY `harness/.claude/skills/chatbi-bootstrap/SKILL.md` Step 8
(`:186-203`)
**Correction noted (§0 discrepancy):** the as-built Step 8 creates `## Tooling`
(`:189`) + `## Metrics` (`:195`) headers ONLY. There is no `## Layers` header.
This edit ADDS a third header + skeleton.

- In the "stub MUST include" list (`:188-201`), add a `## Layers` section
  header with a placeholder skeleton (NOT invented cross-layer rules -- DOC-001;
  the operator/domain owner fills actual rules):
  - layer order: ODS (source-aligned) -> DWD (detail, joins ODS) -> DWS
    (summary, aggregates DWD) -> ADS (application, summarizes DWS). DIM is an
    independent dimension layer, referenceable by DWD/DWS/ADS.
  - no-cross-layer rule: ADS depends only on DWS/DIM; DWS only on DWD/DIM; DWD
    only on ODS/DIM.
  - exceptions must be explicit (which model crosses a layer + reason + human
    approval), recorded in model metadata (DOC-002 layering field).
- Leave the actual per-org rules as placeholders for the operator/domain owner
  to fill; bootstrap does NOT author governed knowledge (DOC-001, same posture
  as the existing `## Metrics` placeholder `:195-201`).

### MODIFY `harness/.claude/commands/chatbi-bootstrap.md`
Trust-boundary / stub description (`chatbi-bootstrap.md:22-24`) already says
"stub `docs/org/data-warehouse-blueprint.md`"; no command-level change required
unless the implementer wants to mention the Layers header explicitly. Optional;
keep within the non-bloat rule (DOC-005).

**Dependency**: none (pure doc/skeleton). This is the declarative input Module 1
Step 2 and Module 4 read.
**Protected points**: cross-layer rule is declarative domain knowledge in the
blueprint, NOT a new governed rule ID (META-003/PORT-001). 46 stays 46.

## 6. Module: product integration

### MODIFY `build-product.sh`
- **Command loop** (`:35-39`): add `chatbi-build-from-requirement` to the
  `for c in ...` list. Update the comment on `:35` from "the 7 chatbi commands"
  to "the 8 chatbi commands".
- **Import canary** (`:60-63`): add `chatbi_harness.build_plan` to the
  `PYTHONPATH=.claude/lib python3 -B -c "import ..."` line so a broken
  `build_plan.py` (bad import) fails the build.
- **Leak sweep** (`:64-74`): `chatbi-build-from-requirement` is a product
  command, NOT dev-only -- it MUST NOT appear in the leak list (same as
  `chatbi-bootstrap`).
- No change to lib/skills/docs rsync (`:25`, `:45`, `:49-50`): they already pick
  up the new `build_plan.py`, `chatbi-build/SKILL.md`, and `build-plan.schema.
  json` automatically.

### MODIFY `harness/CLAUDE.md`
- **Request-routing table** (`:72-80`, 7 rows): add one row.
  `| Build from a requirement | \`/chatbi-build-from-requirement\` | derived build plan + chained maintain-model + hand-off to analyze |`.
  Place after `/chatbi-bootstrap` (logical: bootstrap -> build-from-requirement
  -> analyze -> maintain) or at the end; keep the table well-formed.
- **200-line budget** (`gates.py:194-200`): file is currently **89 lines**
  (verified). Adding one row (~1 line) is far within budget; re-verify line
  count after edit.
- No rule-count change; the 46 governed rule IDs in
  `docs/chatbi-harness-domain-model.md` are untouched.

### MODIFY `harness/product-README.md`
- **Command count** (`:3`): "Seven slash commands, 46 enforced rules" ->
  "Eight slash commands, 46 enforced rules" (rule count unchanged).
- **Commands table** (`:33-46`): add a row:
  `| \`/chatbi-build-from-requirement\` | derive a build plan from a requirement + chain /chatbi-maintain-model (orchestrator; no governed authoring) |`.
- **Install section** (`:30-34`): after the `/chatbi-bootstrap` note, add one
  line that a from-requirement build is orchestrated by
  `/chatbi-build-from-requirement` (bridges analyze and maintain-model). Keep to
  one bullet (DOC-005).
- **Where to look** (`:59-65`): optionally mention the runbook at
  `.claude/skills/chatbi-build/SKILL.md`; one bullet max.

### MODIFY `harness/docs/harness/installation.md`
- Add a short subsection after the bootstrap subsection (`:59-65`) noting
  `/chatbi-build-from-requirement` as the orchestrator that derives a build plan
  when `/chatbi-analyze` stops on "needs new model": it chains
  `/chatbi-maintain-model` and routes protected points (source boundary / metric
  approval) to the human. Keep the evidence-status framing: the deterministic
  lib surface (`build_plan.py`) is VERIFIED OFFLINE; live derivation +
  maintain-model chaining are NOT YET EXERCISED in CI.

### MODIFY `harness/docs/harness/README.md`
- Add `/chatbi-build-from-requirement` as a new entry point (an 8th command),
  near §2.4 (`:97-127`): one paragraph -- orchestrator trust layer, bridges
  analyze and maintain-model, deterministic lib surface (`build_plan.py`)
  VERIFIED OFFLINE, live chaining NOT YET EXERCISED.
- Do NOT change the "production_ready=false" boundary note: this command does
  not alter `production_ready` semantics.

## 7. Module: tests

### ADD `tests/harness/test_build_plan.py`
Mirror `tests/harness/test_bootstrap.py` helpers (`working_directory`,
`install_domain_contract`, `sys.path` bootstrap block). Cases:
- `ModelEntry` / `LayerRule` / `BuildPlan`: frozen-slots (immutable,
  `AttributeError` on setattr), `to_dict()` round-trip shape matches
  `build-plan.schema.json`.
- `read_model_registry`: returns `()` on absent file; parses a sample registry
  into `ModelEntry` tuple; rejects malformed JSON / wrong schema_version.
- `read_source_inventory`: parses a sample `source_inventory.json` (shape from
  `bootstrap.py:232-250`) into `SourceInventory`; round-trips with
  `SourceInventory.to_dict()`.
- `validate_build_plan`: PASS on a well-ordered plan; raises `GateError`
  (HOOK-004) on (a) cross-layer out of order, (b) non-alias target (PORT-001),
  (c) out-of-Workspace asset (SCOPE-001), (d) missing protected-action flag
  (SEM-003).
- `validate_layer_dependency`: PASS when ADS->DWS->DWD->ODS + DIM referenced;
  raises `GateError` when ADS reads ODS/DWD directly, DWS reads ODS directly,
  DWD reads DWS (reverse). Explicit exception (DOC-002 metadata) does NOT
  raise when documented.
- `append_model_registry`: creates file if absent; appends without duplicating
  same (name, created_rev); atomic write (temp+rename, 0o600); does not mutate
  input.

### MODIFY `tests/harness/test_bootstrap.py`
- `merge_source_inventories`: union by name; collision raises/WARNs (does not
  silently overwrite); result is a new frozen `SourceInventory`; schema_version
  stays `1`.
- Incremental introspect path: parse scoped result -> merge -> rewrite round-trip
  (no live MySQL; fixture stdout, mirroring the existing offline discipline
  `test_bootstrap.py` / `chatbi-bootstrap/SKILL.md:231-233`).

### MODIFY `tests/harness/test_maintenance.py` (or ADD a focused case)
- maintain-model append-ModelEntry-after-sync-pass: registry gains one entry on
  a successful build; NO entry when sync gate fails (fail-closed). (The
  maintenance SKILL change is procedural; the test asserts the lib contract
  `append_model_registry` is called only post-gate, via the documented order.)

### MODIFY `tests/harness/test_contract.py` (extend command coverage)
- `test_checked_in_contract_covers_governed_rules_and_root_responsibilities`
  (`:64-71,87-88`) hardcodes `required_routes` as a 6-tuple
  (init/analyze/maintain-model/maintain-knowledge/evaluate/correction) and uses
  `assertIn(route, root_contract)` (subset check). **Pre-existing gap:**
  `/chatbi-bootstrap` is NOT in `required_routes` even though it is in the
  CLAUDE.md routing table (`harness/CLAUDE.md:75`). Adding the 8th command does
  NOT break this test (subset), but the contract will not ENFORCE its presence
  unless extended. Add `"/chatbi-build-from-requirement"` to `required_routes`
  (and, while here, `"/chatbi-bootstrap"` to close the pre-existing gap). The
  `:75` `assertLessEqual(..., 200)` line-budget check is unaffected (CLAUDE.md
  is 89 lines, +1 row = 90).

### MODIFY `tests/harness/test_e2e.py` (extend command coverage)
- `EvaluationE2ETests.test_six_commands_exist_and_route` (`:597-602`) hardcodes
  a 6-tuple of command `.md` filenames and asserts each `is_file()` (subset
  check, `COMMANDS_DIR`). Same pre-existing gap: `chatbi-bootstrap.md` is absent
  from the tuple. Adding the 8th command does NOT break it, but coverage must
  extend. Add `"chatbi-build-from-requirement.md"` to the tuple (and
  `"chatbi-bootstrap.md"` to close the pre-existing gap), and rename the test
  `six_commands` -> `eight_commands` so the name stops drifting from reality.

**Baseline**: 566 tests across 17 files (verified). Target: stay 566+ green;
new cases are additive. `validate_domain_contract` (`gates.py:170-233`) MUST
still pass (no rule added/renamed; CLAUDE.md <200 lines).

## 8. Files NOT changed (and why)

- `harness/.claude/schemas/chatbi-harness.schema.json` - `protected_actions`
  enum (`:44-49`) stays 4 values; extend-source is a `requires_human_approval`
  flag, NOT a new enum value. Top-level `additionalProperties: false` (`:186`)
  means `model_registry.json` (derived evidence under `.chatbi/`) cannot and
  must not enter config. No `models` field added to EffectiveConfig.
- `harness/.claude/lib/chatbi_harness/config.py`, `gates.py`, `diagnostics.py`,
  `adapters/__init__.py`, `policy.py`, `evidence.py`, `impact.py` - reused
  as-is. `build_plan.py` delegates to them; `impact.py` is NOT extended
  (registry lives in `build_plan.py`).
- `harness/.claude/hooks/*`, `harness/.claude/settings.json` - new command is
  SessionStart-routed like the others; no new hook (HOOK-001/003/004 unchanged).
- `harness/.claude/rules/*`, `harness/docs/chatbi-harness-domain-model.md` -
  no rule added/renamed/reworded (46 stays 46). Cross-layer is declarative
  blueprint knowledge, not a governed rule ID.
- `harness/CONTEXT.md`, `harness/e2e-state.py`, `harness/install.sh` -
  unchanged.
- No derivation lib: join/aggregate logic is agent reasoning (SKILL Step 2),
  not a deterministic lib (orchestrator-state.md:31-33, :38-39).

## 9. Constraints checklist (explicit)

- [x] 46 rules unchanged, no new rule ID; `validate_domain_contract`
  (`gates.py:170-233`) continues PASS.
- [x] `chatbi-harness.schema.json` NOT modified: registry is derived evidence
  (`.chatbi/`), not config; extend-source is `requires_human_approval`, NOT in
  `protected_actions` enum (`:44-49`).
- [x] `harness/CLAUDE.md` < 200 lines (currently 89; +1 routing row).
- [x] Derivation (join/aggregate) is agent reasoning, no derivation lib; lib
  only reads + validates plan shape.
- [x] Cross-layer rule is declarative domain knowledge in blueprint `## Layers`,
  not SKILL-hardcoded (META-003/PORT-001).
- [x] fail-closed: plan validation failure / unprotected protected-point ->
  STOP, no delivery (HOOK-004).
- [x] Build plan itself does NOT pass independent review; REV-001 is the answer
  gate only (orchestrator-state.md:12).
- [x] Registry append only after sync gate + stop_gate pass (DOC-004/HOOK-001).

## 10. Open points for the implementer (non-blocking)

1. **`read_source_inventory` ownership.** bootstrap.py has the writer
   (`to_dict`, `:232-250`) but no reader. This doc puts the reader in
   `build_plan.py` (the consumer). Alternative: add the reader to `bootstrap.py`
   (the producer) for symmetry and have `build_plan.py` re-export. The
   technical-design doc should pick one; either keeps `SourceInventory` defined
   once (no redefinition).
2. **`merge_source_inventories` collision policy.** This doc says raise/WARN on
   name collision (don't silently overwrite). If a re-introspect legitimately
   refreshes columns of an existing table, the policy may need an
   "overwrite-with-human-approval" path. Flag for the technical-design doc;
   default to fail-closed (HOOK-004) for v1.
3. **`append_model_registry` idempotency key.** `(name, created_rev)` is the
   dedup key here. If a model is rebuilt at a new rev, both entries persist
   (history). If only "latest" should persist, the append needs an
   upsert-by-name semantics. Decide in technical-design; default to append-
   with-history for v1 (matches the "derived evidence log" posture).
4. **`LayerRule` parsing.** This doc has the agent parse blueprint § Layers into
   `LayerRule` and pass it to the lib (lib does not parse markdown). If a
   deterministic markdown parser is later wanted, it belongs in `build_plan.py`
   -- but keep it out of v1 (META-003: declarative text stays readable by
   humans + agent, not machine-parsed, unless needed).
5. **Build-plan SHA.** feature-flow §5.1 lists `compute_candidate_sha`
   (`evidence.py:163-178`) as reusable. Since the build plan does NOT pass
   independent review, a SHA is optional for v1 (no SHA-bound review gate). If
   persisted for audit/resumption integrity, add it; not required for the
   trust boundary.

STATUS: DRAFTED
