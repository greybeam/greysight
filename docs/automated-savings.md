# Automated Savings Operations Guide

## Overview

The Automated Savings feature suspends idle enrolled Snowflake warehouses once they pass the 60-second billing floor, reclaiming the idle tail between the prepaid minute and a customer's configured `AUTO_SUSPEND` value. The system is opt-in and runs as a standalone Python worker that polls warehouse metadata via Snowflake, reconciles configuration drift, and applies force-suspend directives via Supabase.

## Cloud Services Cost Considerations

### SHOW WAREHOUSES Impact on Cloud Services

Each warehouse poll executes `SHOW WAREHOUSES` to capture idle state and trigger metrics. This metadata command:
- Runs at approximately **3–5 second intervals** (configurable)
- **Does not resume a warehouse** or consume compute credits
- **Accrues against the tenant's daily cloud-services allowance** (measured in Snowflake's Account Usage)
- Is billed only when the tenant's daily cloud-services usage exceeds **10% of their daily compute allowance**

For most accounts, this cost is negligible. However, active monitoring should track cloud-services consumption to confirm it remains below the 10% threshold. Adjust `AUTO_SAVINGS_POLL_INTERVAL_SECONDS` upward (e.g., 10 seconds instead of 3) if cloud-services overhead becomes material.

**Environment variable:** `AUTO_SAVINGS_POLL_INTERVAL_SECONDS` (default: 3 seconds)

## Opt-in Requirement

The feature requires an explicit **`MANAGE WAREHOUSES` GRANT** on the Snowflake role used by Greysight's worker service. This grant permits the worker to alter warehouse configuration, including setting `AUTO_SUSPEND=1` to trigger a suspend.

```sql
GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE <GREYSIGHT_ROLE>;
```

