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

With `AUTH_REQUIRED=true`, the backend validates bearer tokens through Supabase
Auth when `SUPABASE_URL` and `SUPABASE_ANON_KEY` are configured. If either value
is missing, API auth fails closed.

Authenticated organization membership comes from a **live membership lookup**.
`auth.py` queries `organization_memberships` with the service-role key on every
request rather than trusting Supabase JWT claims, so membership grants and
revocations take effect immediately. Dashboard run routes reject organization IDs
the requesting user is not a live member of.

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
- `SNOWFLAKE_PRIVATE_KEY_PATH` (self-host mode only — see below)
- `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` (self-host mode only — see below)

The deployment-level `SNOWFLAKE_*` values apply only in self-host mode
(`AUTH_REQUIRED=false`). In multi-tenant mode, per-org Snowflake credentials live
in Supabase Vault — see [Per-Org Snowflake Credentials](#per-org-snowflake-credentials-supabase-vault).

Snowflake private key contents and private key paths must not be returned to the
frontend or logged.

## Per-Org Snowflake Credentials (Supabase Vault)

In multi-tenant mode (`AUTH_REQUIRED=true`), each org's Snowflake credentials are
provisioned through the connect wizard and stored in **Supabase Vault**, not in a
normal application table:

- **Encrypted at rest.** Vault encrypts the secret; the key material lives
  outside the database. The org's `snowflake_connection` row holds only a
  `secret_id` reference, never the PEM private key. The member-facing view of the
  connection omits `secret_id` entirely.
- **Service-role-only reads.** The secret is read only via a service-role RPC.
  Application/anon roles cannot read the Vault secret directly; RLS and grants
  restrict the secret RPCs to the service role.
- **Fail closed.** The connection resolver maps `org_id → connection config` and
  **fails closed** whenever `AUTH_REQUIRED=true`: a missing connection row, a
  connection whose `status != 'active'`, or any Vault/RPC error results in a hard
  failure — there is **no `.env` fallback** to the deployment-level `SNOWFLAKE_*`
  vars. Those deployment vars are honored only in self-host mode
  (`AUTH_REQUIRED=false`).

### Credential lifecycle and teardown

- **Atomic create.** A `security definer` RPC creates the org, owner membership,
  connection row, and Vault secret atomically; a failed Snowflake validation
  persists nothing.
- **Disconnect (admin-only).** Disconnecting a connection atomically deletes the
  Vault secret and invalidates the connection row (`secret_id` cleared, status
  set to invalid). Disconnect is restricted to org owners/admins and enforced
  server-side; a repeated disconnect is an idempotent no-op.
- **Teardown guarantees.** A `before delete` trigger removes the Vault secret on
  connection-row delete and on org-delete cascade, so no Vault secret is ever
  orphaned.
- **No key material in logs.** Credential lifecycle events (connect, disconnect,
  secret write) are audit-logged **without** key material — never the PEM private
  key or passphrase.

There is no key-rotation/reconnect endpoint in v1; rotating credentials means
disconnecting and reconnecting through the wizard.

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
write audit events with organization context. Credential lifecycle events
(connect, disconnect, Snowflake secret write) are also audit-logged with org
context and **without any key material**. Local unauthenticated demo mode skips
org-scoped audit writes rather than writing null-organization audit rows.

## Non-Goals

Savings estimate generation is post-MVP and is not included in the dashboard.

Local demo mode is for local development. Deployed environments should configure
Supabase auth, use production secrets in the hosting platform, and avoid exposing
backend-only environment values to the browser.
