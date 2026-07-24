---
name: chatbi-evaluation
description: Procedural runbook for governed offline evaluation invoked by /chatbi-evaluate. Enforces ground-truth isolation, seen/unseen separation, snapshot-anchored assertions, per-assertion scoring, EVAL-003 run records, owner-confirmed thresholds (EVAL-004), semantic-layer-use assertions (EVAL-005), and the FBK-003 "pass != absolute correctness" disclosure. Carries reusable procedure, not easily-stale facts.
---

# chatbi-evaluation

Evaluation runbook. A run is reproducible, ground-truth-isolated, and explicitly
disclaims absolute correctness (FBK-003).

## 1. Ground-truth isolation (EVAL-001/002)

Ground truth in `GroundTruthVault`. The session under test gets only
`AssertionResult` (pass/fail + hashes). Never read answers into prompts,
examples, or retrieval. Anchor to snapshots / stable facts or score query/entity
selection, not drift-prone numbers.

## 2. Seen / unseen (ABL-001)

Run seen and unseen slices separately. For ablation, change ONE component at a
time and record before/after deltas, cost, latency (ABL-001).

## 3. Per-assertion scoring

`vault.score(assertion_id, actual)` returns `AssertionResult`. A custom scorer
may score entity selection / query results; default is exact canonical equality.

## 4. Run record (EVAL-003)

`build_evaluation_run` records run_id, skill_version, content_hash (no Git ->
content hash), model_id, assertions, tokens, latency_ms, seen,
threshold_owner_confirmed.

## 5. Threshold (EVAL-004)

Release thresholds are configurable and owner-confirmed. Never hard-code the
~90% blog value as a fixed gate. An unconfirmed threshold is recorded as
`threshold_owner_confirmed=False` (not assumed met).

## 6. Semantic-layer use (EVAL-005)

Semantic-covered cases assert the answer hits the semantic layer. Offline
accuracy target near 100%, but never claim online errors are eliminated.

## 7. FBK-003 (mandatory)

Every run carries: evaluation success is evidence, NOT a guarantee silent
failure is eliminated, NOT absolute correctness. A pass is never described as
proof of correctness.

## 8. Negative experiments (ABL-002)

Keep a concise list of negative experiments (failed retrieval expansion,
doc bloat, cheap-reviewer substitution) in `docs/harness/negative-experiments.md`
to avoid repeating them.
