# Automated Savings — Design

**Date:** 2026-07-10
**Branch:** `issue-52-v1`
**Issue:** [#52 — [feat] Automated savings](https://github.com/greybeam/greysight/issues/52)

## Problem

Snowflake bills a **60-second minimum every time a warehouse resumes**, then keeps
billing while the warehouse sits idle until `AUTO_SUSPEND` fires. On sporadic
workloads that idle tail is pure waste. Automated Savings cuts it: watch each
enrolled warehouse in near-real-time and, once it is genuinely idle past the 60s
billing floor, force Snowflake to suspend it — reclaiming the idle seconds between
the prepaid minute and the customer's natural `AUTO_SUSPEND`.

This is a fundamentally different subsystem from the existing read-only dashboard:
it is an **active control loop** with durable state and **write** privileges on
Snowflake, not a prepared read-only view.

## Goals / Non-goals

**Goals (v1):**
- A standalone worker that suspends idle enrolled warehouses safely.
- An opt-in flow gated on a Snowflake grant the customer runs themselves.
- A dashboard page: per-warehouse enrollment toggle + managed `AUTO_SUSPEND`.
- Multi-tenant, memory-efficient, crash-safe.

**Non-goals (v1):**
- Firing-history chart / savings analytics (explicitly deferred).
- Editable guardrail *numbers* (60s floor, cooldown duration are hardcoded).
- Per-warehouse back-off / bursty-warehouse demotion (dropped; revisit with churn data).
- Bursty/interactive workloads (out of scope; target sporadic workloads).
- **Snowpark-optimized warehouses** (`type != STANDARD`) — excluded in v1.
- **Reclaiming the extra-cluster idle** on multi-cluster warehouses (the ~2–3 min
  scaling-policy shed window). Multi-cluster warehouses *are* enrollable, but we
  only suspend the single-cluster idle tail once they organically reach their
  cluster floor; the shed window is Snowflake's hysteresis (advise-only, see the
  multi-cluster section). No `MIN`/`MAX` mutation.
- Paywall / billing (free for testing in v1).

## Architecture

Three surfaces across the monorepo:

1. **Worker — new app `apps/auto-savings/`** (Python). Deployed as its **own
   Railway service** (own Dockerfile) so a worker crash never takes down the API.
   Owns the control loop only.
2. **API additions — `apps/api/`.** UI-facing reads/writes: opt-in status, live
   warehouse list (`SHOW WAREHOUSES`), per-warehouse enroll/config CRUD. The web
   app never talks to the worker directly.
3. **Web — `apps/web/`.** New top nav + `/automated-savings` page.

Shared Snowflake/Supabase connection code (`snowflake_client.py`,
`org_connection_resolver.py`, and the Vault fetcher) is **extracted to a shared
Python package** both `apps/api` and `apps/auto-savings` import, rather than
duplicated. The existing `shared/` top-level directory is the target; exact
packaging (installable local package vs. path import) is settled in the plan.

### Snowflake identity & privileges

The worker acts as the **org's existing connection/role** — the same credential
the dashboard already uses (key-pair JWT, fetched from Supabase Vault via the
service-role RPC). No new credential.

Opt-in requires the customer to grant that role warehouse-control privileges. The
opt-in SQL shown in the UI (run as `ACCOUNTADMIN`):

```sql
GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE <the org's role>;
```

`MANAGE WAREHOUSES` covers both operating (suspend/resume) and modifying
(`AUTO_SUSPEND`) every warehouse in the account in a single grant.

## The idle signal

`SHOW WAREHOUSES` — a cloud-services **metadata** call (no warehouse compute, no
spin-up, sub-second) that returns **every** warehouse for the account in one call
with the columns the engine needs: `name`, `state`, `type`, `size`,
`started_clusters`, `running`, `queued`, `auto_suspend`, `auto_resume`,
`resumed_on`.

This is the authoritative, low-lag signal — **not** `ACCOUNT_USAGE`, whose
latency (up to ~45 min) makes it unusable for a 60s decision. Empirically our
competitor SELECT.dev polls exactly this (`show warehouses`) on a single
persistent session every ~1.5–2s (confirmed via identical `SESSION_ID` in their
`ACCOUNT_USAGE.QUERY_HISTORY` traffic).

