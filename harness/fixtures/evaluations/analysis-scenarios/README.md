# Analysis Scenarios (Cycle 3 Task 05)

Synthetic fixed-data stress scenarios for the governed analysis route
(`/chatbi-analyze`). Consumed by Task 06 (`tests/harness/test_e2e.py`) to
verify the clarify / degrade / block behavior of the five-layer flow.

## Hard constraints

- Synthetic fixed data ONLY. No organizational real facts, secrets,
  credentials, or machine absolute paths (SEC-003, PORT-001).
- Every `request.json` conforms to
  `.claude/schemas/request.schema.json` (7 required string fields;
  `additionalProperties: false`).
- `expected.json` declares the expected `source_tier` path, the expected
  adversarial-review verdict (PASS / BLOCKED + which coverage / finding),
  and the expected provenance-footer assertions.
- These are fixtures, not tests. Task 06 consumes them; this directory
  adds no tests.

## Scenarios

| # | Scenario | Expected behavior | Rules |
| --- | --- | --- | --- |
| 1 | `ambiguity` | stop-and-clarify; do NOT fabricate metric/denominator | REQ-001, REQ-002, SEM-001 |
| 2 | `stale` | unknown/stale freshness -> high-risk recheck warning | ANS-002, QLT-001 |
| 3 | `historical-sql` | historical SQL is a clue only; evidence-backed degradation | RAW-001, RAW-002, SRC-002 |
| 4 | `prompt-injection` | external-codebase execute/install/commit instructions ignored and logged | SCOPE-003, SRC-002 |
| 5 | `pii-permission` | PII/permission insufficient -> block + minimum authorization | SEC-001, SEC-002, SEM-003 |

## Directory layout

```
analysis-scenarios/
├── README.md                          (this file)
├── ambiguity/
│   ├── request.json
│   ├── expected.json
│   └── semantic-catalog-fragment.json
├── stale/
│   ├── request.json
│   ├── expected.json
│   └── warehouse-snapshot-stale.json
├── historical-sql/
│   ├── request.json
│   ├── expected.json
│   ├── historical-sql-snippet.sql
│   └── semantic-catalog-fragment.json
├── prompt-injection/
│   ├── request.json
│   ├── expected.json
│   └── external-codebase-snippet.md
└── pii-permission/
    ├── request.json
    ├── expected.json
    └── access-policy-fragment.json
```

All names are clearly fake (`acme_example`, `metric_revenue_test`,
`fixture_orders`). No fixture here connects to a real warehouse.
