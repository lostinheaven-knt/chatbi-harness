# Knowledge Authoring (`/chatbi-maintain-knowledge`)

Governed knowledge references are co-located with the Warehouse model (DOC-001)
and carry governed metadata with explicit "use for" / "do not use for" triggers
(DOC-002/003). A reference is route-ready only after passing lint.

## 1. Route entry

- Command: `.claude/commands/chatbi-maintain-knowledge.md`.
- Runbook: `.claude/skills/chatbi-knowledge/SKILL.md`.
- Template: `.claude/skills/chatbi-knowledge/references/_template.md`.
- Sample: `.claude/skills/chatbi-knowledge/references/fixture-domain.md`.
- Lint: `.claude/lib/chatbi_harness/knowledge.py` (`lint_reference`).

## 2. Required fields (DOC-002)

Business context, Grain, Standard filters, Dimensions, Key models, Scope and
exclusions, Joins, Common pitfalls, Best practices, Cross-references, Owner,
Freshness, Use for, Do not use for. Every field is required; state "not
applicable" with a reason rather than deleting a section.

## 3. "Use for" / "Do not use for" (DOC-003)

State explicit trigger conditions, not step-by-step recipes that go stale. Both
must be non-empty. "Use this reference when ..."; "Do NOT use this reference
when ...".

## 4. Paths and references (PORT-001, DOC-001)

Logical aliases and relative references only. No machine absolute paths
(`/Users/...`, `/home/...`). Cross-references must list at least one neighbor
reference (`.md`/`.sql`/`.json`).

## 5. Historical SQL (RAW-001/002, SRC-001/002)

Historical SQL / notebooks / dashboard queries are candidate clues only, never
canonical definitions. Any SQL block must carry the `candidate_only` marker.

## 6. Lint before publish

`lint_reference(text)` returns issues; an empty tuple means route-ready. Every
issue must be resolved (fail-closed). A reference with open issues is hidden
from retrieval by `/chatbi-analyze`. Conflicts with governed facts route to the
domain owner (SRC-002); do not manufacture a compromise reference.

## 7. Maintenance and pruning (DOC-005)

When a model improves, prune obsolete scaffolding and negative-value references
rather than lengthening prompts to paper over failure. Remove a reference only
when evidence supports it; record the removal as an atomic change.

## 8. Honest capability reporting

- **VERIFIED OFFLINE**: the lint contract (required fields, use-for/do-not-use-for,
  absolute paths, candidate_only, cross-references, duplicates) against the
  template and fixture-domain (`test_knowledge.py`).
- **NOT YET EXERCISED (Cycle 5)**: real knowledge-base runtime + live retrieval.
- `fixture-domain.md` is synthetic (no organizational real facts/secrets/paths).