**Uptime is never persisted** — always derived live as
`current_timestamp - resumed_on` (same timezone) from the poll result.

## Engine (worker) design

### Poll loop

Per tenant, every **3–5 seconds** (configurable via env; default 3s):

1. Run one `SHOW WAREHOUSES`.
2. For each **enrolled** warehouse, reconcile config drift, then decide and act
   (below).

**One eligibility filter: `type == 'STANDARD'`** — Snowpark-optimized warehouses
are excluded (different resource/suspend profile, out of scope). There is **no
cluster-count restriction**: multi-cluster STANDARD warehouses are enrollable; the
`started_clusters == min_cluster_count` gate in the decision below is what keeps
them safe (we only ever act at the cluster floor, where a suspend is correct and
race-safe). `type` is re-checked every poll (a warehouse can be `ALTER`ed between
types); if an enrolled warehouse becomes non-STANDARD it is auto-paused + flagged.
See "Multi-cluster warehouses" below.

### Suspend decision

For an **enrolled** warehouse, force a suspend when **all** hold:

- `type == STANDARD` — Snowpark-optimized warehouses are excluded (see eligibility
  above); hard-skip before any mutation.
- `state == STARTED` — we only ever act on running warehouses. `SUSPENDED` has
  nothing to suspend; `RESUMING` / `QUIESCING` are transitional → skip.
- **`started_clusters == min_cluster_count`** — we act **only at the cluster
  floor.** This is the single gate that makes the design correct for *every*
  warehouse shape, using only `AUTO_SUSPEND=1` and never mutating `MIN`/`MAX`:
  - Single-cluster (`min=1`): acts at 1 cluster — unchanged.
  - Auto-scale (`min=1, max=N`): acts **only** once Snowflake has *organically*
    shed to 1 cluster and gone idle — the multi-cluster "wait for the scaling
    policy, don't fight it" tail, folded into v1 for free. While it's above min we
    never touch it (no churn, no stranding, no resume-storm).
  - Maximized (`min=max=N`): acts when all N clusters are idle past 60s — an idle
    maximized warehouse is pure waste; suspend is correct and billing-neutral
    per-cluster (resume re-charges N×60s ≈ the N-cluster idle burn saved).
  Because we never set `AUTO_SUSPEND=1` while `started_clusters > min`, a burst can
  never be suspended out from under a scaled-up warehouse.
- `uptime ≥ 60s` — never waste the prepaid minute. Uptime is computed **in our
  Python backend** (see below), and a NULL `resumed_on` (never-resumed,
  freshly-created warehouse) is **ineligible** → skip.
- `running == 0` **and** `queued == 0` — genuinely idle.
- `auto_resume == TRUE` — otherwise suspending breaks queries instead of slowing
  them.
- Not in cooldown (per-warehouse `cooldown_ts` in the future → skip).
- Not currently marked **drifted** (see below).
- No restore-intent already outstanding for this warehouse (else we'd re-issue
  `SET=1` every cycle — see lifecycle below).

We act on the **first** idle observation (no "N checks in a row"): the worst case
— a query arriving right after we act — is billing-equivalent to doing nothing,
because Snowflake's own idle accounting cancels the suspend race-free.

**Uptime computed in the Python backend — never via a SQL `SELECT`.** We must
**not** compute uptime with `RESULT_SCAN` / any `SELECT`: a `SELECT` executes
against the session's *current warehouse* and would **resume it**, waking (and
billing) the org's warehouse on every 3s poll — the exact opposite of the goal.
`SHOW WAREHOUSES` is pure metadata and needs no warehouse; a `SELECT` does. So we
compute uptime ourselves, tz-safely:

- The connector returns `resumed_on` as a **timezone-aware** `datetime` (from
  `TIMESTAMP_LTZ`). Compare against `datetime.now(timezone.utc)` — both tz-aware,
  so the subtraction is correct regardless of the account timezone. Never subtract
  a naive `datetime.now()` (that throws).
