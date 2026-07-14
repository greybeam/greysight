# Automated Savings Operations Guide

Automated Savings directly requests suspension of an enrolled idle Snowflake
warehouse after at least 62 seconds of its current uptime. The worker does not
modify the customer's `AUTO_SUSPEND` value or cluster configuration. The API
and web client expose live state, quiescing percentage, cluster counts, and
`AUTO_SUSPEND` as read-only metadata. The warehouse table currently renders
name, read-only `AUTO_SUSPEND`, derived status, and the enabled switch.

For the decision model and component map, see
[`automated-savings-how-it-works.md`](./automated-savings-how-it-works.md).

## Snowflake access

The Snowflake role used by Greysight can be provisioned with the account-level
`MANAGE WAREHOUSES` privilege:

```sql
GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE <GREYSIGHT_ROLE>;
```

For narrower provisioning, grant `OPERATE` on each managed warehouse together
with the required `USAGE` context. A development spike proceeded after the
test role received a combined `MODIFY, OPERATE` grant, but that observation does
not establish `MODIFY` as a universal direct-suspend requirement.

The API and UI can check this grant on demand. Agreement, enrollment, tenant
discovery, and final authorization do not read `grant_present`, so the stored
check result is informational rather than an application-side execution gate.
The worker is the only component that issues a warehouse mutation, and that
mutation is limited to:

```sql
ALTER WAREHOUSE "<escaped-name>" SUSPEND
```

Do not run two worker deployments that own the same shard. The authorization
RPC cannot hold a database lock across the external Snowflake command, so
single-writer ownership is an operational requirement.

## Cloud-services usage

Each tenant poll executes `SHOW WAREHOUSES`. This metadata command does not
resume a warehouse or consume warehouse compute, but it contributes to
Snowflake cloud-services usage. Monitor that usage in the target account and
raise `AUTO_SAVINGS_POLL_INTERVAL_SECONDS` if the polling overhead becomes
material. At the default cadence, each active tenant is polled about every
three seconds, with jitter after normal cycles.

## Worker configuration

The worker reads configuration at startup. Changes take effect after a worker
restart.

Required credentials:

- `SUPABASE_URL`: Supabase project URL.
- `SUPABASE_SERVICE_ROLE_KEY`: service-role credential used for settings,
  enrollment, authorization, tenant discovery, direct-event writes, and
  resolving each organization's Snowflake connection.

Snowflake connection material is still required. The worker does not take one
shared Snowflake identity from these two process variables. When it starts a
tenant loop, it loads that organization's active account, user, role,
warehouse, optional database/schema, and private-key material from the
organization connection store; the private key is retrieved through the
service-role secret RPC. A missing, inactive, malformed, or duplicate
organization connection fails closed for that tenant. The listed Supabase
variables are how the worker reaches this per-tenant Snowflake configuration,
not a substitute for Snowflake authentication.

Current worker knobs:

- `AUTO_SAVINGS_POLL_INTERVAL_SECONDS` (default `3`): normal per-tenant poll
  cadence. Must be finite and greater than zero.
- `AUTO_SAVINGS_POLL_TIMEOUT_SECONDS` (default `20`): watchdog for a complete
  tenant tick. Must be finite and greater than zero.
- `AUTO_SAVINGS_SOCKET_TIMEOUT_SECONDS` (default `15`): Snowflake login,
  network, and socket timeout. It must be lower than the poll timeout.
- `AUTO_SAVINGS_STORE_TIMEOUT_SECONDS` (default `5`): timeout for each
  Supabase store request in a tenant tick.
- `AUTO_SAVINGS_UPTIME_FLOOR_SECONDS` (default `62`): minimum warehouse uptime
  before suspension is eligible. Production policy is 62 seconds; this is the
  only time gate.
- `AUTO_SAVINGS_TENANT_REFRESH_SECONDS` (default `30`): supervisor interval
  for adding, removing, and revalidating tenant loops.
