# Greysight Agent Guidelines

Greysight is an open source Snowflake cost observability tool. Keep changes
small, tested, and tied to requested behavior.

## Development Principles

1. Think before coding: surface assumptions, ambiguities, and simpler alternatives.
2. Lazy is correct: write the minimum code that solves the stated problem.
3. Surgical changes: every changed line should trace to the request.
4. Goal-driven execution: convert tasks into verifiable success criteria and run relevant checks.
5. Build for the next agent: prefer obvious names, flat structure, and standard patterns.

## Project Structure

- `apps/web/`: Next.js app, dashboard UI, auth/org shell, browser API clients, and Vitest tests.
- `apps/api/`: FastAPI backend, trusted auth/org guards, Snowflake access, metric calculation, route tests, and `uv` config.
- `sql/snowflake/`: approved read-only Snowflake Account Usage source queries.
- `sql/dashboard_sources.yml`: registry that maps dashboard dataset keys to approved SQL assets and derived datasets.
- `supabase/migrations/`: Supabase schema, RLS policies, organization membership model, and aggregate dataset tables.
- `docs/`: setup, deployment, security, specs, implementation plans, and dependency notes.

## Where To Look

| Task | Location | Notes |
| --- | --- | --- |
| Dashboard UI | `apps/web/src/app/`, `apps/web/src/components/dashboard/` | First screen routes to `/dashboard`; keep UI app-like, not marketing-first. |
| API routes | `apps/api/app/routes/` | Routes are mounted from `apps/api/app/main.py`. |
| Metric calculations | `apps/api/app/services/cost_metrics.py` | Keep demo, Snowflake, and frontend dataset keys aligned. |
| Snowflake source queries | `sql/snowflake/`, `sql/dashboard_sources.yml` | Execute only registry-approved read-only SQL assets. |
| Supabase schema/RLS | `supabase/migrations/` | Preserve member read access and owner/admin-only sensitive mutations. |
| Auth/org behavior | `apps/api/app/auth.py`, `apps/web/src/components/auth/`, `apps/web/src/components/org/` | Keep local demo bypass separate from authenticated org flows. |
| Local dev/deployment docs | `docs/local-development.md`, `docs/snowflake-setup.md`, `docs/deployment.md` | Keep command and environment details in docs, not in this file. |

## Core Principles

1. Every behavior change needs a test that would fail without the change.
2. Assert invariants instead of silently accepting impossible states.
3. Own regressions from your change and debug them directly.
4. Validate hypotheses with evidence before proposing fixes.
