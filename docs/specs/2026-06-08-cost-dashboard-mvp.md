# Cost Dashboard MVP Spec

Date: 2026-06-08
Status: scoped for implementation planning

## Goal

Build the first Greysight MVP: a locally testable Snowflake cost dashboard app with Next.js, FastAPI, Supabase, demo data, and optional real Snowflake metadata queries.

This document is a product and technical scope spec. It is intentionally not the final code-complete execution plan. Before implementation, generate a separate `docs/superpowers/plans/YYYY-MM-DD-*.md` implementation plan with concrete test bodies, SQL bodies, code snippets, commands, and checkpoint-sized steps.

## Explicit Scope Decisions

These are intentional decisions made after reviewing `briefing.md`.

1. Savings estimate is post-MVP.
   - The first build is a cost dashboard only.
   - When savings estimates are added later, access to the estimate requires sharing a bounded workload profile with Greybeam.
   - The self-serve cost dashboard remains available without the savings-estimate sharing flow.

2. Browser DuckDB is deferred.
   - V1 returns chart-ready dashboard datasets from FastAPI.
   - DuckDB should be introduced only when dashboard interaction needs browser-local reshaping over shared datasets.
   - SQL source reuse remains mandatory through `sql/dashboard_sources.yml`.

3. Real auth is MVP scope, not a stub-only placeholder.
   - Demo mode may render the dashboard locally without Snowflake credentials.
   - Local demo mode may render an unauthenticated dashboard when `AUTH_REQUIRED=false`.
   - Shared preview, staging, and production deployments must require Supabase auth.
   - The MVP still requires passwordless Supabase login, user session handling, org creation/access, and org-scoped API authorization.

4. Local Snowflake credentials are single-connection only.
   - Local real-account testing reads one private key path from `.env.local`.
   - The database schema allows per-org connections, but production per-org secret storage and rotation remain explicit future work unless implemented in the first production pass.

5. Completed dashboard datasets are aggregate persisted data, not raw Snowflake usage data.
   - Async run polling requires completed-run datasets to survive beyond the request that created them.
   - Persist chart-ready aggregate datasets in Postgres with a short retention window.
   - Do not persist raw source rows, raw query text, individual query records, or unaggregated Snowflake `ACCOUNT_USAGE` responses.

## In Scope

- Root `npm run dev` starts local web and API.
- Demo mode renders deterministic dashboard data without Snowflake credentials.
- Real Snowflake mode uses `.env.local` with `SNOWFLAKE_PRIVATE_KEY_PATH`.
- Supabase passwordless login.
- Organization model and membership checks.
- Snowflake connection validation.
- Bounded cost-analysis run.
- Short-lived persisted aggregate dashboard datasets for completed runs.
- Dashboard for:
  - account spend
  - daily spend trend
  - warehouse spend
  - service spend
  - compute spend by user
  - storage usage by database
  - top warehouses
  - run status and user-safe errors
- Open-source setup docs and security model.

## Out Of Scope

- Savings estimate generation.
- Greybeam-reviewed estimate flow.
- Required Greybeam sharing consent flow.
- Worker or queue system.
- BI server.
- Snowflake Native App packaging.
- Multiple Snowflake connections per organization in the local/dev implementation.
- Persisting detailed Snowflake usage rows.

## Architecture

Use a small monorepo:

```text
apps/web/              Next.js + Tremor UI
apps/api/              FastAPI backend
sql/snowflake/         approved Snowflake metadata queries
sql/dashboard_sources.yml
supabase/migrations/   operational schema
docs/                  local setup, Snowflake setup, security model
```

FastAPI owns trusted and sensitive work:

- Supabase JWT/session validation.
- Organization authorization.
- Snowflake credential loading and validation.
- Snowflake query execution.
- Approved SQL registry loading.
- Cost metric calculations.
- Run status writes.
- Aggregate dashboard dataset writes and reads.
- Audit event writes.

Next.js owns user experience:

- Passwordless auth entry.
- App shell and dashboard routes.
- Connection setup UI.
- Run polling.
- Tremor charts, tables, cards, badges, and filters.
- Export/share UI when added.

## Local Development Contract

Root command:

```bash
npm run dev
```

Expected behavior:

