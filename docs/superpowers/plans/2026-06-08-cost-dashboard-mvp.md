Implementation Plan

> REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Greysight cost dashboard MVP: a locally testable Next.js + FastAPI app with demo mode, Supabase auth/orgs, Snowflake metadata queries, and chart-ready aggregate dashboard datasets.

**Architecture:** The manager delegates every task to a fresh worker subagent, then runs a spec-compliance review and a code-quality review before accepting the work. FastAPI owns trusted backend work: auth validation, org authorization, SQL registry, Snowflake access, metric calculation, run lifecycle, aggregate dataset persistence, and audit events. Next.js owns the user-facing app: local demo dashboard, passwordless login, org shell, API polling, Tremor dashboard rendering, and Snowflake setup UX.

**Tech Stack:** npm workspaces, Next.js, React, TypeScript, Tremor, FastAPI, Python, pytest, ruff, Supabase Auth/Postgres, Snowflake Python connector, Vercel.

## Manager Operating Model

- [ ] Create a clean work branch before implementation.
- [ ] For each task, spawn one fresh worker subagent with only that task's brief, relevant file paths, required tests, and acceptance criteria.
- [ ] Do not let two workers edit the same file concurrently.
- [ ] When a worker returns, run a spec-compliance review subagent against the task brief.
- [ ] If spec review passes, run a code-quality review subagent for maintainability, security, tests, and integration risk.
- [ ] Fix or delegate fixes for all blocking review findings before marking the task done.
- [ ] Run the task's verification commands locally after applying worker changes.
- [ ] Commit after each completed task or small batch if the user asks for commits.
- [ ] Pause at every "User checkpoint" before proceeding past real account setup or manual local testing.
- [ ] After any task creates or materially changes a major project area, decide whether future agents need an `AGENTS.md` update immediately. Prefer collecting notes and consolidating them in Task 16 unless missing guidance would slow or mislead later workers.
- [ ] Keep `AGENTS.md` tight: high-level structure, ownership boundaries, and where to update major code paths. Do not duplicate setup docs, API contracts, security policy prose, or detailed implementation notes that belong in `docs/`.

## Parallelization Map

Phase 0 is sequential because it creates shared scaffolding.

After Phase 0:

- Batch A can run in parallel:
  - Task 2 API health and config
  - Task 3 dependency compatibility spike
  - Task 4 Supabase migration

- Batch B can run in parallel after Batch A prerequisites:
  - Task 5 Next.js shell
  - Task 6 SQL registry and assets
  - Task 7 metric engine
  - Task 8 auth/org backend boundary

- Batch C can run in parallel after Task 5 and the relevant Batch B prerequisites:
  - Task 9 web dashboard contract and demo rendering depends on Tasks 3 and 5.
  - Task 10 Vercel deployment spike depends on Tasks 1, 3, and 5.

- Batch D has tighter dependencies:
  - Task 11 demo run API depends on Tasks 6 and 7.
  - Task 12 dashboard run UI depends on Tasks 9 and 11.
  - Task 13 Snowflake client depends on Tasks 2 and 6.
  - Task 14 Snowflake validation/run lifecycle depends on Tasks 8, 11, and 13.
  - Task 15 Supabase auth UI/org shell depends on Tasks 5, 8, and 12.
  - Task 16 docs and AGENTS.md guidance depends on completed implementation decisions.

## User Checkpoints

- [ ] Checkpoint 1 after Task 1: user runs `npm install --ignore-scripts` to confirm dependency install starts cleanly without dependency lifecycle scripts.
- [ ] Checkpoint 2 during Task 10 or after local MVP: user decides whether to connect Vercel now or defer deployment proof.
- [ ] Checkpoint 3 after Task 12: user opens the local unauthenticated demo dashboard with `AUTH_REQUIRED=false`.
- [ ] Checkpoint 4 before Task 14 real Snowflake smoke test: user adds Snowflake `.env.local` values including `SNOWFLAKE_PRIVATE_KEY_PATH`.
- [ ] Checkpoint 5 before Task 15 manual testing: user creates/provides Supabase dev values.

## Phase 0: Root Scaffold

### Task 1: Root npm Workspace And Local Dev Bridge

Delegate to: local-dev scaffold worker.

Depends on: none.

Parallel-safe: no.

Files:

- Create `package.json`
- Create `.env.example`
- Modify `.gitignore`
- Create `apps/api/package.json`
- Create `apps/api/pyproject.toml`
- Create `apps/api/app/__init__.py`
- Create `apps/api/tests/test_test_runner.py`
- Create `apps/web/package.json`

Worker brief:

- [ ] Write root npm workspace scripts for `dev`, `dev:web`, `dev:api`, `test`, `test:web`, `test:api`, `lint`, `lint:web`, `lint:api`, and `typecheck`.
- [ ] Pin `concurrently` exactly.
- [ ] Choose the Python dependency manager for the API. Prefer `uv` because `uv run` is already approved in this environment.
- [ ] Pin exact Python dependencies in `apps/api/pyproject.toml`.
- [ ] Make `npm --workspace apps/api run test` call the selected Python test command.
- [ ] Make `npm --workspace apps/api run lint` call ruff through the selected Python command.
- [ ] Create only a minimal `apps/web/package.json` workspace marker; real web scripts and test infra are added in Task 3.
- [ ] Add `.env.example` with `DATA_SOURCE=demo`, `AUTH_REQUIRED=false`, local API URL, Supabase env vars, Snowflake env vars, and `STORAGE_PRICE_USD_PER_TB_MONTH`.
- [ ] Ensure `.env.local`, `.env`, `*.pem`, `*.p8`, and `*.key` are ignored.

