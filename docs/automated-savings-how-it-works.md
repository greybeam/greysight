# Automated Savings — How It Works

A conceptual map of the mechanism. For env vars, cost, and the operational
runbook see [`automated-savings.md`](./automated-savings.md).

## The opportunity

Snowflake bills a **60-second minimum every time a warehouse resumes**, then keeps
billing while it sits idle until the customer's `AUTO_SUSPEND` fires. On sporadic
workloads that idle tail (between the prepaid minute and, say, a 300s `AUTO_SUSPEND`)
is pure waste. Automated Savings watches each enrolled warehouse and, once it is
genuinely idle past the 60s floor, forces it to suspend — reclaiming that tail.

## Three surfaces, one writer

| Surface | Role |
|---|---|
| **Worker** (`apps/auto-savings/`, own Railway service) | The control loop. Polls Snowflake, decides, and is the **only** component that issues `ALTER WAREHOUSE`. |
| **API** (`apps/api/`, routes in `automated_savings.py`) | UI-facing reads/writes. Writes only *intent* to Supabase — **never** touches Snowflake. |
| **Web** (`apps/web/`, `/automated-savings`) | Opt-in gate + per-warehouse enrollment table. |

**Single-writer rule:** the worker owns every Snowflake mutation. The API/UI only
record intent in Supabase; the worker applies it on its next tick. This removes all
API-vs-worker races on the same warehouse.

## The idle signal

The worker polls **`SHOW WAREHOUSES`** every ~3s per tenant on a warm, persistent
Snowflake session (`snowflake_session.py`). Why this call:

- It's a **cloud-services metadata** call — sub-second, no compute, and crucially it
  **does not resume a warehouse**. (A `SELECT` would run on the session's warehouse
  and wake it on every poll — the exact opposite of the goal.)
- It returns every warehouse with the columns we need: `state`, `type`, `running`,
  `queued`, `auto_suspend`, `auto_resume`, `resumed_on`, and (Enterprise+) cluster
  counts.
- `ACCOUNT_USAGE` is unusable here — up to ~45 min latent, far too slow for a 60s
  decision.

**Uptime is never stored** — always derived live in Python as
`now(timezone.utc) − resumed_on` (`warehouse_snapshot.py`). Timestamps are coerced
tz-aware in the parser (the connector may hand them back as strings or naive).

## Force-suspend: lower `AUTO_SUSPEND`, never a hard SUSPEND

We **never** issue `ALTER WAREHOUSE … SUSPEND` — that would race our (milliseconds-stale)
telemetry against an incoming query. Instead we lower `AUTO_SUSPEND` to `1` and let
**Snowflake's own race-free idle accounting** decide: if a query lands, Snowflake
resets its idle timer and simply never suspends.

The lifecycle (`engine.py` → `reconcile.py`):

1. **Write a durable restore-intent row first** (`{org, warehouse, restore_to,
   set_at, baseline_resumed_on}`), *then* `SET AUTO_SUSPEND = 1`. Durability before
   mutation — a crash between the two just means the next tick restores.
2. **The intent row — not the live `=1` value — proves ownership.** A warehouse can
   be created/cloned at `1`, or another cost tool (e.g. SELECT.dev) can set the same
   sentinel. We only ever restore a `=1` we have an intent row for.
3. **Restore on a later tick, driven by the intent** — reachable from every state,
   so a warehouse is never stranded at `1`.

## The restore decision (the subtle part)

The worker only sees discrete snapshots, ~1s apart while an intent is outstanding.
Given an outstanding intent and live `auto_suspend == 1`, `reconcile.py` decides:

- **`SUSPENDED`** → savings captured → restore `managed_auto_suspend`, start cooldown.
- **`STARTED` & busy** (`running`/`queued > 0`) → a query landed → restore, start
  cooldown (it proved bursty, back it off).
- **`STARTED` & idle & still `1`** → ambiguous: either the suspend just hasn't landed
  yet *or* it already suspended-and-resumed between two polls. Disambiguated by
  **`resumed_on`**:
  - If live `resumed_on` differs from `baseline_resumed_on` captured at set-time → a
    full suspend→resume cycle completed → restore now + cooldown (stops a
    resume-storm).
  - Otherwise **hold** the intent — until it ages past `max_intent_hold_ticks ×
    poll_interval` (the anti-stranding backstop).

**Why `baseline_resumed_on` matters:** without it, an idle-again-at-`1` snapshot is
indistinguishable from "hasn't suspended yet," so the worker would keep holding while
the warehouse suspend→resume→suspend cycles, each resume re-billing 60s — billing
*more*, not less.

Other invariants: a restore `ALTER` must **succeed before its intent is deleted** (a
failed ALTER leaves the intent for retry). New intents restore to the live
`managed_auto_suspend`, not the immutable opt-in capture.

## Reconcile-then-decide, every cycle

Each cycle runs off **one `SHOW WAREHOUSES` snapshot**: reconcile first (heal/restore),
then decide (new suspends). Reconcile **always drains** outstanding intents — even for
a disabled, unenrolled, or kill-switched warehouse — so nothing is ever frozen at `1`.