- `resumed_on is None` (never resumed) → ineligible, skip. Non-`STARTED` state →
  skip. Guard both before any arithmetic.
- Our-clock-vs-Snowflake skew is sub-second on NTP; at a 60s threshold this is
  low-stakes (acting a second early only forfeits a second of the prepaid minute).
  The plan may add a small safety margin (e.g. require `≥ 62s`).

### Force-suspend lifecycle (never a hard `SUSPEND`)

We **never** issue `ALTER WAREHOUSE ... SUSPEND` directly — that would race our
(possibly milliseconds-stale) telemetry against an incoming query. Instead we
lower `AUTO_SUSPEND` and let **Snowflake's own race-free idle accounting** decide:
if a query lands, Snowflake resets its idle timer and simply never suspends.

1. **Write a durable restore-intent row** in Supabase *before* touching Snowflake:
   `{organization_id, warehouse_name, restore_to = stored_default, set_at}`. This
   row — **not** the live wire value — is what proves the sentinel is ours and
   drives the restore. It survives a worker crash and is immune to another tool
   (e.g. SELECT.dev) using the identical `AUTO_SUSPEND=1` trick.
2. **Lower:** `ALTER WAREHOUSE <name> SET AUTO_SUSPEND = 1`. Snowflake suspends on
   its next idle tick (~1s) *if it stays idle*; a query resets its timer race-free.
3. **Restore on the next tick — unconditionally.** On the *next* poll where a
   restore-intent exists for this warehouse, restore `AUTO_SUSPEND` to
   `restore_to`, delete the intent row, and start the per-warehouse **cooldown**
   (hardcoded default duration). We do **not** wait to observe `SUSPENDED`: after a
   full 3–5s tick a continuously-idle warehouse is already suspended (savings
   captured); if it is *still running*, a query arrived — we simply back off and
   restore the default. Either way the warehouse never lingers at `1`.

**Why next-tick-unconditional (not gate-on-SUSPENDED):** gating restore on catching
the `SUSPENDED` state is racy against our own cadence — a warehouse that suspends
then resumes between two polls would never be observed suspended and would be
**stranded at `AUTO_SUSPEND=1`**, causing a resume-storm that bills *more*, not
less. Driving restore off the durable intent row on the next tick makes restore
reachable from every state.

**Bounding the `AUTO_SUSPEND=1`-live window (resume-storm guard).** While the
sentinel is live (from set until we observe-and-restore) a bursty warehouse can
cycle suspend→resume→idle→suspend, and each *extra* resume costs a fresh 60s
minimum. Three bounds apply: (a) **fast follow-up cadence** — while any
restore-intent is outstanding the worker polls at `intent_poll_interval_seconds`
(default 1s, ±15% jitter) rather than the normal interval, so at most ~1 resume
occurs before we restore; (b) the `STARTED & busy` restore now **demotes via a
cooldown** (a warehouse that resumed under our sentinel proved it is bursty); and
(c) **resume-aware restore** — the intent captures the warehouse's `resumed_on`
as `baseline_resumed_on` at set-time, and the `STARTED & idle & live == 1` HOLD
branch restores early (and demotes via cooldown) if `resumed_on` advanced past the
baseline, since that proves a suspend→resume cycle already completed. The
anti-stranding hold bound stays computed from the *normal* poll interval so it is
unaffected by the fast cadence.

### The `AUTO_SUSPEND = 1` sentinel — ownership is proven by our intent row

The UI enforces a **floor of 60** on the user-editable managed default, so a human
using our product cannot produce `AUTO_SUSPEND = 1`. But `live == 1` on its own is
**not** proof of ownership — a warehouse can be created/cloned at `1`, or another
cost tool (SELECT.dev runs this exact mechanism) can set it. **Ownership is
therefore established by the presence of our restore-intent row, not by the live
value alone.** At enroll we also reject/flag a captured `stored_default ∈ {0, 1,
NULL}` (always-on or already-sentinel warehouses are out of scope, not silently
reconfigured).

### Single writer for Snowflake mutations

