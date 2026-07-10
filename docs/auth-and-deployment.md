# Auth & Deployment

Email OTP (Spec A) login for Greysight: emailed code → entered on the
sign-in page → client-side code verification → verified Supabase session →
bearer token attached to API calls → the API (the trust boundary) enforces auth
and org membership live on every request. (Magic links are deprecated; the
click-gated `/auth/confirm` route remains only for links from already-sent
emails.)

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
| Web (e.g. Vercel) | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_AUTH_REQUIRED`, `NEXT_PUBLIC_AUTH_CODE_LENGTH` (all public-safe) |
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
2. **Send only the code — no link.** Corporate email security scanners
   (e.g. Avanan, Microsoft Defender Safe Links) fetch every URL in an email and
   many of them *execute JavaScript*, so any single-use sign-in link — even one
   gated behind a client-side confirm page — can be consumed before the user
   ever sees it, causing `otp_expired` errors. Typing a code requires a human,
   which is why the code is the sign-in flow: the user requests a code on
   the login page and enters `{{ .Token }}` from the email on the same screen. In
   the Supabase dashboard, edit both the **Magic Link** and the **Confirm
   signup** templates (since `signInWithOtp` creates new users) so each contains
   **only** the code — no confirmation URL:

   ```html
   <p>Enter this code on the Greysight sign-in page:</p>
   <p><strong>{{ .Token }}</strong></p>
   ```

   The code length is configurable in Supabase (**Auth > Email OTP Length**,
   6–10 digits); the sign-in UI accepts any 6–10 digit code. Set
   `NEXT_PUBLIC_AUTH_CODE_LENGTH` (default `8`) on the web deployment to match
   Supabase's Email OTP Length: the sign-in form auto-submits as soon as that
   many digits are entered. On a mismatch the auto-submit may fire early or
   late (or not at all), but the input always allows up to 10 digits and the
   manual **Sign in with code** button always works for any 6–10 digit code.
   Invalid or out-of-range values (outside 6–10) fall back to the default.

   **Deprecation note:** the click-gated `/auth/confirm` route remains
   temporarily so links from already-sent emails keep working. It can be removed
   once the templates above are updated in all environments.

3. **Verify OTP expiration and auth rate limits.** Brute-force protection for
   the code lives in Supabase, not in the UI. In the dashboard's Auth
   settings, keep the **Email OTP expiration** short (a code should not stay
   valid for long) and confirm the **auth rate limits** (OTP sends and verify
   attempts) are enabled so a code cannot be guessed by repeated
   verification attempts.
4. **Set a short access-token lifetime.** Membership/authorization revocation is
   immediate (the API re-reads the table every request), but the stateless
   Supabase JWT's *global sign-out* is bounded by its `exp`. Keep the
   access-token lifetime short to bound that window.
5. **Set CORS to the web origin.** Set `GREYSIGHT_CORS_ALLOWED_ORIGINS` on the
   API host to the web app's origin (e.g. `https://app.example.com`). Cross-origin
   bearer-token calls fail if the web origin is not allowed.

## First-user bootstrap (self-service onboarding)

Org creation is **self-service**. A brand-new user signs in, lands on the connect
wizard, and provisions their own org by connecting Snowflake — no operator
seeding required.

The flow:

1. **Sign in** with the emailed code, entered on the sign-in page. A
   signed-in user with zero memberships is shown the connect wizard (not a
   dead-end "no organization" screen).
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
