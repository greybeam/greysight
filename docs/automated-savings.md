# Automated Savings operations guide

Automated Savings is a separately deployed worker. It polls enrolled warehouses and directly requests suspension when the current snapshot is eligible. The worker is the only component that mutates Snowflake, and it only executes:

```sql
ALTER WAREHOUSE "<warehouse-name>" SUSPEND
```

For the processing model, eligibility rules, and code map, see [`automated-savings-how-it-works.md`](./automated-savings-how-it-works.md).

## Snowflake access

The configured Greysight role needs permission to suspend the warehouses it manages. The standard account-level grant is:

```sql
GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE <GREYSIGHT_ROLE>;
```

Per-warehouse `OPERATE` with the required `USAGE` context is also supported. Use `SHOW GRANTS TO ROLE <GREYSIGHT_ROLE>` to confirm the role's access.

The API and UI can perform an on-demand grant check. That stored result is informational: the worker does not use it as an application-side execution gate. A revoked privilege is discovered when Snowflake rejects a command; the worker then follows its normal error and backoff path.

## Required configuration

The worker reads configuration at startup. Restart it after changing an environment variable.

| Variable                              | Default | Meaning                                                                                                                      |
| ------------------------------------- | ------: | ---------------------------------------------------------------------------------------------------------------------------- |
| `SUPABASE_URL`                        |       — | Supabase project URL.                                                                                                        |
| `SUPABASE_SERVICE_ROLE_KEY`           |       — | Service-role credential for settings, enrollment, authorization, events, tenant discovery, and tenant connection resolution. |
| `AUTO_SAVINGS_POLL_INTERVAL_SECONDS`  |     `3` | Normal per-tenant polling interval.                                                                                          |
| `AUTO_SAVINGS_POLL_TIMEOUT_SECONDS`   |    `20` | Maximum duration of a tenant cycle.                                                                                          |
| `AUTO_SAVINGS_SOCKET_TIMEOUT_SECONDS` |    `15` | Snowflake login, network, and socket timeout. Must be less than the poll timeout.                                            |
| `AUTO_SAVINGS_STORE_TIMEOUT_SECONDS`  |     `5` | Timeout for each Supabase store request.                                                                                     |
| `AUTO_SAVINGS_UPTIME_FLOOR_SECONDS`   |    `62` | Minimum uptime before a warehouse can be suspended.                                                                          |
| `AUTO_SAVINGS_TENANT_REFRESH_SECONDS` |    `30` | Interval for discovering and revalidating tenant loops.                                                                      |
| `AUTO_SAVINGS_NUM_REPLICAS`           |     `1` | Number of deterministic tenant shards.                                                                                       |
| `AUTO_SAVINGS_REPLICA_INDEX`          |     `0` | Zero-based shard owned by this worker process.                                                                               |
| `AUTO_SAVINGS_MAX_WORKERS`            |    `64` | Thread-pool limit for blocking Snowflake and Supabase work.                                                                  |
| `GREYSIGHT_QUERY_TIMEOUT_SECONDS`     |   `120` | Shared Snowflake query timeout supplied to the organization connection resolver.                                             |

All intervals and timeouts must be finite and greater than zero. The replica index must be in the range `0..AUTO_SAVINGS_NUM_REPLICAS - 1`.

Tenant-specific Snowflake credentials are loaded from the organization connection store. The worker does not use one shared Snowflake account or private key for every organization. A missing, inactive, malformed, or ambiguous connection fails closed for that tenant.

## Deployment and scaling

Run one worker with these settings unless sharding is intentional:

```text
AUTO_SAVINGS_NUM_REPLICAS=1
AUTO_SAVINGS_REPLICA_INDEX=0
```

For `N` replicas, deploy exactly one process for each index from `0` through `N - 1`, using the same replica count on every process. Tenant assignment is a stable SHA-256 partition. Do not overlap old and new deployments that own the same shard.

Each active tenant cycle runs `SHOW WAREHOUSES`. This metadata statement does not resume a warehouse or use warehouse compute, but it contributes to Snowflake cloud-services usage. Monitor that usage and increase `AUTO_SAVINGS_POLL_INTERVAL_SECONDS` if the polling cost is material.

## Switch behavior

An organization must agree to the feature before it can enroll warehouses. The global switch controls whether the organization is discovered for worker processing. Each enrollment has a separate warehouse switch.

The worker checks the switches while loading state and again in the final authorization RPC before a command. Disabling a switch prevents later authorizations. It cannot cancel a command that was already authorized and sent to Snowflake.

## Logs and metrics

The worker has no metrics server or exporter. Use structured logs from the deployment platform.

| Metric                               | Fields                            |
| ------------------------------------ | --------------------------------- |
| `auto_savings_suspend_attempt_total` | `attempt_id`, `outcome`           |
| `auto_savings_suspend_error_total`   | `attempt_id`, `errno`, `sqlstate` |
| `auto_savings_authorization_total`   | `attempt_id`, `result`            |

Use `attempt_id` to relate snapshot, authorization, request, outcome, and metric records. `running` and `queued` are statement counts. `quiescing` is a compute-resource percentage.

## Runbook

### A warehouse is not suspended

Inspect the latest snapshot and authorization log before changing the polling interval. The worker skips a warehouse when it is not `STANDARD` and `STARTED`, has nonzero activity or quiescing, has invalid decision fields, has `AUTO_RESUME` disabled, has not met the 62-second uptime floor, has a different `created_on` timestamp from its enrollment, or fails final authorization.

Cluster counts and `AUTO_SUSPEND` are not eligibility conditions.

### The Snowflake grant is missing

Grant `MANAGE WAREHOUSES` at account level, or the equivalent per-warehouse permissions, to the configured Greysight role. Verify the change with:

```sql
SHOW GRANTS TO ROLE <GREYSIGHT_ROLE>;
```

You can rerun the UI access check to update its display. The worker will retry eligible observations through its normal backoff path until access is restored or automation is disabled.

### `90064`, timeout, or connection failure

For `90064`, verify that the session remains healthy and the tenant enters retry backoff. Do not treat `90064` as evidence that suspension succeeded.

For a timeout or connection failure, verify that the worker closes the session, reconnects, and backs off. Both cases write no accepted suspend event. Use the next snapshot and the same `attempt_id` to determine what the worker observed.

### Duplicate warehouse names in one snapshot

Find the `snapshot_ambiguous` log with `warehouse_name` and `row_count`. The worker does not authorize, delete a stale enrollment, or suspend that name in that cycle. Investigate the Snowflake metadata response before reenabling or reenrolling the warehouse.

### Disable the feature

Disable the global switch to remove the organization from new worker authorizations. No restoration or configuration cleanup is needed because Automated Savings does not change customer warehouse settings.
