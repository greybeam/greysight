# Deployment

Greysight is designed for a Vercel-hosted web app with a FastAPI backend that the
web app can reach.

## API URL Strategy

Local development uses:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

For deployed environments, choose one API URL strategy:

- Same-origin API routing, where the web app calls relative `/api/...` paths.
- An explicit deployed API base URL set in `NEXT_PUBLIC_API_BASE_URL`.

Leaving `NEXT_PUBLIC_API_BASE_URL` empty means the web client calls relative
same-origin `/api/...` paths. That requires committed routing, rewrite, or API
deployment support, which this repository does not currently include. For a
separate backend deployment, set an explicit deployed backend URL.

## Vercel Web Deployment

Deploy the web app from `apps/web`, or configure the Vercel project root to the
repo with the web workspace build command:

```bash
npm --workspace apps/web run build
```

Set browser-facing environment values in Vercel:

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
NEXT_PUBLIC_AUTH_REQUIRED=true
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
```

Keep backend-only values out of the browser environment.

## FastAPI Backend

The FastAPI app entrypoint is `apps/api/app/main.py`. The deployed backend needs
the same trusted environment values used locally for Supabase and, when
Snowflake is enabled, Snowflake credentials:

```bash
AUTH_REQUIRED=true
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=
DATA_SOURCE=demo
SNOWFLAKE_ACCOUNT=
SNOWFLAKE_USER=
SNOWFLAKE_ROLE=
SNOWFLAKE_WAREHOUSE=
SNOWFLAKE_DATABASE=SNOWFLAKE
SNOWFLAKE_SCHEMA=ACCOUNT_USAGE
SNOWFLAKE_PRIVATE_KEY_PATH=
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=
GREYSIGHT_DEFAULT_WINDOW_DAYS=30
GREYSIGHT_QUERY_TIMEOUT_SECONDS=60
STORAGE_PRICE_USD_PER_TB_MONTH=
```

Backend values must be exported, sourced, or injected by the backend hosting
environment. A root `.env.local` file is not automatically loaded by FastAPI.

The frontend passwordless flow can be configured with public Supabase variables.
With `AUTH_REQUIRED=true`, the backend validates bearer tokens through Supabase
Auth when `SUPABASE_URL` and `SUPABASE_ANON_KEY` are configured. If either value
is missing, bearer-token API calls are rejected fail-closed.

If deploying the API on Vercel, add the required Vercel routing or adapter
configuration before relying on production traffic. This repository does not
currently include a committed `vercel.json` or `vercel.ts` deployment adapter.

## Security Defaults

Use `AUTH_REQUIRED=true` for preview, staging, and production deployments. Store
Supabase service keys, JWT secrets, Snowflake key paths, and passphrases only in
the backend hosting environment.

Raw Snowflake rows are not persisted. Completed Snowflake runs persist only run
metadata, aggregate summaries, and chart-ready aggregate datasets with lazy
retention.

Savings estimate generation is post-MVP and is not included.
