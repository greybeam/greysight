# Security Model

Greysight separates browser-facing UI from trusted backend work. Next.js owns the
dashboard experience. FastAPI owns auth validation, org authorization, Snowflake
credential loading, approved SQL execution, metric calculation, aggregate dataset
persistence, and audit events.

## Auth

`AUTH_REQUIRED=false` enables local demo mode. Demo routes can run without a
Supabase session so contributors can view deterministic dashboard data without
external services.

`AUTH_REQUIRED=true` requires Supabase-backed authentication. Org-scoped API
routes must verify organization membership before returning or mutating data.
Supabase RLS in `supabase/migrations/` preserves member read access and limits
sensitive mutations to owners or admins.

The backend currently keeps Supabase JWT validation behind a verifier seam. With
`AUTH_REQUIRED=true` and no concrete verifier configured, API auth fails closed.

Shared preview, staging, and production environments should use
`AUTH_REQUIRED=true`.

## Secrets

Secrets stay in local environment files or the backend hosting environment.
Browser code may use only public values:

- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`

Backend-only values include:

- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_JWT_SECRET`
- `SNOWFLAKE_PRIVATE_KEY_PATH`
- `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`

Snowflake private key contents and private key paths must not be returned to the
frontend or logged.

## Snowflake Data Boundary

Snowflake access is backend-only. The API loads source definitions from
`sql/dashboard_sources.yml` and executes approved SQL files in `sql/snowflake/`.
Runtime values such as the analysis window are validated by FastAPI and passed
through Snowflake connector bind parameters.

API responses should not include raw SQL text, detailed customer query records,
private key paths, or private key contents.

Raw Snowflake rows are not persisted. Completed Snowflake runs persist run
metadata, aggregate summaries, and chart-ready aggregate datasets. Aggregate
dataset retention is lazy: expired persisted aggregate datasets are treated as
unavailable when accessed and cleaned up during normal run access.

## Audit Events

Authenticated validation, run creation, dataset retrieval, and deletion actions
write audit events with organization context. Local unauthenticated demo mode
skips org-scoped audit writes rather than writing null-organization audit rows.

## Non-Goals

Savings estimate generation is post-MVP and is not included in the dashboard.

Local demo mode is for local development. Deployed environments should configure
Supabase auth, use production secrets in the hosting platform, and avoid exposing
backend-only environment values to the browser.
