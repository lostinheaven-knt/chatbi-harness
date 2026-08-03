# Reference: <entity or metric name>

> Governance template (DOC-002/003). Co-located with the Warehouse model or
> linked by an atomic, auditable change (DOC-001). Fill every section. "Use for"
> / "Do not use for" are mandatory triggers, not optional prose. Remove this
> block when authoring. No machine absolute paths (PORT-001).

## Business context

<What business question this entity/metric answers; the decision it supports.>

## Grain

<Row granularity, e.g. one row per order line per day.>

## Standard filters

<Mandatory filters that must always be applied, e.g. status='active', is_test=false.>

## Dimensions

<Breakdown dimensions: region, product_line, channel, ...>

## Key models

<Governed models/tables this reference binds to (logical aliases, not paths).>

## Scope and exclusions

<What is in scope and explicitly excluded; e.g. excludes internal/test orders.>

## Joins

<Join keys and cardinality to neighbor entities.>

## Common pitfalls

<Pitfall 1. Pitfall 2. Double-counting, timezone, NULL handling, ...>

## Best practices

<Recommended usage patterns.>

## Cross-references

- related/reference-example.md
- models/revenue_example.sql

## Owner

<Domain owner (logical role), not a personal account.>

## Freshness

<Snapshot date or freshness SLA; "unknown" triggers a high-risk recheck warning.>

## Citation

> Optional (DOC-002). Machine-captured when this reference is authored from a
> codebase read. Do not hand-edit the git_sha. `/chatbi-audit-drift` compares
> the cited git_sha against the codebase alias's current HEAD to detect
> accumulated reference staleness (FM-STALE). Omit this section entirely when
> the reference is not derived from a codebase read.

- alias: <business_codebase alias>
- relative_path: <portable relative path>
- git_sha: <40/64 hex sha | content_sha256>
- captured_at: <ISO Z>

## Use for

<Trigger conditions: use this reference when ...>

## Do not use for

<Trigger conditions: do NOT use this reference when ...; e.g. real-time, PII-level.>

## Historical SQL

> Historical SQL/notebook/dashboard queries are candidate clues only, never
> canonical definitions (RAW-001/002, SRC-001/002). Marked candidate_only.

```sql
-- candidate_only: example historical clue, not a canonical definition
SELECT ... FROM ...
```
