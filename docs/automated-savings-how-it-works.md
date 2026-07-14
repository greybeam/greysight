# Automated Savings technical reference

Automated Savings is an opt-in worker that suspends enrolled Snowflake warehouses when a current `SHOW WAREHOUSES` result proves they are idle. It uses this command:

```sql
ALTER WAREHOUSE "<warehouse-name>" SUSPEND
```

It does not change `AUTO_SUSPEND`, `AUTO_RESUME`, cluster settings, or query configuration. It does not issue any Snowflake mutation other than the direct suspend command.

For deployment and incident procedures, see [`automated-savings.md`](./automated-savings.md).

## Components

| Component                     | Responsibility                                                                                                                                                         |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Worker (`apps/auto-savings/`) | Polls Snowflake, evaluates eligibility, obtains final authorization, suspends warehouses, and records accepted commands. It is the only Snowflake writer.              |
| API (`apps/api/`)             | Records agreement and switches, captures enrollment identity, lists read-only warehouse metadata, and performs on-demand grant checks. It does not suspend warehouses. |
| Web (`apps/web/`)             | Lets authorized users agree to the feature, enable it globally or per warehouse, and view read-only warehouse metadata.                                                |
| Supabase                      | Stores settings, identity-bound enrollments, and accepted suspend events. It also provides the final authorization RPC.                                                |

Each worker replica owns a deterministic set of organizations. Do not run two processes for the same replica index. A per-tenant lock prevents overlapping cycles in one process; sharding prevents overlapping cycles across processes.

## Processing a warehouse snapshot

Each tenant cycle does the following:

```text
SHOW WAREHOUSES
  → parse the returned rows
  → load enabled enrollments
  → evaluate each enrolled warehouse against the snapshot
  → reauthorize the current switches, identity, and enrollment version
  → ALTER WAREHOUSE … SUSPEND
  → record an accepted command as an audit event
```

The worker makes its decision from one `SHOW WAREHOUSES` response. It does not wait for a second idle observation and does not store an idle timer. The 62-second uptime floor is the only time-based eligibility condition.

## Eligibility

An enrolled warehouse is eligible only when one parsed snapshot proves all of the following:

- The warehouse type is `STANDARD`.
- The warehouse state is `STARTED`.
- `running` and `queued` are valid nonnegative integers and both are zero.
- `quiescing` is zero. A valid nonnegative integer or Snowflake's observed empty-string idle value (`''`) is accepted; a missing or malformed value is rejected.
- `AUTO_RESUME` is enabled.
- `resumed_on` is timezone-aware and the warehouse has been running for at least 62 seconds.
- `created_on` is timezone-aware and exactly matches the value captured when the warehouse was enrolled.

Missing, malformed, fractional, negative, non-finite, or timezone-naive decision fields fail closed. Nonzero quiescing, activity, transitional states, and suspended states fail closed.

Cluster counts and the customer's `AUTO_SUSPEND` value do not affect eligibility. Cluster counts are retained as nullable observation data. Multi-cluster `STANDARD` warehouses are supported, but warehouse-level `resumed_on` does not establish savings for each individual cluster.

## Identity and final authorization

The API captures a warehouse's `created_on` timestamp during enrollment. The worker compares every snapshot to that identity. If the identity is missing, the warehouse is skipped. If it differs, the worker conditionally removes the stale enrollment and does not issue a command.

Immediately before a command, the worker calls `automated_savings_authorize_suspend`. The RPC succeeds only when the current global switch, warehouse switch, warehouse identity, and enrollment `updated_at` version all match. It does not write state.

Disabling a switch before this authorization prevents the command. Disabling a switch after authorization returns does not cancel the command already in flight. Snowflake's command is name-based, so a drop and recreate after the final snapshot but before the command remains a small unavoidable race.

## Command outcomes

| Outcome                     | Worker behavior                                                                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Command accepted            | Records a best-effort `action=suspend`, `reason=idle` audit event. An audit-write failure does not cause a same-cycle retry.                            |
| Snowflake error `90064`     | Treats the result as unknown and idempotent, not as proof of suspension. It records no audit event, keeps a healthy session, and applies retry backoff. |
| Timeout or connection error | Records no audit event, closes the Snowflake session, reconnects, and applies retry backoff.                                                            |
| Other command error         | Records no audit event and applies the normal error backoff.                                                                                            |

The worker does not persist attempt recovery state. A later eligible snapshot may issue another suspend command after an unknown outcome or restart.

## Observability and stored data

Supabase stores settings, enrollments, and accepted direct-suspend events. It does not store cooldowns, restore intents, managed defaults, drift state, or pending command state.

Every unique warehouse observation receives a fresh in-memory `attempt_id`. Snapshot, authorization, request, outcome, and metric logs for that observation use the identifier for correlation. The worker retains no attempt state across cycles; correlate consecutive observations of a warehouse by name and timestamp in the logs.

Duplicate warehouse names in one `SHOW WAREHOUSES` response are ambiguous. The worker logs `snapshot_ambiguous` and does not authorize, remove an enrollment, or suspend that name during that cycle. Other unique names can still proceed.

The worker emits structured `event="metric"` logs:

| Metric                               | Fields                            |
| ------------------------------------ | --------------------------------- |
| `auto_savings_suspend_attempt_total` | `attempt_id`, `outcome`           |
| `auto_savings_suspend_error_total`   | `attempt_id`, `errno`, `sqlstate` |
| `auto_savings_authorization_total`   | `attempt_id`, `result`            |

Outcome logs always include `connector_error_type`, `connector_errno`, `connector_sqlstate`, and `connector_message`; values are null when no connector error exists.

## Code map

| Concern                                                | File                                                                                       |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| Snapshot parsing and uptime calculation                | `apps/auto-savings/src/auto_savings/warehouse_snapshot.py`                                 |
| Eligibility rules                                      | `apps/auto-savings/src/auto_savings/decision.py`                                           |
| Authorization, command orchestration, and audit events | `apps/auto-savings/src/auto_savings/engine.py`                                             |
| Snowflake session and direct suspend command           | `apps/auto-savings/src/auto_savings/snowflake_session.py`                                  |
| Per-tenant loop, retry behavior, and supervisor        | `apps/auto-savings/src/auto_savings/tenant_loop.py`                                        |
| Worker persistence interface                           | `apps/auto-savings/src/auto_savings/store.py`                                              |
| API enrollment and warehouse metadata                  | `apps/api/app/routes/automated_savings.py`, `apps/api/app/services/warehouse_directory.py` |
| Database schema and authorization RPCs                 | `supabase/migrations/202607120001_automated_savings.sql`                                   |
