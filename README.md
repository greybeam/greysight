# Greysight

Greysight is an open source Snowflake cost observability tool. The MVP is a
locally testable Next.js and FastAPI dashboard with deterministic demo data,
optional Supabase auth, and optional Snowflake Account Usage queries.

## Quick Start

Install dependencies from the repository root:

```bash
npm install --ignore-scripts
```

Start the local web and API servers:

```bash
npm run dev
```

The web app runs at `http://localhost:3000`, and the FastAPI backend runs at
`http://localhost:8000`. With the default local settings, `DATA_SOURCE=demo` and
`AUTH_REQUIRED=false`, the dashboard can render demo data without Supabase or
Snowflake credentials.

## Project Structure

- `apps/web/`: Next.js dashboard, auth/org UI, browser API clients, and web tests.
- `apps/api/`: FastAPI routes, auth/org guards, Snowflake access, metric services, and API tests.
- `sql/snowflake/`: approved Snowflake Account Usage source queries.
- `sql/dashboard_sources.yml`: dataset registry for source and derived dashboard datasets.
- `supabase/migrations/`: Supabase schema, RLS policies, org memberships, audit events, and aggregate dataset tables.
- `docs/`: setup, Snowflake, deployment, security, specs, and implementation plans.

## Dashboard Architecture

The dashboard renders prepared view models from the FastAPI backend. Snowflake
or demo datasets are normalized on the API side, and
`apps/api/app/services/dashboard_view_builder.py` owns analytics, date-window
semantics, pricing, rankings, projections, and unsupported states. The Next.js
app fetches, validates, caches, and renders that prepared `DashboardView`
contract through `apps/web/src/lib/dashboard-contracts.ts`.

When adding a chart, use existing `DashboardView` fields when possible. If the
chart needs new derived numbers, add them to the backend view model and builder
first, then mirror the contract and render the new graphic in `apps/web`. If it
needs a new Snowflake source, add the approved SQL asset, registry entry, demo
data, backend builder logic, frontend rendering, and tests together.

## Docs

- Local development: `docs/local-development.md`
- Snowflake setup: `docs/snowflake-setup.md`
- Security model: `docs/security-model.md`
- Deployment: `docs/deployment.md`
- Dependency compatibility: `docs/dependency-compatibility.md`

Savings estimate generation is post-MVP and is not included in the dashboard.
