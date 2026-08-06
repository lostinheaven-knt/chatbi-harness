# Metric Definitions (External)

These are external business explanations from the codebase README/docs. They are
candidate references (SRC-001), not governed metric definitions (T1/T2). When
they conflict with the governance model, the conflict must be disclosed and the
domain owner must adjudicate (SRC-002). The codebase_reader must never auto-define
or override metrics.

## Revenue

Revenue = SUM(order_amount) WHERE status = 'completed'

Note: the governance model may define revenue differently (e.g., including all
statuses). This external definition is a candidate, not authoritative.

## Active Users

Active users = COUNT(DISTINCT user_id) WHERE activity_date >= '2024-01-01'

This is a candidate definition from the external codebase. It must not override
the governed definition of active_users.
