# Greysight Agent Guidelines

Greysight is an open source Snowflake cost observability tool. Keep changes
small, tested, and tied to requested behavior.

## Quick Reference

Run from the repository root unless noted. All test suites are hermetic —
no Supabase or Snowflake credentials required.

```bash
npm run test                 # web (Vitest) + api (pytest)
npm run test:web             # apps/web only
npm run test:api             # apps/api only
npm run lint                 # eslint (web) + ruff check/format (api)
npm run typecheck            # tsc --noEmit (web only)
npm run dev                  # web :3000 + api :8000, demo mode by default
npx vitest run <file>        # single web test (run from apps/web/)
uv run pytest tests/<file>   # single api test (run from apps/api/)
uv run --directory apps/auto-savings pytest   # automated-savings worker suite
uv run --directory shared/connect pytest      # shared greysight-connect package
```

`npm run test` does not cover the `apps/auto-savings` worker or the
`shared/connect` package — run their `uv` suites separately.

Copy `.env.example` to `.env` for local demo mode — no external services
needed. Run shell commands through `rtk` when it is available.

## Core Concepts

- **Dataset pipeline.** Every dashboard metric flows one path: approved
  read-only SQL in `sql/snowflake/` → registered as a source (or composed
  into a derived dataset) in `sql/dashboard_sources.yml` → computed in
  `apps/api/app/services/cost_metrics.py` → prepared into a dashboard view by
  `apps/api/app/services/dashboard_view_builder.py` → fetched, validated, and
  rendered by `apps/web` dashboard components. Demo mode serves the same
  dataset keys from `apps/api/app/services/demo_data.py`.
- **Prepared dashboard views.** Backend code owns analytics, currency/storage
  pricing, date-window semantics, rankings, projections, and unsupported-state
  decisions. Frontend code owns API fetching, cache/prefetch behavior, view
  contracts, and presentation. Do not add frontend-only analytics transforms;
  if a chart needs new derived numbers, add them to the backend
  `DashboardView` model/builder and mirror the contract in
  `apps/web/src/lib/dashboard-contracts.ts`.
- **Dataset key alignment.** Demo data, Snowflake metrics, and frontend
  dataset keys must stay in sync. A new dataset lands as one change touching
  SQL asset + registry entry + metrics + demo data + frontend + tests.
- **Two modes.** Demo mode (`DATA_SOURCE=demo`, `AUTH_REQUIRED=false`) runs
  with no credentials. Authenticated mode uses Supabase auth with
  organization membership and RLS, and executes registry SQL against
  Snowflake. The demo bypass must never leak into authenticated code paths.
- **Automated Savings control loop (separate from the dashboard pipeline).**
  A standalone opt-in worker (`apps/auto-savings/`, its own Railway service)
  polls `SHOW WAREHOUSES` per tenant and directly suspends an enrolled
  `STANDARD` warehouse on the first snapshot that proves it idle, with valid
  zero running and queued statement counts, zero quiescing percentage, and at
  least 62 seconds of uptime.
  Invariants: the worker is the only Snowflake writer and only issues
  `ALTER WAREHOUSE … SUSPEND`; it never changes `AUTO_SUSPEND` or cluster
  settings. Enrollment captures mandatory timezone-aware `created_on`
  identity, and the worker rechecks the versioned enrollment plus global and
  warehouse switches immediately before each command. See
  `docs/automated-savings.md` for the operational runbook and env knobs.

## Dashboard Design System

- Use Tremor chart assets and the shared dashboard primitives before adding
  custom chart or card styling.
- Extend components in `apps/web/src/components/dashboard/` when a reusable
  pattern is missing; avoid one-off dashboard UI.
- Keep spacing on the 4/8/16/24 scale for dashboard layout and controls.
- Structure dashboard pages as header -> controls -> KPI row -> chart/card
  sections.
- Keep dashboard content in a centered container around `1200px` max width.
- Do not add frontend-only analytics transforms; backend prepared views own
  derived dashboard numbers.
- Avoid custom one-off SVGs and chart styling unless a shared asset cannot
  cover the required behavior.

## Project Structure

