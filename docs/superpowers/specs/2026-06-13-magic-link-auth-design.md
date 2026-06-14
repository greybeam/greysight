# Magic-Link Authentication (Spec A) — Design

Status: proposed (revised after Codex review)
Date: 2026-06-13
Author: brainstormed with Claude (manager); adversarial review by Codex applied

## Problem

Greysight has a working dashboard MVP but no usable login. The repo already
contains Supabase auth *scaffolding* — a browser auth client, a `login-form`,
an `org-shell`, a FastAPI bearer-token verifier, and an RLS schema with
organizations + memberships — but it dead-ends for any real user:

1. The org-shell "Create organization" button only sets local React state from
   an org the user **already** belongs to; it never writes to Supabase. A
   first-time user with zero memberships hits "No organization membership is
   available" and cannot proceed.
2. There are **two sources of truth for membership** that nothing keeps in
   sync: Postgres RLS reads the `organization_memberships` *table*
   (`is_organization_member(auth.uid())`), while the FastAPI guard
   (`apps/api/app/auth.py`) reads `app_metadata.organization_ids` from the
   *JWT* — and nothing populates that JWT claim today.
3. The login form sends a magic **link** (`signInWithOtp` + `emailRedirectTo`)
   but there is no `/auth/callback` route and no passcode entry, so the link
   flow cannot actually complete a session.

## Goal & Non-Goals

**Goal:** Ship a usable, secure magic-link login. Email → 6-digit passcode →
verified Supabase session → token attached to API calls → the API (the trust
boundary) enforces auth and org membership. Membership has a single source of
truth. A signed-in user with no org lands on a coherent interim screen instead
of a dead end.

**Non-Goals (explicitly Spec B or later):**
- Collecting, validating, encrypting, or storing **per-org Snowflake
  credentials** entered through the UI.
- **Creating organizations** from a successful Snowflake connection.
- **Team invitations** (additive later; the membership table already has
  roles).
- Migrating session storage to `@supabase/ssr` httpOnly cookies (future
  hardening; see Risks).
- Any change to the global, env-configured Snowflake connection or the data
  path. Real dashboard data continues to come from the single deployment-level
  `SNOWFLAKE_*` connection; demo mode stays credential-free.

## Decisions (resolved during brainstorming)

- **Provider: Supabase Auth, not Clerk.** Supabase is already the database and
  the auth scaffolding is already wired; magic links/OTP are first-class.
  Adding Clerk would mean running two identity systems and configuring Supabase
  to trust Clerk JWTs — complexity that buys nothing for a magic-link v1. Revisit
  Clerk only if pre-built org/invite UI, many social logins, or enterprise
  SSO/SAML become priorities.
- **Passcode-only for v1 (no clickable link).** A 6-digit code sidesteps the
  email-prefetch "link already used" failure mode *and* removes the need for an
  `/auth/callback` redirect route — simpler and more robust.
- **Client-side session, API is the trust boundary.** The FastAPI service is a
  separate origin that already verifies every bearer token server-side. The
  browser keeps the supabase-js session and attaches the access token to API
  calls. We do **not** adopt `@supabase/ssr` cookies in v1 because a
  cross-origin bearer-token API fights httpOnly cookie auth (client JS must read
  the token to send it). Net: server-side enforcement is solid; frontend gating
  is UX.
- **Single source of truth for membership = the `organization_memberships`
  table**, surfaced to the JWT by a Supabase custom-access-token auth hook,
  and read by the API by **verifying the JWT locally** (HS256 with
  `SUPABASE_JWT_SECRET`) — **not** via `GET /auth/v1/user`. This is a required
  backend change (see Design §3); an earlier draft incorrectly claimed no
  backend change was needed. The custom-access-token hook only rewrites the
  *issued JWT*, it does **not** mutate `auth.users.raw_app_meta_data`, so the
  `/auth/v1/user` response would never carry the injected `organization_ids`.

## Background: deployment & config model (why there is no new "split")

Spec A introduces **no new configuration model** — it reuses the Supabase env
vars already present in `.env.example`, gated by `AUTH_REQUIRED`.