To avoid the API and worker both `ALTER`-ing the same warehouse and racing on the
same row, **the worker owns every Snowflake `ALTER WAREHOUSE`.** The API only
writes *intent* to Supabase (enroll, unenroll-requested, managed-default edit,
reconcile choice); the worker reads that intent and performs the actual Snowflake
change on its next tick. Corollary: **`stored_default` is never deleted while a
restore-intent is outstanding** (else there is no value to restore to). Unenroll
and kill-switch **drain** any outstanding sentinel back to the default before
clearing state — they never freeze a warehouse at `1`.

### Crash recovery / reconciliation (every cycle + on startup)

Reconciliation and the suspend decision run off **one shared `SHOW WAREHOUSES`
snapshot per cycle** (never a re-query between them, which could thrash). Order:
reconcile first (heal/restore), then decide.

For each managed warehouse:

- **Outstanding restore-intent row exists** → **we own it.** Restore `restore_to`,
  delete the intent, start cooldown — regardless of current state (this is the
  same next-tick restore, and also the crash-recovery path on startup). Before
  overwriting, re-check the live value: if it is neither `1` nor `restore_to`, the
  customer edited it mid-suspend → treat as drift instead of stomping their edit.
- No intent row, `live ∉ {stored_default}` (and `≠ 1`) → **drift**: the customer changed
  `AUTO_SUSPEND` directly in Snowflake. We **do not** stomp it and we **do not**
  silently adopt it. We:
  - mark the warehouse `drift_state = drifted` with the observed value,
  - **pause automation** on that warehouse (we won't force-suspend a warehouse
    whose config we no longer trust),
  - surface a warning in the UI with a **Reconcile** action (accept the new value
    as the managed default, or re-apply the previous default).

### Multi-cluster warehouses (enrollable; we capture the idle tail)

Snowflake fires `AUTO_SUSPEND` **only when a multi-cluster warehouse is running at
its `MIN_CLUSTER_COUNT` and then idle** — extra clusters are shed by a *separate*
scaling policy (Standard: one-at-a-time after "a sustained period of low load",
historically ~2–3 one-minute checks; Economy: when it estimates a cluster has <6
min of work left), not by `AUTO_SUSPEND`.

We **lean into** that rather than fight it. The `started_clusters ==
min_cluster_count` gate means the worker acts **only once the warehouse has
organically reached its cluster floor and gone idle**, then suspends it with the
same `AUTO_SUSPEND=1` lifecycle. We capture the **single-cluster idle tail**
race-safely, for multi-cluster and single-cluster STANDARD warehouses alike, with
**no `MIN`/`MAX` mutation** — no cluster-count restriction (only the
`type == STANDARD` filter above).

**What we deliberately do NOT reclaim: the extra-cluster idle during the ~2–3 min
shed.** The tempting fix — lower `MAX_CLUSTER_COUNT` to 1 to force scale-down — is
**dead on the merits, not just risky**, and must not be revisited without the
shed-timing fact:

- **No acceleration → no savings.** `ALTER … SET MAX_CLUSTER_COUNT` below the
  running count sheds excess clusters only "when they finish executing statements
  **and the scaling policy conditions are met**" — i.e. the *same* "sustained
  period of low load" timer Snowflake uses anyway. Lowering `MAX` sheds no faster,
  so it buys essentially **zero incremental savings**.
- **No race-free escape hatch.** `AUTO_SUSPEND=1` is safe only because Snowflake
  cancels the suspend the instant a query lands; `MAX=1` has no such hatch, so a
  burst arriving while clusters are capped **queues** — a latency **regression**.
- **Silent blast radius.** Would triple the mutate/restore surface (`MIN`, `MAX`,
  `AUTO_SUSPEND`); `MIN`/`MAX` are *normal values* with no self-identifying
  sentinel, so a lost intent row leaves an **undetectable** concurrency cap.

