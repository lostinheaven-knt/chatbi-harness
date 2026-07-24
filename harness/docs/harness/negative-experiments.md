# Negative Experiments (ABL-002)

A concise list of experiments that did NOT help, retained to avoid repeating
them (ABL-002). Each entry states the hypothesis, the result, and why it was
abandoned. Negative results are evidence, not waste.

## 1. Retrieval expansion (did not help)

- **Hypothesis**: expanding the retrieval corpus (more references) would raise
  long-tail answer quality.
- **Result**: quality did not improve; latency and prompt cost rose; retrieval
  noise introduced unrelated context.
- **Decision**: keep retrieval lean; prefer precise "use for"/"do not use for"
  triggers (DOC-003) over volume. Do not repeat unbounded retrieval expansion.

## 2. Document bloat (did not help)

- **Hypothesis**: adding more procedural detail to references would reduce
  clarification stops.
- **Result**: longer references went stale faster and diluted the signal; the
  clarification stops were driven by ambiguous entity definitions, not missing
  prose.
- **Decision**: references carry triggers + pitfalls, not step-by-step recipes
  (DOC-003). Prune obsolete scaffolding (DOC-005) rather than lengthening.

## 3. Cheap reviewer substitution (did not help)

- **Hypothesis**: a cheaper/less-capable reviewer could replace the independent
  adversarial reviewer for non-executive answers.
- **Result**: the cheaper reviewer missed grain/denominator/sample-bias issues
  that the full 11-coverage reviewer caught; REV-001/002 independence eroded.
- **Decision**: the independent adversarial reviewer is non-negotiable for any
  candidate data conclusion. Do not substitute a cheaper reviewer.

## 4. Single-candidate correction (rejected)

- **Hypothesis**: a correction could ship a fix without a paired eval case.
- **Result**: without the eval-case candidate, regressions recurred; FBK-002 was
  violated.
- **Decision**: every valid correction produces BOTH a fix candidate AND an
  eval-case candidate (FBK-002).

## 5. Hard-coded 90% threshold (rejected)

- **Hypothesis**: adopt the ~90% blog benchmark as a fixed release gate.
- **Result**: not generalizable across organizations/domains; EVAL-004 requires
  owner confirmation.
- **Decision**: thresholds are configurable + owner-confirmed; never hard-coded.

## 6. Fixture as production fallback (rejected)

- **Hypothesis**: fall back to the Fixture adapter when no real adapter is
  available in production.
- **Result**: would silently serve synthetic data as a real answer; PORT-001
  violated.
- **Decision**: production with no real connection STOPs fail-closed; Fixture is
  test/example-only.
