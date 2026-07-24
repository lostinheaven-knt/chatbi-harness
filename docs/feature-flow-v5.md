# Feature Flow v5 - Cycle 4 (模型维护、知识共置与变更影响门控)

> Code-as-read on 2026-07-23 from `chatbi-cc-dev/`. Version v5 (Cycle 3 took v4;
> `dev-cycles.md`'s "v4" label for Cycle 4 is stale - see `dev-cycle-4.md` top).
> Each entry cites real file:line. Not-yet-exercised runtime is marked, never
> claimed verified.

Cycle 4 adds six flows on top of Cycle 1/2/3:

1. **Flow A** - change-impact manifest (`lib/chatbi_harness/impact.py`)
2. **Flow B** - `PostToolUse` impact-record gate (`hooks/posttool_impact.py`)
3. **Flow C** - `/chatbi-maintain-model` + maintenance SKILL
4. **Flow D** - `/chatbi-maintain-knowledge` + knowledge SKILL + template + fixture-domain
5. **Flow E** - knowledge-reference lint (`lib/chatbi_harness/knowledge.py`)
6. **Flow F** - sync gate (Cycle 3 `stop_gate` reuse) + maintenance E2E

## 1. Flow A: change-impact manifest

Entry: `impact.py:80 ImpactManifest` (frozen: run_id/change_kind/target/
affected_assets/evidence_state/p0_eval_failed/protected_action/candidate_sha/
created_rev). `impact.py:60 AffectedAsset` (asset_kind/asset_ref/
change_required/synced). Factory `impact.py` `build_impact_manifest(...)`:
validates enums (`_CHANGE_KINDS:31`, `_EVIDENCE_STATES:38`, `_PROTECTED_ACTIONS:39`),
sanitizes target/asset_ref via `gates._sanitize_text` (PORT-001), binds
`candidate_sha` via `evidence.compute_candidate_sha`, and validates against
`schemas/impact-manifest.schema.json` (`validate_impact_manifest`). Fail-closed
`GateError` on invalid enum / empty target / unknown asset_kind / missing ref.

`ImpactManifest.has_blocking_drift()` (`:106`): True when evidence_state is
missing/uncertain, p0_eval_failed, protected_action, or any
`change_required and not synced` asset. `blocking_reasons()` (`:121`) lists why.

**Rules:** DOC-004, EVAL-001/003, SEM-003, HOOK-001/004, SEC-003, PORT-001,
ABL-001/002.

## 2. Flow B: PostToolUse impact-record gate

Entry: `hooks/posttool_impact.py` invoked as a subprocess (documented Hook seam;
`test_maintenance.py` `_run_gate`). Confirmed field `impact_manifest` (required,
`:60 _REQUIRED_FIELDS`); optional `candidate_sha` (stale/mismatch check),
`tool_name`, `hook_event_name` (`:61 _VALID_EVENT_NAMES = {"PostToolUse"}`),
`stop_hook_active` (recursion guard). Unknown fields tolerated (HOOK-003).

`_check_impact` (`:171`) order: recursion guard -> `hook_event_name` ->
`validate_impact_manifest` (GateError on malformed) -> stale/mismatched
`candidate_sha` -> `_blocking_decision` (`:136`) -> else `_allow` (`:115`).

`_blocking_decision` returns rule_ids/reason/recovery for: protected_action
(SEM-003/DOC-004), p0_eval_failed (EVAL-003/DOC-004), evidence missing
(DOC-004/EVAL-001) or uncertain (DOC-004/HOOK-004), unsynced affected assets
(DOC-004). `_allow` emits a **leak-safe summary** (`:115` - recorded/undo=False/
modified_change=False/change_kind/candidate_sha/evidence_state/affected_count,
no target/asset_ref). **PostToolUse only records; it never undoes or modifies
the change.** First defense remains Cycle 2 PreToolUse + OS sandbox.

`MAX_STDIN_BYTES = 64KiB` (`:58`); oversized/malformed stdin -> exit 2 (HOOK-004).

**Rules:** DOC-004, EVAL-001/003, SEM-003, HOOK-001/003/004/005, SEC-003,
PORT-001. **Gap:** live `settings.json` registration + real CC PostToolUse E2E
= Cycle 5.

## 3. Flow C: /chatbi-maintain-model + maintenance SKILL

Entry: `commands/chatbi-maintain-model.md` (68 lines) + `skills/chatbi-maintenance/
SKILL.md` (60 lines). Classify change_kind + target; protected action -> STOP,
human owner approval (SEM-003). Build `ImpactManifest` (Flow A); produce
candidate changes for each `change_required` asset; mark `synced=True` only after
applied. Sync gate (Flow F): unsynced -> `stop_gate` fails; full sync + affected
tests/evals exist + evidence sufficient + no P0 -> passes; uncertain -> fail-closed.
PostToolUse records (Flow B). Footer: change_kind/target/affected/evidence_state/
p0/protected/round/owner/freshness.

**Rules:** AC-02/05/09, DOC-001/004/005, SEM-003, SRC-001/002, PORT-001,
ABL-001/002. **Gap:** real model-change runtime = Cycle 5.

## 4. Flow D: /chatbi-maintain-knowledge + knowledge SKILL + template

Entry: `commands/chatbi-maintain-knowledge.md` (58) + `skills/chatbi-knowledge/
SKILL.md` (52) + `references/_template.md` (73) + `references/fixture-domain.md`
(86). Author against `_template.md`; lint before publish (Flow E); only
lint-clean references are route-ready (retrievable by `/chatbi-analyze`).
Conflicts with governed facts -> domain owner (SRC-002). Atomic change
co-located with the model (DOC-001). Reference changes affecting downstream
route to `/chatbi-maintain-model` for impact + sync (DOC-004).

**Rules:** AC-02, DOC-001/002/003/005, SEM-003, SRC-001/002, PORT-001.

## 5. Flow E: knowledge-reference lint

Entry: `knowledge.py:68 lint_reference(text) -> tuple[LintIssue, ...]`. Required
fields (`:19 REQUIRED_FIELDS`: Business context/Grain/Standard filters/Dimensions/
Key models/Scope and exclusions/Joins/Common pitfalls/Best practices/
Cross-references/Owner/Freshness/Use for/Do not use for). Checks: required
headers present; no machine absolute paths (`:37 _ABSOLUTE_PATH`); historical SQL
marked `candidate_only` (`:42 _SQL_FENCE`, `:43 _CANDIDATE_ONLY`); no duplicate
headers; Cross-references lists a neighbor (`.md`/`.sql`/`.json`); "Do not use
for" non-empty. Empty tuple = route-ready. Fail-closed; document never mutated.

`_template.md` and `fixture-domain.md` both pass lint (`test_knowledge.py`).

**Rules:** DOC-001/002/003/005, SEM-003, SRC-001/002, PORT-001, SEC-003.

## 6. Flow F: sync gate + maintenance E2E

Entry: `tests/harness/test_e2e.py` `MaintenanceKnowledgeE2ETests`. Reuses the
Cycle 3 `stop_gate` (not a new gate): a model change with blocking drift
(unsynced/protected/p0/missing) feeds an open `block` finding to `stop_gate` ->
exit 2; full sync -> `open_findings=[]` -> exit 0. The PostToolUse gate (Flow B)
blocks the same drift. The Cycle 3 review gate still delivers a clean PASS - the
maintenance extension does NOT bypass the analysis loop.

Knowledge E2E: `_template.md`/`fixture-domain.md` pass lint; a bad reference
(missing "Do not use for") fails. No canary leak through the gate summary.

**Rules:** AC-02/05/09, DOC-001..005, SEM-003, SRC-001/002, EVAL-001..005,
ABL-001/002, HOOK-001/004/005. **Gap:** real model/knowledge change + Hook
process E2E = Cycle 5.

## 7. Known gaps (Cycle 5 hard-gates, not mocked)

1. Real CC `PostToolUse` process run + live `settings.json` registration - NOT
   YET EXERCISED (Cycle 5). Hooks stay unregistered (dev-safety).
2. Real model/knowledge change runtime (real adapter/reviewer) - Cycle 5.
3. OS sandbox deny runtime evidence - BLOCKING GAP (continues from Cycle 2).
4. PostToolUse is record-only, not undo/rollback (by design; first defense =
   Cycle 2 PreToolUse/sandbox).
5. Maintenance extension does not bypass Cycle 3 analysis loop (Stop gate API
   unchanged).
6. PII redaction email-only (Cycle 3 carry-forward; broader PII = owner policy).

Evaluation success is evidence, not a guarantee silent failure is eliminated
(FBK-003).