That warm-idle-cluster window is Snowflake's **deliberate anti-thrash hysteresis**;
reclaiming it means defeating the hysteresis and exposing the next burst, so it is
**not safely reclaimable by any transient mutation.** The honest lever for it is
**advise, not mutate** — a future recommendation to switch to the Economy scaling
policy or lower `MAX` permanently (the customer's decision). Documented as a
possible v2 surface; not built.

**Type filter:** only `type == STANDARD` warehouses are enrolled; Snowpark-optimized
warehouses are excluded in v1 (different resource/suspend profile). The cluster-count
gate handles single- vs. multi-cluster within the STANDARD type.

### Connection management (the memory-critical part)

At 3–5s cadence a fresh connect + JWT login (~200–500ms, rate-limited) per poll is
infeasible. **One warm persistent Snowflake session per active tenant, reused
across polls** (Fable-consulted; confirmed by SELECT.dev's single-`SESSION_ID`
traffic). Memory is O(active tenants) at ~1–5 MB/session (the ~100–150 MB pyarrow
baseline is paid once per process) — trivial at 50, ~0.5–2.5 GB at 500, shard
beyond.

- **Concurrency:** an `asyncio` loop + one **bounded** `ThreadPoolExecutor`
  (`max_workers = min(64, active_tenants)`); blocking connector calls dispatched
  via `run_in_executor`. A **per-tenant `asyncio.Lock`** ensures a slow tenant
  never overlaps its own polls. Not thread-per-tenant, not process-per-tenant.
- **Session hygiene:** `client_session_keep_alive = True` (else the 4h token
  expires); any poll exception → close the session hard and reconnect with
  **jittered exponential backoff** (respects JWT login limits); do not try to
  classify error types in v1.
- **Blast radius (the real risk, not memory):** every poll is wrapped in a
  **timeout + watchdog** so a wedged blocking call costs at most one thread until
  timeout, never the loop. One tenant wedging must not affect others. **A future
  timeout alone does not free the thread** — a blocking socket read is not
  cancellable — so the watchdog must **force-close the underlying connection /
  socket** to release the thread; otherwise repeated wedges exhaust the pool and
  globally stall. Treat a wedge like any other failure: close hard, backoff,
  reconnect.
- **Sharding hook from day one:** the worker only handles tenants where
  `hash(tenant_id) % NUM_REPLICAS == REPLICA_INDEX` (both from env). Single
  process now; horizontal scale becomes a Railway replica-count change, not a
  rewrite. **Do not** build the bounded-LRU pool / dual-cadence scheme in v1 — it
  burns rate-limited logins to solve a >1000-tenant problem.

### Cloud-services cost note

`SHOW WAREHOUSES` at 3–5s is metadata-only and effectively free, but it accrues
against the tenant's cloud-services daily allowance (billed only above 10% of
daily compute). Cadence is env-configurable so it can be relaxed per deployment.
Document this in `docs/`.

## Durable state (Supabase / Postgres)

New migration under `supabase/migrations/`. Tables (final column names settled in
the plan):

- **Org-level opt-in** — one row per org: `agreed_at` (the experimental-feature
  agreement), `global_enabled` (the global switch / master gate — when false, no
  automation runs for the org regardless of per-warehouse toggles). Could live on
  the existing org connection table or a new `automated_savings_settings` table.
- **Per-warehouse enrollment** — `automated_savings_warehouses`, keyed by
  `(organization_id, warehouse_name)`: `enabled` (**defaults false** — nothing is
  automated at opt-in), `stored_default_auto_suspend`,
  `cooldown_ts`, `drift_state` (`ok` | `drifted`), `drifted_value`,
  `warehouse_created_on` (to detect drop+recreate reuse of a name — see review
  M2), `updated_at`.
- **Restore-intent** — the sentinel-ownership record written before each
  `SET AUTO_SUSPEND=1`: `(organization_id, warehouse_name)` (one outstanding per
  warehouse), `restore_to`, `set_at`. Presence of this row is what proves the
  sentinel is ours and drives the next-tick restore; it is the crash-recovery
  source of truth. May be a nullable column-set on the enrollment row rather than
  a separate table — settled in the plan.

**RLS (never widened):** members **read**; only owners/admins mutate opt-in,
toggles, and reconcile actions — mirroring the existing sensitive-mutation policy.
The worker reads/writes via the **service role**, same pattern as
`SupabaseConnectionFetcher`.

## API additions (`apps/api`)

