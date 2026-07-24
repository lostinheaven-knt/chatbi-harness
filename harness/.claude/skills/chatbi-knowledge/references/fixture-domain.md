# Reference: revenue_example (synthetic domain)

> Synthetic fixture domain reference (DOC-002/003). No organizational real
> facts, secrets, or machine paths. Used for offline knowledge-lint/retrieval
> tests. Marked candidate_only where historical SQL appears.

## Business context

`revenue_example` answers "what recognized revenue did acme_example record in a
period?" for the monthly growth review decision. It is a governed, aggregated
metric, not a raw ledger.

## Grain

One row per `order_line` per `revenue_day`.

## Standard filters

Always apply: `is_test = false`, `status = 'fulfilled'`, `revenue_day <=
as_of_date`. Never aggregate raw `amount` without these filters.

## Dimensions

`region`, `product_line`, `channel`, `customer_tier`.

## Key models

- `models/revenue_example.sql` (governed model, logical alias)
- `semantic-catalog:metric:revenue_example`

## Scope and exclusions

In scope: fulfilled, non-test, recognized revenue. Excludes: refunded orders,
internal/test accounts, pre-fulfillment holds.

## Joins

`order_line.order_id -> orders.id` (many-to-one); `orders.customer_id ->
customers.id` (many-to-one). Cardinality documented to prevent fan-out
double-counting.

## Common pitfalls

- Double-counting when joining `order_line` to `orders` without the grain filter.
- Timezone: `revenue_day` is in UTC; align to the actor's reporting tz before
  aggregation.
- NULL `amount` rows are excluded, not zero-filled.

## Best practices

Aggregate at the documented grain; apply all standard filters; disclose the
as_of_date and tz in the answer footer.

## Cross-references

- ../../chatbi-knowledge/references/_template.md
- models/revenue_example.sql

## Owner

`domain_owner_revenue_example` (logical role).

## Freshness

`snapshot_2024_01` - synthetic fixture snapshot; a real unknown freshness would
trigger a high-risk recheck warning (ANS-002).

## Use for

Use `revenue_example` for recognized-revenue growth questions over a bounded
period with the standard filters applied.

## Do not use for

Do NOT use for real-time revenue, refunds/chargebacks, or PII-level customer
breakdowns; do not bypass the semantic layer by writing custom joins for
convenience (RAW-002).

## Historical SQL

> Historical clue only, not canonical. Marked candidate_only.

```sql
-- candidate_only: historical reporting clue, not the canonical definition
SELECT SUM(amount) FROM order_lines WHERE status='fulfilled';
```