- **OSS dev / contributors:** unchanged. `DATA_SOURCE=demo`,
  `AUTH_REQUIRED=false` → no Supabase, no Snowflake, no auth. Hermetic tests and
  demo mode keep working credential-free.
- **Self-deploy / real data:** set the env vars, `AUTH_REQUIRED=true`.

Secrets split by *where the process runs*, not by a new code path:

| Where | Env vars |
| --- | --- |
| Web (e.g. Vercel) | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_AUTH_REQUIRED` (all public-safe) |
| API (Vercel Python functions **or** Render/Fly/Cloud Run) | `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `GREYSIGHT_CORS_ALLOWED_ORIGINS` (must list the web origin, else cross-origin bearer calls fail), `SNOWFLAKE_*` (server-only) |

Vercel *can* host the FastAPI backend as serverless Python functions; the only
caveat is serverless execution-time limits and the lack of persistent pooling
versus potentially slow Snowflake queries. This choice does not affect Spec A.

The env-creds-vs-UI-creds duality is a **Spec B** concern and should be solved
there with a single connection resolver (per-org stored creds if present, else
env fallback) — one app with mode flags, not a maintained fork.

## Design

### 1. Passcode verification in the browser auth client

`apps/web/src/lib/supabase-client.ts` adds `verifyOtp` to the
`BrowserAuthClient` interface and the Supabase implementation, and keeps the
existing `signInWithOtp` but switches the send to **passcode** semantics (no
`emailRedirectTo`; rely on the Supabase email template `{{ .Token }}`).

```ts
// added to BrowserAuthClient
verifyOtp(input: { email: string; token: string }): Promise<{
  error?: { message: string } | null;
}>;
```

The Supabase impl calls `supabase.auth.verifyOtp({ email, token, type: "email" })`.
`signInWithOtp` drops `options.emailRedirectTo` (passcode flow does not redirect).

### 2. Two-step login form

`apps/web/src/components/auth/login-form.tsx` becomes a two-step form:

- **Step 1 — request code:** email field → `signInWithOtp({ email })` →
  on success advance to step 2 ("Enter the 6-digit code we emailed you").
- **Step 2 — verify code:** 6-digit code field → `verifyOtp({ email, token })`
  → on success the existing `onAuthStateChange` in `org-shell` picks up the new
  session. Includes a "Use a different email" reset and resend affordance.

Validation: trim inputs, require a syntactically valid email, require a 6-digit
numeric code; surface Supabase error messages verbatim in the existing
`role="alert"` region. No new analytics transforms, no mutation of props.

### 3. Membership single-source-of-truth: hook writes the JWT, API verifies the JWT

Two coordinated changes make the `organization_memberships` table authoritative
for *both* RLS and the API.

**3a. Supabase custom-access-token hook (writes the claim).** At token-issue
time a Postgres function reads the caller's `organization_memberships` rows and
injects them into the issued JWT under the exact claim path the API will read:

```text
claims.app_metadata.organization_ids = [ <org uuids the user is a member of> ]
```

This is the **single, locked claim contract** — `app_metadata.organization_ids`
only. The frontend (`org-shell`) and backend (`auth.py`) are aligned to this one
path; the legacy `organizations` / top-level `memberships` fallbacks are removed
so the two sides cannot silently diverge.

Migration `supabase/migrations/<ts>_custom_access_token_hook.sql` must mirror the
hardening already used by the existing RLS helpers (migration line ~215):

```sql
create or replace function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''                       -- fixed empty search_path
as $$
declare
  org_ids jsonb;
begin
  select coalesce(jsonb_agg(m.organization_id), '[]'::jsonb)
    into org_ids
    from public.organization_memberships m
   where m.user_id = (event->>'user_id')::uuid;

  return jsonb_set(
    event,
    '{claims,app_metadata,organization_ids}',
    org_ids,
    true
  );
end;
$$;

revoke all on function public.custom_access_token_hook(jsonb) from public, anon, authenticated;
grant  execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;
```

The dashboard/config enables this as the access-token hook. RLS policies are
**not** widened (AGENTS.md invariant). Fail-closed: if the hook is disabled the
claim is simply absent → the API sees zero memberships and denies org access; it
never over-grants.

