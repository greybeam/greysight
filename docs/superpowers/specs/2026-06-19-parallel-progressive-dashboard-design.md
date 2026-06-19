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
- **Concurrency:** bounded pool, default 8, env-configurable.
- **AI branches:** parallelized alongside base queries.
- **Run lifecycle:** `POST` returns `202 running` immediately; frontend polls.
- **Frontend:** per-section readiness; no extra progress UI.

## Layer 1 — Parallel execution (backend)

- **New module `apps/api/app/services/parallel_source_runner.py`** (small,
  unit-tested, pure aside from the injected executor):
  - Accepts the registry sources, an injectable `execute` fn (same signature
    used today, so tests stay hermetic and deterministic), and a
    `max_workers` cap.
  - Runs queries on a bounded `ThreadPoolExecutor(max_workers=N)`. Safe because
    `execute_source_query` opens/closes its own connection per call with no
    shared mutable state.
  - **Dependency handling — one edge only:** the 3 org-usage queries
    (`org_spend_daily`, `rate_sheet_daily`, `capacity_balance_daily`) bind
    `account_locator` from `current_account`; the 4 account-usage queries bind
    only `window_days`. Dispatch `current_account` + the 4 account-usage queries
    + the 10 AI branches at t=0; dispatch the 3 org queries the moment
    `current_account` resolves.
  - Per-query failures map to the existing availability/skip semantics
    (`SnowflakeQueryError`, `SnowflakeObjectUnavailableError`) — a failed/
    unavailable source marks that source `unavailable`, it does not fail the run
    unless **both** org and account groups are entirely unavailable (preserves
    `DashboardSourcesUnavailableError`).
  - Cheap Python transforms (`account_spend_daily`, `top_warehouses_table`,
    `bound_user_compute_rows`) run inline the instant their parent lands.
- **Config:** `GREYSIGHT_QUERY_CONCURRENCY` (default 8) in `app/config.py`.

## Layer 2 — Async run lifecycle (backend)

- **`POST /api/dashboard-runs`** creates the run in a new `running` status and
  returns `202` immediately. A background worker (module-level thread pool —
  consistent with the in-memory repo's existing single-process assumption)
  drives the parallel runner and, **as each query completes**, writes its
  dataset into the repo and marks that source ready (generalizing
  `complete_source` / `fail_source` beyond deferred sources). When all sources
  reach a terminal state, the run flips to `completed` (or `failed` if both
  source groups are unavailable).
- **Repository changes** (`InMemoryDashboardRunRepository`):
  - `create_running_run(...)` — registers a run in `running` with all expected
    sources `pending` (replaces the synchronous `create_completed_snapshot` on
    the create path; the method stays for tests / demo).
  - `set_dataset(run_id, key, rows)` + `mark_source_ready/unavailable` —
    incremental, lock-guarded writes (reuse the existing `RLock`).
  - `finalize_run(run_id)` — sets `completed`/`failed`, recomputes
    `source_bounds`, stores `metadata` + `summary` (so the existing
    range-change fast path is unchanged once the run completes).
  - Retention/expiry semantics unchanged.

## Layer 2 — Partial view endpoint (backend)

- **`GET /{run_id}/view`** becomes tolerant of partial data. Refactor
  `build_dashboard_view` (`dashboard_view_builder.py`) into:
  - A **pricing core** resolver: `through_date` (from `account_usage_through_date`
    once account data lands, else `billing_through_date`), `currency`, and the
    rate index (`rate_sheet_daily`). Ready once `current_account` + org/rate
    land.
  - **Per-section builders** (capacity, total/service, warehouse, storage,
    detail tables) — each invoked only when *(its own datasets)* AND *(pricing
    core)* are ready; otherwise the section reports `status: "pending"`.
  - The response gains a per-section `status` field
    (`pending` | `ready` | `unavailable`) plus the overall run `status`. The
    `unsupported` / estimated-conversion gating stays global and is evaluated
    once the pricing core is ready.
- The view payload is safe to call repeatedly while the run is `running`; once
  `completed`, behavior (incl. range changes) is identical to today.
- AI keeps its existing `/sources/{id}` poll; its 10 branches now run in
  parallel inside the bounded pool.

## Layer 2 — Frontend (apps/web)

- After the `202`, **poll `GET /view`** on an interval (reuse the existing
  poll/backoff helpers in `dashboard-api.ts`). Render each section/chart the
  moment its `status` flips to `ready`; keep skeletons for `pending`; stop
  polling when the run is `completed`/`failed`.
- Replace the single `dataReady` boolean + timed 140 ms stagger in
  `use-section-statuses.ts` with **real per-section readiness** derived from the
  view payload. Existing skeleton components are reused untouched.
- The date-range-change fast path (re-derive `/view` from cached datasets, no
  Snowflake) is preserved — progressive logic applies only while `running`.
- `dashboard-contracts.ts` mirrors the new per-section `status` field; parsers
  tolerate `pending` sections (data absent).

## Demo mode

`build_demo_dashboard_dataset` is instant, so the demo `/view` reports every
section `ready` and the run `completed` on the first poll. Same contract, no
special-casing in the progressive frontend path.

## Tests

- **api:** `parallel_source_runner` — fan-out, the `current_account → org`
  dependency edge, concurrency cap, per-source failure → `unavailable` without
  failing the run, both-groups-unavailable → run `failed`. Injected `execute`
  keeps it deterministic.
- **api:** partial `/view` — section `status` transitions, pricing-core gating,
  `unsupported` still global, completed-run parity with today's output.
- **api:** async lifecycle — `POST` → `202 running`, poll → `completed`; update
  existing tests that assert a synchronous `completed` `POST`.
- **web:** render-on-arrival — sections reveal independently by `status`;
  skeletons persist for `pending`; polling stops at `completed`.
- **web:** range-change fast path unchanged after completion.

## Risks

- **Biggest change:** `POST` contract `completed` → `running`. Mitigated by
  updating the affected api tests and keeping the completed-run shape identical.
- **Thread-safety** on incremental repo writes — mitigated by the existing
  `RLock`; all incremental writes go through it.
- **Pricing-core coupling** means dollar-denominated charts can't render before
  org/rate data lands; parallelism keeps that window small (org queries are only
  3 and run concurrently).
- **Single-process assumption** is unchanged — the in-memory repo and the
  background thread pool both require one worker process, as today.