- Starts Next.js on `http://localhost:3000`.
- Starts FastAPI on `http://localhost:8000`.
- `GET http://localhost:8000/health` returns healthy status.
- Dashboard renders demo data when `DATA_SOURCE=demo`.
- Local demo mode may skip auth only when `AUTH_REQUIRED=false`; this preserves clone-and-run onboarding for contributors.
- Dashboard can use Snowflake when `DATA_SOURCE=snowflake` and required env vars are configured.

Required root scripts:

```json
{
  "scripts": {
    "dev": "concurrently \"npm run dev:web\" \"npm run dev:api\"",
    "dev:web": "npm --workspace apps/web run dev",
    "dev:api": "npm --workspace apps/api run dev",
    "test": "npm run test:web && npm run test:api",
    "test:web": "npm --workspace apps/web run test",
    "test:api": "npm --workspace apps/api run test",
    "lint": "npm run lint:web && npm run lint:api",
    "lint:web": "npm --workspace apps/web run lint",
    "lint:api": "npm --workspace apps/api run lint",
    "typecheck": "npm --workspace apps/web run typecheck"
  },
  "workspaces": [
    "apps/web",
    "apps/api"
  ]
}
```

FastAPI local dev may use `uvicorn --reload`; this is local-only and must not be treated as Vercel deployment proof.

Local and deployed API base URLs:

- Local web uses `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`.
- Deployed web should prefer a relative same-origin API base path when the API is mounted under the same Vercel project.
- If Vercel deploys web and API as separate services, use Vercel-generated service routing environment variables or an explicit deployment-scoped API URL.
- The deployment spike must choose one of these shapes before dashboard work depends on it.

## Dependency Compatibility

Pin exact dependency versions during the scaffold; do not leave core framework packages on floating `latest` ranges.

Required compatibility spike:

- Verify the selected Tremor package works with the chosen Next.js and React majors.
- Prefer the current stable Next.js/React release line only if Tremor renders and builds cleanly.
- If Tremor blocks the current stable React/Next line, document the blocker and pin the newest compatible React/Next/Tremor combination.
- Run a build with at least one Tremor card, line chart, bar chart, and table before committing to dashboard implementation.
- Verify the root `npm run dev` path installs or syncs Python dependencies for `apps/api`; npm workspace install alone is not enough for Python packages.

The executable implementation plan must include exact versions for:

- `concurrently`
- `next`
- `react`
- `react-dom`
- `@tremor/react` or the chosen Tremor package
- `typescript`
- `fastapi`
- `uvicorn`
- `pydantic-settings`
- `snowflake-connector-python`
- `pytest`
- `ruff`

## Environment

Create `.env.example`:

```bash
DATA_SOURCE=demo
AUTH_REQUIRED=false
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=

SNOWFLAKE_ACCOUNT=
SNOWFLAKE_USER=
SNOWFLAKE_ROLE=
SNOWFLAKE_WAREHOUSE=
SNOWFLAKE_DATABASE=SNOWFLAKE
SNOWFLAKE_SCHEMA=ACCOUNT_USAGE
SNOWFLAKE_PRIVATE_KEY_PATH=
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=

GREYSIGHT_QUERY_TIMEOUT_SECONDS=60
GREYSIGHT_DEFAULT_WINDOW_DAYS=30
STORAGE_PRICE_USD_PER_TB_MONTH=
```

Update `.gitignore`:

```text
*.p8
*.key
```

`.env.local` remains ignored. It may contain a private key path; it must not contain committed key material.

## API Contract

Initial endpoints:

```text
GET /health
GET /api/dashboard-runs/demo
POST /api/dashboard-runs
GET /api/dashboard-runs/{run_id}
GET /api/dashboard-runs/{run_id}/datasets
DELETE /api/dashboard-runs/{run_id}
POST /api/snowflake/validate
```

Routing requirement:

- Constrain `run_id` to UUIDs or register `/api/dashboard-runs/demo` before dynamic run routes so the demo endpoint cannot be captured by `/{run_id}`.

`GET /health` response:

```json
{
  "status": "ok",
  "service": "greysight-api"
}
```

Dashboard run:

```json
{
  "id": "demo-run",
  "status": "completed",
  "source": "demo",
  "window_days": 30,
  "started_at": "2026-06-08T00:00:00Z",
  "completed_at": "2026-06-08T00:00:01Z",
  "error": null
}
```

