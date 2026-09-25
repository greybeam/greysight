# Auto Savings — local DuckDB backend

The default Auto Savings backend is Supabase; that stays the recommended
option for multi-tenant deployments. For a **single-user local trial** the
worker can persist state in a single DuckDB file instead, so you can
suspend a test warehouse without provisioning Supabase.

Why DuckDB (not SQLite): the codebase commits to a "DuckDB refactor" in
`apps/web/src/lib/section-filters.ts` and `apps/web/src/lib/currency-format.ts`
that migrates dashboard analytics from the frontend into a server-side
DuckDB engine. Landing the local Auto Savings backend on DuckDB lets the
follow-up local dashboard-data PR reuse the same embedded file.

> **Single-user only.** RLS lives in the Supabase backend. DuckDB mode
> assumes exactly one operator on one Snowflake account; do not use it in
> a shared or production environment.

## Requirements

- Python 3.12 with `uv`.
- Snowflake role that can `MANAGE WAREHOUSES` on the test warehouse (see
  [`docs/automated-savings.md`](automated-savings.md#snowflake-access)).
- A writable path for the local DB file, e.g. `./greysight-local.duckdb`.

## Setup

1. Copy `.env.example` to `.env` and set:

    ```bash
    DATA_SOURCE=snowflake
    AUTH_REQUIRED=false
    NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

    SNOWFLAKE_ACCOUNT=…
    SNOWFLAKE_USER=…
    SNOWFLAKE_ROLE=…
    SNOWFLAKE_WAREHOUSE=…
    SNOWFLAKE_PRIVATE_KEY_PATH=/absolute/path/to/key.p8
    SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=

    AUTO_SAVINGS_BACKEND=duckdb
    AUTO_SAVINGS_DUCKDB_PATH=./greysight-local.duckdb
    ```

    Do not set `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY`; the worker
    refuses to start in DuckDB mode if either is present.

2. Bootstrap enrollment state.

    ```bash
    cd apps/auto-savings
    uv run dev.py agree
    uv run dev.py enroll --warehouse GREYSIGHT_TEST_WH \
      --warehouse-created-on 2026-06-01T00:00:00Z
    uv run dev.py enable --warehouse GREYSIGHT_TEST_WH
    uv run dev.py enable-global
    ```

    `warehouse-created-on` must match the exact `created_on` Snowflake
    reports for that warehouse — copy it from `SHOW WAREHOUSES LIKE
    'GREYSIGHT_TEST_WH'`. This is the same versioned identity the
    Supabase backend enforces; a mismatch fails the authorization check.

3. Start the worker.

    ```bash
    uv run dev.py
    ```

4. In Snowflake, `ALTER WAREHOUSE GREYSIGHT_TEST_WH RESUME;` and leave
   it idle for at least 62 seconds. The worker logs one suspend event
   and then holds steady.

5. Review the audit trail:

    ```bash
    uv run dev.py events
    ```

## Safety semantics

The DuckDB backend runs the **same** authorization contract as the
Supabase backend:

- `authorize_suspend` is a single-transaction equality check on
  `(organization_id, warehouse_name, warehouse_created_on, updated_at)`
  under DuckDB SERIALIZABLE isolation.
- Any bootstrap change to the enrollment row bumps `updated_at`,
  invalidating in-flight authorizations that used the old value.
- Events are append-only and CHECK-constrained to `action='suspend'`,
  `reason='idle'`.
- The worker never issues anything but `ALTER WAREHOUSE … SUSPEND`.

## Deleting local state

`rm ./greysight-local.duckdb`. That is the entire uninstall.

## Switching back to Supabase

Set `AUTO_SAVINGS_BACKEND=supabase` (or unset it — Supabase is the
default) and restore `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`. The
DuckDB file becomes inert; nothing reads it.
