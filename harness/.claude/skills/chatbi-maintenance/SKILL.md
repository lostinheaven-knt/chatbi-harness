---
name: chatbi-maintenance
description: Procedural runbook for governed model/semantic maintenance invoked by /chatbi-maintain-model. Generates a change-impact manifest (DOC-004), drives the model-metadata-semantic-reference-Skill-tests-downstream-eval sync gate, reuses the Cycle 3 Stop gate, and routes protected actions to the human owner (SEM-003). Carries reusable procedure, not easily-stale facts.
---

# chatbi-maintenance

Maintenance runbook for governed models and semantic definitions. A model change
is delivered only after an impact manifest is generated and all affected assets
are synced (DOC-001/004).

## 0. Tooling (read before running dbt)

Before invoking `dbt run` / `dbt test` / any dbt command (Step 3 candidate
verification, Step 4 sync gate), read `docs/org/data-warehouse-blueprint.md`
§ Tooling for the environment-specific dbt executable path and MySQL connection.
dbt's Python runtime is separate from the harness `CHATBI_PYTHON` - on some
hosts dbt requires a different Python (e.g. 3.12 vs 3.14 where `mashumaro`
crashes), so an arbitrary `dbt` found on PATH may be the wrong one. Use the dbt
named in the Tooling section, not one discovered on PATH. If the Tooling section
is absent or does not name a dbt executable, STOP and ask the operator which dbt
to use - do not guess from PATH (unconfirmed PATH entries are not execution
authority, PORT-001 spirit). The Tooling section is operator guidance, not
governed knowledge (DOC-001 does not apply to it).

## 1. Classify the change

`change_kind` ∈ model/column/semantic/reference/Skill/downstream/eval; target is
a logical alias. If the change is a protected action (`approve_metric`,
`change_access_policy`, `production_publish`, `destructive_migration`), STOP and
request human owner approval (SEM-003). Agent drafting is not approval.

## 2. Build the impact manifest

`chatbi_harness.impact.build_impact_manifest(...)` over affected assets
(metadata/semantic/reference/Skill/tests/downstream/eval/code). Record
`evidence_state` (sufficient/missing/uncertain) and `p0_eval_failed`. Missing or
uncertain evidence is explicit, never an empty placeholder.

## 3. Produce candidate changes

For each affected asset with `change_required=True`, produce the candidate
change (code/metadata/reference/Skill/test/eval). Mark `synced=True` only after
the change is applied.

Before drafting an ODS/DWD/DWS model, read
`docs/org/data-warehouse-blueprint.md` § Metrics for the design intent (which
tables are facts/dimensions, which columns are numerators/denominators, the
function axis). This lets you annotate column roles (e.g. "denominator source")
in the model reference without the operator re-stating them per request. If the
Metrics section is absent or empty, ask the operator for the design intent - do
not invent column roles.

## 4. Sync gate (DOC-004)

- Any `change_required` asset with `synced=False` -> blocking drift -> the Cycle
  3 `stop_gate` fails (open finding). Do not deliver.
- All synced + affected tests/evals exist + evidence sufficient + no P0 failure
  -> `stop_gate` passes.
- Uncertain sync completeness -> fail-closed; `stop_gate` fails.

## 5. PostToolUse record (not undo)

`posttool_impact.py` records the manifest after the change and flags blocking
drift. It does NOT undo/revert. First defense = Cycle 2 PreToolUse + OS sandbox.

## 6. Knowledge co-location (DOC-001)

Reference changes co-located with the model go through
`/chatbi-maintain-knowledge` lint. A model change that affects references routes
the reference update atomically with the model change.

## 7. Pruning (DOC-005)

When a model improves, prune obsolete scaffolding and negative-value references
rather than lengthening prompts. Record removals as atomic changes.

## 8. Footer

change_kind, target, affected assets (synced/unsynced), evidence_state,
p0_eval_failed, protected_action, review round, owner, freshness. Observation
vs interpretation separated (FBK-003).
