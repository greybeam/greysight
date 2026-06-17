# Per-Org Snowflake Onboarding — Design

**Status:** Approved (design); setup SQL Codex-reviewed (see §4.3).
**Date:** 2026-06-16

## Goal

Let a signed-in user self-serve: create an organization and connect their own
Snowflake account by entering connection details + an RSA private key, with the
connection validated before anything is persisted. After a successful connect,
the org's dashboard runs against *that org's* Snowflake credentials.

This moves Greysight from "login for operator-seeded orgs" (Spec A) to
self-service multi-tenant onboarding. Only Snowflake **metadata** access
(`ACCOUNT_USAGE` views) is required; no usage data or query results leave the
customer account.

## Scope

- **In:** per-org Snowflake credential storage (Supabase Vault), a self-service
  onboarding endpoint that validates before creating, a per-org credential
  resolver, the client refactor needed to use in-memory PEM content, an
  org-admin reconnect/rotate path, Vault-secret teardown semantics, audit
  **hooks** at credential call sites, and the web onboarding wizard.
- **Out (tracked fast-follows):** converting the in-memory `audit_events` store
  to a durable table; an org switcher / multi-org UI; Snowflake OAuth; automated
  periodic re-validation jobs.

## Audience & key principle

Multi-tenant SaaS is primary; OSS self-host is secondary. Per-org Snowflake
credentials override the deployment-level `.env`, falling back to `.env` **only
when `auth_required=false`** (single-tenant self-host / demo). In authenticated
SaaS mode the resolver **fails closed** — never falls back to `.env` — so one
org can never be served another tenant's (or the deployment's) Snowflake data.

---

## §1 — Data model & secret storage

New migration adds `organization_snowflake_connections` (non-secret metadata
only; one row per org in v1, but no schema assumption that blocks many later):

| Column | Notes |
|---|---|
| `organization_id uuid` PK | FK → `organizations(id) on delete cascade` |
| `account text` | Snowflake account identifier (format-validated, see §4.1) |
| `snowflake_user text` | |
| `role text`, `warehouse text` | required connection params |
| `database text`, `schema text` | **nullable**; default `SNOWFLAKE` / `ACCOUNT_USAGE` at resolve time |
| `secret_id uuid` | Vault secret reference; PEM + passphrase live in Vault, never here |
| `has_passphrase boolean` | UI hint only |
| `status text` check (`active`, `invalid`) | last validation outcome |
| `last_validated_at timestamptz` | |
| `created_by_user_id uuid`, `created_at`, `updated_at` | `set_updated_at` trigger as existing tables |

**Vault.** The PEM private key (and passphrase, if any) are stored via
`vault.create_secret(...)`; the returned UUID is `secret_id`. Verified facts
(2026-06): Vault encrypts at rest with an AEAD key Supabase manages *outside* the
DB (absent from dumps); the `vault.create_secret` / `update_secret` /
`decrypted_secrets` interface is stable while internals migrate off the
soon-deprecated pgsodium; access is via schema grants + `security definer`
functions, not RLS.

**RPCs** (all `security definer`, `revoke … from public, authenticated`,
`grant execute … to service_role` ONLY):

- `create_org_with_snowflake_connection(p_user_id, p_org_name, p_account, p_user, p_role, p_warehouse, p_database, p_schema, p_pem, p_passphrase)` —
  see §2; atomic create.
- `set_organization_snowflake_secret(p_org_id, p_pem, p_passphrase) -> uuid` —
  create/replace the Vault secret for an existing org (rotate path).
- `get_organization_snowflake_secret(p_org_id) -> (pem, passphrase)` — reads
  `vault.decrypted_secrets`; the resolver's read path.
- `delete_organization_snowflake_secret(p_org_id)` — drops the Vault secret on
  teardown (no orphans).

**RLS on `organization_snowflake_connections`:**

- `SELECT` for members is **restricted to non-sensitive columns** via a
  dedicated view (status, `last_validated_at`, account label). `secret_id` is
  **never** member-readable; full detail (role/warehouse/db/schema) is
  admin-only.
- **No** authenticated `INSERT/UPDATE/DELETE` policy — all writes happen
  service-role-side, mirroring the org-`INSERT` lockdown
  (`202606150001_restrict_org_insert.sql`).

---

## §2 — Self-service onboarding flow