**Drift:** if a warehouse's live `AUTO_SUSPEND` doesn't match `managed_auto_suspend`
(and isn't our `1`) with no intent, the customer changed it directly. We **don't** stomp
it — we flag `drifted`, pause automation on it, and surface a **Reconcile** action in
the UI. Same for a warehouse that turns non-STANDARD (→ `unsupported`, auto-paused).
Reconcile has two choices: **accept** (adopt the drifted value as the new managed
default, API-only) or **re-apply old default**. Since only the worker can `ALTER`, the
API records the re-apply as a restore-intent with `kind='reapply'`; the worker then
overwrites the drifted value on its next tick. The `reapply` kind is what lets the
worker distinguish "admin asked to overwrite this" from "customer just edited it"
(which it must not stomp).

## The suspend decision (`decision.py`)

A pure truth table — force-suspend only when **all** hold: `type == STANDARD`,
`state == STARTED`, `started_clusters == min_cluster_count` (cluster floor),
`uptime ≥ 62s`, `running == 0`, `queued == 0`, `auto_resume == true`, not in cooldown,
not drifted, no intent already outstanding.

- **`auto_resume` required** — else suspending breaks queries instead of slowing them.
- **62s, not 60s** — a small margin so clock skew never forfeits the prepaid minute.
- **Cluster floor gate** keeps multi-cluster warehouses safe with only `AUTO_SUSPEND`
  mutation: we act only once Snowflake has organically shed to its `MIN` cluster count
  and gone idle. We never touch `MIN`/`MAX`. (On Standard edition the cluster columns
  are absent → treated as single-cluster.)

## Kill switch & tenant discovery

`global_enabled` is the master gate. When off, the **decide** step is skipped but
reconcile still drains. `worker_tenants()` returns any org with `global_enabled` **or**
an outstanding intent — so a kill-switched org keeps being polled until its sentinels
drain. A supervisor re-enumerates tenants on an interval, so opt-ins/kill-switches
after startup are picked up without a restart.

## Crash recovery

State lives in Supabase (`automated_savings_settings`, `_warehouses`,
`_restore_intents`, `_events`). Because the intent row is written *before* the ALTER
and reconcile runs every cycle unconditionally, a worker restart simply finds each
outstanding intent and restores it from whatever state the warehouse is in.
Fully-dropped warehouses have their stale intent + enrollment cleaned up after
`orphan_grace_seconds`.

## Audit trail — best-effort mutation events

`automated_savings_events` is **application append-only** while an organization
exists: application code attempts to insert one row after every successful
`AUTO_SUSPEND` mutation and never updates or individually deletes events. Deleting
an organization intentionally cascades to its events as part of tenant-data cleanup:

- **`set_sentinel`** (`reason=decide`, `to_value=1`) — written right after we set the
  sentinel (`engine.py`).
- **`restore`** (`reason ∈ {suspended, busy, resume_aware, aged_out, reconcile_reapply}`,
  `to_value=managed default`) — attempted **before** the intent is deleted
  (`reconcile.py`), so a failed event write leaves the intent for safe reconciliation.

Each row snapshots what we observed at decision time (`observed_state`, `running`,
`queued`, `resumed_on`, `from_value`). A `set_sentinel` and its matching `restore`
share a **`cycle_id`** (carried on the restore-intent row), so a suspend can be paired
with its restore to derive reclaimed idle seconds — the foundation for the deferred
savings-analytics surface. Members can read the log (RLS); only the worker writes it.

The log covers **Snowflake mutations only** — our own state transitions (drift flag,
unsupported-pause, enrollment cleanup) are not events, since they don't touch the
customer's warehouse. Writes are best-effort: a restore-event failure retains its
intent so reconciliation can retry safely, while a set-sentinel event failure can
leave an audit gap. Exactly-once logging is a possible follow-up after v1.

## Resume-storm bounding (why it can't cost more)

While a `=1` sentinel is live, a bursty warehouse could cycle and re-bill. Three bounds:
1. **Fast-poll** (~1s, jittered) while any intent is outstanding → ~1 resume max before
   we observe and restore.
2. **Busy-restore cooldown** → a warehouse that resumed under our sentinel is backed off.
3. **Resume-aware restore** (`baseline_resumed_on`) → restore the instant a completed
   suspend→resume cycle is detected, without waiting out the age backstop.

## Code map

| Concern | File |
|---|---|
| Snapshot parse + uptime | `apps/auto-savings/src/auto_savings/warehouse_snapshot.py` |
| Suspend truth table | `.../decision.py` |
| Reconcile + restore decision | `.../reconcile.py` |
| Cycle orchestration | `.../engine.py` |
| Warm session + watchdog | `.../snowflake_session.py` |
| Per-tenant loop + supervisor | `.../tenant_loop.py`, `main.py` |
| Durable state | `supabase/migrations/202607120001_automated_savings.sql` |
| API routes / services | `apps/api/app/routes/automated_savings.py`, `services/warehouse_directory.py` |
| Web UI | `apps/web/src/components/automated-savings/` |
