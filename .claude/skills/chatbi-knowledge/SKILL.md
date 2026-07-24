---
name: chatbi-knowledge
description: Procedural runbook for authoring and maintaining governed knowledge references co-located with the Warehouse model. Enforces required metadata, "use for"/"do not use for" triggers, no machine absolute paths, historical-SQL-as-candidate-only, and cross-references (DOC-001/002/003/005). Carries reusable procedure, not easily-stale facts.
---

# chatbi-knowledge

Authoring + maintenance runbook for governed knowledge references. A reference
must be route-ready before it can be retrieved by `/chatbi-analyze` or updated by
`/chatbi-maintain-knowledge`.

## 1. Bind to the template

Start from `references/_template.md`. Every section is required (DOC-002/003).
Do not delete a section because you have nothing to say - state "not applicable"
with the reason.

## 2. Required fields (DOC-002)

Business context, Grain, Standard filters, Dimensions, Key models, Scope and
exclusions, Joins, Common pitfalls, Best practices, Cross-references, Owner,
Freshness, Use for, Do not use for.

## 3. "Use for" / "Do not use for" (DOC-003)

State explicit trigger conditions, not step-by-step recipes that go stale. "Use
this reference when ..."; "Do NOT use this reference when ...". Both must be
non-empty.

## 4. Paths and references (PORT-001, DOC-001)

Use logical aliases and relative references only. Never machine absolute paths
(`/Users/...`, `/home/...`). Cross-references must list at least one neighbor
reference (`.md`/`.sql`/`.json`).

## 5. Historical SQL (RAW-001/002, SRC-001/002)

Historical SQL / notebooks / dashboard queries are candidate clues only, never
canonical definitions. Any SQL block must carry the `candidate_only` marker.

## 6. Lint before publish

Run `chatbi_harness.knowledge.lint_reference(text)`. An empty issue tuple means
route-ready. Every issue must be resolved (fail-closed); never publish a
reference with open issues. Conflicts between a reference and governed facts go
to the domain owner (SRC-002).

## 7. Maintenance and pruning (DOC-005)

When a model improves, prune obsolete scaffolding and negative-value references
rather than lengthening prompts to paper over failure. Remove a reference only
when evidence supports doing so; record the removal as an atomic change.