```
New user signs in → no membership → wizard → POST /api/onboarding/connect  [authenticated]
  1. Read caller identity from the VERIFIED bearer token (auth.uid()), never the body.
  2. Validate inputs at the boundary: account-identifier format, PEM size cap (§4.1).
  3. Build a SnowflakeConnectionConfig in-memory from submitted fields + PEM.
  4. validate_snowflake_connection(config)  ← existing 4 ACCOUNT_USAGE probes,
     with short validation-specific timeouts.
       • failure → 422 user-safe message; NOTHING persisted.
  5. on success → ONE service-role RPC create_org_with_snowflake_connection(...)
     which, in a SINGLE transaction:
       a. takes a pg advisory lock keyed on p_user_id;
       b. re-checks the v1 one-org guard (see below);
       c. inserts organizations(name, created_by_user_id = p_user_id)
          → existing after-insert trigger creates the 'owner' membership;
       d. vault.create_secret(pem, passphrase) → secret_id;
       e. inserts organization_snowflake_connections(... status='active',
          last_validated_at = now).
  6. return the new org → web refreshes membership → dashboard.
```

**Why this is safe (no reopening of org-`INSERT` RLS):** an org is *born only as
the result of a server-validated action*, executed service-role-side. The
dropped `organizations_insert_for_authenticated` policy stays dropped.

**Atomicity (Codex HIGH).** Org + owner membership + Vault secret + connection
row are created in **one transaction** inside the RPC. There is no window where
an org is visible without active credentials; any failure rolls back all
DB-visible state.

**One-org guard, race-safe (Codex HIGH).** Enforced in Postgres, not the app:
a `pg_advisory_xact_lock` on `p_user_id` plus a re-check (and a partial unique
constraint guaranteeing at most one `owner` membership per user in v1) so two
concurrent requests cannot both create an org. The guard is a single deletable
check — lifting it later (multi-org) needs no schema change.

**Reconnect / rotate.** Same validate-first shape, **admin-only**, reusing
`set_organization_snowflake_secret` + a connection-row update. Requires the
role-aware auth change in §3.

**Abuse surface (Codex HIGH).** Each org requires a *working* Snowflake account
the caller controls, so creation is naturally bounded; plus per-user rate
limiting and concurrency limits on the endpoint, and `account` format validation
to prevent SSRF/outbound abuse (§4.1).

---

## §3 — Per-org credential resolver & auth changes

**Client refactor (`snowflake_client.py`).** Per-org keys are PEM **content** in
Vault; serverless hosts have no per-org filesystem.

- Add `private_key_pem: str | None` alongside `private_key_path: Path | None`.
  `_load_private_key_der()` accepts either (PEM content takes precedence; path
  remains for `.env`/self-host). No behavior change for existing `.env` callers.
- Mark `private_key_pem` and `private_key_passphrase` `field(repr=False)`
  (Codex MEDIUM) so PEM material never appears in `repr()`, exception context, or
  logs. Keep PEM out of any Pydantic model that echoes validation errors.
- Make `database`/`schema` optional in `connector_kwargs()` with defaults
  `SNOWFLAKE` / `ACCOUNT_USAGE` (supports the optional form fields in §4).

**Resolver** `resolve_snowflake_config(org_id, settings) -> SnowflakeConnectionConfig`:

```
1. Fetch the org's connection row via service-role (PostgREST, like
   membership_directory.py).
2. row present → call get_organization_snowflake_secret(org_id) for the PEM;
   build per-org config from row fields + PEM content.
3. no row:
     • auth_required == True  → FAIL CLOSED (raise; no .env fallback).   ← Codex CRITICAL
     • auth_required == False → SnowflakeConnectionConfig.from_environment().
4. any Vault/RPC error → FAIL CLOSED (raise; never fall through).
```

**Thread org into the run path.** `_create_snowflake_dashboard_run` /
`build_snowflake_dashboard_data` take the resolved per-org config (membership is
already checked upstream in `_require_dashboard_run_membership`).

**Role-aware auth (Codex HIGH).** `AuthContext` carries only org IDs today.
Extend the membership lookup (`membership_directory.py`) to select `role`, expose
it on `AuthContext`, and add `require_org_admin`. Enforce it on all connection
create/update/rotate/delete paths.

---

## §4 — Web onboarding wizard

Replaces the interim "no organization" / "connecting Snowflake coming soon"
screen whenever an authenticated user has zero memberships. Card/wizard, two
columns: **left = inputs, right = guidance aligned to the active field.**

**Visual consistency.** The wizard reuses the existing dashboard's design
language — the same background color, surface/card styling, typography, spacing,
and shared assets/components. It should read as part of the product, not a
bolted-on form. Pull from the dashboard's existing Tailwind theme tokens /
components rather than introducing new colors or one-off styles.

### §4.1 Inputs (left) & validation
- Org name; Snowflake `account`*, `user`*, `role`*, `warehouse`*, PEM private
  key*, optional passphrase. `database` and `schema` are **optional** (blank →
  `SNOWFLAKE` / `ACCOUNT_USAGE`). (* required)