- `apps/web/`: Next.js app, dashboard UI, auth/org shell, browser API clients, and Vitest tests.
- `apps/api/`: FastAPI backend, trusted auth/org guards, Snowflake access, metric calculation, route tests, and `uv` config.
- `apps/auto-savings/`: standalone Automated Savings worker (own Railway service + Dockerfile) — per-tenant async poll/authorize/suspend loop; hermetic pytest suite mocking the Snowflake session and store.
- `shared/connect/`: installable `greysight-connect` package (Snowflake/Supabase connection code) consumed by `apps/api` and `apps/auto-savings` via a uv path dependency; thin re-export shims remain at `apps/api/app/services/`.
- `sql/snowflake/`: approved read-only Snowflake Account Usage source queries.
- `sql/dashboard_sources.yml`: registry that maps dashboard dataset keys to approved SQL assets and derived datasets.
- `supabase/migrations/`: Supabase schema, RLS policies, organization membership model, and aggregate dataset tables.
- `docs/`: setup, deployment, security, specs, implementation plans, and dependency notes.

## Where To Look

| Task | Location | Notes |
| --- | --- | --- |
| Dashboard UI | `apps/web/src/app/`, `apps/web/src/components/dashboard/` | First screen routes to `/dashboard`; keep UI app-like, not marketing-first. |
| API routes | `apps/api/app/routes/` | Mounted from `apps/api/app/main.py`; `dashboard_runs.py` is the main surface. |
| Metric calculations | `apps/api/app/services/cost_metrics.py` | Dataset keys must match `demo_data.py` and the frontend. |
| Prepared dashboard views | `apps/api/app/services/dashboard_view_builder.py`, `apps/api/app/services/dashboard_view_models.py`, `apps/web/src/lib/dashboard-contracts.ts` | Backend owns derived dashboard numbers; frontend renders the prepared contract. |
| Demo fixtures | `apps/api/app/services/demo_data.py` | Same keys and shapes as live datasets. |
| Snowflake source queries | `sql/snowflake/`, `sql/dashboard_sources.yml` | Execute only registry-approved read-only SQL assets. |
| Supabase schema/RLS | `supabase/migrations/` | Preserve member read access and owner/admin-only sensitive mutations. |
| Auth/org behavior | `apps/api/app/auth.py`, `apps/web/src/components/auth/`, `apps/web/src/components/org/` | Keep local demo bypass separate from authenticated org flows. |
| Automated Savings worker | `apps/auto-savings/src/auto_savings/` | Cycle: `engine.py` (observe→decide→authorize→suspend), `decision.py` (suspend truth table), `warehouse_snapshot.py` (fail-closed `SHOW WAREHOUSES` parsing + uptime), `store.py` (service-role Supabase + in-memory fake), `snowflake_session.py` (warm session, direct suspend, socket-timeout watchdog), `tenant_loop.py`/`main.py` (async supervisor), `config.py` (env knobs). |
| Automated Savings API | `apps/api/app/routes/automated_savings.py`, `apps/api/app/services/automated_savings_store.py`, `apps/api/app/services/warehouse_directory.py` | API never `ALTER`s Snowflake. Opt-in/enroll/config + `SHOW WAREHOUSES`/`SHOW GRANTS` reads; `AUTO_SUSPEND` and cluster counts are read-only display data. |
| Automated Savings UI | `apps/web/src/app/(workspace)/automated-savings/page.tsx`, `apps/web/src/components/automated-savings/`, `apps/web/src/lib/automated-savings-api.ts` | Opt-in gate, warehouse enrollment table, global switch. Client parsers are the single snake_case→camelCase boundary; top-nav tab in `components/dashboard/app-nav.tsx`. |
| Shared connection code | `shared/connect/src/greysight_connect/` | Snowflake/Supabase clients incl. `execute_metadata_query` (SHOW statements). Edit here, not the `apps/api/app/services/` shims. |
| Automated Savings schema | `supabase/migrations/202607120001_automated_savings.sql` | Settings, identity-bound per-warehouse enrollment, direct suspend events, RLS, and service-role authorization/tenant RPCs. Shape-tested in `apps/api/tests/test_automated_savings_migration.py`. |
| Web tests | `*.test.tsx` colocated in `apps/web/src/` | Vitest + Testing Library on jsdom. |
| API tests | `apps/api/tests/` | pytest + httpx test client; no network calls. |
| Worker tests | `apps/auto-savings/tests/` | pytest; Snowflake session + store mocked, no network. |

