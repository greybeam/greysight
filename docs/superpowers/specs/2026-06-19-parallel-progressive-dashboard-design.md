# Parallel base queries + progressive chart rendering

## Goal

Cut dashboard load time from ~2 min to roughly the slowest single query, and
render each chart the moment its own data is ready instead of waiting for whole
sections (or the whole run) to finish.

Two independent layers:

- **Layer 1 — parallel execution.** Fan out all base queries to Snowflake
  concurrently. This alone collapses ~9 base queries (+10 AI branches) from the
  *sum* of their durations (~2 min) to roughly the *slowest single query*
  (~26 s in the 2026-06-18 run).
- **Layer 2 — progressive delivery.** The run becomes asynchronous; datasets
  land incrementally and the frontend renders each chart as it arrives.

## Current state (why this is more than a flag flip)

- `build_snowflake_dashboard_data` (`apps/api/app/services/dashboard_datasets.py:42`)
  runs every source through a **sequential for-loop**
  (`_fetch_source_group`, lines 220–221), one fresh Snowflake connection per
  query (`snowflake_client.py:151,166`).
- AI spend is a **deferred source** that loops 10 Cortex branch queries
  sequentially (`ai_consumption.py:140-144`).
- `POST /api/dashboard-runs` **blocks** until everything is done, then stores a
  fully `completed` snapshot (`dashboard_runs.py:_create_snowflake_dashboard_run`).
- `GET /{run_id}/view` builds the **entire** `DashboardView` atomically and
  resolves a shared date range from `metadata.through_date` + global source
  bounds (`dashboard_view_builder.py:205`). Range changes re-derive the view
  from cached datasets with **no** Snowflake round-trip — this fast path must be
  preserved.
- The repository already has an **incremental per-source state machine**
  (`claim_source` / `complete_source` / `fail_source`, `dashboard_runs.py:259-322`)
  used by the AI deferred source. Layer 2 generalizes this rather than inventing
  a new transport.

## Decisions (confirmed)

- **Delivery:** poll per-dataset (reuse the deferred-source/poll pattern).
- **Concurrency:** bounded, default 8, env-configurable — enforced **process-wide**
  by a single module-level `ThreadPoolExecutor`, not per-run (Codex finding 4).
- **AI branches:** parallelized alongside base queries.
- **Run lifecycle:** `POST` returns `202 running` immediately; frontend polls.
- **Frontend:** per-section readiness; no extra progress UI.
- **Account locator:** persisted in Supabase at connection-setup time (below);
  the dashboard run binds the stored value, so there is **no runtime
  `current_account()` pre-query and no dependency edge** — all queries launch at
  t=0 (Codex finding 1).

## Prerequisite — persist `account_locator` at connection setup

`organization_snowflake_connections.account` stores the **connection
identifier** (often `orgname-accountname`), which is *not* the **account
locator** (e.g. `XY12345`) returned by `current_account()`. The org-usage views
(`org_spend_daily`, `rate_sheet_daily`) filter on the locator
(`where account_locator = %(account_locator)s`), so binding the stored `account`
directly would silently filter to **zero rows**. Resolution:

- **Migration:** add nullable `account_locator text` to
  `organization_snowflake_connections` (`supabase/migrations/`). Existing rows
  start `null` and use the run-only fallback below until re-validated.
- **Capture at validation:** `validate_snowflake_connection`
  (`snowflake_client.py:169`) already runs validation queries on the
  connection — add `select current_account() as account_locator`, return it, and
  persist it to the new column on connection save/validate
  (`routes/snowflake.py`, the connection upsert RPC in the migration).
- **Resolver:** `OrgConnectionConfig` / `resolve_snowflake_config`
  (`org_connection_resolver.py`) expose `account_locator`.
- **Fallback (defensive):** if the stored locator is `null` (legacy row not yet
  re-validated), the run fetches it once via `current_account()` at worker
  start and uses it only for that run. It does not persist the fallback value;
  the connection save/validate path remains the persistence path. The
  optimization degrades gracefully without breaking legacy rows.