**3b. API verifies the JWT locally (required backend change).**
`apps/api/app/auth.py` currently calls `GET /auth/v1/user`, whose `app_metadata`
reflects stored `raw_app_meta_data` — which the hook does **not** touch. So the
verifier is changed to **decode and verify the access token directly**:

- Verify signature (HS256) with `SUPABASE_JWT_SECRET`, plus `exp` and `aud`
  (`authenticated`). Reject on any failure with the existing 401.
- Read `sub` and `app_metadata.organization_ids` from the verified claims.
- `SupabaseAuthServerVerifier` (the `/auth/v1/user` HTTP path) is replaced by a
  local-verification verifier; the `SupabaseSessionVerifier` seam and
  `validate_supabase_session` / `_extract_memberships` shape are preserved so
  tests and the demo bypass are unaffected. Library: `PyJWT` (add to `apps/api`
  deps) — `pip`/`uv` standard, well-tested.

Tradeoff (documented in Risks): local verification trusts the token until `exp`,
so membership/revocation changes are visible only after refresh. Mitigated by a
short access-token lifetime. The alternative — a live `organization_memberships`
lookup per request via the service role — removes the stale window but adds a DB
dependency to the API; deferred unless the stale window proves unacceptable.

### 4. Interim "no organization" landing

`apps/web/src/components/org/org-shell.tsx`: when `authRequired` and the session
has **zero** memberships, render a clear interim panel — "You're signed in.
Connecting your Snowflake account is coming soon." — plus the signed-in email
and a working **Sign out** button. This replaces today's dead-end
create-organization form for the zero-membership case. The existing
single-membership path (user already provisioned against the global connection)
continues to the dashboard.

The non-functional "Create organization" form is removed from Spec A scope (it
belongs to Spec B). Demo mode (`authRequired === false`) is untouched.

### 5. Token attachment & sign-out (already partly present)

`org-shell` already exposes `onAccessTokenChange`; confirm the dashboard runtime
attaches that token as `Authorization: Bearer` on API calls and that sign-out
clears it. Add a sign-out control to the signed-in header that clears the session, the
attached access token, and any selected org. (The `auth.py` verifier change is
covered in §3b — it is required, contrary to an earlier draft.)

### 6. Env + docs

- `.env.example`: keep keys as-is; add comments clarifying which vars are
  web-public vs API-server, and that `AUTH_REQUIRED=true` turns auth on.
- `docs/`: short note on the web/API deployment split and the Vercel-functions
  option with its serverless caveats.

### 7. First-user bootstrap (manual provisioning for v1)

Because org creation is deferred to Spec B, Spec A is **login for
pre-provisioned members**. To avoid a dead-ended deployment, the operator seeds
the first org + owner membership directly in Supabase (SQL editor or migration).
The existing `organizations_create_owner_membership` trigger means inserting the
org as the user auto-creates the owner membership:

```sql
-- after the user has signed in once (so auth.users has their row):
insert into organizations (name, created_by_user_id)
values ('Greybeam', '<auth.users.id of the operator>');
-- trigger creates the owner membership; the next token issued carries
-- app_metadata.organization_ids via the hook.
```

This is documented in `docs/` as the v1 onboarding path. Self-serve org creation
(via validated Snowflake connection) is Spec B. A signed-in user who is *not*
seeded sees the interim screen (§4) — expected, not a bug.

## Affected files

| File | Change |
| --- | --- |
| `apps/web/src/lib/supabase-client.ts` | add `verifyOtp`; passcode-mode `signInWithOtp` |
| `apps/web/src/components/auth/login-form.tsx` | two-step email → code form |
| `apps/web/src/components/org/org-shell.tsx` | interim no-org screen; sign-out; drop dead create-org form |
| `supabase/migrations/<ts>_custom_access_token_hook.sql` | hardened membership→JWT claim hook (`search_path=''`, revoke public/anon/authenticated, grant `supabase_auth_admin`) |
| `apps/api/app/auth.py` | **CHANGE**: local JWT verify (HS256 + `SUPABASE_JWT_SECRET`, `exp`/`aud`) replacing `/auth/v1/user`; read `app_metadata.organization_ids` |
| `apps/api/pyproject.toml` | add `PyJWT` dependency |
| `apps/web/src/components/org/org-shell.tsx` | lock claim read to `app_metadata.organization_ids` (drop divergent fallbacks) |
| `.env.example` | clarifying comments; web-public vs API-server grouping |
| `docs/` | deployment/env split, Vercel-functions caveat, first-user bootstrap, **deployment checklist** (enable hook; set short JWT expiry; email template `{{ .Token }}`; set `GREYSIGHT_CORS_ALLOWED_ORIGINS`) |

