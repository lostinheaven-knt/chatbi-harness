# Validation and Completion

Applicable rules: QLT-001, REV-001, REV-002, REV-003, ANS-001, ANS-002,
ANS-003, EVAL-001, EVAL-002, EVAL-003, EVAL-004, EVAL-005, ABL-001,
ABL-002, FBK-001, FBK-002, FBK-003, HOOK-001, HOOK-002, HOOK-003,
HOOK-004, HOOK-005.

## Data conclusions

- Check source freshness, completeness, and anomalies; executable SQL does not
  compensate for bad data.
- Every candidate data conclusion requires an independent adversarial reviewer.
  Review covers entity, grain, joins, filters/exclusions, date/timezone,
  denominator, sample bias, quality, observation versus interpretation,
  disclosure, and provenance.
- Fix every blocking finding and review the changed candidate again. If a
  finding cannot close, stop delivery or escalate it explicitly.
- Final answers separate observations from interpretations and disclose method,
  filters, inclusions, exclusions, limitations, source tier, confidence,
  reviewer round, maximum data date or unknown freshness, and model owner.
- Raw exploration or unknown freshness carries a high-risk recheck warning;
  management material requires human sign-off.

## Evaluation and feedback

- Each released domain has owner-verified high-frequency and long-tail cases.
- Anchor numeric cases to snapshots or score stable query/entity contracts.
- Record suite and Skill version, repository revision or content hash, model,
  each assertion, tokens, latency, and review rounds.
- Release thresholds are configurable and owner-approved; do not hard-code a
  vendor benchmark. Semantic-covered cases should assert semantic-layer use.
- Isolate ground truth from runtime prompts, examples, and retrieval material.
- Change one evaluated component at a time and retain concise negative
  experiments where useful.
- Collect corrections as structured records. Each accepted correction proposes
  both a model/knowledge/Skill repair and a regression evaluation case, subject
  to human approval.
- Evaluation reduces risk but cannot prove that silent failure is eliminated.

## Deterministic gates

- Hooks perform deterministic checks, not open-ended business interpretation.
- Map a gate capability to an event only after local version and input/exit
  behavior are verified. Do not guess Hook fields or semantics.
- Every failure includes governed rule IDs, sanitized evidence, a concrete
  reason, and a recovery action.
- High-cost or nondeterministic evaluation belongs in an explicit Command or CI;
  Hooks only determine whether evidence is required and present.