Root `package.json` script target:

```json
{
  "private": true,
  "workspaces": [
    "apps/web",
    "apps/api"
  ],
  "scripts": {
    "dev": "concurrently \"npm run dev:web\" \"npm run dev:api\"",
    "dev:web": "npm --workspace apps/web run dev",
    "dev:api": "npm --workspace apps/api run dev",
    "test": "npm run test:web && npm run test:api",
    "test:web": "npm --workspace apps/web run test",
    "test:api": "npm --workspace apps/api run test",
    "test:coverage": "npm run test:coverage:web && npm run test:coverage:api",
    "test:coverage:web": "npm --workspace apps/web run test:coverage",
    "test:coverage:api": "npm --workspace apps/api run test:coverage",
    "lint": "npm run lint:web && npm run lint:api",
    "lint:web": "npm --workspace apps/web run lint",
    "lint:api": "npm --workspace apps/api run lint",
    "typecheck": "npm --workspace apps/web run typecheck"
  },
  "devDependencies": {
    "concurrently": "10.0.1"
  }
}
```

API `package.json` script target:

```json
{
  "private": true,
  "scripts": {
    "dev": "uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000",
    "test": "uv run pytest",
    "test:coverage": "uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=80",
    "lint": "uv run ruff check app tests && uv run ruff format --check app tests"
  }
}
```

Python dependency target in `apps/api/pyproject.toml`:

```toml
[project]
name = "greysight-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi==0.115.6",
  "uvicorn==0.34.0",
  "pydantic-settings==2.7.1",
  "pytest==8.3.4",
  "pytest-cov==6.0.0",
  "httpx==0.28.1",
  "pyyaml==6.0.2",
  "snowflake-connector-python==3.12.4",
  "ruff==0.8.4"
]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

Runner smoke test:

```python
# apps/api/tests/test_test_runner.py
def test_api_test_runner_is_wired() -> None:
    assert True
```

Verification:

```bash
rtk npm install --ignore-scripts
rtk npm run test:api
```

Expected result:

- `npm install --ignore-scripts` completes.
- API test command runs through npm from repo root.
- Root `npm run test`, `npm run lint`, `npm run typecheck`, and `npm run dev` are not expected to pass until later tasks create the web app and API health endpoint.

User checkpoint 1:

- [ ] Tell user to run `npm install --ignore-scripts`.
- [ ] Tell user that `npm run dev` will become fully usable after Tasks 2, 3, and 5.

## Phase 1: Parallel Foundation Batch

### Task 2: FastAPI Health, Settings, And Config Tests

Delegate to: API foundation worker.

Depends on: Task 1.

Parallel-safe: yes with Tasks 3 and 4. Do not mark Task 5 parallel with this phase; Task 5 waits for Task 3.

Files:

- Create `apps/api/app/main.py`
- Create `apps/api/app/config.py`
- Create `apps/api/app/routes/__init__.py`
- Create `apps/api/app/routes/health.py`
- Create `apps/api/tests/test_health.py`
- Create `apps/api/tests/test_config.py`

Test first:

```python
# apps/api/tests/test_health.py
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "greysight-api"}
```

```python
# apps/api/tests/test_config.py
from app.config import Settings


def test_settings_defaults_to_demo_without_auth() -> None:
    settings = Settings()

    assert settings.data_source == "demo"
    assert settings.auth_required is False
    assert settings.default_window_days == 30


def test_settings_reads_greysight_prefixed_aliases(monkeypatch) -> None:
    monkeypatch.setenv("GREYSIGHT_DEFAULT_WINDOW_DAYS", "45")
    monkeypatch.setenv("GREYSIGHT_QUERY_TIMEOUT_SECONDS", "90")

    settings = Settings()

    assert settings.default_window_days == 45
    assert settings.query_timeout_seconds == 90
