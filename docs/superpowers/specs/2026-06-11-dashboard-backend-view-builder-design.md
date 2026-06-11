# Dashboard Backend View Builder Design

## Purpose

The dashboard currently feels harder to understand than it should because
analytics logic is split between backend Snowflake source orchestration and
frontend TypeScript transforms. This design simplifies the ownership boundary:
the backend owns dashboard analytics and the frontend renders prepared views.

This is a refactor of where dashboard shaping happens. It is not a DuckDB
rewrite, persistence overhaul, or visual redesign.

## Goals

- Make the dashboard easier to reason about for future product changes.
- Move billed/estimated/demo analytics out of frontend transforms and into the
  FastAPI backend.
- Keep filter interactions fast for common relative windows.
- Support custom date ranges by asking the backend for a prepared view.
- In the first implementation slice, support only date ranges that are already
  inside the existing 100-day source fetch.
- Preserve existing Snowflake source-query safety: only approved bounded SQL
  assets from `sql/dashboard_sources.yml` execute against Snowflake.
- Keep credentials and billing source data server-side.

## Non-Goals

- Do not introduce DuckDB in this slice.
- Do not introduce browser DuckDB or IndexedDB caching.
- Do not persist run datasets to Supabase yet.
- Do not rewrite the Snowflake source SQL layer in slice 1.
- Do not support out-of-bounds custom date ranges in slice 1.
- Do not redesign the dashboard UI.

## Current State

Run analysis currently performs this flow:

1. The browser starts a dashboard run.
2. FastAPI executes registered Snowflake source SQL and stores aggregate source
   datasets in the in-memory run repository.
3. The browser fetches the source dataset payload.
4. `dashboard-transforms.ts` builds cards, chart series, rankings, dollar
   conversions, freshness labels, display caps, and empty states.
5. React components render the resulting view models.

The most confusing part is step 4. The frontend contains business and analytics
rules that are easier to inspect, test, and protect on the backend.

## Proposed Architecture

The new flow:

1. The browser starts a dashboard run.
2. FastAPI executes registered Snowflake source SQL and stores bounded aggregate
   source datasets in the existing run repository.
3. FastAPI builds prepared dashboard views from those source datasets.
4. The browser fetches prepared dashboard views.
5. React components render prepared view models and keep a small local cache of
   fetched views by date range.

The backend becomes the owner of:

- billed vs estimated vs demo mode selection
- rate-sheet fallback
- credit-to-dollar conversion
- included adjustment row inclusion
- freshness labels
- window/date-range filtering
- projected monthly semantics
- rankings
- detail row caps
- unsupported and empty-state decisions

The frontend becomes the owner of:

- selected date range state
- relative-window buttons
- date picker interactions
- cache lookup for already-fetched views
- rendering prepared cards, charts, tables, and messages

## API Contract

### Existing Run Creation

`POST /api/dashboard-runs`

This continues to create a run and fetch bounded source datasets from Snowflake.
For slice 1, the default run fetch remains the existing
100-day rolling window.

The run snapshot should persist source bounds:

- `source_start_date`: earliest source date available for the run.
- `source_end_date`: latest source date available for the run.

For slice 1, derive these bounds from the fetched source datasets. Do not add
explicit source-range request fields to this endpoint yet.

Out-of-bounds reruns are deferred to slice 2 because supporting explicit source
ranges requires safety-critical changes to all date-bearing Snowflake SQL
assets and bind plumbing:

- `sql/snowflake/database_storage_daily.sql`
- `sql/snowflake/org_spend_daily.sql`
- `sql/snowflake/query_compute_by_user_daily.sql`
- `sql/snowflake/rate_sheet_daily.sql`
- `sql/snowflake/service_spend_daily.sql`
- `sql/snowflake/warehouse_spend_daily.sql`
- `build_snowflake_dashboard_data()`
- `_fetch_source_group()`
- Snowflake bind validation

The response can stay as the existing `DashboardRun`.

### New Prepared View Endpoint

`GET /api/dashboard-runs/{run_id}/view`

Query parameters:

- `window_days`: optional enum-like integer for relative windows, initially
  `7`, `30`, or `90`.
- `start_date`: optional ISO date.
- `end_date`: optional ISO date.

Rules:

- Exactly one range mode is accepted: either `window_days` or
  `start_date` + `end_date`.
- If no range is provided, the endpoint returns the default 30-day view.
- Date ranges are inclusive.
- Relative windows always end on the run's data through-date, not wall-clock
  today. That through-date comes from metadata: billing freshness for billed and
  demo views, Account Usage freshness for estimated views.
