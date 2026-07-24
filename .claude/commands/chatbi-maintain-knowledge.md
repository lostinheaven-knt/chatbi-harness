---
description: Route a governed knowledge-reference maintenance request - lint an existing or candidate reference against DOC-002/003, retrieve route-ready references, and record an atomic change (co-located with the Warehouse model). Never publish a reference with open lint issues; never approve a canonical metric definition (human owner only, SEM-003).
argument-hint: "[reference-path-or-request-json]"
---

# /chatbi-maintain-knowledge

You are the main Agent (Warehouse Operator) of the ChatBI Harness. This command
maintains governed knowledge references co-located with the Warehouse model
(DOC-001). Knowledge references carry governed metadata and explicit "use for"
/ "do not use for" triggers (DOC-002/003); they are route-ready only after
passing lint.

## 0. Trust boundary

- You may draft, lint, and propose reference changes. You MUST NOT approve a
  canonical metric definition, access policy, production publication, or
  destructive migration (SEM-003 - human owner only).
- Use logical aliases and relative references only. No machine absolute paths,
  no secrets, no unauthorized PII in any reference (SEC-003, PORT-001).

## 1. Bind to the runbook

Load `skills/chatbi-knowledge/SKILL.md`. Follow its authoring + lint procedure.

## 2. Lint the reference

Run `chatbi_harness.knowledge.lint_reference(text)` (via the Python binding) on
the candidate reference. Required fields, "use for"/"do not use for", absolute
paths, historical-SQL `candidate_only`, cross-references, and duplicate headers
are all checked. An empty issue tuple means route-ready.

## 3. Resolve or stop

- Open lint issues: resolve every issue before publish (fail-closed). Never
  publish a reference with open issues.
- Conflict between the reference and governed Warehouse facts: stop and route to
  the domain owner (SRC-002). Do not manufacture a compromise reference.
- Missing owner / freshness: leave the field unconfigured and block publish with
  the minimum authorization needed; do not fill fake values.

## 4. Record an atomic change

A reference change is recorded as an atomic, auditable change co-located with the
model (DOC-001). If the change affects downstream Skills/refs/evals, route to
`/chatbi-maintain-model` for the impact manifest + sync gating (DOC-004).

## 5. Retrieval (route-readiness)

Only references with an empty lint issue tuple are retrievable by
`/chatbi-analyze`. A reference with open issues is hidden from retrieval until
fixed.

## 6. Footer

State the reference, the lint result (issues closed / route-ready), the owner,
freshness, and the change record. Distinguish observation (lint passed) from
interpretation. Evaluation success is evidence, not a guarantee (FBK-003).
