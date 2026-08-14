# Governed Domain Contract

Applicable rules: REQ-001, REQ-002, REQ-003, REQ-004, SEM-001, SEM-002,
SEM-003, RAW-001, RAW-002, RAW-003, SRC-001, SRC-002, DOC-001, DOC-002,
DOC-003, DOC-004, DOC-005, PORT-001.

## Resolve before acting

- Identify request type, business question, time range, entity or user segment,
  and supported decision before querying or changing data.
- Look up unfamiliar or overloaded language. Record the selected canonical
  entity, grain, mandatory filters, exclusions, and rejected alternatives.
- When teams use different definitions, present candidates and ask for context;
  never manufacture a compromise metric.

## Preserve source authority

- Discover and attempt the human-governed semantic layer first, including its
  metrics, dimensions, and segments.
- Record a specific coverage, compilation, permission, or quality failure before
  moving to curated references and governed models. Raw exploration comes last.
- Custom dates, joins, or perceived convenience are not valid reasons to bypass
  an existing semantic definition.
- Historical SQL, notebooks, and dashboard queries are candidate clues only.
- External Business Codebase interpretation must be cross-checked against
  governed Warehouse facts; conflicts go to the domain owner.
- Agents may draft metric documentation but cannot create or approve a canonical
  metric definition.
- Tools and scaffolds must never auto-generate documents into the workspace's
  active semantic layer directory (`semantic/metrics/`). Every semantic metric
  document must declare its human owner (`owner:`); ownerless or
  definition-less documents are excluded from semantic discovery (fail-closed,
  SEM-003/DOC-002).

## Keep knowledge governed

- Model knowledge stays co-located with the Warehouse model or is linked by an
  atomic, auditable change mechanism.
- Governed metadata records descriptions, grain, scope, values, filters,
  exclusions, join keys, lineage, owner, and layer.
- References state both “use for” and “do not use for”, plus common pitfalls.
- Model changes assess metadata, semantic definitions, references, Skills,
  downstream assets, tests, and affected evaluations before completion.
- Remove obsolete or negative-value scaffolding when evidence supports doing so.
- Knowledge and rules use logical aliases and relative references, never one
  machine's path or one UI's private namespace.