## Testing (failing-first, per AGENTS.md)

- `login-form.test.tsx`: step 1 sends code; step 2 verifies code; verify error
  surfaces in alert region; invalid email / non-6-digit code blocked; "different
  email" resets to step 1. (Mock `BrowserAuthClient`.)
- `supabase-client.test.ts`: `verifyOtp` maps Supabase success/error; passcode
  `signInWithOtp` sends no `emailRedirectTo`.
- `org-shell.test.tsx`: zero-membership session → interim panel (not the
  create-org form); single-membership → dashboard children; demo mode unchanged;
  **`TOKEN_REFRESHED` updates the token**; **sign-out clears session, access
  token, and selected org**; claim read is `app_metadata.organization_ids` only.
- Auth-hook SQL: a pgTAP/SQL test (or documented manual verification if pgTAP is
  not set up) asserting the hook emits `organization_ids` matching the
  membership table for a member and `[]` for a non-member, and that the function
  is **not executable by `public`, `anon`, or `authenticated`** (only
  `supabase_auth_admin`).
- API verifier: replace/extend `apps/api` auth tests — valid signed token →
  `sub` + memberships from `app_metadata.organization_ids`; **bad signature →
  401; expired (`exp`) → 401; wrong `aud` → 401**; demo bypass
  (`AUTH_REQUIRED=false`) still short-circuits before any verification.
- OTP error UX: login-form surfaces expired/invalid-code and rate-limited
  (HTTP 429) responses from Supabase, not just generic failure.

### Verification commands

```bash
npm run test         # web (Vitest) + api (pytest)
npm run lint         # eslint + ruff
npm run typecheck    # tsc --noEmit (web)
```

## Risks & mitigations

- **XSS token theft (client-side session).** supabase-js stores the session in
  the browser, so a successful XSS could exfiltrate the access token. Mitigation
  for v1: standard React escaping, no `dangerouslySetInnerHTML`, short token
  lifetimes; recorded future hardening: migrate to `@supabase/ssr` httpOnly
  cookies behind a same-origin BFF/proxy. Accepted for v1 because the API
  independently verifies every token.
- **Stale membership / revocation window.** Local JWT verification trusts the
  token until `exp`, so a removed member keeps API access until refresh/expiry.
  Mitigation: short access-token lifetime (configure in Supabase); document the
  window; fail-closed (no claim → no access). Live per-request table lookups are
  the recorded escape hatch if the window becomes unacceptable.
- **OTP brute force / resend abuse.** Client-side format checks don't stop
  abuse. Rely on Supabase's built-in OTP expiry and per-email/IP rate limits;
  the UI must handle `429`, expired, and invalid-code responses explicitly and
  apply a resend cooldown. (Captcha/Turnstile recorded as future hardening.)
- **Email prefetch consuming codes.** Avoided by passcode-only (no one-time
  link to prefetch).
- **`POST /api/snowflake/validate` lacks an org-membership check** (only
  `require_auth_context`). Acknowledged, low-risk in v1 because it validates the
  single global connection and exposes no org data. Recorded for Spec B, which
  introduces per-org connections and must add a membership/admin guard here.
- **Auth hook misconfig leaks/empties claims.** The hook is `security definer`,
  granted only to `supabase_auth_admin`, and covered by a claim-shape test; if
  the hook is disabled, the API simply sees no memberships and denies org access
  (fail-closed), it does not over-grant.
- **Demo-mode bypass leaking into authed paths.** Unchanged guard:
  `authRequired === false` short-circuits before any Supabase call; tests assert
  both modes.

## Spec B follow-on (recorded, not built)

Secure per-org Snowflake credential onboarding (collect key-pair creds →
validate against Snowflake → encrypt at rest → create org + owner membership on
success), a per-org connection resolver with env fallback, and team invitations
that add first-login users to the inviting org.