Dashboard datasets:

```json
{
  "run": {
    "id": "demo-run",
    "status": "completed",
    "source": "demo",
    "window_days": 30
  },
    "summary": {
      "total_credits": 1240.5,
      "estimated_monthly_credits": 1240.5,
      "average_daily_credits": 41.35,
      "warehouse_count": 5,
      "top_warehouse_name": "BI_WH",
      "storage_bytes": 8500000000000,
    "estimated_monthly_storage_cost_usd": null
  },
  "datasets": {
    "account_spend_daily": [],
    "warehouse_spend_daily": [],
    "service_spend_daily": [],
    "query_compute_by_user_daily": [],
    "database_storage_daily": []
  }
}
```

Metric rules:

- Exclude the current UTC usage date from default headline metric calculations because Snowflake `ACCOUNT_USAGE` views have natural latency and the trailing day may be incomplete.
- Define the complete analysis window as the requested trailing window excluding the current UTC date.
- `total_credits = sum(account_spend_daily.credits_used)` for complete days in the selected window.
- `average_daily_credits = total_credits / complete_day_count`, not days-with-data count.
- `estimated_monthly_credits = average_daily_credits * 30`.
- `warehouse_count = count(distinct warehouse_name)` from `warehouse_spend_daily`.
- `top_warehouse_name` is the warehouse with the largest summed `credits_used`.
- Storage is represented in bytes by default.
- Storage is a gauge, not a flow: `storage_bytes = sum(database_storage_daily.average_database_bytes + database_storage_daily.average_failsafe_bytes)` for the latest complete usage date.
- Storage trend charts plot the same per-day sum for each usage date.
- Storage cost is `null` unless `STORAGE_PRICE_USD_PER_TB_MONTH` or another explicit pricing config is supplied.
- `estimated_monthly_storage_cost_usd = (storage_bytes / 1_000_000_000_000) * STORAGE_PRICE_USD_PER_TB_MONTH`.
- Account-level spend is authoritative for headline totals.
- Warehouse-level spend is a breakdown of warehouse-metered compute/cloud-services credits only and is not expected to reconcile exactly to account-level total credits.
- The UI must label this distinction so users do not expect account total credits to equal the sum of warehouses.

Dataset lifecycle rules:

- `POST /api/dashboard-runs` creates a run, executes approved source queries, derives chart-ready aggregate datasets, persists the run summary and aggregate datasets, then returns run status.
- `GET /api/dashboard-runs/{run_id}` returns persisted run metadata and summary.
- `GET /api/dashboard-runs/{run_id}/datasets` returns persisted aggregate dashboard datasets for the completed run.
- `DELETE /api/dashboard-runs/{run_id}` deletes the run summary and persisted aggregate datasets for authorized org members.
- Demo mode may regenerate deterministic datasets instead of reading persisted datasets.
- Snowflake mode must not re-run Snowflake queries on every datasets fetch unless the user explicitly starts a new run.
- Persisted aggregate datasets must include retention metadata.
- Because workers and queues are out of scope, V1 uses lazy retention: expired datasets are treated as unavailable and purged on read/write paths.
- Explicit run deletion removes the run summary and all persisted aggregate datasets immediately.

## SQL Source Registry

Create `sql/dashboard_sources.yml`:

```yaml
sources:
  warehouse_spend_daily:
    sql: snowflake/warehouse_spend_daily.sql
    grain:
      - usage_date
      - warehouse_name
    required_columns:
      - usage_date
      - warehouse_name
      - credits_used
      - credits_used_compute
      - credits_used_cloud_services
    feeds:
      - top_warehouses_table
      - warehouse_spend_trend_chart
      - warehouse_share_bar_chart
  service_spend_daily:
    sql: snowflake/service_spend_daily.sql
    grain:
      - usage_date
      - service_type
    required_columns:
      - usage_date
      - service_type
      - credits_used
    feeds:
      - total_spend_card
      - average_daily_spend_card
      - daily_spend_trend_chart
      - service_spend_bar_chart
      - service_spend_table
  query_compute_by_user_daily:
    sql: snowflake/query_compute_by_user_daily.sql
    grain:
      - usage_date
      - user_name
    required_columns:
      - usage_date
      - user_name
      - credits_attributed_compute
      - query_count
    feeds:
      - user_compute_table
      - user_compute_bar_chart
  database_storage_daily:
    sql: snowflake/database_storage_daily.sql
    grain:
      - usage_date
      - database_name
    required_columns:
      - usage_date
      - database_name
      - average_database_bytes
      - average_failsafe_bytes
    feeds:
      - storage_by_database_table
      - storage_trend_chart

derived_datasets:
  account_spend_daily:
    derives_from: service_spend_daily
    grain:
      - usage_date
    required_columns:
      - usage_date
      - credits_used
    feeds:
      - total_spend_card
      - average_daily_spend_card
      - daily_spend_trend_chart
```