- `AUTO_SAVINGS_NUM_REPLICAS` (default `1`): total deterministic tenant
  shards.
- `AUTO_SAVINGS_REPLICA_INDEX` (default `0`): this process's zero-based shard
  index. It must be less than `AUTO_SAVINGS_NUM_REPLICAS`.
- `AUTO_SAVINGS_MAX_WORKERS` (default `64`): thread-pool bound for blocking
  Snowflake and Supabase work.
- `GREYSIGHT_QUERY_TIMEOUT_SECONDS` (default `120`): shared Snowflake
  connection setting passed through the organization connection resolver.

Every interval and timeout is required to be finite and positive. Startup also
rejects invalid replica ranges and a socket timeout greater than or equal to
the poll timeout.

## Eligibility and authorization

The worker acts on the first `SHOW WAREHOUSES` row that proves all of the
following:

- the enrollment and global switch are active;
- warehouse type is `STANDARD` and state is `STARTED`;
- `running` and `queued` are present, valid nonnegative statement counts and
  both equal zero;
- `quiescing` equals zero after parsing either a valid nonnegative integer or
  Snowflake's observed idle empty-string encoding (`''`);
- `AUTO_RESUME` is enabled;
- `resumed_on` is timezone-aware and warehouse uptime is at least 62 seconds;
- live `created_on` is timezone-aware and exactly matches the identity captured
  at enrollment.

Missing, naive, malformed, negative, fractional, or non-finite decision fields
fail closed. There is no confirmed-idle window or second eligible snapshot.
The 62-second uptime floor is the only time gate.

Only the specifically observed empty string is normalized for `quiescing`.
Missing (`None`/absent) or any other malformed encoding continues to fail
closed. During a real compute drain, the spike observed `SUSPENDING` with
`quiescing='100'`. `SYSTEM$WAIT` did not reproduce that transition, and
`SELECT 1` did not force a suspended warehouse to resume; operational smoke
tests must use a real compute query.

Immediately before the Snowflake command, the worker calls the service-role
authorization RPC with the warehouse identity and the enrollment's `updated_at`
version. Authorization succeeds only if the latest global switch, enrollment
switch, identity, and version still match. A disable committed before this
check prevents the command. A disable after authorization does not cancel the
already authorized in-flight command; it prevents later authorizations.

Cluster counts never gate eligibility. They are nullable audit/display data.
Direct suspension is warehouse-level and can apply to a multi-cluster
`STANDARD` warehouse, but warehouse-level `resumed_on` does not prove savings
for each individual cluster. Greysight never changes minimum, maximum, or
started cluster counts.

## Outcomes and backoff

- **Accepted:** the worker records a best-effort `action=suspend`,
  `reason=idle` audit event. Audit failure does not cause a same-cycle retry.
- **Snowflake `90064`:** the command outcome is unknown and idempotent, not a
  claimed success. The worker preserves sanitized connector type, errno,
  SQLSTATE, and message in structured logs, writes no audit event, keeps the
  healthy session, and uses exponential backoff. The next snapshot decides
  whether another request is eligible.
- **Timeout or connection failure:** the outcome is ambiguous. The worker
  writes no audit event, hard-closes the session, reconnects, and backs off.
- **Other command error:** the worker writes no audit event and follows the
  normal failure/backoff path.

After restart there is no recovery state to replay. A transitional,
`SUSPENDED`, or busy snapshot fails closed, as does a snapshot with nonzero
quiescing percentage. A new snapshot that is still fully eligible may safely
issue another idempotent suspend request.

## Logs and counters

The worker has no metrics exporter. Build counters from structured
`event="metric"` records in the deployment log backend. Preserve these exact
metric names and labels when writing aggregation queries:

- `auto_savings_suspend_attempt_total`: `attempt_id`, `outcome`;
- `auto_savings_suspend_error_total`: `attempt_id`, `errno`, `sqlstate`;
- `auto_savings_authorization_total`: `attempt_id`, `result`.

