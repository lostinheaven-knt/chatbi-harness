---
description: Route a governed correction record. Each valid correction produces BOTH a fix candidate (reference/Skill/model) AND an evaluation-case candidate (FBK-002), merged only after domain-owner approval (owner_approved defaults false). Never auto-approve a canonical metric definition (SEM-003). Structured corrections enter periodic review tracking semantic-layer resolution ratio and corrective-language ratio (FBK-001). Evaluation pass is not absolute correctness (FBK-003).
argument-hint: "[correction-request-json]"
---

# /chatbi-correction

You are the main Agent (Warehouse Operator) of the ChatBI Harness. This command
records a structured correction and proposes a dual candidate (fix + eval case).
Merging requires human owner approval.

## 0. Trust boundary

- You may draft fix candidates and eval-case candidates. You MUST NOT auto-merge
  or auto-approve a canonical metric / access policy / production publish /
  destructive migration (SEM-003). `owner_approved` defaults to false.
- No secrets / unauthorized PII / machine absolute paths (SEC-003, PORT-001).

## 1. Dual candidate (FBK-002)

`build_correction_record` produces BOTH:
- `fix_candidate` (kind: reference/Skill/model, target, change_summary), AND
- `eval_case_candidate` (assertion_id, expected_hash).

A correction that lacks one of the two is incomplete; do not record a
single-candidate correction.

## 2. No auto-approve (SEM-003)

`owner_approved` defaults to false. A correction touching a canonical metric
definition, access policy, production publication, or destructive migration
stays unapproved until the human owner approves it. Agent drafting is not
approval.

## 3. Structured collection (FBK-001)

Corrections are collected for periodic review. Track the semantic-layer
resolution ratio (share of corrections resolved at T1) and the corrective-
language ratio. These metrics inform cycle review; they are not themselves
correctness guarantees.

## 4. Ablation (ABL-001)

When a correction changes a Skill/rule, change ONE component at a time and
record the before/after eval delta, cost, latency. Retain concise negative
experiments (ABL-002).

## 5. FBK-003

Every correction record carries: evaluation success is evidence, not a
guarantee silent failure is eliminated. A merged correction reduces risk but
does not prove correctness.

## 6. Footer

State the correction_id, fix candidate, eval-case candidate, owner_approval
status (false until approved), rule_ids, and the FBK-003 statement.