- Custom ranges with `end_date` after the run's through-date should be clamped
  down to the through-date and served. The prepared view response should include
  the effective clamped `range.end_date`, not the requested future/today date.
  Do not append trailing zero-spend days.
- Date ranges must be inside the source data bounds stored for that run.
- If a requested range is outside the stored source data bounds, return a
  typed `range_out_of_bounds` error with the stored bounds so the frontend can
  show that broader ranges are not supported yet.

Projection:

- `projected_monthly` is always computed from the latest 30 calendar days of
  the stored source data ending at the run's through-date.
- This projection window is independent of the selected view range. A 7-day
  view and a custom 12-day view still project from the latest 30 days.
- The builder must receive the full stored source datasets plus the requested
  view range. It must not receive only a pre-filtered selected-range slice.
- If a future explicit-range run contains fewer than the latest 30 source days,
  preserve current transform behavior: evaluate the 30 calendar-day projection
  window and treat missing days as zero.

Response shape:

```json
{
  "schema_version": 1,
  "run": {},
  "range": {
    "mode": "relative",
    "window_days": 30,
    "start_date": "2026-05-10",
    "end_date": "2026-06-08"
  },
  "projection_range": {
    "start_date": "2026-05-10",
    "end_date": "2026-06-08"
  },
  "header": {},
  "unsupported": null,
  "total_spend": {},
  "compute_spend": {},
  "storage_spend": {},
  "service_spend": {},
  "detail_tables": {}
}
```

The response models should use these top-level snake_case keys. The frontend
should not need source rows to draw the dashboard.

### Existing Dataset Endpoint

`GET /api/dashboard-runs/{run_id}/datasets`

Keep this endpoint during the migration for debug compatibility and existing
tests. It should not be the main rendering path after this refactor.

## Backend Components

### `dashboard_view_models.py`

Defines Pydantic response models for the prepared dashboard view:

- range metadata
- header
- total spend
- compute spend
- storage spend
- service spend
- detail tables
- unsupported state

The response model should carry data mode as first-class state:
`"billed"`, `"estimated"`, or `"demo"`. The builder can share billed-row math
for billed and demo views internally, but model fields, labels, freshness, and
tests must treat demo explicitly.

These models should closely mirror the current TypeScript view-model types so
the frontend migration is mechanical.

### `dashboard_view_builder.py`

Pure backend module that builds a prepared view from:

- stored source datasets
- dashboard metadata
- selected date range

Responsibilities:

- Determine the through date from metadata.
- Resolve relative windows into inclusive `start_date` and `end_date`.
- Clamp custom range end dates down to the through-date before validating
  source bounds.
- Validate the effective requested range against stored source bounds.
- Use the full source datasets plus the selected range; do not require callers
  to pre-filter source rows.
- Filter source rows to the selected range for visible cards, charts, rankings,
  and details.
- Build the fixed latest-30-days projection range from the full source data.
- Build the rate index.
- Convert credits to dollars.
- Build total spend, compute spend, storage spend, service spend, and detail
  tables.
- Apply ranking and row caps.
- Build labels that depend on data mode, currency, and freshness.

This module should be deterministic and unit tested without FastAPI or
Snowflake.

### Route Changes

Add a route in `dashboard_runs.py`:

```text
GET /api/dashboard-runs/{run_id}/view
```

The route should:

1. Load the run and enforce organization membership.
2. Load stored source datasets and metadata.
3. Validate range parameters.
4. Call `dashboard_view_builder.py`.
5. Record a `dashboard_run.view_retrieved` audit event with `run_id`,
   `range_mode`, `start_date`, `end_date`, and `window_days`.
6. Return the prepared view response.

## Frontend Changes

The frontend should stop importing source dataset analytics helpers for normal
rendering.

`cost-dashboard.tsx` should:

- create a run
- fetch the default prepared view
- cache prepared views by a stable `(run_id, range)` key
- switch instantly when a relative/custom range is already cached
- fetch `/view` when the selected range is not cached
- render loading state only for uncached range fetches

Dashboard components should continue receiving prepared view models.

`dashboard-transforms.ts` should be reduced or removed after migration. Any
remaining frontend transform code should be presentation-only formatting that
does not know Snowflake billing semantics.

## Filter Behavior

Relative filters:

