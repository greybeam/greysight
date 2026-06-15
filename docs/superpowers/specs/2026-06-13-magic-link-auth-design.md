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
  table, read live on every request.** The API authenticates the user via
  Supabase, then queries the membership table **live** (Supabase service-role
  REST) for that user's org ids. **Membership revocation is therefore immediate**
  — a removed member is denied on their next API call, with no token-lifetime
  stale window. This was a deliberate v1 choice (correctness over the
  per-request-lookup cost). It also removes two components an earlier draft
  proposed — the custom-access-token hook and JWT-claim plumbing — because the
  API no longer derives memberships from the token at all.

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
| API (Vercel Python functions **or** Render/Fly/Cloud Run) | `SUPABASE_URL`, `SUPABASE_ANON_KEY` (token auth via `/auth/v1/user`), `SUPABASE_SERVICE_ROLE_KEY` (live membership lookup), `GREYSIGHT_CORS_ALLOWED_ORIGINS` (must list the web origin, else cross-origin bearer calls fail), `SNOWFLAKE_*` (server-only). `SUPABASE_JWT_SECRET` not required in v1 (no local JWT decode). |

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

### 3. Membership via live lookup (immediate revocation)

The API derives org membership from a **live read of the
`organization_memberships` table on every request**, so revocation is immediate.
No custom-access-token hook and no JWT membership claim are involved.

**3a. API authorization = live membership query (backend change).**
`apps/api/app/auth.py` keeps using `GET /auth/v1/user` to authenticate the
caller and obtain their `sub`. It then queries the membership table live, using
the **Supabase service-role REST** endpoint (same `httpx` pattern as the
existing verifier — no new DB driver):

```text
GET {SUPABASE_URL}/rest/v1/organization_memberships
    params: user_id = eq.<sub>, select = organization_id
    headers:
      apikey:        {settings.supabase_service_role_key}
      authorization: Bearer {settings.supabase_service_role_key}
```

Exact details (locked to avoid subtle auth failures):
- **Config prerequisite:** add `supabase_service_role_key` to
  `apps/api/app/config.py` `Settings` (alias `SUPABASE_SERVICE_ROLE_KEY`);
  **require it when `AUTH_REQUIRED=true`** and fail closed at startup if missing.
  Today `Settings` only exposes `supabase_url` + `supabase_anon_key`.
- **Header `authorization` carries the `Bearer ` prefix** (matching the existing
  verifier at `auth.py:43`); `apikey` is the raw key. Build the query with
  `params=`, not manual URL string concatenation.
- **Fail closed on lookup failure.** A network error / non-200 / malformed JSON
  from PostgREST raises the existing 401 path — it is **never** silently treated
  as "zero memberships" (which would be indistinguishable from revocation). Mirror
  the existing verifier's timeout + error handling.
- **Pagination:** v1 assumes a small membership count; request with an explicit
  capped `Range`/`limit` and treat truncation as an error rather than silently
  dropping org ids.

The result populates `AuthContext.memberships`. The query is **strictly scoped
to the authenticated `sub`** — the service role bypasses RLS, so the API must
never accept a client-supplied user id here. The `SupabaseSessionVerifier` seam,
`AuthContext` shape, and demo bypass (`AUTH_REQUIRED=false` short-circuits before
any Supabase call) are preserved. `_extract_memberships`' JWT-claim path is
removed (the token no longer carries memberships). Reuse a single
`httpx.AsyncClient` across the auth + membership calls rather than opening one
per call.

This corrects the original CRITICAL: the prior draft read memberships from
`/auth/v1/user`'s `app_metadata`, which never carried them. Live lookup sources
membership from the table directly, so there is no claim-path to mismatch.

**3b. Frontend reads memberships from the API, not the JWT.** A small
authenticated endpoint returns the caller's live memberships **with display
names** (PostgREST embed `organizations(name)` via the FK), since the dashboard
runtime needs an org name, not just an id (`org-shell`'s `SelectedOrganization`
has `{ id, name }`):

```text
GET /api/session/memberships  ->  { organizations: [{ id: string, name: string }] }
```

`org-shell` calls this after sign-in (token attached) to decide interim-screen
vs. dashboard and to drive org selection, replacing today's read of
`session.user.appMetadata`. Backend owns the membership decision (AGENTS.md:
backend is the trust boundary); the frontend never infers membership from the
token. New route file `apps/api/app/routes/session.py` (small, focused),
**mounted in `apps/api/app/main.py`** alongside the existing routers.

The frontend handles three explicit states: **loading** (lookup in flight),
**lookup failed** (error with retry + sign-out — *not* shown as "no org"), and
**resolved** → zero orgs = interim onboarding screen (§4) / ≥1 org = dashboard.

