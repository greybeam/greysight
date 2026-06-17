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

## Railway API Deployment

The committed artifacts build and run the backend on Railway:

- `apps/api/Dockerfile` — uv-based, reproducible image. Installs from the
  `--frozen` lockfile and starts `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- `.dockerignore` (repo root) — keeps node_modules, the web app, venvs, caches,
  and any local env/secret files out of the build context.
- `railway.json` (repo root) — selects the Dockerfile builder
  (`apps/api/Dockerfile`) and points the health check at `/health`.

**The build context is the repo root, not `apps/api`.** The API loads the shared
SQL registry from `<repo-root>/sql` at runtime, which lives outside `apps/api`,
so the image must include it. The Dockerfile mirrors the repo layout
(`/app/apps/api` + `/app/sql`) so the registry's path resolution keeps working.

Setup:

1. Create a Railway service from this repo and leave **Root Directory** at the
   repo root (do not set it to `apps/api`). Railway reads `railway.json` at the
   root and builds `apps/api/Dockerfile` with the repo root as the context.
2. Set the service environment variables (below). Railway injects `$PORT`
   automatically — do not set it.
3. Deploy. Railway waits for `/health` to return `200` before routing traffic.

To build the same image locally (context = repo root, explicit Dockerfile path):

```bash
docker build -f apps/api/Dockerfile -t greysight-api .
docker run --rm -e PORT=8080 -p 8080:8080 greysight-api   # then GET /health
```

### Backend environment variables (private auth'd deploy)

```bash
AUTH_REQUIRED=true
DATA_SOURCE=demo                     # demo first to validate hosting + auth + CORS;
                                     # switch to snowflake once per-org creds are wired
SUPABASE_URL=                        # https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=                   # Publishable key (sb_publishable_…); verifies bearer tokens
SUPABASE_SERVICE_ROLE_KEY=           # Secret key (sb_secret_…); membership lookup, never browser-exposed
GREYSIGHT_CORS_ALLOWED_ORIGINS=["https://your-web-app.vercel.app"]
```

Notes:

- **No deployment-level `SNOWFLAKE_*`.** Those are self-host (single-tenant)
  mode only; per-org Snowflake credentials are resolved per organization, not
  from the API host environment.
- `SUPABASE_JWT_SECRET` is **not** read by the API — token verification calls
  Supabase's `/auth/v1/user` with the anon key. Leave it unset here.
- `GREYSIGHT_CORS_ALLOWED_ORIGINS` is parsed as **JSON**, not a comma-separated
  string. Use a JSON array (`["https://a.app","https://b.app"]`). It defaults to
  `["http://localhost:3000"]` when unset, so the deployed web origin must be set
  explicitly or browser calls are blocked by CORS.

### After the API is up

Point the Vercel web app at the Railway URL and rebuild:

```bash
NEXT_PUBLIC_API_BASE_URL=https://<service>.up.railway.app
```

Then add that same Vercel origin to `GREYSIGHT_CORS_ALLOWED_ORIGINS` on the
Railway service.

## Security Defaults

Use `AUTH_REQUIRED=true` for preview, staging, and production deployments. Store
Supabase service keys, JWT secrets, Snowflake key paths, and passphrases only in
the backend hosting environment.

Raw Snowflake rows are not persisted. Completed Snowflake runs persist only run
metadata, aggregate summaries, and chart-ready aggregate datasets with lazy
retention.

Savings estimate generation is post-MVP and is not included.