Source intent:

- `service_spend_daily` uses `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` at service-type grain.
- `account_spend_daily` is derived by rolling up `service_spend_daily`; it does not issue a second Snowflake query.
- `warehouse_spend_daily` uses warehouse metering account usage data.
- `query_compute_by_user_daily` uses query attribution data where available.
- `database_storage_daily` uses database storage usage history.

Rules:

- SQL files must contain only approved read-only metadata queries.
- SQL files must use bounded time windows.
- Runtime parameters must be validated by FastAPI.
- Runtime parameters must be passed to the Snowflake connector with bind parameters, not string interpolation.
- Use named binds such as `%(window_days)s` for Python connector `pyformat` binding.
- Do not accept arbitrary SQL from the frontend.
- Do not put SQL strings inside chart components.
- Do not return raw query text or individual customer query records to the frontend.

## Supabase Schema

Create `supabase/migrations/202606080001_initial_cost_dashboard.sql` with:

- `organizations`
- `organization_memberships`
- `snowflake_connections`
- `connection_validation_results`
- `analysis_runs`
- `analysis_run_datasets`
- `audit_events`
- `dashboard_filter_preferences`

Required design:

- `organizations` has `id`, `name`, `created_at`.
- `organization_memberships` has `organization_id`, `user_id`, `role`, `created_at`.
- `snowflake_connections` has org id, display name, account identifier, username, role, warehouse, credential reference, status, timestamps.
- `connection_validation_results` has connection id, status, JSON checks, error code, user-safe message, created timestamp.
- `analysis_runs` has org id, optional connection id, source, status, window, aggregate summary JSON, error code, user-safe message, timestamps.
- `analysis_run_datasets` has run id, dataset key, aggregate rows JSON, row count, retention expiration timestamp, created timestamp.
- `audit_events` stores org id, user id, event type, redacted metadata, timestamp.
- `dashboard_filter_preferences` stores org/user filter JSON.

Migration requirements:

- Enable row-level security in the first migration for every public table.
- Add membership-based RLS policies for authenticated users.
- Do not rely only on FastAPI authorization because Supabase exposes browser-accessible APIs through the anon key.
- Add indexes on every `organization_id` foreign key.
- Add an index on `analysis_runs(organization_id, created_at desc)`.
- Add an index on `analysis_run_datasets(run_id, dataset_key)`.
- Add an `updated_at` trigger for tables with `updated_at`.
- Add a lazy-retention purge query path for expired `analysis_run_datasets`; do not introduce a worker, queue, or scheduler for V1 retention.

FastAPI must still enforce org membership on every org-scoped endpoint.

## Security Requirements

- Frontend never receives Snowflake credentials, private key paths, or private key contents.
- FastAPI reads `SNOWFLAKE_PRIVATE_KEY_PATH` only on the server.
- Do not log secrets, private key paths, full SQL responses, raw query text, or detailed usage rows.
- Only approved SQL assets may run.
- Every Snowflake query must be bounded by a time window.
- Persist run status and aggregate summaries only.
- Persist chart-ready aggregate datasets with short retention so completed async runs can be viewed.
- Detailed Snowflake usage results stay ephemeral.
- Every org-scoped API endpoint validates organization membership.
- Audit connection creation, validation, analysis run creation, dataset retrieval, and deletion events.
- Audit export events when export endpoints are added.

## Dashboard Requirements

Initial dashboard components:

- API health indicator.
- Auth/session state.
- Run status indicator.
- Total credits card.
- Average daily credits card.
- Estimated monthly credits card.
- Warehouse count card.
- Daily spend line chart.
- Warehouse spend bar chart.
- Service spend bar chart.
- Compute by user table or bar chart.
- Storage by database table or chart.
- Top warehouses table.