With the locator available up front, the run no longer runs `current_account()`
as a gating query; the `current_account` dataset (used only for the
metadata's account display) is synthesized from the stored locator.

## Layer 1 — Parallel execution (backend)

- **New module `apps/api/app/services/parallel_source_runner.py`** (small,
  unit-tested; pure aside from the injected executor):
  - Accepts the registry sources, an injectable `execute` fn (same signature
    used today, so tests stay hermetic and deterministic), the bound
    `account_locator`, and the shared module-level executor.
  - Runs queries concurrently on threads. Safe because `execute_source_query`
    opens/closes its own connection per call with no shared mutable state.
  - **No dependency edge.** With the stored locator, every source launches at
    t=0: the 4 account-usage queries + `org_spend_daily` + `rate_sheet_daily`
    (locator-bound) + `capacity_balance_daily` (**org-scoped, no locator** —
    Codex finding 5) + the 10 AI branches.
  - Per-query outcome is recorded as `ready` or `unavailable` from the
    **exception type** (`SnowflakeQueryError`,
    `SnowflakeObjectUnavailableError`), never from row count — a zero-row query
    is `ready`, not `unavailable` (Codex finding 7). The run only `fails`
    wholesale if **both** the org and account groups are entirely unavailable
    (preserves `DashboardSourcesUnavailableError`).
  - Cheap Python transforms (`account_spend_daily`, `top_warehouses_table`,
    `bound_user_compute_rows`) run inline the instant their parent dataset lands.
- **Concurrency is process-wide.** A single module-level
  `ThreadPoolExecutor(max_workers=GREYSIGHT_QUERY_CONCURRENCY)` (default 8)
  caps total in-flight Snowflake queries **across all active runs**, so two
  simultaneous users cannot push an X-Small warehouse past its default
  `max_concurrency_level` of 8 (Codex finding 4). The executor is shared, not
  created per call.
- **Config:** `GREYSIGHT_QUERY_CONCURRENCY` (default 8) in `app/config.py`.

## Layer 2 — Async run lifecycle (backend)

- **`POST /api/dashboard-runs`** creates the run in a new `running` status and
  returns `202` immediately. A background worker drives the parallel runner and,
  **as each query completes**, writes its dataset into the repo and marks that
  source ready (generalizing `complete_source` / `fail_source` beyond deferred
  sources). When all sources reach a terminal state the run flips to `completed`
  (or `failed` per the rule above).
- **Worker robustness** (Codex finding 3):
  - The worker body is wrapped in `try/except/finally` that **always**
    transitions the run to a terminal state (`completed`/`failed`) and records
    the error for debuggability — a run can never be left stuck `running`.
  - Every incremental write **re-checks under the lock** that the run is still
    `running` before storing; results for an already-`expired`/`failed`/`deleted`
    run are discarded silently (no stale mutation).
  - A wall-clock TTL (e.g. 5 min) auto-expires runs stuck `running`, independent
    of dataset retention.
- **Repository changes** (`InMemoryDashboardRunRepository`, existing `RLock`):
  - `create_running_run(...)` — registers a `running` run with all expected
    sources `pending` (replaces the synchronous `create_completed_snapshot` on
    the create path; the old method stays for tests / demo).
  - `set_dataset(run_id, key, rows)` plus generalized
    `claim_source` / `complete_source` / `fail_source` for base-query sources —
    incremental, lock-guarded, with the running-state staleness guard above.
  - `finalize_run(run_id)` — sets `completed`/`failed`, recomputes
    `source_bounds`, stores `metadata` + `summary` (so the existing
    range-change fast path is unchanged once the run completes).
  - Retention/expiry semantics otherwise unchanged.

## Layer 2 — Partial view contract (backend)

- **Section wire format** (Codex finding 8): rather than a discriminated
  per-section wrapper (which would force rewriting every existing parser and
  component), the running view keeps the **existing section shapes** — each
  required section is always present, carrying empty/zeroed data when not yet
  ready — and adds a **top-level `section_statuses` map**
  `{ "overview" | "warehouse" | "storage": "pending" | "ready" | "unavailable" }`.
  The current `parseDashboardView` already tolerates empty sections and the
  frontend already keys reveal state by these section names, so this is the
  lower-churn choice. `section_statuses` defaults to all-`ready` for legacy/demo
  payloads; Snowflake completed/failed views compute it from final source
  records so unavailable sources remain visible.
- **Running-run bounds** (Codex finding 2): while `running`, the view uses
  **provisional bounds** derived from whatever datasets have landed —
  `through_date = max(usage_date across ready account/org datasets)` (falling
  back to today when none are ready). The frontend treats the date axis as
  provisional until the run is `completed`, at which point `finalize_run`'s
  authoritative bounds apply. Sections not yet computable report `pending`.
- **Per-section dependency map** (Codex findings 6 & 7): replace the single
  coarse "pricing core" gate with an explicit source→section matrix. Each
  section declares the sources it needs and renders as soon as *those* are
  `ready`; it reports `unavailable` if a required source is `failed`. The matrix
  is verified per section during implementation — e.g. credit-count /
  utilization sections need no rate data, whereas converted-currency sections
  need `rate_sheet_daily`. (The existing global `unsupported` /
  estimated-conversion decision still applies once its inputs are ready.)
- `GET /{run_id}/view` is safe to call repeatedly while `running`; once
  `completed`, behavior (incl. range changes) is identical to today.
- AI keeps its existing `/sources/{id}` poll; its 10 branches now run in
  parallel on the shared executor.

## Layer 2 — Frontend (apps/web)

- After the `202`, **poll `GET /view`** on an interval (extend the existing
  poll/backoff helper in `dashboard-api.ts`, or keep the `/view` loop local to
  `loadSnowflakeRun`). Render each section/chart the moment its `status` flips
  to `ready`; keep skeletons for `pending` and `unavailable`; stop polling when
  the run is `completed`/`failed`.
- Replace the single `dataReady` boolean + timed 140 ms stagger in
  `use-section-statuses.ts` with **real per-section readiness** from the view
  payload. Existing skeleton components are reused untouched.
- `dashboard-contracts.ts` adds the top-level `section_statuses` map to
  `DashboardView` (defaulting to all-`ready` when absent); existing section
  parsers are unchanged and continue to tolerate empty sections.
- The date-range-change fast path (re-derive `/view` from cached datasets, no
  Snowflake) is preserved — progressive logic applies only while `running`.

## Demo mode

`build_demo_dashboard_dataset` is instant, so the demo `/view` reports every
section `{status: "ready"}` and the run `completed` on the first poll. Same
contract, no special-casing in the progressive frontend path.

## Tests

- **api:** `parallel_source_runner` — full t=0 fan-out (no dependency edge),
  process-wide executor cap, per-source failure → `unavailable` (by exception
  type, not row count) without failing the run, both-groups-unavailable → run
  `failed`. Injected `execute` keeps it deterministic.
- **api:** worker lifecycle — unhandled exception still reaches a terminal
  state; stale write into an expired run is discarded; TTL auto-expiry.
- **api:** locator persistence — validation captures + stores the locator;
  null-locator legacy row falls back to a one-time run-only fetch.
- **api:** partial `/view` — section `status` transitions, per-section
  dependency gating, provisional vs finalized bounds, final-source statuses for
  completed/failed runs.
- **api:** async lifecycle — `POST` → `202 running`, poll → `completed`; update
  existing tests that assert a synchronous `completed` `POST`.
- **web:** render-on-arrival — sections reveal independently by `status`;
  skeletons persist for `pending` and `unavailable`; polling
  stops at `completed`. Range-change fast path unchanged after completion.

## Risks

- **Biggest change:** `POST` contract `completed` → `running`. Mitigated by
  updating the affected api tests and keeping the completed-run shape identical.
- **Schema change:** the `account_locator` column + connection-setup capture
  touch Supabase and the validation flow; the null-locator fallback keeps legacy
  connections working until re-validated.
- **Multi-tenant load:** addressed by the process-wide executor cap; without it,
  N concurrent runs would multiply warehouse concurrency.
- **Stale/stuck runs:** addressed by the terminal-state guarantee, the
  running-state write guard, and the wall-clock TTL.
- **Provisional bounds** mean a running view's date axis can shift slightly
  until `finalize_run`; acceptable and converges on completion.
- **Single-process assumption** is unchanged — the in-memory repo, the
  module-level executor, and the background worker all require one worker
  process, as today.
