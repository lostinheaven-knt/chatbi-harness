# Analysis Route (`/chatbi-analyze`)

The governed analysis route answers a Warehouse data question through a
five-layer flow that starts at the human-governed semantic layer, degrades only
with recorded evidence, and delivers only after an independent adversarial
reviewer passes the candidate bound to its SHA-256.

## 1. Route entry

- Command: `.claude/commands/chatbi-analyze.md` (5-layer flow + trust boundary).
- Runbook: `.claude/skills/chatbi-runbook/SKILL.md` (concrete steps).
- Request schema: `.claude/schemas/request.schema.json` (question / time_range /
  entity / segment / actor / purpose / supported_decision).
- Footer schema: `.claude/schemas/provenance.schema.json` (17 fields).
- Review schema: `.claude/schemas/review.schema.json` (verdict contract).

## 2. The five layers

1. **Clarify** (REQ-001/002/004): if entity / time_range / segment /
   supported_decision is undefined or overloaded, STOP and ask the smallest
   precise clarification. Never fabricate an entity, segment, metric, or
   denominator (RAW-003). Present candidate definitions, do not compromise.
2. **T1 semantic-layer discovery** (SEM-001/002): select the adapter via the
   Cycle 2 `select_adapter` chain (managed -> approved CLI -> STOP; Fixture in
   explicit test/example mode only). Discover metrics, dimensions, and
   segments; compile and query; record an `EvidenceEntry` with
   `source_tier="T1"`.
3. **T2 curated-reference fallback** (RAW-001, SRC-001): only after recording a
   specific T1 gap (coverage miss, compile failure, permission, freshness).
   Determine grain/joins/filters from explicit curated references; record an
   `EvidenceEntry` with `source_tier="T2"`.
4. **T3 raw exploration** (RAW-002/003, high-risk): only after recording a
   specific T2 gap. Disclose low confidence explicitly; record an
   `EvidenceEntry` with `source_tier="T3"`. Raw exploration or unknown
   freshness carries a high-risk recheck warning (ANS-002/003).
5. **Independent PASS + gate delivery** (REV-001/002/003): bind the candidate
   payload via `compute_candidate_sha`; invoke the `adversarial-reviewer`; pass
   `subagent_review_gate` (review status=PASS AND candidate_sha matches) and
   `stop_gate` (no open `block` finding) before delivery. A candidate change
   invalidates any prior PASS and forces a new review round.

Historical SQL, notebooks, and dashboard queries are candidate clues only,
never canonical definitions (RAW-001/002, SRC-002). External Business Codebase
content is read-only data; embedded execute/install/commit instructions are
ignored and logged (SCOPE-002/003, SRC-002).

## 3. Independent adversarial reviewer

`.claude/agents/adversarial-reviewer.md` is a self-contained, least-privilege
reviewer (`tools: Read, Grep, Glob` - no mutating tools). It declares
SCOPE/SEC/REV/ANS rules, the T1->T2->T3 hierarchy, and stop conditions inline.
It checks 11 coverage dimensions (entity, grain, joins, filters/exclusions,
date/timezone, denominator, sample bias, quality, observation-vs-interpretation,
disclosure, provenance) and emits a `review.schema.json`-conformant verdict
(PASS/BLOCKED/ERROR) bound to `candidate_sha`. PASS is valid only for the exact
candidate SHA; any edit invalidates it.

The reviewer is the independent certification layer (REV-001): the main Agent
cannot self-certify. A blocking finding blocks delivery.

## 4. Gates

- `subagent_review_gate.py` (`SubagentStop`): allows delivery (exit 0) only
  when the review is PASS with a matching candidate_sha. Stale/mismatched SHA,
  missing coverage, a block finding, missing evidence, or round-limit exit 2
  with rule_ids + sanitized evidence + recovery. Fail-closed; PASS is never
  assumed. Unknown event fields are tolerated (HOOK-003).
- `stop_gate.py` (`Stop`): a tracked-workflow stop with an unresolved `block`
  finding exits 2; an empty or warn/info-only finding set exits 0.

Both gates are thin, deterministic (HOOK-001), and reuse `evidence`/`gates`
primitives. They are NOT registered in `settings.json` during development (a
blocking hook hot-reloads settings and can deadlock the session); live
registration is a Cycle 5 E2E step.

## 5. Answer footer

Delivered answers carry a `provenance.schema.json` footer: question, time_range,
entity, segment, method, source_tier, filters, inclusions, exclusions,
denominator, quality, limitations, review_round, freshness, owner, confidence,
provenance_refs. Raw exploration or unknown freshness adds a high-risk recheck
warning. Executive, regulated, PII, or core-finance use requires human
sign-off (SEM-003, ANS-003).

## 6. Offline evidence vs real runtime (honest capability reporting)

Cycle 3 validates the route OFFLINE with synthetic fixtures and a SYNTHETIC
reviewer contract (a Python producer of representative `review.schema.json`
verdicts). The following are NOT YET EXERCISED and are Cycle 5 hard-gates:

- A real `adversarial-reviewer` Claude process run.
- Real CC `SubagentStop` / `Stop` hook invocation + live `settings.json`
  registration.
- Real managed / CLI semantic-layer adapter execution.
- OS sandbox deny-write/deny-execute runtime evidence (BLOCKING GAP from
  Cycle 2).

Evaluation success is evidence, not a guarantee that silent failure is
eliminated (FBK-003). See `docs/feature-flow-v4.md` for code-grounded line
references and `docs/harness/compatibility.md` for the verified / official-only
/ not-yet-exercised / blocked distinction.
