# Automated Savings — How It Works

Automated Savings observes one live warehouse snapshot, makes a fail-closed
decision, reauthorizes against current Supabase state, and asks Snowflake to
suspend the warehouse directly.

```text
SHOW WAREHOUSES
  → parse decision fields and warehouse identity
  → match an enabled enrollment
  → evaluate the first eligible snapshot
  → reauthorize current settings, identity, and enrollment version
  → ALTER WAREHOUSE … SUSPEND
  → write a best-effort direct audit event
```

For deployment settings and incident procedures, see
[`automated-savings.md`](./automated-savings.md).

## Three surfaces, one Snowflake writer

| Surface | Responsibility |
|---|---|
| Worker (`apps/auto-savings/`) | Polls, decides, authorizes, and exclusively issues direct suspend commands. |
| API (`apps/api/`) | Manages agreement, switches, identity-bound enrollment, on-demand access checks, and read-only warehouse metadata. It never alters a warehouse. |
| Web (`apps/web/`) | Its client contract carries live state, quiescing percentage, cluster counts, and `AUTO_SUSPEND`. The table renders name, read-only `AUTO_SUSPEND`, derived status, and the enabled switch. |

The worker must remain the single Snowflake writer for each shard. Per-tenant
locks prevent overlapping ticks inside a process, and deterministic sharding
assigns an organization to one replica. Operators must not deploy overlapping
processes for the same shard.

## One snapshot, one time gate

The worker uses `SHOW WAREHOUSES` because it supplies current warehouse state
without resuming compute. It derives uptime from `resumed_on`; uptime is not
stored in Supabase.

One snapshot is eligible only when all of these facts are proven:

- `type == STANDARD`;
- `state == STARTED`;
- `running == 0` and `queued == 0` after strict parsing of those statement
  counts;
- `quiescing == 0` after parsing a valid nonnegative integer or Snowflake's
  observed idle empty-string encoding (`''`);
- `AUTO_RESUME == true`;
- `now - resumed_on >= 62 seconds`;
- live `created_on` exactly matches the enrollment identity.

`resumed_on` and `created_on` must include timezone information. Missing,
timezone-naive, malformed, negative, fractional, or non-finite critical values
fail closed. Transitional states and any nonzero quiescing percentage fail
closed.

The empty-string exception applies only to `quiescing`; a missing (`None` or
absent) value still fails closed. A real compute drain exposed
`SUSPENDING`/`quiescing='100'` in the Snowflake spike. `SYSTEM$WAIT` did not
reproduce that transition, and `SELECT 1` did not force auto-resume, so use a
real compute query when testing those behaviors.

The worker acts on the first eligible snapshot. It does not require consecutive
idle observations and has no separate idle timer. The 62-second uptime floor is
the only time gate and protects Snowflake's first billed minute after resume.

## Direct suspension leaves customer configuration alone

For an eligible and authorized warehouse, the warm tenant session issues:

```sql
ALTER WAREHOUSE "<escaped-name>" SUSPEND
```

Snowflake allows executing statements to finish while the warehouse quiesces.
Greysight never changes `AUTO_SUSPEND`, `AUTO_RESUME`, minimum or maximum
clusters, or a query. `AUTO_SUSPEND` remains visible through the API and UI as
read-only customer configuration.

The API/client contract also carries live state, quiescing percentage, and
cluster counts, but the current table folds operational state into a derived
status instead of rendering those fields as separate columns. Worker cluster
observations are nullable audit data, not decision gates. Direct suspension is
a warehouse-level command and supports multi-cluster `STANDARD` warehouses.
The caveat is analytical: warehouse-level `resumed_on` cannot prove per-cluster
uptime or savings, so Greysight does not claim per-cluster savings from those
fields.

## Grant checks are informational

The configured Snowflake role may use account-level `MANAGE WAREHOUSES`, or
per-warehouse `OPERATE` with the required `USAGE` context, for the direct
command.
The API and UI can inspect that privilege on demand, but agreement, enrollment,
worker tenant discovery, and final authorization do not gate on the stored
`grant_present` value. If the privilege is missing or revoked, Snowflake rejects
the command and the tenant uses the normal failure path: close the failed
session, reconnect, and back off until access is restored or automation is
disabled. The application does not mark the tenant paused.

## Identity prevents stale enrollment reuse

