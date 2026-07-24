# Model Maintenance Route (`/chatbi-maintain-model`)

The governed model-maintenance route changes a model or semantic definition,
generates a change-impact manifest, and gates delivery on full sync of affected
assets. It reuses the Cycle 3 `Stop` gate; it does not bypass the analysis loop.

## 1. Route entry

- Command: `.claude/commands/chatbi-maintain-model.md`.
- Runbook: `.claude/skills/chatbi-maintenance/SKILL.md`.
- Impact manifest: `.claude/lib/chatbi_harness/impact.py` (`ImpactManifest`).
- Schema: `.claude/schemas/impact-manifest.schema.json`.
- PostToolUse record: `.claude/hooks/posttool_impact.py`.

## 2. Classify the change

`change_kind` ∈ model/column/semantic/reference/Skill/downstream/eval; target is
a logical alias. If the change is a protected action (`approve_metric`,
`change_access_policy`, `production_publish`, `destructive_migration`), STOP and
request human owner approval (SEM-003). Agent drafting is not approval.

## 3. Impact manifest

`ImpactManifest` records the blast radius: affected assets (metadata/semantic/
reference/Skill/tests/downstream/eval/code), each with `change_required` and
`synced`; `evidence_state` (sufficient/missing/uncertain); `p0_eval_failed`;
`protected_action`; `candidate_sha`. Missing or uncertain evidence is recorded
explicitly, never degraded to a placeholder.

## 4. Sync gate (DOC-004)

- Any `change_required` asset with `synced=False` -> blocking drift -> the Cycle 3
  `stop_gate` fails (open finding). Do not deliver.
- All synced + affected tests/evals exist + evidence sufficient + no P0 eval
  failure -> `stop_gate` passes.
- Uncertain sync completeness -> fail-closed; `stop_gate` fails; do not assume
  clean.

## 5. PostToolUse record (not undo)

`posttool_impact.py` records the manifest after the change and flags blocking
drift. It does NOT undo, revert, or modify the change. The first line of defense
remains the Cycle 2 `PreToolUse` gate + OS sandbox. PostToolUse is an
after-the-fact record + flag.

## 6. Knowledge co-location (DOC-001)

Reference changes co-located with the model go through `/chatbi-maintain-knowledge`
lint. A model change that affects references routes the reference update
atomically with the model change.

## 7. Honest capability reporting (not verified vs verified)

- **VERIFIED OFFLINE**: impact manifest build/validate, PostToolUse gate
  enforcement, sync-gate reuse (Stop gate), knowledge lint, and the maintenance
  E2E slice (`test_maintenance.py`, `test_knowledge.py`, `test_e2e.py`).
- **NOT YET EXERCISED (Cycle 5)**: a real CC `PostToolUse` process run; live
  `settings.json` hook registration; real model-change runtime (real adapter/
  reviewer). Hooks are NOT registered during development (a blocking hook
  hot-reloads `settings.json` and can deadlock the dev session).
- **BLOCKING GAP (continues)**: OS sandbox deny runtime evidence (1 skipped
  test). Not faked; not downgraded.

See `docs/feature-flow-v5.md` for code-grounded line references and
`docs/harness/compatibility.md` for the verified / not-yet-exercised / blocked
distinction.
