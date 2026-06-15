# Local Development

This guide covers the local clone-and-run path for the Greysight MVP.

## Install

Run dependency installation from the repository root:

```bash
npm install --ignore-scripts
```

The root workspace installs the web app dependencies and wires the API workspace.
The API uses `uv` through npm scripts.

## Run

Start both local services from the repository root:

```bash
npm run dev
```

The web app runs at `http://localhost:3000`. The FastAPI backend runs at
`http://localhost:8000`, and the local web app should use:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

## Local Demo Mode

The default local path uses deterministic dashboard data:

```bash
DATA_SOURCE=demo
AUTH_REQUIRED=false
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

With `AUTH_REQUIRED=false`, local demo routes can be used without a Supabase
session. This bypass is for local development only; shared preview, staging, and
production deployments should require Supabase auth.

## Supabase Setup

Supabase is needed when testing passwordless login, organization membership,
RLS, or authenticated API requests.

1. Create a Supabase project.
2. Apply the migration in `supabase/migrations/`.
3. Use `.env.example` as a checklist for the values below. Copy it to a root
   `.env` and fill in the values: `npm run dev` auto-loads root `.env` for both
   the web and API dev servers (tolerantly), so no shell-export step is needed.
   `.env.local` is an optional Next.js-only personal override.
4. Set the backend Supabase values:

```bash
AUTH_REQUIRED=true
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=
```

5. Set the browser-facing Supabase values used by the Next.js app:

```bash
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_AUTH_REQUIRED=true
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

`NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` are
browser-facing and configure the frontend passwordless flow.
`SUPABASE_SERVICE_ROLE_KEY` and `SUPABASE_JWT_SECRET` are backend-only and must
not be exposed to client code.

In the dashboard (Project Settings > API keys), the keys are labeled
"Publishable" and "Secret". The `*_ANON_KEY` vars take the **Publishable key**
(`sb_publishable_…`, browser-safe); `SUPABASE_SERVICE_ROLE_KEY` takes the
**Secret key** (`sb_secret_…`, server-only) — it bypasses RLS for the live
membership lookup, so don't paste the publishable key into it.

**Restart after editing `.env`.** The dev servers read `.env` only when they
start — the API's `dev.py` loads it at launch, and `uvicorn --reload` reloads
code, not env. After changing any value, fully restart `npm run dev` (stop and
re-run); saving the file is not enough.

With `AUTH_REQUIRED=true`, the backend validates bearer tokens through Supabase
Auth when `SUPABASE_URL` and `SUPABASE_ANON_KEY` are configured. If either value
is missing, bearer-token API calls are rejected fail-closed.

Authenticated dashboard runs require a real `organization_memberships` row for
the signed-in user. Membership is read **live** by the API via the service-role
lookup (`apps/api/app/services/membership_directory.py`, surfaced to the web app
through `apps/web/src/lib/session-memberships.ts`) — it is **not** read from JWT
metadata, so seeding `app_metadata` claims has no effect. A signed-in user with
no membership sees the interim "no organization" screen, which is expected.

There is no self-serve org creation in v1, so the first user/org must be
provisioned through the service-role bootstrap path: the user signs in once (to
create their `auth.users` row), then an operator inserts an `organizations` row,
and a trigger grants the owner membership automatically. See
[First-user bootstrap](./auth-and-deployment.md#first-user-bootstrap-v1-onboarding)
for the exact SQL.

## Snowflake Setup

For real Snowflake data, use `DATA_SOURCE=snowflake` and follow
`docs/snowflake-setup.md`. Local demo mode does not require Snowflake
credentials.

## Verification

Useful local checks from the repository root:

```bash
npm run test
npm run lint
npm run typecheck
```

When acting as an agent in this repository, run shell commands through `rtk`.