New route module (e.g. `apps/api/app/routes/automated_savings.py`), mounted in
`main.py`, all behind the existing auth/org guards:

- `GET` opt-in status + settings (agreed?, global switch state, **`MANAGE
  WAREHOUSES` grant present** via `SHOW GRANTS TO ROLE <role>` — metadata-only,
  never resumes a warehouse). Backs the "Check access / Refresh" button.
- `POST` agree (records `agreed_at`; owner/admin only).
- `POST` kill switch on/off (owner/admin only).
- `GET` warehouse list — live `SHOW WAREHOUSES` joined with our per-warehouse
  enrollment rows (state, size, min/max clusters, `started_clusters`, auto_resume
  health, managed default, drift/cooldown status, enabled).
- `POST` per-warehouse enroll/unenroll toggle. Enroll **captures the current
  `AUTO_SUSPEND` as `stored_default`**; unenroll restores the default and clears
  managed state. Owner/admin only.
- `POST` per-warehouse managed-default update (floor 60, validated server-side).
- `POST` reconcile a drifted warehouse (accept new / re-apply old).

Snowflake reads here reuse the existing `resolve_snowflake_config` +
`execute` path; `SHOW WAREHOUSES` is a new command the client must support (it is
a metadata command, not a registry `ACCOUNT_USAGE` query — the registry rule is
about `ACCOUNT_USAGE` source queries and is not violated).

## Web page (`apps/web`)

**Top nav (horizontal, added to the app shell):** **Home** (`/dashboard`) ·
**Automated Savings** (`/automated-savings`). Existing first-screen routing to
`/dashboard` is preserved; the dashboard is simply labeled "Home".

**Not opted in — explainer gate:**
- What it does (reduces idle warehouse spend by suspending warehouses once idle
  past the 60s billing floor).
- **Experimental feature** notice + link to the auditable code (this repo).
- **Agree** button (owner/admin) recording the agreement.
- The `GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE <role>` SQL to run as
  ACCOUNTADMIN, copyable, with the org's role name filled in.

**Opted in — dashboard:**
- **On opt-in, every warehouse is toggled OFF by default.** Nothing is automated
  until the user turns warehouses on — no warehouse is enrolled implicitly.
- **Global switch** = **enable/disable automation for *all* warehouses at once**
  (bulk on/off) and doubles as the **kill switch** (flip off → everything pauses
  immediately, regardless of individual toggles). The user can either flip the
  global switch or toggle warehouses individually. Global reflects all-on /
  all-off / mixed; flipping it forces all toggles.