## Core Principles

1. **Every behavior change needs a test** that fails without the change and
   passes with it — and **no tests that restate the implementation**. Before
   writing a test, ask: "would this catch a real regression that a typecheck
   or a glance at the JSX wouldn't?" If not, don't write it. Never write
   tests that assert copy/labels, that a component renders, that props are
   forwarded, or that a wrapper calls the function it wraps. Do write tests
   for guards, state machines, edge cases, and error paths (e.g. double-submit
   guards, retry-loop prevention, input normalization, error differentiation).
   Fewer, sharper tests beat coverage-padding.
2. **Execute only approved Snowflake SQL.** Dashboard queries must come from
   assets approved in `sql/dashboard_sources.yml`. Automated Savings is the
   narrow control-plane exception: approved metadata reads plus the worker-only
   `ALTER WAREHOUSE … SUSPEND`; do not add other dynamic Snowflake mutations.
3. **Never widen RLS.** Members read; owners/admins perform sensitive
   mutations. Treat any loosening as a security change needing explicit
   user approval.
4. **Assert invariants.** Fail loudly on impossible states instead of
   hedging them with if-statements or spurious error handling.
5. **Think before coding.** Surface assumptions, ambiguities, and simpler
   alternatives; convert tasks into verifiable success criteria.
6. **Surgical changes.** Every changed line traces to the request; write the
   minimum code that solves the stated problem.
7. **Own your regressions.** If tests fail after your change, debug them
   directly — never revert to "check if they fail on main."
8. **Validate hypotheses with evidence** before proposing fixes; never make
   unearned assumptions.
9. **Build for the next agent.** Prefer obvious names, flat structure, and
   standard patterns.

## Manager → Subworker Delegation Workflow

When the user asks for manager-mode delegation, the main session acts as an
engineering manager and does not write implementation code itself.

1. **Scout inline, then brief.** The manager reads just enough of the
   codebase to write a precise, self-contained brief: context, exact files,
   required changes, constraints (code style, tests, checks to run), and the
   report format the subagent must return.
2. **Delegate implementation to a different model.** Code-writing subagents
   run **Opus 4.8** (`model: "opus"`) — never Fable 5. Subagents must not
   commit; changes stay in the working tree for user review.
3. **Cross-model review every round.** After each implementation pass, run an
   adversarial review with the **Codex plugin** (a different model catches
   issues a same-model reviewer misses). Triage findings and delegate real
   fixes back to an Opus subagent with the findings quoted verbatim.
4. **Verify independently.** The manager re-runs the full checks itself
   (`npx vitest run`, `npx tsc --noEmit`, `npx eslint .`) rather than
   trusting subagent reports.
5. **User verifies visually.** For browser/visual confirmation (layout, CSS,
   tooltips), ask the user to check in their running dev server instead of
   driving it from the agent environment.
6. **Report and wait.** Summarize what changed, review findings and how they
   were resolved, and verification evidence. Commits happen only after user
   approval.

## Docs

Docs under `docs/` are **living documentation**. When you change architecture,
an invariant, a dev workflow, or a convention, update the affected doc in the
**same branch** as the code change — not just the PR description. New
architectural decisions worth remembering get captured in `docs/`, not left in
review threads.

- `docs/local-development.md` — full env setup (Supabase keys, env wiring); read when Quick Reference isn't enough.
- `docs/snowflake-setup.md` — provisioning the Snowflake role, warehouse, and key pair for live mode.
- `docs/security-model.md` — auth, org membership, and RLS rationale; read before touching `auth.py` or migrations.
- `docs/deployment.md` — hosting and deploy steps.
- `docs/dependency-compatibility.md` — version pinning constraints; read before bumping dependencies.
- `docs/automated-savings.md` — Automated Savings worker: direct-suspend runbook, cloud-services cost note, current `AUTO_SAVINGS_*` env knobs, sharding, and the `MANAGE WAREHOUSES` opt-in grant.
- `docs/query-cache-architecture.md` — session-scoped browser query cache (identity contract, `transitioning` guard, key registry) and backend httpx connection-pool invariants; keep in sync when the cache or pool changes.
- `docs/specs/` — implementation plans and specs for in-flight work.
