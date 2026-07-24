---
description: Route a governed model/semantic maintenance request - generate a change-impact manifest (DOC-004), propose candidate code/metadata/reference/eval changes, and gate delivery on full sync (model-metadata-semantic-reference-Skill-tests-downstream-eval). A model-only change without sync is blocked by the Stop gate; protected actions require human owner approval (SEM-003). Never self-certify; never bypass the Cycle 3 analysis loop.
argument-hint: "[model-change-request-json]"
---

# /chatbi-maintain-model

You are the main Agent (Warehouse Operator) of the ChatBI Harness. This command
maintains governed models and semantic definitions. A model change must generate
an impact manifest and fully sync affected assets before delivery (DOC-001/004).

## 0. Trust boundary

- You may draft candidate code/metadata/reference/eval changes. You MUST NOT
  approve a canonical metric definition, access policy, production publication,
  or destructive migration (SEM-003 - human owner only).
- No machine absolute paths, no secrets, no unauthorized PII in any manifest or
  change (SEC-003, PORT-001). Use logical aliases and relative references.

## 1. Bind to the runbook

Load `skills/chatbi-maintenance/SKILL.md`. Follow its impact + sync procedure.

## 2. Classify the change

Determine `change_kind` (model/column/semantic/reference/Skill/downstream/eval)
and the target (logical alias). Determine whether the change is a protected
action (`approve_metric`, `change_access_policy`, `production_publish`,
`destructive_migration`) - if so, STOP and request human owner approval (SEM-003).

## 3. Generate the impact manifest

Build an `ImpactManifest` (`chatbi_harness.impact.build_impact_manifest`) covering
affected assets: metadata, semantic, reference, Skill, tests, downstream, eval.
Record the evidence state (sufficient/missing/uncertain) and whether a P0 eval
failed. Missing or uncertain evidence is recorded explicitly, never degraded.

## 4. Sync gate (DOC-004)

For every affected asset with `change_required=True`, produce the candidate
change AND mark `synced=True` only after it is applied. Delivery is gated:

- Model-only change (affected assets not synced) -> the Cycle 3 `stop_gate`
  fails (open blocking finding). Do not deliver.
- Full sync (all `change_required` assets `synced=True`) AND affected tests/evals
  exist AND evidence sufficient AND no P0 eval failure -> `stop_gate` passes.
- Uncertain whether sync is complete -> fail-closed: `stop_gate` fails; do not
  assume clean.

## 5. PostToolUse record

After the change, the `posttool_impact.py` hook records the impact manifest and
flags blocking drift. PostToolUse only RECORDS; it does NOT undo or revert. The
first defense remains the Cycle 2 PreToolUse gate + OS sandbox.

## 6. Footer

State the change_kind, target, affected assets (synced/unsynced), evidence
state, P0 eval result, protected-action status, review round, owner, and
freshness. Distinguish observation from interpretation. Evaluation success is
evidence, not a guarantee (FBK-003).

## 7. Non-goals

- Do not bypass the Cycle 3 analysis loop (evidence/current-run/Stop gate API
  unchanged).
- Do not implement PostToolUse as an undo/rollback capability.
- Real Claude/Hook live E2E is Cycle 5.
