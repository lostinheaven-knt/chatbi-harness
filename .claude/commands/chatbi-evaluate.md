---
description: Route a governed evaluation run over the fixed suite (high-frequency + long-tail + 5 stress scenarios) with ground-truth isolation. Record run_id/skill_version/content_hash/model_id/per-assertion(seen/unseen)/tokens/latency. Owner-confirmed threshold (EVAL-004, never hard-coded 90%). Semantic-covered cases assert semantic-layer use (EVAL-005). Evaluation pass is NOT absolute correctness (FBK-003). Never auto-approve a canonical metric (SEM-003).
argument-hint: "[evaluation-request-json]"
---

# /chatbi-evaluate

You are the main Agent (Warehouse Operator) of the ChatBI Harness. This command
runs the governed offline evaluation suite. Ground truth is isolated from the
session under test; only pass/fail + hashes are exposed.

## 0. Trust boundary

- You may run the suite and record results. You MUST NOT approve a release
  threshold on your own (EVAL-004 - owner confirmed) or approve a canonical
  metric (SEM-003).
- No secrets / unauthorized PII / machine absolute paths in any run record
  (SEC-003, PORT-001).

## 1. Bind to the runbook

Load `skills/chatbi-evaluation/SKILL.md`. Follow its isolation + scoring +
record procedure.

## 2. Ground-truth isolation

Ground truth lives in `GroundTruthVault`; the session under test receives only
`AssertionResult` (pass/fail + expected_hash/actual_hash), never the raw answer
(EVAL-001/002). Never exfiltrate answers into prompts, examples, or retrieval
material.

## 3. Run the fixed suite

Score high-frequency + long-tail + 5 stress scenarios. Anchor to snapshots /
stable facts or score query/entity selection (not drift-prone numbers, EVAL-002).
Separate seen and unseen results. Semantic-covered cases assert semantic-layer
use (EVAL-005).

## 4. Record the run (EVAL-003)

`build_evaluation_run` records run_id, skill_version, content_hash (no Git ->
content hash), model_id, per-assertion results, tokens, latency_ms, seen,
threshold_owner_confirmed. Thresholds are configurable and owner-confirmed
(EVAL-004); never hard-code the ~90% blog value as a fixed gate.

## 5. FBK-003 (mandatory)

Every run carries the statement: evaluation success is evidence, NOT a guarantee
that silent failure is eliminated and NOT absolute correctness. State this in
the result; do not describe a pass as proof of correctness.

## 6. Footer

State the suite, seen/unseen counts, pass/fail per assertion, threshold +
owner-confirmation status, model, tokens, latency, content_hash, and the
FBK-003 statement. Distinguish observation (assertions passed) from
interpretation.
