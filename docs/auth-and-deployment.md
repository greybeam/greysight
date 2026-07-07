# Auth & Deployment

Magic-link (Spec A) login for Greysight: email link → `/auth/confirm` →
client-side token verification → verified Supabase session → bearer token
attached to API calls → the API (the trust boundary) enforces auth and org
membership live on every request.

This document covers the environment-variable split, the Supabase deployment
checklist, the first-user bootstrap, and notes on hosting the FastAPI backend.

OSS dev / contributors are unaffected: `DATA_SOURCE=demo`, `AUTH_REQUIRED=false`
→ no Supabase, no Snowflake, no auth. Everything below applies to a self-deploy
with real data (`AUTH_REQUIRED=true`).

## Environment-variable split

Secrets split by *where the process runs*, not by a new code path. Web vars are
public-safe (they ship to the browser); API vars stay on the API host only.

| Where | Env vars |
| --- | --- |
| Web (e.g. Vercel) | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_AUTH_REQUIRED` (all public-safe) |
| API (Vercel Python functions **or** Render/Fly/Cloud Run) | `SUPABASE_URL`, `SUPABASE_ANON_KEY` (token auth via `/auth/v1/user`), `SUPABASE_SERVICE_ROLE_KEY` (live membership lookup), `GREYSIGHT_CORS_ALLOWED_ORIGINS` (must list the web origin, else cross-origin bearer calls fail), `SNOWFLAKE_*` (server-only) |

**Which dashboard key goes in which var.** New Supabase projects label the keys
"Publishable" and "Secret" (Dashboard > Project Settings > API keys); our env
vars keep the legacy names but accept the new `sb_*` keys:

| Env var | Dashboard key | Prefix |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` / `SUPABASE_ANON_KEY` | Publishable (a.k.a. legacy "anon") — browser-safe | `sb_publishable_…` |
| `SUPABASE_SERVICE_ROLE_KEY` | **Secret** (a.k.a. legacy "service_role") — server-only | `sb_secret_…` |

Notes:

- `SUPABASE_SERVICE_ROLE_KEY` takes the **Secret key** (`sb_secret_…`), **not**
  the publishable key. It is **REQUIRED when `AUTH_REQUIRED=true`** — the API uses
  it for the live membership lookup against `organization_memberships` and fails
  closed at startup if it is missing. Pasting the publishable key here breaks the
  lookup: it cannot bypass RLS, so the user sees no orgs.
- `SUPABASE_JWT_SECRET` is **not** required in v1 (no local JWT decode; tokens
  are verified by calling Supabase `/auth/v1/user`).
- Never expose `SUPABASE_SERVICE_ROLE_KEY` to the browser — it bypasses RLS.

## Supabase deployment checklist

1. **Enable email OTP.** In the Supabase dashboard, enable the Email provider
   and email OTP sign-in so `signInWithOtp` / `verifyOtp` work.
2. **Point the Magic Link template at our confirm route.** Corporate email
   security scanners (e.g. Avanan) perform a plain GET on the magic link URL and
   consume Supabase's single-use token before the user clicks, causing
   `otp_expired` errors. Fix: send users to `/auth/confirm` on our domain, where
   a client-side script does the verification — a plain GET of that page verifies
   nothing. In the Supabase dashboard, edit both the **Magic Link** and the
   **Confirm signup** templates (since `signInWithOtp` creates new users) and
   replace `{{ .ConfirmationURL }}` with:

   ```
   {{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email
   ```

3. **Set a short access-token lifetime.** Membership/authorization revocation is
   immediate (the API re-reads the table every request), but the stateless
   Supabase JWT's *global sign-out* is bounded by its `exp`. Keep the
   access-token lifetime short to bound that window.
4. **Set CORS to the web origin.** Set `GREYSIGHT_CORS_ALLOWED_ORIGINS` on the
   API host to the web app's origin (e.g. `https://app.example.com`). Cross-origin
   bearer-token calls fail if the web origin is not allowed.

## First-user bootstrap (self-service onboarding)

Org creation is **self-service**. A brand-new user signs in, lands on the connect
wizard, and provisions their own org by connecting Snowflake — no operator
seeding required.

The flow:

1. **Sign in** via the magic link. Clicking the link lands on `/auth/confirm`,
   where the token is verified client-side. A signed-in user with zero
   memberships is shown the connect wizard (not a dead-end "no organization"
   screen).
2. **Connect wizard.** The user enters their org name and Snowflake keypair
   credentials (see [`snowflake-setup.md`](./snowflake-setup.md) for the
   least-privilege setup SQL) and clicks **"Test connection & save"**.
3. **Validation.** The API validates the Snowflake connection server-side before
   persisting anything. A failed connection returns a user-safe error and
   persists nothing.
4. **Atomic org creation.** On a successful connection, a single
   `security definer` RPC creates the organization, the owner membership, the
   Snowflake connection row, and the Vault-stored secret **atomically** — all or
   nothing. A one-org guard rejects a second org for the same user. The API's
   live membership lookup picks up the new org on the user's next request and the
   dashboard loads against the org's own credentials.

### Where Snowflake credentials come from

The deployment-level `SNOWFLAKE_*` env vars are used **only in self-host mode**
(`AUTH_REQUIRED=false`) for a single-tenant local/self-hosted deployment. In
multi-tenant mode (`AUTH_REQUIRED=true`), each org's Snowflake credentials come
from **its own Vault-backed connection** provisioned through the wizard above;
the connection resolver **fails closed** — there is no `.env` fallback. A
request for an org with no active connection is rejected rather than silently
falling back to the deployment's `SNOWFLAKE_*` vars. See
[`security-model.md`](./security-model.md) for the Vault storage and
fail-closed resolver details.

## Hosting the FastAPI backend

Vercel *can* host the FastAPI backend as Python serverless functions, alongside
the Next.js web app. The caveats are serverless execution-time limits and the
lack of persistent connection pooling versus potentially slow Snowflake queries.
If those limits bite, host the API on a long-running platform instead
(Render / Fly / Cloud Run) — the env-var split above is identical either way.
