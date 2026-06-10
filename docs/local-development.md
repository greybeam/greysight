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
3. Use `.env.example` as a checklist for the values below. A root
   `.env.local` is not automatically loaded by the FastAPI backend; export or
   source backend values in the shell that starts the API, or inject them with
   your process manager.
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

With `AUTH_REQUIRED=true`, the backend validates bearer tokens through Supabase
Auth when `SUPABASE_URL` and `SUPABASE_ANON_KEY` are configured. If either value
is missing, bearer-token API calls are rejected fail-closed.

Authenticated dashboard runs require the Supabase user metadata to contain an
organization membership ID in `app_metadata.organization_ids`,
`app_metadata.organizations`, or top-level `memberships`. Until the app has a
backend org-provisioning flow, seed those IDs in Supabase before testing
authenticated run creation.

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