```

Implementation requirements:

- [ ] `Settings` uses `pydantic-settings`.
- [ ] `DATA_SOURCE` accepts only `demo` or `snowflake`.
- [ ] `AUTH_REQUIRED` defaults to false for local clone-and-run.
- [ ] `GREYSIGHT_DEFAULT_WINDOW_DAYS` and `GREYSIGHT_QUERY_TIMEOUT_SECONDS` bind to `default_window_days` and `query_timeout_seconds` with explicit field aliases such as `AliasChoices` or `validation_alias`.
- [ ] Health route returns exactly `{"status": "ok", "service": "greysight-api"}`.

Verification:

```bash
rtk npm run test:api
rtk npm run lint:api
```

### Task 3: Dependency Compatibility Spike

Delegate to: frontend dependency worker.

Depends on: Task 1.

Parallel-safe: yes with Tasks 2 and 4. Task 5 depends on this task.

Files:

- Modify `apps/web/package.json`
- Create `apps/web/next.config.ts`
- Create `apps/web/tsconfig.json`
- Create `apps/web/postcss.config.mjs`
- Create `apps/web/tailwind.config.ts`
- Create `apps/web/src/app/layout.tsx`
- Create `apps/web/src/app/page.tsx`
- Create `apps/web/src/styles/globals.css`
- Create `apps/web/vitest.config.ts`
- Create `apps/web/src/test/setup.ts`
- Create `apps/web/src/__tests__/test-runner.test.ts`
- Create `apps/web/src/components/compat/tremor-compat.tsx`
- Create `apps/web/src/components/compat/tremor-compat.test.tsx`
- Create `docs/dependency-compatibility.md`

Worker brief:

- [ ] Pin exact versions for `next`, `react`, `react-dom`, `typescript`, `@tremor/react` or chosen Tremor package, `tailwindcss`, `postcss`, `autoprefixer`, `vitest`, `@vitest/coverage-v8`, `@testing-library/react`, `@testing-library/jest-dom`, and `jsdom`.
- [ ] Treat Tailwind major version as part of the Tremor compatibility matrix. If `@tremor/react` requires Tailwind v3, pin Tailwind v3 and document why rather than accepting a default incompatible Tailwind v4 setup.
- [ ] Add web workspace scripts for `dev`, `test`, `test:coverage`, `lint`, `typecheck`, and `build`.
- [ ] Configure Vitest with `environment: "jsdom"` and a setup file that imports `@testing-library/jest-dom/vitest`.
- [ ] Configure web coverage thresholds at 80% for statements, branches, functions, and lines in `vitest.config.ts`.
- [ ] Configure Tailwind content paths for `apps/web/src/**/*` and Tremor package files in `node_modules/@tremor/**`.
- [ ] Create a minimal Next app scaffold so `typecheck` and `build` are meaningful in this task.
- [ ] Render `TremorCompat` from the temporary `apps/web/src/app/page.tsx` or another route used by the build.
- [ ] Add `"use client"` to `tremor-compat.tsx` because Tremor components render inside Next App Router client components.
- [ ] Build a minimal Tremor metric/card, line chart, bar chart, and table.
- [ ] If Tremor fails with current stable React/Next, pin the newest compatible version set and document why.

Test first:

```ts
// apps/web/src/__tests__/test-runner.test.ts
import { describe, expect, it } from "vitest";

describe("web test runner", () => {
  it("is wired", () => {
    expect(true).toBe(true);
  });
});
```

```tsx
// apps/web/src/components/compat/tremor-compat.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TremorCompat } from "./tremor-compat";

describe("TremorCompat", () => {
  it("renders required dashboard primitives", () => {
    render(<TremorCompat />);

    expect(screen.getByText("Compatibility")).toBeInTheDocument();
    expect(screen.getByText("Warehouse spend")).toBeInTheDocument();
    expect(screen.getByText("Top warehouses")).toBeInTheDocument();
  });
});
```

Verification:

```bash
rtk npm run test:web
rtk npm run typecheck
rtk npm --workspace apps/web run build
```

### Task 4: Supabase Migration With RLS

Delegate to: database worker.

Depends on: Task 1.

Parallel-safe: yes with Tasks 2 and 3. Do not mark Task 5 parallel with this phase; Task 5 waits for Task 3.

Files:

- Create `supabase/migrations/202606080001_initial_cost_dashboard.sql`
- Create `apps/api/tests/test_supabase_migration.py`

Test first:

```python
# apps/api/tests/test_supabase_migration.py
from pathlib import Path


MIGRATION = Path("supabase/migrations/202606080001_initial_cost_dashboard.sql")


def test_migration_enables_rls_for_public_tables() -> None:
    sql = MIGRATION.read_text()

    for table in [
        "organizations",
        "organization_memberships",
        "snowflake_connections",
        "connection_validation_results",
        "analysis_runs",
        "analysis_run_datasets",
        "audit_events",
        "dashboard_filter_preferences",
    ]:
        assert f"alter table {table} enable row level security" in sql.lower()


def test_migration_indexes_org_and_run_access_patterns() -> None:
    sql = MIGRATION.read_text().lower()

    assert "analysis_runs(organization_id, created_at desc)" in sql
    assert "analysis_run_datasets(run_id, dataset_key)" in sql
    assert "credential_reference" in sql
    assert "private_key" not in sql
```

Implementation requirements:

- [ ] Create required tables from the spec.
- [ ] Enable RLS in the first migration.
- [ ] Add membership-based policies for authenticated users.
- [ ] Add indexes for org foreign keys and natural query patterns.
- [ ] Add `updated_at` trigger for tables with `updated_at`.
- [ ] Add `retention_expires_at` to `analysis_run_datasets`.

Verification:

```bash
rtk npm run test:api
```

### Task 5: Next.js App Shell And API Client

Delegate to: frontend shell worker.

Depends on: Task 3.

Parallel-safe: yes with Tasks 6, 7, 8, and 10 after Task 3 is complete. Do not run in parallel with Task 3. Task 9 depends on this task.

Files:

- Modify `apps/web/next.config.ts` if the shell needs additional config
- Modify `apps/web/tsconfig.json` if path aliases or stricter options are needed
- Modify `apps/web/src/app/layout.tsx`
- Modify `apps/web/src/app/page.tsx`
- Create `apps/web/src/app/dashboard/page.tsx`
- Create `apps/web/src/lib/env.ts`
- Create `apps/web/src/lib/api-client.ts`
- Create `apps/web/src/lib/api-client.test.ts`
- Create `apps/web/src/components/api-health.tsx`
- Create `apps/web/src/components/api-health.test.tsx`
- Modify `apps/web/src/styles/globals.css`

Test first:

```ts
// apps/web/src/lib/api-client.test.ts
import { describe, expect, it } from "vitest";

import { resolveApiUrl } from "./api-client";

describe("resolveApiUrl", () => {
  it("uses configured local API base URL", () => {
    expect(resolveApiUrl("/health", "http://localhost:8000")).toBe(
      "http://localhost:8000/health",
    );
  });

  it("uses relative same-origin paths when no external base URL is configured", () => {
    expect(resolveApiUrl("/health", "")).toBe("/health");
  });
});
```

```tsx
// apps/web/src/components/api-health.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiHealth } from "./api-health";

describe("ApiHealth", () => {
  it("renders healthy state", () => {
    render(<ApiHealth status="ok" />);

    expect(screen.getByText("API healthy")).toBeInTheDocument();
  });
});
```

Implementation requirements:

- [ ] First screen links or redirects to `/dashboard`.
- [ ] Dashboard page renders an app shell and API health component mount point.
- [ ] `globals.css` includes the Tailwind directives/imports required by the Tailwind major chosen in Task 3.
- [ ] It is acceptable for `TremorCompat` to become a spike-only artifact referenced only by its own test after `app/page.tsx` moves to the real dashboard entry.
- [ ] No marketing landing page.
- [ ] API client supports local `http://localhost:8000` and deployed same-origin relative URL strategy.

Verification:

```bash
rtk npm run test:web
rtk npm run typecheck
```

## Phase 2: Parallel Core Batch

### Task 6: SQL Registry And Approved SQL Assets

Delegate to: SQL registry worker.

Depends on: Task 2.

Parallel-safe: yes with Tasks 7, 8, 9, and 10.

Files:

- Create `sql/dashboard_sources.yml`
- Create `sql/snowflake/warehouse_spend_daily.sql`
- Create `sql/snowflake/service_spend_daily.sql`
- Create `sql/snowflake/query_compute_by_user_daily.sql`
- Create `sql/snowflake/database_storage_daily.sql`
- Create `apps/api/app/services/dashboard_registry.py`
- Create `apps/api/tests/test_dashboard_registry.py`

Test first:

```python
# apps/api/tests/test_dashboard_registry.py
from pathlib import Path

from app.services.dashboard_registry import load_dashboard_registry


def test_registry_sources_have_existing_sql_files() -> None:
    registry = load_dashboard_registry()

    for source in registry.sources.values():
        sql_path = Path("sql") / source.sql
        assert sql_path.exists(), source.sql


def test_account_spend_is_derived_not_a_snowflake_query() -> None:
    registry = load_dashboard_registry()

    assert "account_spend_daily" in registry.derived_datasets
    assert "account_spend_daily" not in registry.sources


def test_sql_uses_bind_parameters_and_has_no_write_statements() -> None:
    registry = load_dashboard_registry()

    for source in registry.sources.values():
        sql = (Path("sql") / source.sql).read_text().lower()
        assert "%(window_days)s" in sql
        assert "{window_days}" not in sql
        assert " insert " not in sql
        assert " update " not in sql
        assert " delete " not in sql
        assert " merge " not in sql
        assert " drop " not in sql
```

SQL body requirements:

- [ ] `service_spend_daily.sql` queries `SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY` grouped by date and service type.
- [ ] `warehouse_spend_daily.sql` queries warehouse metering history grouped by date and warehouse.
- [ ] `query_compute_by_user_daily.sql` queries query attribution history grouped by date and user where available.
- [ ] `database_storage_daily.sql` queries database storage usage history grouped by date and database.
- [ ] Every SQL file excludes the current UTC date and bounds the window with `%(window_days)s`.
- [ ] `account_spend_daily` is derived by rolling up `service_spend_daily`; no `account_spend_daily.sql`.

Verification:

```bash
rtk npm run test:api
```

### Task 7: Metric Engine And Demo Data

Delegate to: metrics worker.

Depends on: Task 2.

Parallel-safe: yes with Tasks 6, 8, 9, and 10.

Files:

- Create `apps/api/app/services/cost_metrics.py`
- Create `apps/api/app/services/demo_data.py`
- Create `apps/api/tests/test_cost_metrics.py`
- Create `apps/api/tests/test_demo_data.py`

Test first:

```python
# apps/api/tests/test_cost_metrics.py
from datetime import date

from app.services.cost_metrics import build_dashboard_summary, derive_account_spend_daily


def test_average_daily_credits_uses_complete_window_days_not_sparse_rows() -> None:
    service_rows = [
        {"usage_date": date(2026, 6, 5), "service_type": "WAREHOUSE_METERING", "credits_used": 30.0},
        {"usage_date": date(2026, 6, 7), "service_type": "WAREHOUSE_METERING", "credits_used": 60.0},
    ]
    account_rows = derive_account_spend_daily(service_rows)

    summary = build_dashboard_summary(
        account_spend_daily=account_rows,
        warehouse_spend_daily=[],
        database_storage_daily=[],
        complete_day_count=3,
        storage_price_usd_per_tb_month=None,
    )

    assert summary.total_credits == 90.0
    assert summary.average_daily_credits == 30.0
    assert summary.estimated_monthly_credits == 900.0


def test_storage_bytes_uses_latest_complete_day_gauge() -> None:
    summary = build_dashboard_summary(
        account_spend_daily=[],
        warehouse_spend_daily=[],
        database_storage_daily=[
            {"usage_date": date(2026, 6, 5), "average_database_bytes": 1_000_000_000_000, "average_failsafe_bytes": 0},
            {"usage_date": date(2026, 6, 6), "average_database_bytes": 2_000_000_000_000, "average_failsafe_bytes": 500_000_000_000},
        ],
        complete_day_count=2,
        storage_price_usd_per_tb_month=23.0,
    )

    assert summary.storage_bytes == 2_500_000_000_000
    assert summary.estimated_monthly_storage_cost_usd == 57.5
```

Implementation requirements:

- [ ] Define typed metric result models.
- [ ] Derive `account_spend_daily` from `service_spend_daily`.
- [ ] Exclude current UTC date in the query layer or complete-window calculation.
- [ ] Treat storage as a gauge using latest complete date.
- [ ] Keep demo data deterministic.

Verification:

```bash
rtk npm run test:api
rtk npm run lint:api
```

### Task 8: Backend Auth And Org Guard

Delegate to: backend auth worker.

Depends on: Tasks 2 and 4.

Parallel-safe: yes with Tasks 6, 7, 9, and 10.

Files:

- Create `apps/api/app/auth.py`
- Create `apps/api/tests/test_auth.py`

Test first:

```python
# apps/api/tests/test_auth.py
import pytest
from fastapi import HTTPException

from app.auth import AuthContext, require_org_membership


def test_demo_route_allows_no_user_when_auth_not_required() -> None:
    context = AuthContext(user_id=None, auth_required=False, memberships=set())

    assert require_org_membership(context, "demo-org", allow_demo=True) is None


def test_org_route_rejects_missing_membership() -> None:
    context = AuthContext(user_id="user-1", auth_required=True, memberships=set())

    with pytest.raises(HTTPException) as exc_info:
        require_org_membership(context, "org-1")

    assert exc_info.value.status_code == 403


def test_org_route_accepts_member() -> None:
    context = AuthContext(user_id="user-1", auth_required=True, memberships={"org-1"})

    assert require_org_membership(context, "org-1") is None
```

Implementation requirements:

- [ ] `AUTH_REQUIRED=false` can bypass auth only for local demo routes.
- [ ] `AUTH_REQUIRED=true` requires a valid session/JWT.
- [ ] Org-scoped routes must verify membership.
- [ ] `require_org_membership` raises `fastapi.HTTPException` for rejected access and returns `None` for allowed access.
- [ ] Keep Supabase JWT validation behind a test seam.

Verification:

```bash
rtk npm run test:api
```

### Task 9: Web Dashboard Contracts And Demo Rendering

Delegate to: web dashboard worker.

Depends on: Tasks 3 and 5.

Parallel-safe: yes with Tasks 6, 7, 8, and 10.

Files:

- Create `apps/web/src/lib/dashboard-contracts.ts`
- Create `apps/web/src/lib/dashboard-contracts.test.ts`
- Create `apps/web/src/lib/demo-dashboard-data.ts`
- Create `apps/web/src/components/dashboard/cost-dashboard.tsx`
- Create `apps/web/src/components/dashboard/cost-dashboard.test.tsx`
- Modify `apps/web/src/app/dashboard/page.tsx`

Test first:

```ts
// apps/web/src/lib/dashboard-contracts.test.ts
import { describe, expect, it } from "vitest";

import { parseDashboardDatasets } from "./dashboard-contracts";
import { demoDashboardDatasets } from "./demo-dashboard-data";

describe("parseDashboardDatasets", () => {
  it("accepts the demo dashboard response shape", () => {
    const parsed = parseDashboardDatasets(demoDashboardDatasets);

    expect(parsed.run.status).toBe("completed");
    expect(parsed.datasets.service_spend_daily.length).toBeGreaterThan(0);
  });
});
```

```tsx
// apps/web/src/components/dashboard/cost-dashboard.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CostDashboard } from "./cost-dashboard";
import { demoDashboardDatasets } from "../../lib/demo-dashboard-data";

describe("CostDashboard", () => {
  it("renders the required dashboard sections", () => {
    render(<CostDashboard data={demoDashboardDatasets} />);

    expect(screen.getByText("Total credits")).toBeInTheDocument();
    expect(screen.getByText("Warehouse spend")).toBeInTheDocument();
    expect(screen.getByText("Service spend")).toBeInTheDocument();
    expect(screen.getByText("Compute by user")).toBeInTheDocument();
    expect(screen.getByText("Storage by database")).toBeInTheDocument();
  });
});
```

Implementation requirements:

- [ ] Use the Tremor components verified in Task 3.
- [ ] Add `"use client"` to `cost-dashboard.tsx` because it renders Tremor components.
- [ ] Treat `demo-dashboard-data.ts` as a web test fixture and last-resort local render fallback only.
- [ ] Runtime dashboard data should come from the API demo endpoint once Task 11 is integrated.
- [ ] Keep the fixture shape validated by `parseDashboardDatasets` so it cannot silently drift from the API contract.
- [ ] Keep Python demo API response keys exactly aligned with the spec JSON dataset keys; Task 12 must fetch the API demo endpoint and parse it with the TS contract to catch drift.
- [ ] Keep dashboard dimensions stable.
- [ ] Render account, warehouse, service, user compute, database storage, top warehouses, and run status.
- [ ] No savings estimate UI.

Verification:

```bash
rtk npm run test:web
rtk npm run typecheck
```

### Task 10: Vercel Deployment Spike

Delegate to: deployment worker.

Depends on: Tasks 1, 3, and 5.

Parallel-safe: yes with Tasks 6, 7, 8, and 9 after Task 5 is complete.

Files:

- Create or modify `vercel.ts` if selected
- Create `docs/deployment.md`
- Modify package scripts if the deployment spike requires a build script

Worker brief:

- [ ] Decide one Vercel project vs separate web/API services.
- [ ] Verify whether `apps/api/app/main.py` can be the Vercel FastAPI entrypoint or whether an adapter/script mapping is needed.
- [ ] Prefer `vercel.ts` unless the spike proves `vercel.json` is simpler for this layout.
- [ ] Document local `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`.
- [ ] Document deployed API URL strategy: same-origin `/api`, Vercel service routing env vars, or explicit API URL.
- [ ] Do not block local MVP if user chooses to defer Vercel project connection.

Verification:

```bash
rtk npm --workspace apps/web run build
rtk npm run typecheck
```

User checkpoint 2:

- [ ] Ask user whether to connect Vercel now or defer until local MVP works.
- [ ] If connecting now, ask user to confirm Vercel project/team setup.

## Phase 3: Run APIs And Dashboard Integration

### Task 11: Demo Run API And Aggregate Dataset Persistence

Delegate to: run API worker.

Depends on: Tasks 6, 7, and 8.

Parallel-safe: no with Task 12 until API contract is stable.

Files:

- Create `apps/api/app/models.py`
- Create `apps/api/app/routes/dashboard_runs.py`
- Modify `apps/api/app/main.py`
- Create `apps/api/tests/test_demo_dashboard_run.py`
- Create `apps/api/tests/test_dataset_retention.py`

Test first:

```python
# apps/api/tests/test_demo_dashboard_run.py
from fastapi.testclient import TestClient

from app.main import app


def test_demo_run_returns_completed_run_and_datasets() -> None:
    client = TestClient(app)

    run_response = client.get("/api/dashboard-runs/demo")
    datasets_response = client.get("/api/dashboard-runs/demo/datasets")

    assert run_response.status_code == 200
    assert run_response.json()["status"] == "completed"
    assert datasets_response.status_code == 200
    assert "service_spend_daily" in datasets_response.json()["datasets"]
```

```python
# apps/api/tests/test_dataset_retention.py
from datetime import datetime, timedelta, timezone

from app.routes.dashboard_runs import dataset_is_expired


def test_expired_dataset_is_treated_as_unavailable() -> None:
    expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert dataset_is_expired(expires_at) is True
```

Implementation requirements:

- [ ] Register `/api/dashboard-runs/demo` before UUID run routes or constrain dynamic route IDs to UUID.
- [ ] Implement `GET /api/dashboard-runs/demo`.
- [ ] Implement `GET /api/dashboard-runs/demo/datasets`.
- [ ] Implement `POST /api/dashboard-runs`.
- [ ] Implement `GET /api/dashboard-runs/{run_id}`.
- [ ] Implement `GET /api/dashboard-runs/{run_id}/datasets`.
- [ ] Implement `DELETE /api/dashboard-runs/{run_id}`.
- [ ] Persist aggregate datasets for completed Snowflake runs.
- [ ] Use lazy retention for expired persisted aggregate datasets.
- [ ] Do not persist raw Snowflake rows.

Verification:

```bash
rtk npm run test:api
rtk npm run lint:api
```

### Task 12: Dashboard Run UI And Polling

Delegate to: dashboard integration worker.

Depends on: Tasks 9 and 11.

Parallel-safe: no.

Files:

- Modify `apps/web/src/lib/api-client.ts`
- Create `apps/web/src/lib/dashboard-api.ts`
- Create `apps/web/src/lib/dashboard-api.test.ts`
- Modify `apps/web/src/app/dashboard/page.tsx`
- Modify `apps/web/src/components/dashboard/cost-dashboard.tsx`
- Create `apps/web/src/components/dashboard/run-status.tsx`
- Create `apps/web/src/components/dashboard/run-status.test.tsx`

Test first:

```tsx
// apps/web/src/components/dashboard/run-status.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunStatus } from "./run-status";

describe("RunStatus", () => {
  it("renders failed run errors as user-safe messages", () => {
    render(<RunStatus status="failed" message="Could not access Snowflake Account Usage." />);

    expect(screen.getByText("Could not access Snowflake Account Usage.")).toBeInTheDocument();
  });

  it("renders expired dataset state", () => {
    render(<RunStatus status="expired" message="Run data expired. Start a new analysis." />);

    expect(screen.getByText("Run data expired. Start a new analysis.")).toBeInTheDocument();
  });
});
```

Implementation requirements:

- [ ] Add a run start action.
- [ ] Add `"use client"` to `run-status.tsx` and any dashboard component that uses React state/effects or Tremor client components.
- [ ] Poll run metadata until completed or failed.
- [ ] Fetch datasets after completion.
- [ ] Add a fetch-and-parse test that mocks the API demo endpoint response and validates it with `parseDashboardDatasets`.
- [ ] Show loading, running, completed, failed, and expired states.
- [ ] Use demo endpoint when `DATA_SOURCE=demo`.

Verification:

```bash
rtk npm run test:web
rtk npm run typecheck
```

User checkpoint 3:

- [ ] Tell user to run `npm run dev`.
- [ ] Tell user to visit `http://localhost:3000`.
- [ ] With `AUTH_REQUIRED=false`, user confirms demo dashboard loads without Supabase or Snowflake setup.

## Phase 4: Snowflake And Auth Integration

### Task 13: Snowflake Client And Validation Service

Delegate to: Snowflake backend worker.

Depends on: Tasks 2 and 6.

Parallel-safe: yes with Task 15 frontend auth work only after Task 12 is stable.

Files:

- Create `apps/api/app/services/snowflake_client.py`
- Create `apps/api/tests/test_snowflake_client.py`

Test first:

```python
# apps/api/tests/test_snowflake_client.py
from unittest.mock import Mock, patch

from app.services.snowflake_client import execute_source_query, validate_snowflake_connection


def test_execute_source_query_uses_named_bind_params() -> None:
    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with patch("app.services.snowflake_client.snowflake.connector.connect", return_value=connection):
        execute_source_query("select %(window_days)s as window_days", {"window_days": 30})

    cursor.execute.assert_called_once_with(
        "select %(window_days)s as window_days",
        {"window_days": 30},
    )
```

Implementation requirements:

- [ ] Load private key from `SNOWFLAKE_PRIVATE_KEY_PATH` only in FastAPI.
- [ ] Export `validate_snowflake_connection` from `app.services.snowflake_client`; Task 14 imports or wraps this exact symbol.
- [ ] Never return or log private key paths or key contents.
- [ ] Use Snowflake connector bind params for runtime values.
- [ ] Validate window bounds before execution.
- [ ] Map auth, role, warehouse, and privilege failures to user-safe messages.

Verification:

```bash
rtk npm run test:api
rtk npm run lint:api
```

### Task 14: Snowflake Validation Route And Real Run Path

Delegate to: Snowflake route worker.

Depends on: Tasks 8, 11, and 13.

Parallel-safe: no.

Files:

- Create `apps/api/app/routes/snowflake.py`
- Modify `apps/api/app/routes/dashboard_runs.py`
- Modify `apps/api/app/main.py`
- Create `apps/api/tests/test_snowflake_validation.py`
- Create `apps/api/tests/test_snowflake_dashboard_run.py`

Test first:

```python
# apps/api/tests/test_snowflake_validation.py
from fastapi.testclient import TestClient

from app.main import app


def test_snowflake_validation_returns_user_safe_error(monkeypatch) -> None:
    def fail_validation() -> None:
        raise PermissionError("raw private backend detail")

    monkeypatch.setattr("app.routes.snowflake.validate_snowflake_connection", fail_validation)
    client = TestClient(app)

    response = client.post("/api/snowflake/validate")

    assert response.status_code in {400, 403}
    assert "raw private backend detail" not in response.text
```

Implementation requirements:

- [ ] Implement `POST /api/snowflake/validate`.
- [ ] Validate access to required Account Usage views.
- [ ] In `DATA_SOURCE=snowflake`, `POST /api/dashboard-runs` executes approved SQL sources and persists aggregate datasets.
- [ ] `GET /api/dashboard-runs/{run_id}/datasets` returns persisted aggregate data without re-running Snowflake.
- [ ] Add audit events for validation, run creation, dataset retrieval, and deletion.
- [ ] In `AUTH_REQUIRED=false` demo mode without an organization, skip org-scoped database writes and audit events rather than writing null-org audit rows.
- [ ] In authenticated mode or Snowflake mode with an organization, write audit events with organization context.

Verification:

```bash
rtk npm run test:api
rtk npm run lint:api
```

User checkpoint 4:

- [ ] Ask user to create `.env.local` with:

```bash
DATA_SOURCE=snowflake
AUTH_REQUIRED=false
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
SNOWFLAKE_ACCOUNT=
SNOWFLAKE_USER=
SNOWFLAKE_ROLE=
SNOWFLAKE_WAREHOUSE=
SNOWFLAKE_DATABASE=SNOWFLAKE
SNOWFLAKE_SCHEMA=ACCOUNT_USAGE
SNOWFLAKE_PRIVATE_KEY_PATH=/absolute/path/to/key.p8
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=
GREYSIGHT_DEFAULT_WINDOW_DAYS=30
STORAGE_PRICE_USD_PER_TB_MONTH=
```

- [ ] Ask user to confirm the private key path exists locally.
- [ ] Ask user to run `npm run dev`, validate Snowflake, and start one real dashboard run.

### Task 15: Supabase Passwordless Auth And Org Shell

Delegate to: frontend auth worker.

Depends on: Tasks 5, 8, and 12.

Parallel-safe: yes with Task 13 after Task 12 is stable.

Files:

- Create `apps/web/src/lib/supabase-client.ts`
- Create `apps/web/src/lib/auth-mode.ts`
- Create `apps/web/src/lib/auth-mode.test.ts`
- Create `apps/web/src/components/auth/login-form.tsx`
- Create `apps/web/src/components/auth/login-form.test.tsx`
- Create `apps/web/src/components/org/org-shell.tsx`
- Create `apps/web/src/components/org/org-shell.test.tsx`
- Modify `apps/web/src/app/dashboard/page.tsx`

Test first:

```ts
// apps/web/src/lib/auth-mode.test.ts
import { describe, expect, it } from "vitest";

import { authIsRequired } from "./auth-mode";

describe("authIsRequired", () => {
  it("allows local demo bypass when AUTH_REQUIRED is false", () => {
    expect(authIsRequired("false")).toBe(false);
  });

  it("requires auth when AUTH_REQUIRED is true", () => {
    expect(authIsRequired("true")).toBe(true);
  });
});
```

Implementation requirements:

- [ ] Add passwordless email login UI.
- [ ] Add session state handling.
- [ ] Add org creation/access shell.
- [ ] Pass auth token to API client when auth is enabled.
- [ ] Keep `AUTH_REQUIRED=false` local demo bypass working.
- [ ] Do not expose Snowflake key paths or backend env vars in frontend.

Verification:

```bash
rtk npm run test:web
rtk npm run typecheck
```

User checkpoint 5:

- [ ] Ask user to provide Supabase dev values:

```bash
AUTH_REQUIRED=true
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=
```

- [ ] Ask user to test passwordless email login locally.
- [ ] Ask user to confirm org shell is reachable after login.

## Phase 5: Docs And Final Verification

### Task 16: Local Development, Snowflake, Security, Deployment Docs, And AGENTS.md

Delegate to: docs worker.

Depends on: Tasks 10, 12, 14, and 15.

Parallel-safe: no.

Files:

- Create `docs/local-development.md`
- Create `docs/snowflake-setup.md`
- Create `docs/security-model.md`
- Create `docs/deployment.md` if not already created by Task 10
- Modify `README.md` if present or create it if absent
- Modify `AGENTS.md`

Worker brief:

- [ ] Document `npm install --ignore-scripts` and `npm run dev`.
- [ ] Document `AUTH_REQUIRED=false` local demo mode.
- [ ] Document Supabase env setup for auth mode.
- [ ] Document Snowflake `.env.local` setup with `SNOWFLAKE_PRIVATE_KEY_PATH`.
- [ ] Document least-privilege Snowflake setup.
- [ ] Document that raw Snowflake rows are not persisted.
- [ ] Document aggregate dataset retention and lazy deletion.
- [ ] Document Vercel deployment shape chosen by Task 10.
- [ ] State that savings estimate is post-MVP and not included.
- [ ] Update `AGENTS.md` only with concise, durable project navigation guidance discovered during implementation.
- [ ] Add a compact project-structure section covering major areas such as `apps/web`, `apps/api`, `sql/snowflake`, `sql/dashboard_sources.yml`, `supabase/migrations`, and `docs`.
- [ ] Add a short "where to update" table for major work types: dashboard UI, API routes, metric calculations, Snowflake source queries, Supabase schema/RLS, auth/org behavior, local dev/deployment docs.
- [ ] Keep `AGENTS.md` free of long command lists, env var dumps, API response examples, SQL bodies, and milestone history. Link to docs files instead.
- [ ] If a section of the app was not implemented, do not add pretend guidance for it.

Verification:

```bash
rtk npm run test
rtk npm run lint
rtk npm run typecheck
rtk npm run test:coverage
```

### Task 17: Manager Final Integration Pass

Delegate to: no implementation worker; manager owns this.

Depends on: all tasks.

Parallel-safe: no.

Steps:

- [ ] Run final commands:

```bash
rtk npm run test
rtk npm run lint
rtk npm run typecheck
rtk npm run test:coverage
rtk npm run dev
```

- [ ] Verify `http://localhost:8000/health`.
- [ ] Verify `AUTH_REQUIRED=false` local demo dashboard with no Supabase/Snowflake env.
- [ ] Verify `AUTH_REQUIRED=true` Supabase login after user provides env values.
- [ ] Verify Snowflake validation and one bounded run after user provides Snowflake env values.
- [ ] Confirm dashboard responses contain no private key paths, raw SQL, raw query records, or detailed raw rows.
- [ ] Confirm no savings estimate UI or backend route exists.
- [ ] Run `rtk git status --short`.
- [ ] Summarize changed files, verification output, and remaining deployment/account setup notes to the user.

## Worker Review Template

For every delegated task, the manager sends this after the implementation worker returns:

Spec-compliance review:

```text
Review the worker changes for the assigned task only. Compare the diff against docs/superpowers/plans/2026-06-08-cost-dashboard-mvp.md and docs/specs/2026-06-08-cost-dashboard-mvp.md. Report only blocking or material gaps: missing files, missing tests, violated security rules, incorrect scope, route/API contract mismatches, or missing user checkpoint behavior. Do not edit files.
```

Code-quality review:

```text
Review the accepted task diff for maintainability, correctness, security, and test quality. Focus on bugs, unsafe auth/secret handling, flaky tests, bad frontend state patterns, SQL injection risk, and local-dev breakage. Do not request broad refactors unless they block the MVP. Do not edit files.
```
