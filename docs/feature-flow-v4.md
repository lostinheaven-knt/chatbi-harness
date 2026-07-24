# Feature Flow v4 - Cycle 3 (受治理分析、运行证据与独立对抗审查)

> Code-as-read on 2026-07-23 from `chatbi-cc-dev/`. Version v4 (Cycle 2 took v3
> globally; `dev-cycles.md`'s "v3" label for Cycle 3 is stale - see
> `dev-cycle-3.md` top note). Each entry cites real file:line. Where a runtime
> capability is not yet exercised, it is marked NOT YET EXERCISED / Cycle 5,
> never as verified.

Cycle 3 adds six flows on top of the Cycle 1/2 boundary:

1. **Flow A** - runtime evidence + schemas (`evidence.py`, `schemas/`)
2. **Flow B** - independent adversarial reviewer contract (`agents/adversarial-reviewer.md`)
3. **Flow C** - `SubagentStop` review gate (`hooks/subagent_review_gate.py`)
4. **Flow D** - tracked-workflow `Stop` gate (`hooks/stop_gate.py`)
5. **Flow E** - `/chatbi-analyze` 5-layer flow (`commands/chatbi-analyze.md` + `skills/chatbi-runbook/SKILL.md`)
6. **Flow F** - provenance footer assembly + offline E2E (`test_e2e.py`)

## 1. Flow A: runtime evidence + schemas

Entry: `evidence.py:163 compute_candidate_sha(candidate_payload)` ->
deterministic SHA-256 over canonical JSON (sorted keys, `allow_nan=False`).
`evidence.py:187 RunRecord` (frozen: run_id/round/candidate_sha/created_rev/
actor/purpose). `evidence.py:250 EvidenceEntry`, factory
`evidence.py:289 EvidenceEntry.create(*, source_tier, evidence_source,
rule_ids, payload)`:

- `evidence.py:79 _sanitize_payload` reuses `gates` sanitization (secrets /
  paths) + PII email redaction; `evidence.py:109 _verify_sanitization_idempotent`
  fail-closed if a sensitive pattern survives the first pass.
- `evidence.py:126 _content_sha256` binds the sanitized payload.
- Fail-closed `GateError` (not a placeholder) when payload is None
  (missing evidence), non-JSON-serializable, or non-idempotent sanitization.

Schemas (JSON Schema subset validator at `evidence.py:388
_validate_schema_subset`, loaded via `evidence.py:462 _get_schema`):

- `schemas/request.schema.json` - 7 required fields (question/time_range/
  entity/segment/actor/purpose/supported_decision), `additionalProperties:false`.
- `schemas/review.schema.json` - 8 required fields; 11 coverage keys
  (entity/grain/joins/filters_exclusions/date_timezone/denominator/
  sample_bias/quality/observation_vs_interpretation/disclosure/provenance)
  each enum pass|fail|not_applicable; findings (severity block|warn|info,
  rule_ids minItems 1, evidence_refs, reason, recovery); status PASS|BLOCKED|ERROR.
- `schemas/provenance.schema.json` - 17 required footer fields; source_tier
  enum T1|T2|T3.

Validators: `evidence.py:493 validate_request`, `:501 validate_review`,
`:512 validate_provenance` (each raises `GateError` on schema failure).

**Rules:** EVID, QLT-001, SEC-003, PORT-001, HOOK-001/004, SEM-001/002,
RAW-001/003, REV-002, ANS-002.

## 2. Flow B: independent adversarial reviewer contract

Entry: `agents/adversarial-reviewer.md` (frontmatter `tools: Read, Grep, Glob`
- line 4, **no mutating tools**). Self-contained sections:

- `:19 §1` declares SCOPE/SEC/REV/ANS/PORT rule IDs inline.
- `:57 §2` declares T1>T2>T3 hierarchy + T4 clue-only + per-tier stop/degrade.
- `:84 §3` least-privilege read-only tool restriction (REV-001/002 independence);
  forbids Bash/Write/Edit/Agent/Task/WebFetch/WebSearch/mutating MCP.
