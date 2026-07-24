# Evaluation Route (`/chatbi-evaluate`)

The governed evaluation route runs a fixed offline suite (high-frequency +
long-tail + 5 stress) with ground-truth isolation, records a reproducible run,
and explicitly disclaims absolute correctness (FBK-003).

## 1. Route entry

- Command: `.claude/commands/chatbi-evaluate.md`.
- Runbook: `.claude/skills/chatbi-evaluation/SKILL.md`.
- Evaluator: `.claude/lib/chatbi_harness/evaluator.py` (`GroundTruthVault`,
  `EvaluationRun`, `build_evaluation_run`).
- Schemas: `.claude/schemas/evaluation.schema.json`.
- Suite fixtures: `.claude/fixtures/evaluations/suite/{high-freq,long-tail}.json`
  + `.claude/fixtures/evaluations/analysis-scenarios/**` (5 stress).

## 2. Ground-truth isolation (EVAL-001/002)

Ground truth lives in `GroundTruthVault`. The session under test receives only
`AssertionResult` (pass/fail + `expected_hash`/`actual_hash`). There is no API
that returns the raw expected answer, so a tested session cannot read answers.
Never exfiltrate answers into prompts, examples, or retrieval material. Anchor
to snapshots / stable facts or score query/entity selection, not drift-prone
numbers.

## 3. Seen / unseen (ABL-001)

Run seen and unseen slices separately. For ablation, change ONE component at a
time and record before/after deltas, cost, latency.

## 4. Run record (EVAL-003)

`EvaluationRun` records run_id, skill_version, content_hash (no Git -> content
hash), model_id, per-assertion results, tokens, latency_ms, seen,
threshold_owner_confirmed.

## 5. Threshold (EVAL-004)

Release thresholds are configurable and owner-confirmed. Never hard-code the
~90% blog value as a fixed gate. An unconfirmed threshold is recorded as
`threshold_owner_confirmed=False` (not assumed met).

## 6. Semantic-layer use (EVAL-005)

Semantic-covered cases assert the answer hits the semantic layer. Offline
accuracy target near 100%, but never claim online errors are eliminated.

## 7. FBK-003 (mandatory)

Every run carries: evaluation success is evidence, NOT a guarantee that silent
failure is eliminated and NOT absolute correctness. A pass is never described as
proof of correctness.

## 8. Honest capability reporting

- **VERIFIED OFFLINE**: ground-truth isolation, per-assertion scoring, run
  records, seen/unseen, dual-candidate correction, six-Command routing,
  production-no-connection STOP (`test_evaluation.py`, `test_correction.py`,
  `test_e2e.py`).
- **NOT YET EXERCISED (Task 06 human gate)**: real evaluation runtime with real
  adapter/reviewer; real CC Hook process E2E.
- **BLOCKING GAP (continues)**: OS sandbox deny runtime evidence.

See `docs/feature-flow-v6.md` for code-grounded line references.