- `7`, `30`, and `90` should feel instant.
- After run creation, the frontend should fetch and cache the 30-day view, then
  prefetch and cache the 7-day and 90-day views. Clicking any relative filter
  after prefetch completes should not issue another request.

Custom date ranges:

- If a custom date range has already been fetched, switch instantly from cache.
- If it has not been fetched and is inside the run's source bounds, call the
  backend `/view` endpoint and cache the response.
- If it is outside the run's source bounds in slice 1, show that broader ranges
  are not supported yet and include the available source bounds.
- In slice 2, out-of-bounds ranges can start a new dashboard run with explicit
  source bounds, then fetch and cache the prepared view for that range.

## Error Handling

- Missing run: `404`.
- Missing/expired source datasets: existing expired/not-found behavior.
- Invalid range parameters: `422`.
- Range outside stored source bounds: `409` with a user-safe
  `range_out_of_bounds` code, stored source bounds, and copy explaining that
  broader ranges are not supported yet in slice 1.
- Mixed currency: return a prepared unsupported view, not a frontend-invented
  state.
- Missing Organization Usage: return an estimated prepared view when Account
  Usage is available.

## Testing

Backend tests:

- Unit tests for range resolution.
- Unit tests for billed totals including negative adjustment rows.
- Unit tests for demo mode labels, freshness, and billed-like use of demo
  Organization Usage rows.
- Unit tests for estimated mode fallback using rate sheet and configured
  estimated credit price.
- Unit tests proving projected monthly always uses the latest 30-day source
  window, independent of selected range and custom range.
- Unit tests proving custom range end dates after the through-date clamp to the
  through-date instead of returning `range_out_of_bounds`.
- Unit tests for storage daily spend and latest database rankings.
- Unit tests for ranking caps and detail row caps.
- Route tests for `/view` default, relative windows, custom ranges, invalid
  ranges, and out-of-bounds ranges.
- A temporary parity test comparing backend prepared-view output against the
  current TypeScript `buildDashboardViewModel()` output for the same fixture
  before deleting or reducing `dashboard-transforms.ts`.

Frontend tests:

- Run analysis fetches a prepared view.
- Relative window switch uses cache when present.
- Uncached custom date range calls `/view`.
- Out-of-bounds response shows the stored source bounds and explains that
  broader ranges are not supported yet.
- Components render prepared view models without importing analytics helpers.
- Cache keys include both `run_id` and range.

Verification:

- `npm run test`
- `npm run lint`
- `npm run typecheck`
- Manual live check: billed view, estimated fallback, custom range, relative
  window cache, demo parity.

## Migration Plan

1. Add backend view-model types and pure view-builder tests.
2. Port the existing `dashboard-transforms.ts` analytics logic into the backend
   view builder, preserving latest-30-day projection behavior.
3. Add source-bound storage to completed run snapshots.
4. Add the `/view` endpoint and view-retrieved audit event.
5. Update frontend API client and `cost-dashboard.tsx` to fetch prepared views
   and cache by `(run_id, range)`.
6. Add relative-window prefetch for 7-day and 90-day prepared views after the
   default 30-day view loads.
7. Keep out-of-bounds custom ranges as a clear unsupported state in slice 1.
8. Add backend/TypeScript transform parity coverage, then remove analytics
   responsibilities from `dashboard-transforms.ts`.
9. Keep the existing `/datasets` endpoint for debug compatibility.
10. Run full automated and live verification.

## Deferred Slice 2: Explicit Source Ranges

Slice 2 can add out-of-bounds custom date ranges by introducing explicit
Snowflake source date binds.

This slice must be planned separately because it touches the safety-critical
source-query layer:

- Add `start_date` and `end_date` request fields to run creation.
- Update Snowflake bind validation to allow date binds.
- Replace `window_days` date predicates in all date-bearing Snowflake SQL files
  with bounded inclusive/exclusive date predicates.
- Preserve the current default 100-day rolling behavior when explicit dates are
  absent.
- Verify live Snowflake results for billed, estimated, and demo parity.

## Success Criteria

- A reader can identify one backend module as the dashboard analytics owner.
- React dashboard components render prepared view models and do not perform
  billed/estimated/rate/ranking/window analytics.
- Relative window interactions feel instant after cache warmup.
- Custom ranges inside stored source bounds work through backend prepared-view
  requests.
- Custom ranges outside stored source bounds show an explicit unsupported state
  in slice 1.
- No Snowflake credentials or billing source-query logic move into the browser.
- No DuckDB runtime is introduced in this refactor.
