# Snowflake Setup

Greysight reads Snowflake Account Usage metadata through approved backend SQL
assets. The frontend never receives Snowflake credentials, private key contents,
or private key paths.

## Required Access

Use a Snowflake user, role, and warehouse that can read these Account Usage
views:

- `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY`
- `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`
- `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY`
- `SNOWFLAKE.ACCOUNT_USAGE.DATABASE_STORAGE_USAGE_HISTORY`

SQL files live in `sql/snowflake/` and are registered by
`sql/dashboard_sources.yml`. The API executes only approved, bounded, read-only
source queries from that registry.

## Local Environment

Use `.env.example` as a checklist and set Snowflake mode. A root `.env.local` is
not automatically loaded by the FastAPI backend; export or source backend values
in the shell that starts the API, or inject them with your process manager.

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
GREYSIGHT_DEFAULT_WINDOW_DAYS=30
STORAGE_PRICE_USD_PER_TB_MONTH=
```

`SNOWFLAKE_PRIVATE_KEY_PATH` must point to a local private key file. Do not
commit the key file or inline private key material in environment files. The
repo ignores `.env.local`, `.env`, `*.pem`, `*.p8`, and `*.key`.

`AUTH_REQUIRED=false` is acceptable for local single-user Snowflake smoke tests.
Use `AUTH_REQUIRED=true` with Supabase values when testing org-scoped
authenticated flows.

## Snowflake Check

1. Start the app with `npm run dev`.
2. Open the dashboard.
3. Validate the Snowflake connection.
4. Start a dashboard run.

The backend validates required Account Usage access before real Snowflake runs.
Run datasets are built from approved source queries and returned as chart-ready
aggregate datasets.

Raw Snowflake rows are not persisted. FastAPI reads approved source results,
derives chart-ready aggregate datasets, and persists run metadata, aggregate
summaries, and aggregate datasets for retrieval.

Aggregate dataset retention is lazy: expired persisted aggregate datasets are
treated as unavailable when read or deleted during normal run access. There is no
separate background retention worker in the MVP.

Savings estimate generation is post-MVP and is not included.