Enrollment succeeds only after the API captures a live, timezone-aware
`created_on`. The database requires that identity. Every worker decision checks
the same field; if it is absent, the decision stops, and if it differs, the
worker conditionally deletes the stale versioned enrollment without touching
the live warehouse.

This closes ordinary drop/recreate reuse, but not the final name race.
Snowflake's `ALTER WAREHOUSE` identifies a warehouse only by name. A drop and
recreate after the snapshot and authorization but before the command is an
irreducible TOCTOU window. Another `SHOW` could narrow but not eliminate it.

## Final authorization and disabling

Eligibility is not authority. Immediately before each command, the worker asks
the service-role `automated_savings_authorize_suspend` RPC to match:

- the current global switch;
- the current warehouse switch;
- warehouse name and captured identity;
- the enrollment's exact `updated_at` version.

The RPC is read-only. A disable or enrollment edit committed before it runs
rejects authorization. Once it returns true, that authorization owns the
in-flight command: disabling afterward prevents later authorizations but does
not cancel this command. Supabase cannot hold a transaction lock across an
external Snowflake request, which is why operational single-writer ownership
also matters.

## Command results

An accepted request produces a best-effort append-only event with
`action=suspend`, `reason=idle`, and complete decision observations. Failure to
write that event is logged but does not reissue the accepted command in the
same cycle.

Snowflake connector error `90064` is treated as an unknown idempotent outcome,
not proof that the warehouse was already suspended. No audit event is written.
The healthy session remains open and the tenant loop backs off. The next
snapshot independently decides whether the warehouse is transitional,
suspended, or still eligible.

A timeout or connection failure is also ambiguous, but the safe response is to
hard-close the session, reconnect, write no audit event, and use exponential
backoff. Other command errors use the same failure/backoff path. A restart has
no durable attempt state; if a later snapshot is still eligible, an idempotent
duplicate request is allowed.

## Bounded attempt correlation

Every observed unique warehouse gets an ephemeral attempt UUID. Structured
snapshot, authorization, request, outcome, and metric records use it for
correlation. In those records, `running` and `queued` are statement counts;
`quiescing` is a compute-resource percentage.

After `90064`, the tenant loop keeps only the warehouse name and attempt ID in
memory. The next matching snapshot emits that prior ID and removes it even if
the enrollment no longer exists. If the name disappears, the worker emits
`unknown_attempt_retired` and removes it. Restart may discard the map. It is
observability state only and never changes eligibility.

Duplicate same-name rows in a single `SHOW WAREHOUSES` response are ambiguous.
The worker logs the safe name and row count, consumes any prior unknown entry,
and performs no authorization, stale deletion, or suspension for that name.
Other unique names in the response continue normally.

## Audit and operational metrics

Supabase stores only settings, identity/version-bound enrollments, and accepted
direct suspend events. Members can read their organization's records; only the
service role authorizes, conditionally deletes stale enrollments, and appends
events.

There is no metrics exporter. Operators aggregate exact structured
`event="metric"` logs:

| Metric name | Labels |
|---|---|
| `auto_savings_suspend_attempt_total` | `attempt_id`, `outcome` |
| `auto_savings_suspend_error_total` | `attempt_id`, `errno`, `sqlstate` |
| `auto_savings_authorization_total` | `attempt_id`, `result` |

Outcome logs always include `connector_error_type`, `connector_errno`,
`connector_sqlstate`, and `connector_message`; fields are null when no connector
error exists. These records, plus the next correlated snapshot after an unknown
result, are the source of operational counts and incident evidence.

## Code map

| Concern | File |
|---|---|
| Snapshot parsing and uptime | `apps/auto-savings/src/auto_savings/warehouse_snapshot.py` |
| Eligibility truth table | `apps/auto-savings/src/auto_savings/decision.py` |
| Correlation, authorization, command, and audit | `apps/auto-savings/src/auto_savings/engine.py` |
| Warm session, direct command, and connector outcomes | `apps/auto-savings/src/auto_savings/snowflake_session.py` |
| Per-tenant backoff and supervisor | `apps/auto-savings/src/auto_savings/tenant_loop.py` |
| Durable state contract | `apps/auto-savings/src/auto_savings/store.py`, `supabase/migrations/202607120001_automated_savings.sql` |
| API enrollment and live metadata | `apps/api/app/routes/automated_savings.py`, `apps/api/app/services/warehouse_directory.py` |
| Web controls and display | `apps/web/src/components/automated-savings/` |
