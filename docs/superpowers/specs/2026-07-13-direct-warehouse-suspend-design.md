# Automated Savings — Direct Warehouse Suspension Design

**Date:** 2026-07-13  
**Status:** Approved greenfield replacement; spike decision GO  
**Deployment state:** Automated Savings has not been released to production. No customer has run the sentinel worker, and no production restore intents exist.

## Context

The pre-release implementation forces suspension by setting `AUTO_SUSPEND=1`, recording a restore intent, and restoring a managed default. None of it has shipped. Development data and migration history are disposable.

Snowflake documents `ALTER WAREHOUSE … SUSPEND` as allowing executing statements to finish before compute shuts down; it does not abort them. A warehouse can remain `STARTED` while compute is quiescing, then transitions to `SUSPENDED`. Suspension is warehouse-level and supports multi-cluster warehouses.

References:

- [Working with warehouses](https://docs.snowflake.com/en/user-guide/warehouses-tasks)
- [ALTER WAREHOUSE](https://docs.snowflake.com/en/sql-reference/sql/alter-warehouse)
- [Multi-cluster warehouses](https://docs.snowflake.com/en/user-guide/warehouses-multicluster)

## Decision

Automated Savings will be implemented greenfield as a direct-only worker. For an eligible warehouse it issues:

```sql
ALTER WAREHOUSE "<escaped-name>" SUSPEND
```

It never changes customer `AUTO_SUSPEND`, cluster settings, or queries; creates restore state; manages defaults; reconciles drift; or runs a compatibility path.

There is no persisted cooldown. The 62-second `resumed_on` floor already prevents repeated suspension during the first billed minute after every resume. A separate cooldown would duplicate that anti-churn rule. Duplicate commands caused by stale or ambiguous observations are safe at the product level; command errors use retry backoff instead of customer-specific durable state.

Because the sentinel implementation is unreleased, consolidate the automated-savings migrations into one final schema, delete the sentinel follow-ups and active machinery immediately, and reset every development database that applied the old migration chain. Do not ship staged compatibility logic.

## Goals

- Act on the first valid idle snapshot; do not add a second idle timer or consecutive-observation state.
- Preserve the 62-second warehouse uptime floor.
- Support single- and multi-cluster `STANDARD` warehouses without a cluster-count gate.
- Preserve the global switch, audit, tenant isolation, session recovery, sharding, and operational single-writer invariant.
- Normalize Snowflake's observed idle `quiescing=''` encoding to zero and fail
  closed on missing or otherwise malformed decision-critical state, activity,
  quiescing, timestamp, or identity fields.
- Keep live `AUTO_SUSPEND` and cluster counts as read-only information only.

For auto-scaling warehouses, `resumed_on` is warehouse-level and is not a per-cluster savings guarantee.

## Non-goals

- Predicting the next idle gap or adding a confirmed-idle window.
- Eliminating cold-cache or provisioning latency after resume.
- Supporting Snowpark-optimized, Adaptive, or Interactive warehouses.
- Removing the documented account-level `MANAGE WAREHOUSES` provisioning path;
  per-warehouse `OPERATE` with required `USAGE` context is also supported.
- Preserving pre-release sentinel data or migration compatibility.
- Providing instantaneous cancellation of a command already authorized when an admin flips the kill switch.

## Direct-suspend eligibility

An enabled enrollment is eligible when one parsed `SHOW WAREHOUSES` snapshot proves:

- `type == STANDARD`;
- `state == STARTED`;
- `running` and `queued` are present, valid finite integers and equal zero;
- `quiescing` is zero after accepting either a valid finite integer encoding or
  Snowflake's observed idle empty-string encoding;
- `AUTO_RESUME == true`;
- `now - resumed_on >= 62 seconds`, with missing/invalid timestamps failing closed;
- stored and observed `warehouse_created_on` are both present and equal.

Global enablement and enrollment enablement are checked at snapshot load and re-authorized immediately before the command.

Cluster counts do not determine eligibility. Invalid or absent started/min/max counts remain nullable audit data. The customer's `AUTO_SUSPEND` value also does not determine eligibility and may be null or any Snowflake-supported value.

Nonzero quiescing or states such as `SUSPENDING`, `RESUMING`, and `SUSPENDED` fail closed. The worker acts on the first eligible snapshot; the uptime floor is the only time gate.

## Warehouse identity and residual race

Enrollment requires a non-null live `created_on`; failure to find the warehouse or capture its timestamp rejects enrollment. The schema stores `warehouse_created_on NOT NULL`. Every worker snapshot must provide the same identity. Missing identity fails closed; mismatch conditionally deletes the stale enrollment and never mutates that warehouse in the tick.

Snowflake's `ALTER WAREHOUSE` is name-based and offers no identity-conditional form. A drop/recreate between the final identity observation and `ALTER` is therefore irreducible. A second `SHOW` would only narrow, not close, the window and would violate the one-snapshot decision model. This residual administrative race is documented; enrollment identity checks and the very short observation-to-command path minimize it.

## Go/no-go Snowflake spike

The sanitized spike report is
[`2026-07-13-direct-suspend-spike-notes.md`](../plans/2026-07-13-direct-suspend-spike-notes.md).
It used an authorized development account, `DEV_WH`, and three independent
sessions. Never invoke production tenant discovery.

1. Start a long-running query, request `SUSPEND`, and confirm it completes without abort.
2. Submit a query in the `SHOW WAREHOUSES` → `SUSPEND` race window.
3. Submit a query while quiescing is nonzero.
4. Submit a query after full suspension and confirm auto-resume behavior.
5. Record query outcomes, state/quiescing transitions, and elapsed times.
6. Reproduce `90064` and record connector type, errno, SQLSTATE, message, and resulting state.

If any running or race-window query errors or is aborted, direct suspension is a no-go and this design must be revisited.

All five measured cases completed without a query abort, cancellation, or query
error attributable to suspension. Real compute exposed
`state='SUSPENDING'`, `quiescing='100'`; `SYSTEM$WAIT` did not reproduce that
transition, and `SELECT 1` did not force auto-resume. The observed idle
`quiescing=''` encoding is normalized to zero, its dedicated regression test
passes, and full verification passes. The spike decision is GO.

The two reissued suspend attempts returned `errno=90064` with Snowflake's
sanitized `Invalid state … cannot be suspended` message. The supplied evidence
did not retain exception class or SQLSTATE, so no values are inferred for them.
This does not change the unknown/idempotent contract below.

## Final authorization and kill-switch semantics

Immediately before `ALTER`, the worker calls a service-role `automated_savings_authorize_suspend` RPC with organization, warehouse, non-null identity, and enrollment `updated_at`. The `SECURITY INVOKER` function uses schema-qualified objects and an empty fixed search path. It returns true only if the current settings and exact enrollment row are still globally enabled, enabled, identity-matched, and version-matched. It writes no state.

The authorization closes changes committed before it executes. An admin can still disable the switch after authorization returns and before Snowflake receives the command. The explicit product contract is: disabling prevents new authorizations; it does not cancel a command already authorized. Adding cancellable in-flight state is out of scope.

The database does not provide a lock that spans an external Snowflake call. Single-writer behavior therefore remains an operational invariant enforced by sharding, per-tenant locking, and never running overlapping worker deployments for the same shard.

## Command outcomes and retry behavior

1. **Accepted:** write a best-effort direct event. Audit failure does not reissue the command in the same cycle. A later stale snapshot may issue an idempotent duplicate; observable quiescing/state normally prevents it.
2. **`90064`:** classify as unknown/idempotent, not “already suspended.” Return a typed suspend result that preserves the sanitized connector type, errno, SQLSTATE, and message for the correlated engine log. Write no mutation event. Return a retry-backoff cycle result without closing the healthy session. The next snapshot decides whether to skip or retry.
3. **Timeout/connection failure:** write no event or recovery state. Use the existing hard-close/reconnect watchdog and exponential backoff. The next snapshot decides.
4. **Other Snowflake error:** write no event; use normal tenant-loop error backoff.

A restart carries no attempt state. Transitional/suspended/quiescing snapshots skip; a still-eligible `STARTED` snapshot may safely reissue the idempotent command.

## State and audit model

Persisted state is limited to settings, enrollment with mandatory identity, and append-only direct events. There is no cooldown timestamp, restore-intent table, managed/stored default, drift state, reapply action, intent cadence, hold timer, cycle pairing, or sentinel-intent cleanup RPC.

The events table accepts only `action='suspend'`, `reason='idle'`. Decision-critical observations are non-null: state, running, queued, quiescing, resumed time, and observation time. Activity/quiescing have nonnegative checks. Cluster counts are nullable. There are no cycle/from/to value fields.

Each attempt gets an ephemeral UUID included in structured request/outcome logs. Unknown attempts are retained only in the tenant loop's in-memory observability map. At cycle start, a prior unknown name missing from the new `SHOW WAREHOUSES` result emits a safe `unknown_attempt_retired` record correlated by the prior attempt ID and is removed. Before enrollment iteration, every matching snapshot logs the prior unknown attempt ID and removes it, including when the enrollment disappeared. Duplicate same-name snapshots are ambiguous: log only safe name/count correlation, remove any prior unknown entry, and fail closed for that name regardless of row order while other unique names proceed. This state never affects decisions and may be lost on restart.

## Final schema, RLS, and migration strategy

Rewrite `supabase/migrations/202607120001_automated_savings.sql` to contain only:

- settings;
- warehouses with `warehouse_created_on NOT NULL`, enabled, and `updated_at`;
- the `set_updated_at` trigger;
- direct-only events;
- explicit member-select policies;
- explicit admin insert/update policies on settings/enrollments and no authenticated delete policy;
- no authenticated event mutation policy;
- service-role `automated_savings_upsert_enrollment`, `automated_savings_authorize_suspend`, `automated_savings_delete_stale_enrollment`, and `automated_savings_worker_tenants` functions.

All RPCs use `SECURITY INVOKER`, `SET search_path = ''`, schema-qualified relations, exact signature revokes from `PUBLIC`, and grants only to `service_role`.

Delete these unreleased migrations after folding relevant guards into the base:

- `202607130001_automated_savings_upsert_enrollment_fn.sql`
- `202607130002_automated_savings_baseline_resumed_on.sql`
- `20260713223505_automated_savings_sentinel_confirmed.sql`
- `20260714002106_automated_savings_intent_safety.sql`
- `20260714005623_automated_savings_atomic_guards.sql`
- `20260714010713_automated_savings_activity_unknown_reason.sql`

Inventory every local/shared development Supabase environment that applied the old chain. Reset each and verify migration history plus exact tables, columns, triggers, policies, grants, and functions. If any environment cannot be reset, stop and create one immediate development cleanup migration before continuing; never rely on editing an already-applied file to transform it.

## Worker flow

```text
SHOW WAREHOUSES
  → parse fail-closed snapshots, including quiescing and identity
  → load settings/enrollments
  → delete stale identity or evaluate direct eligibility
  → authorize against current settings/enrollment version
  → ALTER WAREHOUSE … SUSPEND
  → best-effort direct event
```

`run_cycle` returns `NORMAL` or `RETRY_BACKOFF`. `90064` returns retry backoff without closing the session. Successful cycles use normal jittered cadence; connection/command exceptions retain the watchdog and exponential backoff.

## API and UI contract

Enrollment captures identity only and rejects missing warehouse/identity. The API removes managed-default and reconcile endpoints and all default/drift/cooldown fields.

The warehouse response contains name, size, state, type, supported, nullable cluster counts, nonnegative/nullable live quiescing for display, `auto_resume_ok`, read-only live `auto_suspend`, enabled, and status.

Final status values are:

- `unsupported`: non-`STANDARD` type;
- `transitioning`: `SUSPENDING`, `RESUMING`, or valid quiescing greater than zero;
- `idle`: every other supported display state.

Decision eligibility remains stricter than display status. The UI keeps global/per-warehouse switches, shows live `AUTO_SUSPEND`, and removes managed-default editing, drift, reconcile, and cooldown presentation.

## Observability

The worker has no metrics exporter or metrics-server dependency. Its operational counter source is structured `event="metric"` log records aggregated by the deployment log backend; do not add fake in-process counters. The exact event schemas are:

- `metric_name="auto_savings_suspend_attempt_total"`, `attempt_id`, and `outcome`;
- `metric_name="auto_savings_suspend_error_total"`, `attempt_id`, `errno`, and `sqlstate`;
- `metric_name="auto_savings_authorization_total"`, `attempt_id`, and `result`.

Correlated snapshot/request/outcome records carry attempt ID, org, warehouse, state, running, queued, quiescing, cluster counts, `resumed_on`, uptime, sanitized connector error details, and the next observed state for unknown outcomes. Accepted, unknown, and error outcome records use one homogeneous connector schema: `connector_error_type`, `connector_errno`, `connector_sqlstate`, and `connector_message` are always present and explicitly null when absent. Tests assert the exact custom-field schemas and exact metric names/label schemas rather than formatted message text. Active operational documentation will define log aggregation in Task 10.

## Acceptance criteria

- The isolated spike proves the measured availability cases, records real
  `90064` behavior without assigning it success semantics, and records GO after
  the idle-empty-string parser regression and full verification pass.
- One direct path exists; no sentinel, compatibility, cooldown, or mode flag remains.
- Eligibility requires zero valid quiescing and mandatory matching identity.
- Multi-cluster and arbitrary customer `AUTO_SUSPEND` values do not block eligibility.
- Final authorization rechecks settings/enrollment; disabling prevents new authorizations but not an already authorized command.
- `90064` backs off without closing a healthy session; ambiguous connection failures reconnect/back off.
- `90064` connector metadata survives the session-to-engine result boundary and appears on the correlated outcome/error metric events.
- Matching snapshots consume unknown-attempt observability entries even when enrollment has disappeared; those entries never influence eligibility.
- Missing snapshots explicitly retire prior unknown attempts, bounding the in-memory map under warehouse churn.
- Duplicate same-name snapshots fail closed without authorization, stale deletion, or suspension regardless of row order; unique names in the cycle still proceed.
- All command outcomes share one exact connector-field schema, using explicit nulls when no connector error exists.
- Exact named structured metric events are the operational counter source; no unconfigured exporter or fake counter is introduced.
- Restart may idempotently reissue only when a new snapshot is still eligible.
- Direct audit columns and constraints enforce complete decision observations.
- RLS permits member reads and admin insert/update but no authenticated enrollment delete or event mutation.
- Every development database is reset or explicitly cleaned and introspected.
- API/client status values and response fields match exactly.
- All sentinel-era migrations, code, config, tests, endpoints, and active guidance are deleted.

## Required implementation-plan coverage

The plan must include failing-first tests for quiescing parsing/eligibility, mandatory identity, multi-cluster eligibility, final authorization and disable-before/after semantics, `90064` healthy-session backoff, ambiguous outcomes, restart idempotence, correlated logs, direct event constraints, RLS/grants/triggers, API status fixtures, and UI removal. Functional database verification after every environment reset is authoritative; substring migration tests are supplemental.