If this grant is missing, the worker will detect it during the grant-check phase (see [Runbook](#runbook) below) and the feature will pause for that organization until the grant is confirmed.

## Worker Configuration

The worker reads environment variables at startup to configure behavior. All values support live updates via environment management in Railway (or your deployment platform) and take effect on the next worker restart.

### Polling and Timeouts

- **`AUTO_SAVINGS_POLL_INTERVAL_SECONDS`** (default: 3)
  - How often (in seconds) the worker polls `SHOW WAREHOUSES` per tenant.
  - Affects cloud-services cost; increase to reduce overhead if needed.

- **`AUTO_SAVINGS_POLL_TIMEOUT_SECONDS`** (default: 20)
  - Watchdog timeout for each tenant's poll cycle.
  - If a cycle takes longer than this, the worker logs a warning and moves to the next tenant.

- **`AUTO_SAVINGS_SOCKET_TIMEOUT_SECONDS`** (default: 15)
  - Snowflake connector network socket read timeout.
  - Must be **strictly less than** `POLL_TIMEOUT_SECONDS`; this OS-level timeout is what frees a blocked thread if Snowflake becomes unresponsive.
  - The watchdog timer complements this; together they prevent thread exhaustion.

### Suspend and Restore Behavior

- **`AUTO_SAVINGS_COOLDOWN_SECONDS`** (default: 60)
  - Anti-thrash guard after a warehouse restore.
  - After a warehouse is restored from `AUTO_SUSPEND=1`, it cannot be force-suspended again until this cooldown period (in seconds) has elapsed.
  - Matches Snowflake's one-minute billing floor to avoid oscillation.

- **`AUTO_SAVINGS_UPTIME_FLOOR_SECONDS`** (default: 62)
  - Minimum warehouse uptime (in seconds) before the worker will consider suspending it.
  - Set to 62 to provide a 2-second safety margin above Snowflake's 60-second billing floor.
  - Do not lower this value without understanding Snowflake's billing semantics.

- **`AUTO_SAVINGS_MAX_INTENT_HOLD_TICKS`** (default: 8, recommended: 8)
  - **CRITICAL:** This must be set based on measured suspend latency in your environment.
  - The worker uses a conditional restore strategy: when a warehouse remains idle after `AUTO_SUSPEND=1` is applied, it "holds" the restore intent for up to `MAX_INTENT_HOLD_TICKS` polling cycles before force-restoring to ensure Snowflake's suspend operation completes.
  - **Recommended value: 8 ticks (24s at the default 3s poll interval)**, per the Task 0 spike (`docs/superpowers/plans/2026-07-12-automated-savings-spike-notes.md`). Rationale: measured worker-regime suspend latency (uptime already past the 62s floor) is sub-second (~0.26s median), so 8 ticks satisfies the ≥2–3× rule with huge margin; it also dominates the observed ~17–20s resume-floor latency (a freshly-resumed warehouse won't suspend for ~15–20s even if idle) as defense-in-depth, while still bounding anti-stranding recovery to ≤24s.
  - To find the right value: measure how many seconds it takes for a warehouse to move from `STARTED` to `SUSPENDED` after `AUTO_SUSPEND=1` is applied, in the worker's actual precondition regime (uptime already ≥ `UPTIME_FLOOR_SECONDS`). Divide by your `POLL_INTERVAL_SECONDS` and round up; then use at least 2–3× that to account for variance. **Re-measure on the actual deploy target account, especially Enterprise/multi-cluster editions** — cluster columns and resume-floor behavior may differ from the Standard-edition account used for the Task 0 spike.

- **Operational note — `SHOW WAREHOUSES` cluster columns on Standard edition:**
  `started_clusters`, `min_cluster_count`, and `max_cluster_count` are Enterprise+-only
  columns; on **Standard edition** `SHOW WAREHOUSES` omits them entirely (confirmed by the
  Task 0 spike, not account-specific noise). The parser (`warehouse_snapshot.py`) treats an
  absent `started_clusters` as equal to the resolved `min_cluster_count` (default 1) — i.e.
  single-cluster — since a Standard-edition warehouse is always single-cluster and safe to
  suspend. When the columns ARE present (Enterprise+), the parser and API layer
  (`warehouse_directory.py`) use the real values so multi-cluster warehouses stay
  protected. Re-verify this behavior on the actual deploy target, especially
  Enterprise/multi-cluster accounts.

- **`AUTO_SAVINGS_ORPHAN_GRACE_SECONDS`** (default: 120)
  - Grace period (in seconds) before the worker cleans up stale restore intents for warehouses that have been dropped from Snowflake.
  - If a warehouse is deleted and a restore intent row remains, it survives for this period before the worker removes it.
  - Prevents accidental cleanup of intents that may be momentarily missing from a snapshot due to eventual consistency.

### Multi-Replica Sharding (Dormant in v1)

- **`AUTO_SAVINGS_NUM_REPLICAS`** (default: 1)
  - Total number of worker replicas in your deployment.
  - In v1, this is always 1 (single worker).

- **`AUTO_SAVINGS_REPLICA_INDEX`** (default: 0)
  - Zero-indexed replica ID (0 through `NUM_REPLICAS - 1`).
  - In v1, this is always 0.
  - Sharding logic `hash(tenant_id) % NUM_REPLICAS == REPLICA_INDEX` is implemented but dormant. When multi-replica scaling is added, set these to enable horizontal partitioning of the tenant load.

### Tenant Enumeration

- **`AUTO_SAVINGS_TENANT_REFRESH_SECONDS`** (default: 30)
  - How often (in seconds) the worker re-enumerates the list of active tenants to poll.
  - Allows the worker to pick up organizations that opt in (or disable automation) without a restart.

### Shared Snowflake Configuration

These settings are inherited from the broader Greysight deployment:

- **`AUTH_REQUIRED`** (default: true)
  - Whether to authenticate against Supabase when reading configuration.

- **`GREYSIGHT_QUERY_TIMEOUT_SECONDS`** (default: 120)
  - Timeout for user-facing Snowflake queries. The worker does not execute user queries, but this value is passed to the Snowflake config resolver.

### Supabase Credentials

- **`SUPABASE_URL`**
  - The Supabase project URL (no default; required).

- **`SUPABASE_SERVICE_ROLE_KEY`**
  - Supabase service-role key with permissions to read/write automation state (no default; required).
  - This key must have access to `automated_savings_settings`, `automated_savings_warehouses`, and `automated_savings_restore_intents` tables.

## Runbook

### Scenario: Grant Missing

**Symptoms:**
- The worker logs grant-check failures for a tenant.
- The opt-in UI shows `grantPresent: false` and prompts the user to grant `MANAGE WAREHOUSES`.
- Feature appears paused (no suspend operations occur).

**Resolution:**
1. The tenant (or Snowflake admin) must run:
   ```sql
   GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE <GREYSIGHT_ROLE>;
   ```
2. Verify the grant is present:
   ```sql
   SHOW GRANTS TO ROLE <GREYSIGHT_ROLE>;
   ```
3. Reload the opt-in UI; the system will re-check on the next worker cycle and confirm the grant.
4. Once confirmed, feature automation resumes.

### Scenario: Drift Detected

**Symptoms:**
- The dashboard shows a warehouse with `driftState: 'drifted'`.
- The warehouse's current `AUTO_SUSPEND` value in Snowflake differs from the stored `managed_auto_suspend` value.
- The worker logs a drift flag.

**Resolution:**
1. The user can click **Reconcile** in the dashboard to align the warehouse's live `AUTO_SUSPEND` with the stored `managed_auto_suspend`.
2. Behind the scenes, reconcile:
   - Reads the current live value from Snowflake.
   - Compares it to `managed_auto_suspend`.
   - If different, applies a restore-intent row to correct it.
   - The worker picks up the intent on the next cycle and applies the correct `AUTO_SUSPEND` value.
3. After reconcile completes and the worker processes it, the warehouse returns to `driftState: 'ok'`.

**Common causes of drift:**
- A user manually altered `AUTO_SUSPEND` in Snowflake outside of Greysight.
- The warehouse type changed (e.g., STANDARD → Snowpark-optimized), causing auto-pause and a state reset.
- A warehouse was dropped and recreated, and Greysight's enrollment record was not cleaned up.

### Scenario: Crash Recovery

**Symptoms:**
- The worker process was killed or crashed while a warehouse had a pending restore-intent.
- On restart, the warehouse may still have `AUTO_SUSPEND=1` and be paused.

**Recovery:**
1. The worker's reconciliation step runs on every cycle **regardless of whether the feature is enabled**.
2. On startup, the worker queries `automated_savings_restore_intents` in Supabase.
3. For every outstanding intent found, the worker:
   - Checks the warehouse's current state in Snowflake via `SHOW WAREHOUSES`.
   - If the warehouse is `SUSPENDED`, applies a restore (sets `AUTO_SUSPEND` back to `managed_auto_suspend`).
   - If the warehouse is `STARTED`, checks whether a restore is in cooldown or already completed.
   - Once the restore is applied, deletes the intent row and records the cooldown timestamp.
4. The warehouse is thus restored and ready for use on the next startup.

**Preventing stranding:**
- Restore-intents are durable and logged in Supabase, so a crash does not lose the signal.
- The orphan-grace period (default 120 seconds) ensures that intents for fully-dropped warehouses are eventually cleaned up.
- If a warehouse is deleted from Snowflake while the intent exists, the worker waits 120 seconds, then removes both the stale intent and the enrollment record.

### Single-Worker v1 with Dormant Sharding

In the current release:
- Only **one worker replica** should be deployed (set `AUTO_SAVINGS_NUM_REPLICAS=1`, `AUTO_SAVINGS_REPLICA_INDEX=0`).
- The worker enumerates all eligible tenants and polls them sequentially using `asyncio`.
- Sharding fields are present in the code but inactive; they are set up for future multi-replica scaling.

To scale horizontally in a future release:
1. Increase `NUM_REPLICAS` to the desired count (e.g., 3).
2. Deploy multiple worker instances with different `REPLICA_INDEX` values (0, 1, 2).
3. Each instance will compute `hash(tenant_id) % 3 == REPLICA_INDEX` and only poll tenants it owns.
4. The `tenant_refresh_seconds` interval ensures even if an instance starts after another, tenant enumeration will self-correct.

## Summary

- **Cost note:** Monitor cloud-services consumption; adjust polling cadence if needed.
- **Grant:** Always require and verify `MANAGE WAREHOUSES` before enrollment.
- **Config:** Set `AUTO_SAVINGS_MAX_INTENT_HOLD_TICKS` from real suspend-latency measurements.
- **Grant missing:** Pause state; resolve by granting `MANAGE WAREHOUSES`.
- **Drift:** Reconcile via the dashboard to re-apply `managed_auto_suspend`.
- **Crash recovery:** Outstanding intents are restored on worker restart via durable state.
- **Sharding:** Dormant in v1; future releases can scale by increasing replicas and setting indices.
