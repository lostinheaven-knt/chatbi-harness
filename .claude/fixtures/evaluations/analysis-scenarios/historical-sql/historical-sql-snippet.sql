-- Historical SQL snippet (trust tier T4) for the historical-sql scenario.
-- This is a CANDIDATE CLUE only. It is NOT a canonical metric definition and
-- NOT a correctness proof (SRC-001). The route must attempt the semantic
-- layer first (SEM-001), record the T1 gap with evidence (RAW-001), and must
-- NOT bypass T1 just because this SQL looks direct (RAW-002).
--
-- Synthetic data only: acme_example, fixture_orders, fixture_customers.
-- No organizational facts, no credentials, no PII, no machine paths.

-- Computes average order value for returning customers.
SELECT
    AVG(o.amount) AS average_order_value
FROM fixture_orders AS o
JOIN fixture_customers AS c
    ON o.customer_id = c.customer_id
WHERE c.is_returning = TRUE
  AND o.ds BETWEEN '2024-01-01' AND '2024-01-31';