- `:103 §4` the 11 coverage dimensions (exact keys).
- `:151 §5` stop conditions: PASS only if all 11 pass/justified-not_applicable
  AND no block finding AND determinable; BLOCKED on fail/block; ERROR on
  undeterminable; "Never emit a silent PASS".
- `:185 §6` output contract conformance to `review.schema.json`.
- `:233 §7` SHA binding: PASS valid only for exact `candidate_sha`; any edit
  invalidates prior PASS (new round); stale/mismatched SHA is ERROR.
- `:251 §8` sanitization (SEC-003/PORT-001); `/Users/`/`/home/` appear only as
  prohibition examples (line 256-257), never leaked.

**Rules:** REV-001/002/003, SCOPE-001/002/003, SEC-001/002/003, ANS-001/002/003,
PORT-001. **Gap:** real Claude reviewer process run = Cycle 5 (stated `:330 §11`);
this is a contract/prompt artifact verified offline by Flow C tests.

## 3. Flow C: SubagentStop review gate

Entry: `hooks/subagent_review_gate.py` invoked as a subprocess (the documented
Hook seam; see `test_review_gate.py:87 run_gate`). Confirmed fields
(`:94 _REQUIRED_FIELDS = {"review","candidate_sha"}`), optional
`hook_event_name`/`stop_hook_active`. Unknown event fields ignored (HOOK-003,
`:22` forward-compat note, `:176`).

Enforcement order in `_check_review`:

1. recursion guard (`stop_hook_active`) -> exit 2 HOOK-001
2. `validate_review` (schema; `GateError` on malformed) -> exit 2
3. round-limit `:92 MAX_REVIEW_ROUNDS=3` -> exit 2 REV-003 (stop, escalate)
4. stale/mismatched `candidate_sha` (`:202-209`) -> exit 2 REV-001/003 (force new round)
5. status != PASS -> exit 2 REV-001/003
6. any coverage not pass/not_applicable -> exit 2 REV-002 (defense-in-depth)
7. any `block` finding -> exit 2 REV-003
8. `sanitized_output` not true -> exit 2 SEC-003/PORT-001
9. else exit 0 (allow delivery)

Every exit-2 path emits `GateDecision.block` with fixed abstract rule_ids /
evidence_refs / reason / recovery; finding content is never echoed (leak-safe).
Fail-closed: missing/malformed review, missing/invalid `candidate_sha`,
BLOCKED/ERROR status, undeterminable state all exit 2 - PASS never assumed.

**Rules:** REV-001/002/003, HOOK-001/003/004/005, SEC-003, PORT-001, SCOPE.
**Gap:** live hook registration + real CC SubagentStop E2E = Cycle 5
(`settings.json` stays SessionStart-only to avoid dev-session self-deadlock).

## 4. Flow D: tracked-workflow Stop gate

Entry: `hooks/stop_gate.py` invoked as a subprocess. Confirmed field
`open_findings` (array of finding objects mirroring `review.schema.json`
finding shape); optional `hook_event_name`/`stop_hook_active`. Unknown fields
ignored (HOOK-003, `:22`).

- `:86 _SEVERITIES = {"block","warn","info"}`; each finding structurally
  validated (severity, rule_ids non-empty, evidence_refs, reason, recovery).
- any finding `severity=="block"` -> exit 2 REV-003 (open blocking finding
  unresolved; recovery: resolve and re-review).
- missing/non-list `open_findings` or malformed finding -> exit 2 fail-closed
  (`:145`).
- empty or warn/info-only -> exit 0.

**Rules:** REV-003, HOOK-001/003/004/005, SEC-003, PORT-001. **Gap:** live
registration = Cycle 5 (`:41` note: a blocking Stop hook hot-reloads
`settings.json`).

## 5. Flow E: /chatbi-analyze 5-layer flow

Entry: `commands/chatbi-analyze.md` (`:6`), procedural detail in
`skills/chatbi-runbook/SKILL.md`. Input contract `:34 §1` (request.schema.json).

- `:61 Layer 1 - Clarify`: undefined entity/time_range/segment/supported_decision
  -> STOP ask smallest clarification (REQ-001/002/004); never fabricate
  metric/denominator (RAW-003). Runbook `:33 Step 1`.