- Warehouse table (Tremor / shared dashboard primitives, per the design system):
  columns **name · size · # clusters · current state · AUTO_SUSPEND (managed
  default) · AUTO_RESUME health · status · on/off toggle**.
  - **AUTO_SUSPEND (managed default):** editable number, **floor 60** with a
    warning tooltip near/at 60 (going lower erodes savings vs. the billing floor);
    tooltip: *"captured at opt-in — the value we restore the warehouse to."*
  - **AUTO_RESUME health:** badge; if `FALSE`, warn "AUTO_RESUME off — can't
    automate safely" and disable the toggle.
  - **Multi-cluster STANDARD warehouses are enrollable** — the table shows min/max
    clusters; an optional note explains we suspend only once the warehouse is at its
    cluster floor and idle (we don't force scale-down).
  - **Snowpark-optimized warehouses** (`type != STANDARD`): toggle **disabled** with
    a tooltip ("Snowpark-optimized warehouses aren't supported yet"). An enrolled
    warehouse that becomes non-STANDARD is shown auto-paused.
  - **status:** idle / mid-suspend / in-cooldown / **drifted** (with Reconcile
    action) / **unsupported** (non-STANDARD).
- **"Check access / Refresh" button** that verifies the role can manage warehouses
  (`SHOW GRANTS TO ROLE`, metadata-only). If the grant is missing/revoked, show a
  **"grant missing"** banner (re-display the GRANT SQL) and pause automation.
- Firing-history chart: **out of scope** (v1).

## Testing

TDD; every behavior change gets a failing-first test; **no tests that restate the
implementation** (no copy/label/render assertions). Focus on guards, the state
machine, and edge cases:

**Worker / engine (pytest, hermetic — mock the Snowflake session):**
- Suspend decision truth table: fires only when *all* preconditions hold; each
  precondition individually blocks (uptime < 60, running > 0, queued > 0,
  auto_resume false, in cooldown, drifted).
- Uptime derivation from tz-aware `resumed_on` (never persisted); NULL → ineligible.
- Lifecycle: write restore-intent → set `AUTO_SUSPEND=1` → next-tick restore →
  intent cleared, cooldown set.
- Next-tick restore: with an outstanding restore-intent, restore fires whether the
  warehouse is `SUSPENDED` **or** still `STARTED` (query landed) — never stranded
  at `1`; intent row deleted; cooldown set.
- Restore re-checks live before overwriting: `live ∉ {1, restore_to}` mid-suspend
  → treated as drift, not stomped.
- Reconciliation driven by the intent row (not by `live==1` alone): a warehouse
  independently sitting at `1` with **no** intent row is left untouched (not ours).
- Drift: `live ∉ {stored_default, 1}` with no intent → pause + flag, including when
  the warehouse is `SUSPENDED`.
- Crash recovery: on startup, every outstanding intent restores its `restore_to`,
  leaving no warehouse misconfigured from any intermediate state.
- Eligibility gates: NULL `resumed_on` and non-`STARTED` states are ineligible;
  uptime computed from a tz-aware `resumed_on` vs. `now(timezone.utc)` (no naive
  subtraction, no `SELECT`/warehouse resume).
- Type filter: `type != STANDARD` (Snowpark-optimized) is hard-skipped before any
  mutation; an enrolled warehouse that becomes non-STANDARD is auto-paused + flagged.
- Cluster-floor gate: acts only when `started_clusters == min_cluster_count`. A
  multi-cluster warehouse idle at `started_clusters > min` is **not** acted on (no
  `AUTO_SUSPEND=1` set); the same warehouse once organically shed to `min` and idle
  **is** acted on. Maximized (`min==max==N`) acts at N idle clusters. Verified for
  single-cluster, auto-scale, and maximized shapes.
- Connection: exception → close + backoff reconnect; per-tenant lock prevents
  overlapping polls; watchdog force-closes the socket so a wedged tenant frees its
  thread; sharding predicate selects the right tenant subset.

**API (pytest + httpx):**
- Auth/RLS guards: member cannot mutate opt-in/toggle/default/reconcile; owner/
  admin can.
- Enroll captures `stored_default`; managed-default update rejects `< 60`.
- Warehouse-list join maps live `SHOW WAREHOUSES` + enrollment rows correctly,
  including auto_resume-off and drifted rows.

**Web (Vitest):**
- Gate vs. dashboard branching on opt-in state.
- Managed-default input enforces the 60 floor (guard, not label).
- Toggle disabled when AUTO_RESUME off; Reconcile surfaced when drifted.

## Adversarial review — findings & resolutions

Reviewed by a Fable pass (Codex froze mid-review; its captured partial pointed at
the same persistence/lifecycle concern). Resolutions folded into the sections above.

| # | Sev | Finding | Resolution |
|---|-----|---------|------------|
| C1 | CRIT | Restore gated on observing `SUSPENDED` is racy → warehouse stranded at `AUTO_SUSPEND=1` → resume-storm bills *more*. | **Restore on the next tick, unconditionally, driven by a durable restore-intent row** — not by catching `SUSPENDED`. Reachable from every state. |
| H1 | HIGH | `resumed_on` NULL (never-resumed) and tz-aware `TIMESTAMP_LTZ` → naive Python subtraction crashes the poll. | Gate on `state==STARTED` + non-NULL; compute uptime in Python from the tz-aware `resumed_on` vs `now(timezone.utc)`. **Not** server-side — a `SELECT`/`RESULT_SCAN` would resume the session warehouse every poll. |
| H2 | HIGH | `live==1` is not proof of ownership (clone/create at 1, or SELECT.dev running the same trick). | Ownership proven by **our restore-intent row**, not the live value; reject enroll when captured default ∈ {0,1,NULL}. |
| H3 | HIGH | API + worker both `ALTER` the same warehouse; API wiping `stored_default` mid-suspend → no restore target. | **Worker owns all Snowflake ALTERs**; API writes intent only; never delete `stored_default` while an intent is outstanding; unenroll/kill-switch **drain**, never freeze at 1. |
| M1 | MED | Watchdog timeout doesn't free a blocked socket thread → pool exhaustion. | Watchdog **force-closes the connection/socket**; treat as failure → backoff reconnect. |
| M2 | MED | Warehouse identity is by name; drop+recreate stomps the new one. | Track `warehouse_created_on`; invalidate enrollment when a name disappears or `created_on` changes. |
| M3 | MED | Customer edit invisible while `live==1` → restore stomps their change. | Re-read live at restore time; `live ∉ {1, restore_to}` → drift, don't overwrite. |
| M4 | MED | Decide + reconcile on separate queries can thrash. | **One `SHOW WAREHOUSES` snapshot per cycle**; reconcile-then-decide; skip decision if intent already outstanding. |
| M5 | MED | Always-on warehouses (`AUTO_SUSPEND=0`/NULL) don't fit the floor-60 model. | Reject enrollment when captured default is `0`/NULL (out of scope, not silently changed). |
| — | LOW | Drift only checked "while idle" misses drift on suspended warehouses. | Drift check no longer state-gated (fires on `SUSPENDED` too). |
| — | LOW | Positional parsing of `SHOW WAREHOUSES` brittle across editions. | Parse **by column name**, tolerate absence/case. |
| MC | HIGH | Multi-cluster warehouses: `AUTO_SUSPEND` only fires at `MIN_CLUSTER_COUNT`, so acting while `started_clusters > min` is a churny no-op that could strand/resume-storm. The `MAX=1` "force scale-down" fix is **dead**: lowering `MAX` sheds no faster than the scaling policy's own "sustained low load" timer → zero acceleration, all risk. | **`started_clusters == min_cluster_count` gate**: act only at the cluster floor (works for single/auto-scale/maximized), using only `AUTO_SUSPEND=1`, never `MIN`/`MAX`. Multi-cluster is enrollable; we capture just the idle tail. Extra-cluster idle is advise-only. |

**Deferred to future (not v1):**
- **Replica double-ownership on scale changes.** Only relevant if we ever run **more
  than one worker process** (Railway replicas). v1 runs a **single worker**, so the
  `hash(tenant)%N` sharding is dormant and this cannot occur. When we scale out
  later, the plan will add a short drain/handoff or coordination lock.

**Resolved above / clarified:**
- **`MANAGE WAREHOUSES` access check.** Verified via `SHOW GRANTS TO ROLE <role>`
  (metadata, no warehouse) behind a **"Check access / Refresh" button** in the UI
  and the opt-in status endpoint. If the grant is missing (or later revoked so a
  restore ALTER fails), surface a customer-visible **"grant missing"** state and
  pause automation for that org until re-checked.
- **Reconnect backoff ≠ the dropped guardrail back-off.** The per-warehouse
  bursty-demotion back-off from the issue stays **dropped**. Separately, when a
  *connection* fails we reconnect with **jittered exponential backoff** so a shared
  Snowflake/Supabase outage doesn't synchronize every tenant into a login storm —
  this is standard resilience, not a savings guardrail.

## Open items for the plan (not blocking design)

- Exact shared-package mechanics for `apps/auto-savings` importing the Snowflake/
  Supabase code (`shared/` layout, Dockerfile, `uv` config).
- Whether opt-in/kill-switch live on the existing connection table or a new
  settings table.
- Detecting "grant present" (probe `MANAGE WAREHOUSES` vs. attempt-and-catch) for
  the opt-in status endpoint.
- Cooldown default duration and poll-cadence default values.
- Exact global-switch UX: confirm it's a **master gate** that pauses without
  losing per-warehouse toggles, plus an "enable all / disable all" bulk action —
  vs. a pure bulk toggle. (Leaning master-gate + bulk; low-stakes, UI-only.)