- Client-side required + `account`-format checks for fast feedback, but the
  **server is the trust boundary**: it independently validates the `account`
  identifier (alphanumerics + dots/hyphens/underscores; reject URL-like,
  slash, colon, whitespace), caps PEM body size, and rate-limits per user.
- PEM lives only in component state for the request — never localStorage, URLs,
  or logs (per `security-model.md`).

### §4.2 Guidance (right)
- Tooltip on `role`: "The role must read the `SNOWFLAKE.ACCOUNT_USAGE` views.
  Only metadata is read — no query results or usage data leave your account,"
  with brief enable instructions.
- Collapsible **"Create a dedicated user + role (recommended for isolation)"**
  copy-able SQL snippet (§4.3).
- Link to Snowflake's key-pair generation guide next to the PEM field:
  https://docs.snowflake.com/en/user-guide/key-pair-auth#generate-the-private-keys
- Optional note: granting `SNOWFLAKE.ORGANIZATION_BILLING_VIEWER` unlocks billed
  dollars; without it Greysight shows estimated dollars.

### §4.3 Recommended setup SQL (Codex-reviewed)

Corrected per Codex review (2026-06-16). Key fixes: `MUST_CHANGE_PASSWORD`
removed; the RSA key is set via `ALTER USER` (not a 256-byte-capped session
variable) with the PEM header/footer stripped and the base64 body on one line;
`CREATE USER IF NOT EXISTS` + `TYPE = SERVICE` for a programmatic keypair-only
user; `AUTO_RESUME = TRUE` on the warehouse; least-privilege grants
(`SNOWFLAKE.USAGE_VIEWER` database role + `USAGE` on the warehouse); no
`BEGIN…COMMIT` wrapper (Snowflake DDL auto-commits).

> Implementation checkpoint: confirm `SNOWFLAKE.USAGE_VIEWER` actually covers all
> four probe views — including `QUERY_ATTRIBUTION_HISTORY` — against the Greybeam
> dev account (locator `TU24199`) during the test-on-save build. If a probe is
> denied, fall back to documenting `GRANT IMPORTED PRIVILEGES ON DATABASE
> SNOWFLAKE` (broad) for that view.

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

GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE IDENTIFIER($role_name);
```

### §4.4 Action
Single primary action **"Test connection & save"** → server validates →
persists only on success → routes to the dashboard. Loading state during
validation; inline user-safe error on `422`.

**Reconnect/rotate** is an admin-only settings affordance re-entering the same
wizard; hidden client-side for non-admins and enforced server-side.

No org switcher in v1 (one-org guard), but wizard + API are written so
"Add account" drops in later with no contract change.

---

## §5 — Teardown & audit

- **Vault teardown (Codex LOW, in-scope).** Disconnecting a connection or
  deleting an org calls `delete_organization_snowflake_secret` so no Vault secret
  is orphaned; the connection row cascades on org delete.
- **Audit hooks (in-scope).** Record events at onboarding, validation
  success/failure, secret write/rotate, and disconnect call sites, with org
  context and **no key material**. (Converting `audit_events` from in-memory to
  a durable table is a tracked fast-follow.)

---

## §6 — Testing strategy (TDD, ≥80%)

- **Client refactor:** load from PEM content vs path; `repr()`/error output
  asserts no PEM or passphrase leakage; `database`/`schema` default correctly.
- **`account` validation:** reject URL-like/slash/colon/overlong; accept valid
  identifiers.
- **Resolver:** row present → per-org config; no row + `auth_required=true` →
  **hard fail (no `.env`)**; no row + `auth_required=false` → `.env` fallback;
  Vault/RPC error → fail closed.
- **Onboarding endpoint:** validation-fail → nothing persisted; success → org +
  owner membership + connection row + Vault secret all present; one-org guard
  rejects a second org (incl. concurrent); rotation rejected for non-admin,
  allowed for admin; identity from token not body.
- **DB/migration:** new table, restricted member view (no `secret_id`), RLS,
  the `create_org_with_snowflake_connection` RPC + advisory-lock/partial-unique
  guard, grants (service-role only on secret RPCs); **Vault round-trip**
  integration test (write → read → not-found).
- **Teardown:** disconnect/org-delete removes the Vault secret and cascades the
  connection row.

---

## Open items

1. Confirm `SNOWFLAKE.USAGE_VIEWER` covers all four probe views (esp.
   `QUERY_ATTRIBUTION_HISTORY`) against the dev account during the test-on-save
   build; broad-grant fallback documented in §4.3.
2. Durable `audit_events` table — fast-follow spec.
3. Org switcher / multi-org UI — fast-follow.
