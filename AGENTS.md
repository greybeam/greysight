# Greysight Agent Guidelines

Greysight is an open source free Snowflake cost observability tool.

## Development Principles
Always use subagent driven development if possible. You are the manager delegating work to other workers. This allows you to retain full context as long as reasonable possible.

1. Think Before Coding — Surface assumptions, ambiguities, and simpler alternatives before writing anything; ask rather than silently picking an interpretation.
2. Lazy Is Correct — Write the minimum code that solves the stated problem; if it can be expressed in fewer lines, it should be. No speculative features, abstractions, or error handling. The shortest honest solution is usually the most maintainable one.
3. Surgical Changes — Every changed line should trace to the request; match existing style, don't touch adjacent code, and only clean up orphans your own changes created.
4. Goal-Driven Execution — Convert tasks into verifiable success criteria (usually tests), then loop until they pass rather than declaring done.
5. Build for the Next Agent — Extensibility comes from simplicity, not flexibility: obvious names, flat structure, standard patterns. Code should be easy to build on because there's less of it to understand, not because it anticipated future needs.

## Structure

- `apps/web/`: Next.js app, React UI, Tremor dashboard components, browser-facing API client, and Vitest tests.
- `apps/api/`: FastAPI backend, trusted config/routes/services, Python tests, and `uv` dependency lockfile.
- `supabase/migrations/`: Supabase schema, RLS policies, org membership model, and short-lived aggregate dataset storage.
- `docs/`: Product specs, implementation plans, dependency notes, and future setup/security/deployment guides.

## Where to look

| Task | Location | Notes |
| --- | --- | --- |
| Web app shell and pages | `apps/web/src/app/` | First screen routes to `/dashboard`; keep UI app-like, not marketing-first. |
| Dashboard UI components | `apps/web/src/components/` | Shared components live near focused tests. Tremor compatibility spike is in `components/compat/`. |
| Web API/env helpers | `apps/web/src/lib/` | Only expose `NEXT_PUBLIC_*` values to the browser. Backend secrets stay in FastAPI. |
| FastAPI routes | `apps/api/app/routes/` | Route modules are mounted from `apps/api/app/main.py`. |
| Backend settings | `apps/api/app/config.py` | Environment parsing and defaults live here. |
| API tests | `apps/api/tests/` | Keep behavior and migration invariant tests close to backend code. |
| Supabase schema/RLS | `supabase/migrations/` | RLS must preserve member read access and admin/owner-only sensitive mutations. |
| Dependency compatibility | `docs/dependency-compatibility.md` | Records Next/React/Tremor/Tailwind pinning decisions and npm install safety. |

## Guides

- `docs/specs/2026-06-08-cost-dashboard-mvp.md`: scoped MVP product and security requirements.
- `docs/superpowers/plans/2026-06-08-cost-dashboard-mvp.md`: execution plan with task dependencies and verification commands.
- `docs/dependency-compatibility.md`: frontend dependency compatibility and install guidance.

## Core Principles
1. **Every change needs a test.** Must fail without change, pass with it
2. **Assert invariants.** Don't silently fail. Don't hedge with if-statements
3. **Own your regressions.** If tests fail after your change, they are your regressions. Debug them directly. Never stash/revert to "check if they fail on main" — that wastes time and is categorically banned.
4. **Validate your hypotheses.**: If you suspect a given cause for a bug, validate it and provide incontrovertible evidence. NEVER make unearned assumptions.