**Scope of "immediate":** membership/authorization revocation is immediate
(next request re-reads the table). The Supabase **access token** is a stateless
JWT, so global session sign-out is still bounded by token `exp` — standard for
stateless JWTs, mitigated by a short access-token lifetime. RLS is **not**
widened (AGENTS.md invariant).

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
-- trigger creates the owner membership; the API's live membership lookup
-- picks it up on the user's next request.
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
| `apps/api/app/auth.py` | **CHANGE**: after `/auth/v1/user` auth, live-query `organization_memberships` (service-role REST, scoped to `sub`, fail-closed) for `AuthContext.memberships`; drop JWT-claim membership path; shared `httpx.AsyncClient` |
| `apps/api/app/config.py` | **CHANGE**: add `supabase_service_role_key` to `Settings`; require it when `AUTH_REQUIRED=true` (fail closed at startup) |
| `apps/api/app/routes/session.py` | **NEW**: `GET /api/session/memberships` → `{ organizations: [{ id, name }] }` for the caller (live) |
| `apps/api/app/main.py` | **CHANGE**: import + `include_router(session_router)` |
| `apps/web/src/components/org/org-shell.tsx` | read memberships from `GET /api/session/memberships` instead of `session.user.appMetadata` |
| `.env.example` | clarifying comments; web-public vs API-server grouping |
| `docs/` | deployment/env split, Vercel-functions caveat, first-user bootstrap, **deployment checklist** (short JWT expiry; email template `{{ .Token }}`; set `GREYSIGHT_CORS_ALLOWED_ORIGINS`) |

## Testing (failing-first, per AGENTS.md)

- `login-form.test.tsx`: step 1 sends code; step 2 verifies code; verify error
  surfaces in alert region; invalid email / non-6-digit code blocked; "different
  email" resets to step 1. (Mock `BrowserAuthClient`.)
- `supabase-client.test.ts`: `verifyOtp` maps Supabase success/error; passcode
  `signInWithOtp` sends no `emailRedirectTo`.
- `org-shell.test.tsx`: zero-membership response → interim panel (not the
  create-org form); single-membership → dashboard children; demo mode unchanged;
  **`TOKEN_REFRESHED` updates the token**; **sign-out clears session, access
  token, and selected org**; memberships come from `GET /api/session/memberships`
  (mocked), not the JWT; **membership-lookup failure renders the error/retry
  state, not the no-org screen**.
- API auth (`apps/api` tests): valid session → `AuthContext.memberships` equals
  the **live** `organization_memberships` rows for `sub` (mock the service-role
  REST call); **a membership removed between requests → denied on the next
  request** (the revocation-is-immediate assertion); invalid/expired token →
  401; demo bypass (`AUTH_REQUIRED=false`) short-circuits before any Supabase
  call; the membership query is scoped to `sub` and never to client input;
  **lookup failure (timeout / non-200 / bad JSON) → 401 fail-closed, not empty
  memberships**.
- Config: with `AUTH_REQUIRED=true` and **`SUPABASE_SERVICE_ROLE_KEY` missing,
  startup fails** (assertion test); present → loads.
- `GET /api/session/memberships`: returns `{ organizations: [{ id, name }] }`
  for the caller; requires auth; **error states tested (401/403/500/timeout)**;
  demo/unauth handling matches the rest of the API.
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
- **Membership revocation is immediate** (live per-request lookup) — the v1
  requirement. Cost: each authenticated request makes Supabase calls (auth +
  membership). Acceptable for this low-RPS dashboard; **no membership caching in
  v1** (caching would reintroduce a stale window). If latency becomes an issue,
  short-TTL caching is a deliberate future tradeoff, not a silent default.
- **Session token revocation is bounded by `exp`.** Global sign-out of a
  stateless Supabase JWT is not instantaneous — standard for stateless JWTs,
  mitigated by a short access-token lifetime. (Distinct from membership
  revocation above, which *is* immediate.)
- **Service-role query must be scoped to `sub`.** The membership lookup uses the
  service role (bypasses RLS), so it must filter strictly by the authenticated
  `sub` and never trust any client-supplied user id — covered by test.
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
- **Demo-mode bypass leaking into authed paths.** Unchanged guard:
  `authRequired === false` short-circuits before any Supabase call; tests assert
  both modes.

## Spec B follow-on (recorded, not built)

Secure per-org Snowflake credential onboarding (collect key-pair creds →
validate against Snowflake → encrypt at rest → create org + owner membership on
success), a per-org connection resolver with env fallback, and team invitations
that add first-login users to the inviting org.