Use Tremor for cards, charts, badges, tables, and filters. The first screen should be the working dashboard, not a marketing landing page.

## Deployment Spike

Do this before building the full dashboard.

Questions to answer:

- Should this be one Vercel project or separate web/API Vercel projects?
- Can the selected monorepo layout deploy FastAPI without custom routing hacks?
- Which FastAPI entrypoint path should be used for Vercel?
- Should config be `vercel.ts` or static `vercel.json`?
- What function max duration is available in the target Vercel plan?
- Should deployed browser calls use same-origin `/api`, Vercel service routing variables, or a separate API URL?

Current preferred spike:

- Use `vercel.ts` for typed project configuration unless it causes friction.
- Make the FastAPI app export discoverable by Vercel through one of Vercel's supported FastAPI entrypoints or a `pyproject.toml` app script.
- Confirm a minimal health endpoint deploys before wiring Snowflake.
- Document whether `apps/api/app/main.py` is viable as-is or whether the backend needs an adapter entrypoint such as `apps/api/app/app.py`, `apps/api/app/server.py`, or a script mapping.
- Document the local and deployed `NEXT_PUBLIC_API_BASE_URL` strategy.

## Implementation Plan Requirements

The executable plan generated from this spec must include:

- Exact test code for each test-first step.
- Exact SQL for each `sql/snowflake/*.sql` file.
- Exact bind-parameter usage for every SQL runtime parameter.
- Exact Python code skeletons for FastAPI routes and services.
- Exact TypeScript types for dashboard contracts.
- Exact web test code for dashboard contract parsing and demo dashboard rendering.
- Commands to verify each failing test before implementation.
- Commands to verify passing tests after implementation.
- A deployment spike before large frontend/dashboard work.
- A dependency compatibility spike before relying on Tremor components.
- A root local-dev dependency step that installs/syncs both npm packages and Python packages.
- The selected Python dependency manager and exact npm wrapper scripts for API dev/test/lint commands.
- Checkpoint-sized tasks that can be delegated safely.

## Acceptance Criteria

- `npm run dev` starts both local apps from the repo root.
- `http://localhost:3000` renders the cost dashboard.
- `http://localhost:8000/health` returns `{"status":"ok","service":"greysight-api"}`.
- Dashboard works with demo data and no Snowflake credentials.
- Local demo dashboard can render unauthenticated when `AUTH_REQUIRED=false`.
- Auth is required when `AUTH_REQUIRED=true`.
- `.env.local` can point to a local Snowflake private key file with `SNOWFLAKE_PRIVATE_KEY_PATH`.
- Supabase passwordless login works.
- A signed-in user can create or access an organization.
- Org-scoped API routes reject unauthorized users.
- FastAPI owns all Snowflake access.
- SQL assets are reusable and registered in `sql/dashboard_sources.yml`.
- Dashboard includes account, warehouse, service, user-compute, and database-storage views.
- Completed Snowflake dashboard datasets remain viewable after polling without re-running Snowflake queries.
- RLS is enabled in the first Supabase migration for all public tables.
- SQL runtime parameters use connector bind parameters.
- No savings estimate UI or backend path exists in the MVP.
- API tests cover registry validation, cost metric calculations, health endpoint, demo dashboard run, auth helper behavior, and org authorization.
- Web tests cover dashboard contract parsing and demo dashboard rendering.
- Docs explain local development, Snowflake setup, deployment shape, and security model.

## Verification Commands

Run from repo root after implementation:

```bash
rtk npm run test
rtk npm run lint
rtk npm run typecheck
rtk npm run dev
```

Manual verification:

- Visit `http://localhost:3000`.
- With `AUTH_REQUIRED=false`, confirm the local demo dashboard loads without Supabase setup.
- With `AUTH_REQUIRED=true`, sign in with passwordless Supabase auth.
- Create or access an organization.
- Confirm API health indicator shows healthy.
- Set `DATA_SOURCE=snowflake` and valid Snowflake env vars in `.env.local`.
- Restart `npm run dev`.
- Validate Snowflake connection.
- Run a dashboard analysis.
- Confirm dashboard renders aggregate cost data without exposing secrets, private key paths, raw query text, or detailed raw rows.