- `:73 Layer 2 - T1 semantic-layer discovery`: Cycle 2 `select_adapter` chain
  (managed/CLI/Fixture); on T1 coverage compile+query, record `EvidenceEntry`
  source_tier=T1. Runbook `:66 Step 2`.
- `:82 Layer 3 - T2 curated-reference fallback`: only after recording a specific
  T1 gap (coverage/compile/permission/freshness); record EvidenceEntry T2
  (RAW-001). Runbook `:87 Step 3`.
- `Layer 4 - T3 raw exploration`: only after a specific T2 gap; high-risk
  recheck warning; EvidenceEntry T3 (RAW-002/003). Runbook `:108 Step 4`.
  Historical SQL/notebooks/dashboard queries are clues only, not canonical
  (RAW-001/002, SRC-002).
- `Layer 5 - independent PASS + gate delivery`: bind candidate SHA, invoke
  `adversarial-reviewer`; pass `subagent_review_gate` (PASS+SHA match) and
  `stop_gate` (no open block finding) before delivery. Candidate change
  invalidates prior PASS (REV-001). Runbook Step 6.
- Footer per `provenance.schema.json` (17 fields); raw/unknown freshness ->
  high-risk recheck warning (ANS-002/003); executive/regulated/PII/core-finance
  -> human sign-off (SEM-003, ANS-003). Runbook `:Step 7`.

**Rules:** REQ-001..004, SEM-001..003, RAW-001..003, SRC-001/002, QLT-001,
REV-001/002/003, ANS-001/002/003, SCOPE/SEC, HOOK-001/003/004.
**Gap:** real semantic-layer/adapter/reviewer execution = Cycle 5; Flow F
exercises the loop offline with synthetic fixtures.

## 6. Flow F: provenance footer + offline E2E

Entry: `tests/harness/test_e2e.py` wires the full loop:
`validate_request` -> `policy.decide` -> `FixtureAdapter.discover` (T1) ->
`EvidenceEntry` chain -> `compute_candidate_sha` -> synthetic review verdict
(`validate_review`) -> `subagent_review_gate` (subprocess) -> `stop_gate`
(subprocess) -> `validate_provenance` footer.

- 5 stress scenarios (`fixtures/evaluations/analysis-scenarios/`): ambiguity
  (clarify, BLOCKED), stale (T1, BLOCKED quality+date), historical-sql
  (T1->T2->T3, PASS, freshness+signoff), prompt-injection (T1-hit, PASS,
  instructions logged not executed), pii-permission (block pre-T1, BLOCKED,
  min_auth).
- PASS+SHA-match -> review gate exit 0, stop gate exit 0, deliver, full footer.
- BLOCKED / stale SHA / missing evidence -> review gate exit 2, no delivery.
- No canary leak through gate stdout/stderr.

`tests/harness/test_analysis.py` `EvidenceIntegrationTests` covers the
evidence-layer loop (chain -> review schema -> provenance schema -> SHA binding).

**Rules:** REQ/SEM/RAW/SRC/QLT/REV/ANS full families. **Gap:** the reviewer is
a SYNTHETIC producer (representative verdict); the real Claude reviewer process
is Cycle 5. OS-sandbox BLOCKING GAP from Cycle 2 carries forward (the 1 skipped
test).

## 7. Known gaps (Cycle 5 hard-gates, not mocked)

1. Real Claude `adversarial-reviewer` process run + real CC `SubagentStop`/`Stop`
   hook E2E - NOT YET EXERCISED (Cycle 5).
2. Live hook registration in `settings.json` - deferred (dev-safety; Cycle 5).
3. OS sandbox deny-write/deny-execute runtime evidence - BLOCKING GAP (Cycle 5).
4. Real managed/CLI semantic-layer adapter execution - Cycle 5.
5. FixtureAdapter/CodebaseReader not wired into `select_adapter` (Cycle 2
   deferral); Flow F uses FixtureAdapter by direct construction (test mode).
6. PII redaction is email-only in `evidence.py` (phone/SSN deferred; broader PII
   policy is governance/owner-approved).

Evaluation success is evidence, not a guarantee silent failure is eliminated
(FBK-003).
