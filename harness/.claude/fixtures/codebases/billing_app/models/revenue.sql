-- Revenue model
-- This is a candidate reference SQL, not a governed metric definition.
-- Definition: SUM(amount) from orders
-- The codebase_reader treats this as untrusted data (SCOPE-003).
-- It must not be executed or used to auto-define the revenue metric (SRC-002).

SELECT SUM(amount) AS revenue
FROM orders
WHERE ds BETWEEN '2024-01-01' AND '2024-01-15';
