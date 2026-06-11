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
- Preserve existing Snowflake source-query safety: only approved bounded SQL
  assets from `sql/dashboard_sources.yml` execute against Snowflake.
- Keep credentials and billing source data server-side.

## Non-Goals

- Do not introduce DuckDB in this slice.
- Do not introduce browser DuckDB or IndexedDB caching.
- Do not persist run datasets to Supabase yet.
- Do not rewrite the Snowflake source SQL layer except where date-range binds
  are required.
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

- billed vs estimated mode selection
- rate-sheet fallback
- credit-to-dollar conversion
- included adjustment row inclusion
- freshness labels
- window/date-range filtering
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
For the first implementation, the default run fetch remains the existing
100-day rolling window.

This endpoint should also accept an optional explicit source range:

- `start_date`: optional ISO date.
- `end_date`: optional ISO date.

If both dates are provided, Snowflake source queries should fetch that inclusive
range instead of the default rolling 100-day window. If neither date is
provided, behavior stays unchanged.

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
- Date ranges must be inside the source data bounds stored for that run.
- If a requested range is outside the stored source data bounds, return a
  typed `range_out_of_bounds` error with the stored bounds so the frontend can
  start a broader run with the requested explicit range.

Response shape:

```json
{
  "schema_version": 1,
  "run": {},
  "range": {
    "mode": "relative",
    "window_days": 30,
    "start_date": "2026-05-12",
    "end_date": "2026-06-10"
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
- Filter source rows to the selected range.
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
5. Return the prepared view response.

## Frontend Changes

The frontend should stop importing source dataset analytics helpers for normal
rendering.

`cost-dashboard.tsx` should:

- create a run
- fetch the default prepared view
- cache prepared views by a stable range key
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
- If it is outside the run's source bounds, start a new dashboard run with the
  requested explicit source range, then fetch and cache the prepared view for
  that range. The backend must still use bounded Snowflake SQL with date bind
  parameters.

## Error Handling

- Missing run: `404`.
- Missing/expired source datasets: existing expired/not-found behavior.
- Invalid range parameters: `422`.
- Range outside stored source bounds: `409` with a user-safe
  `range_out_of_bounds` code and stored source bounds.
- Mixed currency: return a prepared unsupported view, not a frontend-invented
  state.
- Missing Organization Usage: return an estimated prepared view when Account
  Usage is available.

## Testing

Backend tests:

- Unit tests for range resolution.
- Unit tests for billed totals including negative adjustment rows.
- Unit tests for estimated mode fallback using rate sheet and configured
  estimated credit price.
- Unit tests for storage daily spend and latest database rankings.
- Unit tests for ranking caps and detail row caps.
- Route tests for `/view` default, relative windows, custom ranges, invalid
  ranges, and out-of-bounds ranges.

Frontend tests:

- Run analysis fetches a prepared view.
- Relative window switch uses cache when present.
- Uncached custom date range calls `/view`.
- Out-of-bounds response shows rerun guidance.
- Components render prepared view models without importing analytics helpers.

Verification:

- `npm run test`
- `npm run lint`
- `npm run typecheck`
- Manual live check: billed view, estimated fallback, custom range, relative
  window cache, demo parity.

## Migration Plan

1. Add backend view-model types and pure view-builder tests.
2. Port the existing `dashboard-transforms.ts` analytics logic into the backend
   view builder.
3. Add the `/view` endpoint.
4. Update frontend API client and `cost-dashboard.tsx` to fetch prepared views.
5. Remove analytics responsibilities from `dashboard-transforms.ts`.
6. Keep the existing `/datasets` endpoint for debug compatibility.
7. Run full automated and live verification.

## Success Criteria

- A reader can identify one backend module as the dashboard analytics owner.
- React dashboard components render prepared view models and do not perform
  billed/estimated/rate/ranking/window analytics.
- Relative window interactions feel instant after cache warmup.
- Custom ranges work through backend prepared-view requests.
- No Snowflake credentials or billing source-query logic move into the browser.
- No DuckDB runtime is introduced in this refactor.