Snapshot, request, and outcome records share an ephemeral `attempt_id` and
include organization, warehouse, statement-count activity, quiescing
percentage, cluster observations, resume time, and uptime. Interpret `running`
and `queued` as statement counts; interpret `quiescing` as a compute-resource
percentage, not a statement count. Accepted, unknown, and error outcomes expose
the same connector fields, explicitly null when no connector error exists.

For a `90064` result, the tenant loop keeps only `warehouse name → attempt_id`
in memory so the next observation can be correlated. A matching snapshot logs
the previous ID and consumes it, even if enrollment disappeared. A missing
warehouse emits `unknown_attempt_retired` and consumes it. The map is bounded
by this consume/retire behavior and is intentionally lost on restart; it never
changes a decision.

If one `SHOW WAREHOUSES` response contains duplicate rows for the same name,
the worker emits `snapshot_ambiguous`, consumes any prior unknown-attempt entry,
and fails closed for that name. Other uniquely named warehouses in the same
snapshot can still proceed.

## Residual warehouse-name race

Enrollment stores the warehouse's timezone-aware `created_on`. A missing or
mismatched identity prevents suspension, and a mismatch conditionally removes
the stale enrollment. Snowflake's suspend command is name-based and has no
identity-conditional form, so a warehouse dropped and recreated after the
final observation but before the command remains an irreducible TOCTOU window.
Keep the observation-to-command path short and treat warehouse drop/recreate as
an administrative operation that requires reenrollment.

## Runbook

### Grant is missing

1. Grant account-level `MANAGE WAREHOUSES`, or per-warehouse `OPERATE` with the
   required `USAGE` context, to the configured Greysight role.
2. Verify with `SHOW GRANTS TO ROLE <GREYSIGHT_ROLE>`.
3. Optionally re-run the on-demand access check in the Automated Savings UI to
   refresh the displayed grant status.

A missing or revoked grant does not pause automation in application state.
When an enabled warehouse reaches the command, Snowflake rejects the suspend;
the worker records the command error, closes the failed session, and backs off.
It continues retrying eligible observations with backoff until the privilege is
restored or an operator disables automation.

### No suspend is requested

Check the latest structured snapshot before changing cadence. A warehouse is
expected to skip when any required field is invalid, it is not `STANDARD` and
`STARTED`, either statement count or the quiescing percentage is nonzero,
`AUTO_RESUME` is disabled, uptime is below 62 seconds, identity mismatches, or
final authorization is rejected. Cluster counts and the displayed
`AUTO_SUSPEND` value are not eligibility gates.

### Repeated `90064` or command failures

Correlate `suspend_outcome`, `event="metric"`, and the next `snapshot` by
`attempt_id`. For `90064`, confirm the session stays healthy and the tenant
uses retry backoff. For timeouts or connection failures, confirm a hard close,
reconnect, and backoff. Do not infer a successful mutation without an accepted
outcome.

### Duplicate warehouse names in a snapshot

Look for `event="snapshot_ambiguous"` with `warehouse_name` and `row_count`.
The worker deliberately performs no authorization, stale-enrollment deletion,
or suspend for that name. Investigate the Snowflake metadata response before
reenabling or reenrolling it.

### Deploying or scaling workers

Run one process with `AUTO_SAVINGS_NUM_REPLICAS=1` and
`AUTO_SAVINGS_REPLICA_INDEX=0` unless sharding is intentional. For `N`
replicas, deploy exactly one process for each index `0..N-1` with the same
replica count. Tenant ownership is a stable SHA-256 partition. Never overlap
old and new deployments that claim the same shard.

### Kill switch

Disabling the global switch removes the organization from tenant discovery and
prevents new authorizations. Allow any command that was already authorized to
finish; the worker has no cross-system cancellation mechanism. No cleanup or
restoration pass is required because direct suspension leaves customer
configuration unchanged.
