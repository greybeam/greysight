/**
 * Codex-reviewed least-privilege setup SQL (spec §4.3, 2026-06-16).
 *
 * Creates a programmatic keypair-only service user, a least-privilege role, and
 * an auto-resuming warehouse, then grants the `SNOWFLAKE.USAGE_VIEWER` database
 * role. No `MUST_CHANGE_PASSWORD` and no `BEGIN…COMMIT` wrapper (Snowflake DDL
 * auto-commits). The RSA public key is set via `ALTER USER` with the PEM
 * header/footer stripped and the base64 body on one line.
 */
export const SNOWFLAKE_SETUP_SQL = `-- Replace object names if needed.
SET user_name = 'GREYBEAM_USER';
SET role_name = 'GREYBEAM_ROLE';
SET warehouse_name = 'GREYBEAM_WH';

USE ROLE USERADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($role_name)
  COMMENT = 'Used by Greybeam';

CREATE USER IF NOT EXISTS IDENTIFIER($user_name)
  TYPE = SERVICE
  COMMENT = 'Used by Greybeam';

-- Paste the single-line public key body only: no BEGIN/END PUBLIC KEY lines.
ALTER USER IDENTIFIER($user_name)
  SET RSA_PUBLIC_KEY = 'PASTE_BASE64_PUBLIC_KEY_BODY_HERE';

USE ROLE SYSADMIN;

CREATE WAREHOUSE IF NOT EXISTS IDENTIFIER($warehouse_name)
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Used by Greybeam';

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
`;
