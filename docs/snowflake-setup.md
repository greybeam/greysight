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

## Dedicated User Setup (recommended)

When you connect Snowflake to Greysight through the in-app connect wizard, create
a dedicated, least-privilege service user and key-pair credential rather than
reusing a human login. The block below provisions a keypair-only `TYPE = SERVICE`
user, a role, and an XSMALL warehouse, then grants the role read access to the
Account Usage views through the `SNOWFLAKE.USAGE_VIEWER` database role.

The `GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER` grant gives the role read access
to the `SNOWFLAKE.ACCOUNT_USAGE` views the dashboard probes (the four views
listed under [Required Access](#required-access) above) — without granting any
account-administration or data-access privileges.

Generate the key pair first using Snowflake's
[key-pair authentication guide](https://docs.snowflake.com/en/user-guide/key-pair-auth#generate-the-private-keys),
then run:

```sql
-- Replace object names if needed.
SET user_name = 'GREYSIGHT_USER';
SET role_name = 'GREYSIGHT_ROLE';
SET warehouse_name = 'GREYSIGHT_WH';

USE ROLE USERADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($role_name)
  COMMENT = 'Used by Greysight';

CREATE USER IF NOT EXISTS IDENTIFIER($user_name)
  TYPE = SERVICE
  COMMENT = 'Used by Greysight';

-- Paste the single-line public key body only: no BEGIN/END PUBLIC KEY lines.
ALTER USER IDENTIFIER($user_name)
  SET RSA_PUBLIC_KEY = 'PASTE_BASE64_PUBLIC_KEY_BODY_HERE';

USE ROLE SYSADMIN;

CREATE WAREHOUSE IF NOT EXISTS IDENTIFIER($warehouse_name)
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Used by Greysight';

USE ROLE SECURITYADMIN;

GRANT ROLE IDENTIFIER($role_name) TO ROLE SYSADMIN;
GRANT ROLE IDENTIFIER($role_name) TO USER IDENTIFIER($user_name);
GRANT USAGE ON WAREHOUSE IDENTIFIER($warehouse_name) TO ROLE IDENTIFIER($role_name);

ALTER USER IDENTIFIER($user_name)
  SET DEFAULT_ROLE = $role_name
      DEFAULT_WAREHOUSE = $warehouse_name;

USE ROLE ACCOUNTADMIN;

GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER TO ROLE IDENTIFIER($role_name);

-- Optional billed-dollar views (requires ACCOUNTADMIN):
-- GRANT DATABASE ROLE SNOWFLAKE.ORGANIZATION_BILLING_VIEWER TO ROLE IDENTIFIER($role_name);
```

Paste the **private** key (PEM contents) into the connect wizard; the public key
goes to Snowflake via the `ALTER USER … SET RSA_PUBLIC_KEY` statement above. The
optional `SNOWFLAKE.ORGANIZATION_BILLING_VIEWER` grant unlocks billed dollars;
without it Greysight shows estimated dollars (see
[Organization Usage Access](#organization-usage-access-billed-dollars) below).

## Organization Usage Access (Billed Dollars)

Greysight V0 reads billed spend from `SNOWFLAKE.ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY`
and effective rates from `SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY`. These views
require an Organization Usage grant on the Greysight role:

```sql
grant database role SNOWFLAKE.ORGANIZATION_BILLING_VIEWER to role <GREYSIGHT_ROLE>;
```

Notes:

- The account locator used to filter Organization Usage rows is derived at run
  time via `select current_account()`; no extra environment variable is needed.
- If the grant is missing, Greysight degrades to estimated dollars computed from
  Account Usage credits and `ESTIMATED_CREDIT_PRICE_USD`; setup validation does
  not require Organization Usage access.
- Verified populated in the Greybeam dev account `GOPGUKF-JO19546`
  (locator `TU24199`) on 2026-06-10.

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
ESTIMATED_CREDIT_PRICE_USD=3.00
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
