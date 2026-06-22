# Greysight

Greysight is an open source dashboard for Snowflake costs.

The short version: it shows where spend is going without making you start from
raw Account Usage views. The backend reads approved Snowflake metadata queries,
turns them into dashboard views, and the Next.js app renders those views.

Version `0.0.1` is intentionally narrow. It covers total spend, service spend,
warehouse spend, storage spend, AI spend, capacity balance, and early
organization and auth flows. It is not a recommendations engine yet.

<!--
Dashboard screenshot placeholder:

![Greysight dashboard](docs/images/dashboard-overview.png)
-->

## What it shows

- Total Snowflake spend over the selected window.
- Spend by service, warehouse, user, database, and AI consumption type.
- Warehouse idle percentage, based on attributed query compute.
- Capacity balance, when Organization Usage data is available.
- Billed dollars from Organization Usage, with estimated dollars as a fallback.
- A demo dashboard that works before you connect anything.

## Quick start

Prerequisites: Node.js 20+, npm, and `uv` with Python 3.12 available for the
FastAPI workspace.

From the repository root:

```bash
npm install --ignore-scripts
npm run dev
```

Open `http://localhost:3000`.

The web app runs on `:3000`. The FastAPI backend runs on `:8000`.

A fresh checkout uses demo mode:

```bash
DATA_SOURCE=demo
AUTH_REQUIRED=false
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

You do not need Supabase or Snowflake credentials to try the dashboard.

## Spinning up a local instance:

Copy `.env.example` to `.env`, set
`DATA_SOURCE=snowflake`, keep `AUTH_REQUIRED=false`, and fill in the Snowflake
connection values:

```bash
DATA_SOURCE=snowflake
AUTH_REQUIRED=false
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
SNOWFLAKE_ACCOUNT=
SNOWFLAKE_USER=
SNOWFLAKE_ROLE=
SNOWFLAKE_WAREHOUSE=
SNOWFLAKE_DATABASE=SNOWFLAKE
SNOWFLAKE_SCHEMA=ACCOUNT_USAGE
SNOWFLAKE_PRIVATE_KEY_PATH=/absolute/path/to/key.p8
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=
```

Ensure the role you authenticate with has access to `ACCOUNT_USAGE` and `ORGANIZATION_USAGE` views.

Then restart:

```bash
npm run dev
```

## How the data moves

Greysight keeps the dashboard data path boring on purpose:

1. SQL files live in [sql/snowflake](sql/snowflake).
2. Dataset keys are registered in
   [sql/dashboard_sources.yml](sql/dashboard_sources.yml).
3. FastAPI runs the approved sources or loads demo data.
4. The backend computes metrics and builds the dashboard view model.
5. The Next.js app fetches, validates, caches, and renders that view model.

The frontend should not invent analytics on its own. If a chart needs a new
derived number, add it to the backend dashboard view contract first.

## Project layout

```text
apps/web/              Next.js dashboard, auth UI, browser API clients, tests
apps/api/              FastAPI backend, Snowflake access, metrics, route tests
sql/snowflake/         Approved read-only Snowflake SQL assets
sql/dashboard_sources.yml
                       Dataset registry for dashboard sources
supabase/migrations/   Supabase schema, RLS, org membership, credential storage
docs/                  Local setup, Snowflake setup, security, deployment notes
```

## Development

Useful commands from the repository root:

```bash
npm run dev          # web :3000 + API :8000
npm run test         # Vitest + pytest
npm run lint         # ESLint + ruff
npm run typecheck    # TypeScript
```

Targeted checks:

```bash
npm run test:web
npm run test:api
npm --workspace apps/web run build
```

The API tests do not call Snowflake or Supabase. Demo mode also stays local, so
you can work on the dashboard without external credentials.

## Docs

- [Local development](docs/local-development.md)
- [Snowflake setup](docs/snowflake-setup.md)
- [Security model](docs/security-model.md)
- [Deployment](docs/deployment.md)
- [Dependency compatibility](docs/dependency-compatibility.md)

## Contributing

Greysight is young. Small changes are easiest to review.

For dashboard work, a complete change usually touches the SQL source or backend
metric, the prepared view contract, the frontend view, demo data, and tests.

Keep analytics in the backend and presentation in the frontend. Keep secrets out
of the browser. Supabase owns auth. The backend owns authorization and Snowflake
access.

## License

Apache-2.0. See [LICENSE](LICENSE).
