# Parallel base queries + progressive chart rendering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut dashboard load from ~2 min to ~the slowest single query by running all base Snowflake queries in parallel, and render each chart the moment its data lands instead of waiting for the whole run.

**Architecture:** (Phase 0) Persist the Snowflake account locator at connection-setup so org queries need no runtime pre-query. (Phase 1) Fan out all base queries on a process-wide bounded thread pool. (Phase 2) Make the run asynchronous — `POST` returns `202 running`, a background worker writes datasets incrementally, and a partial `/view` reports per-section readiness. (Phase 3) The frontend polls `/view` and reveals each section as it becomes ready.

**Tech Stack:** FastAPI + `snowflake-connector-python` (sync) + `concurrent.futures`, in-memory run repository, Next.js (App Router, client components) + Tremor/Recharts, Supabase Postgres migrations, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-06-19-parallel-progressive-dashboard-design.md`

## Global Constraints

- **Run commands** from repo root unless noted. API: `uv run pytest tests/<file>` (from `apps/api/`). Web: `npx vitest run <file>` (from `apps/web/`). Lint: `npm run lint`; typecheck: `npm run typecheck`.
- **Both test suites are hermetic** — no Snowflake/Supabase network. Always inject the `execute` fn / fetchers in tests.
- **Execute only registry SQL.** Never construct ad-hoc Snowflake SQL outside `sql/dashboard_sources.yml` assets (+ the AI branches + `current_account()`).
- **Never widen RLS.** The locator migration only adds a column + RPC param; preserve existing policies.
- **Immutability:** return new objects; never mutate inputs (repo writes go through the existing `RLock`).
- **Concurrency cap:** `GREYSIGHT_QUERY_CONCURRENCY` default **8**, enforced **process-wide** by one module-level `ThreadPoolExecutor`, not per-run.
- **`DashboardRunStatus`** already = `Literal["queued","running","completed","failed","expired","deleted"]` — no model change needed for `running`.
- **Single-process assumption** is unchanged (in-memory repo + module-level executor + background worker all require one worker process).
- **Demo mode** (`DATA_SOURCE=demo`) must keep returning a fully-`ready` completed view on the first call.

---

## Phase 0 — Persist `account_locator` at connection setup

Removes the `current_account()` dependency edge: with the locator stored, org queries (`org_spend_daily`, `rate_sheet_daily`) bind it directly and all queries launch at t=0. `capacity_balance_daily` is org-scoped (no locator). Legacy rows (null locator) are handled by a one-time run-only runtime fetch (Task 7), without persisting the fallback value.

### Task 1: Migration — add `account_locator` column + RPC param

**Files:**
- Create: `supabase/migrations/202606190001_connection_account_locator.sql`
- Reference (do NOT edit): `supabase/migrations/202606160001_org_snowflake_connections.sql:9-24,222-291`

**Interfaces:**
- Produces: `organization_snowflake_connections.account_locator text` (nullable); `create_org_with_snowflake_connection(...)` gains a trailing `p_account_locator text default null` param and writes it.

- [ ] **Step 1: Write the migration**

```sql
-- 202606190001_connection_account_locator.sql
-- Persist the Snowflake account locator (current_account()) captured at
-- connection validation, so dashboard org-usage queries bind it directly
-- instead of running a serial current_account() pre-query. Nullable: legacy
-- rows use a one-time run-only runtime fallback until re-validated.

alter table organization_snowflake_connections
  add column if not exists account_locator text;

-- Recreate the create RPC with a trailing account_locator param (default null
-- keeps any existing callers working). Body mirrors the original plus the new
-- column in the connection insert.
create or replace function create_org_with_snowflake_connection(
  p_user_id uuid,
  p_org_name text,
  p_account text,
  p_user text,
  p_role text,
  p_warehouse text,
  p_database text,
  p_schema text,
  p_private_key_pem text,
  p_passphrase text,
  p_account_locator text default null
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  new_org_id uuid;
begin
  -- NOTE: copy the original function body from
  -- 202606160001_org_snowflake_connections.sql verbatim, with ONLY the
  -- connection insert changed to include account_locator (below). The org
  -- creation, membership, and Vault secret steps are unchanged.
  -- ... original org + membership + secret logic ...

  insert into organization_snowflake_connections (
    organization_id, account, snowflake_user, role, warehouse,
    database, schema, has_passphrase, status, last_validated_at,
    created_by_user_id, account_locator
  )
  values (
    new_org_id, p_account, p_user, p_role, p_warehouse,
    nullif(p_database, ''), nullif(p_schema, ''),
    p_passphrase is not null and p_passphrase <> '',
    'active', now(), p_user_id, nullif(p_account_locator, '')
  );

  return new_org_id;
end;
$$;
```

> Implementer note: open `202606160001_org_snowflake_connections.sql` lines 222-291, copy the full function body, and apply only the two edits shown (param list + insert columns/values). Do not invent the org/secret logic.

- [ ] **Step 2: Verify the migration applies**

Run: `npx supabase db reset` (or the project's migration check). Expected: applies cleanly, no errors; `\d organization_snowflake_connections` shows `account_locator`.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/202606190001_connection_account_locator.sql
git commit -m "feat(db): add account_locator to org snowflake connections + RPC param"
```

### Task 2: Capture the locator during connection validation

**Files:**
- Modify: `apps/api/app/services/snowflake_client.py:169-180` (`validate_snowflake_connection`)
- Modify: `apps/api/app/routes/onboarding.py:93-152` (`_validate_and_create`)
- Modify: `apps/api/app/services/org_provisioning.py` (`create_org_with_connection` wrapper)
- Test: `apps/api/tests/test_onboarding.py`, `apps/api/tests/test_snowflake_client.py` (extend existing)

**Interfaces:**
- Produces: `validate_snowflake_connection(config) -> str | None` (returns the account locator). `create_org_with_connection(..., p_account_locator: str | None = None)`.
- Consumes: `_connect`, `_validation_queries` (unchanged).

- [ ] **Step 1: Write the failing test for locator capture**

```python
# tests/test_snowflake_client.py
from app.services import snowflake_client


def test_validate_snowflake_connection_returns_account_locator(monkeypatch):
    executed: list[str] = []

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, *args): executed.append(sql)
        def fetchone(self): return ("XY12345",)

    class FakeConn:
        def cursor(self): return FakeCursor()
        def close(self): pass

    monkeypatch.setattr(snowflake_client, "_connect", lambda config: FakeConn())
    locator = snowflake_client.validate_snowflake_connection()
    assert locator == "XY12345"
    assert any("current_account()" in sql.lower() for sql in executed)
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_snowflake_client.py::test_validate_snowflake_connection_returns_account_locator -v`
Expected: FAIL (`validate_snowflake_connection` returns `None`).

- [ ] **Step 3: Implement — return the locator**

```python
# snowflake_client.py
def validate_snowflake_connection(
    config: SnowflakeConnectionConfig | None = None,
) -> str | None:
    """Validate access and return the account locator (current_account())."""
    connection = _connect(config)
    try:
        with connection.cursor() as cursor:
            for sql in _validation_queries():
                cursor.execute(sql)
            cursor.execute("select current_account()")
            row = cursor.fetchone()
            return str(row[0]) if row and row[0] is not None else None
    except Exception as exc:
        raise SnowflakeValidationError(_user_safe_message(exc)) from None
    finally:
        connection.close()
```

- [ ] **Step 4: Thread the locator through onboarding**

```python
# onboarding.py _validate_and_create — replace the validate call + RPC call
    try:
        account_locator = validate_snowflake_connection(config)
    except SnowflakeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except SnowflakeConfigurationError:
        raise HTTPException(
            status_code=422,
            detail="Snowflake private key could not be loaded. Check the PEM and passphrase.",
        ) from None
    ...
        organization_id = create_org_with_connection(
            p_user_id=auth_context.user_id,
            p_org_name=request.org_name,
            p_account=account,
            p_user=request.user,
            p_role=request.role,
            p_warehouse=request.warehouse,
            p_database=request.database or "",
            p_schema=request.schema or "",
            p_private_key_pem=request.private_key_pem,
            p_passphrase=request.passphrase or "",
            p_account_locator=account_locator or "",
        )
```

In `org_provisioning.py`, add `p_account_locator: str | None = None` to `create_org_with_connection`'s signature and include it in the RPC payload dict it POSTs.

> Follow-up check: confirm onboarding is the only connection save/validate path. If `routes/snowflake.py` has a separate connection upsert RPC, update that route to capture/pass `p_account_locator` too; if that is outside Task 2's scope, record it explicitly before moving on.

- [ ] **Step 5: Run validation + onboarding tests — expect PASS**

Run: `uv run pytest tests/test_snowflake_client.py tests/test_onboarding.py -v`
Expected: PASS. (Update any existing onboarding test stub whose `validate_snowflake_connection` mock returned `None` to return a locator string, and whose `create_org_with_connection` asserts kwargs — add `p_account_locator`.)

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/snowflake_client.py apps/api/app/routes/onboarding.py apps/api/app/services/org_provisioning.py apps/api/tests/
git commit -m "feat(api): capture account_locator at connection validation"
```

### Task 3: Expose the locator through the connection resolver

**Files:**
- Modify: `apps/api/app/services/org_connection_resolver.py` (`OrgConnectionRow`, `SupabaseConnectionFetcher.__call__` select + mapping, `resolve_snowflake_config`)
- Modify: `apps/api/app/services/snowflake_client.py` (`SnowflakeConnectionConfig` + `from_environment`)
- Test: `apps/api/tests/test_org_connection_resolver.py` (extend)

**Interfaces:**
- Produces: `SnowflakeConnectionConfig.account_locator: str | None = None` (ignored by `connector_kwargs`). `OrgConnectionRow.account_locator: str | None`. `resolve_snowflake_config(...)` populates `config.account_locator` from the row (or `SNOWFLAKE_ACCOUNT_LOCATOR` env in self-host).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_org_connection_resolver.py
from app.config import Settings
from app.services.org_connection_resolver import OrgConnectionRow, resolve_snowflake_config


def _row(**over):
    base = dict(account="myorg-acct", snowflake_user="u", role="r", warehouse="w",
               database=None, schema=None, private_key_pem="pem", passphrase=None,
               status="active", account_locator="XY12345")
    base.update(over)
    return OrgConnectionRow(**base)


def test_resolver_threads_account_locator():
    config = resolve_snowflake_config(
        "org-1", Settings(), fetch_connection=lambda _id: _row()
    )
    assert config.account == "myorg-acct"
    assert config.account_locator == "XY12345"
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_org_connection_resolver.py::test_resolver_threads_account_locator -v`
Expected: FAIL (`OrgConnectionRow` has no `account_locator`; `SnowflakeConnectionConfig` has no `account_locator`).

- [ ] **Step 3: Implement**

In `snowflake_client.py`, add to `SnowflakeConnectionConfig` (dataclass): `account_locator: str | None = None`. Add to `from_environment`: `account_locator=os.environ.get("SNOWFLAKE_ACCOUNT_LOCATOR")`. `connector_kwargs()` is unchanged (it already cherry-picks connection fields, so the locator is ignored there).

In `org_connection_resolver.py`:
- Add `account_locator: str | None = None` to `OrgConnectionRow`.
- In the fetcher `select` string add `account_locator`: `"account,account_locator,snowflake_user,role,warehouse,database,schema,status,secret_id"`.
- In the returned `OrgConnectionRow(...)` add `account_locator=meta.get("account_locator")`.
- In `resolve_snowflake_config`, in the `row is not None` branch add `account_locator=row.account_locator` to the `SnowflakeConnectionConfig(...)` call.

- [ ] **Step 4: Run it — expect PASS**

Run: `uv run pytest tests/test_org_connection_resolver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/org_connection_resolver.py apps/api/app/services/snowflake_client.py apps/api/tests/test_org_connection_resolver.py
git commit -m "feat(api): thread account_locator from connection row into config"
```

---

## Phase 1 — Parallel execution (Layer 1, shippable on its own)

After this phase `POST /api/dashboard-runs` still blocks, but internally runs every base query concurrently (~26s instead of ~2min). No progressive rendering yet.

### Task 4: Concurrency config + module-level query executor

**Files:**
- Modify: `apps/api/app/config.py` (`Settings.query_concurrency`)
- Create: `apps/api/app/services/query_concurrency.py`
- Test: `apps/api/tests/test_query_concurrency.py`

**Interfaces:**
- Produces: `Settings.query_concurrency: int` (default 8, env `GREYSIGHT_QUERY_CONCURRENCY`). `query_concurrency.get_query_executor()` returns a single module-level `ThreadPoolExecutor`; `query_concurrency.configure(max_workers: int)` rebuilds that process-wide executor at app startup and in tests. No semaphore is acquired or released.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_query_concurrency.py
import threading
import time
from app.services import query_concurrency


def test_executor_respects_max_workers_cap():
    query_concurrency.configure(2)
    active = 0
    peak = 0
    lock = threading.Lock()

    def worker():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)
        with lock:
            active -= 1

    futures = [
        query_concurrency.get_query_executor().submit(worker)
        for _ in range(6)
    ]
    for future in futures:
        future.result(timeout=2)
    assert peak <= 2
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_query_concurrency.py -v`
Expected: FAIL (`No module named app.services.query_concurrency`).

- [ ] **Step 3: Implement**

```python
# apps/api/app/services/query_concurrency.py
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

DEFAULT_MAX_WORKERS = 8
_executor = ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS)


def configure(max_workers: int) -> None:
    """Rebuild the process-wide query executor. Call once at app startup."""
    global _executor
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    old_executor = _executor
    _executor = ThreadPoolExecutor(max_workers=max_workers)
    old_executor.shutdown(wait=False, cancel_futures=True)


def get_query_executor() -> ThreadPoolExecutor:
    return _executor
```

Add to `config.py` `Settings`:

```python
    query_concurrency: int = Field(
        default=8, gt=0, le=64,
        validation_alias=AliasChoices("GREYSIGHT_QUERY_CONCURRENCY"),
    )
```

In `apps/api/app/main.py`, call `query_concurrency.configure(Settings().query_concurrency)` at startup (next to existing app setup).

- [ ] **Step 4: Run it — expect PASS**

Run: `uv run pytest tests/test_query_concurrency.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/query_concurrency.py apps/api/app/config.py apps/api/app/main.py apps/api/tests/test_query_concurrency.py
git commit -m "feat(api): process-wide query executor + config"
```

### Task 5: Parallel source runner

**Files:**
- Create: `apps/api/app/services/parallel_source_runner.py`
- Test: `apps/api/tests/test_parallel_source_runner.py`

**Interfaces:**
- Consumes: `query_concurrency.get_query_executor`, `DashboardSource` (`.sql`), `SnowflakeQueryError`, `SnowflakeObjectUnavailableError`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class SourceOutcome:
      key: str
      rows: list[dict] | None        # None when unavailable
      available: bool

  def run_sources_parallel(
      jobs: list[SourceJob],           # SourceJob(key, sql, bind_params)
      execute: ExecuteFn,              # (sql, bind_params) -> list[dict]
      *,
      on_complete: Callable[[SourceOutcome], None] | None = None,
  ) -> dict[str, SourceOutcome]
  ```
  Runs each job concurrently on the module-level executor whose
  `max_workers` is capped by `Settings.query_concurrency`. The executor is
  shared process-wide and is not created per call. A
  `SnowflakeObjectUnavailableError` / `SnowflakeQueryError` → `available=False`
  (NOT a raise). `on_complete` is called as each job finishes (for Phase 2's
  incremental writes). Availability is from the exception, never row count.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_parallel_source_runner.py
import time
from app.services.parallel_source_runner import SourceJob, run_sources_parallel
from app.services.snowflake_client import SnowflakeQueryError
from app.services import query_concurrency


def test_runs_all_jobs_and_collects_rows():
    query_concurrency.configure(8)
    jobs = [SourceJob(f"k{i}", f"sql{i}", {"window_days": 100}) for i in range(4)]

    def execute(sql, params):
        return [{"sql": sql}]

    outcomes = run_sources_parallel(jobs, execute)
    assert set(outcomes) == {"k0", "k1", "k2", "k3"}
    assert outcomes["k0"].available is True
    assert outcomes["k0"].rows == [{"sql": "sql0"}]


def test_unavailable_source_does_not_fail_run():
    query_concurrency.configure(8)
    jobs = [SourceJob("ok", "s", {}), SourceJob("bad", "s", {})]

    def execute(sql, params):  # noqa: F811
        if params.get("fail"):
            raise SnowflakeQueryError("boom")
        return []

    jobs = [SourceJob("ok", "s", {}), SourceJob("bad", "s", {"fail": True})]
    outcomes = run_sources_parallel(jobs, execute)
    assert outcomes["ok"].available is True
    assert outcomes["ok"].rows == []          # zero rows != unavailable
    assert outcomes["bad"].available is False
    assert outcomes["bad"].rows is None


def test_runs_concurrently():
    query_concurrency.configure(8)
    jobs = [SourceJob(f"k{i}", "s", {}) for i in range(4)]

    def execute(sql, params):
        time.sleep(0.2)
        return []

    start = time.monotonic()
    run_sources_parallel(jobs, execute)
    # 4 jobs * 0.2s sequential = 0.8s; parallel should be well under 0.5s
    assert time.monotonic() - start < 0.5


def test_on_complete_called_per_job():
    query_concurrency.configure(8)
    seen = []
    jobs = [SourceJob("a", "s", {}), SourceJob("b", "s", {})]
    run_sources_parallel(jobs, lambda s, p: [], on_complete=lambda o: seen.append(o.key))
    assert sorted(seen) == ["a", "b"]
```

- [ ] **Step 2: Run them — expect FAIL**

Run: `uv run pytest tests/test_parallel_source_runner.py -v`
Expected: FAIL (`No module named ...parallel_source_runner`).

- [ ] **Step 3: Implement**

```python
# apps/api/app/services/parallel_source_runner.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.query_concurrency import get_query_executor
from app.services.snowflake_client import SnowflakeQueryError

ExecuteFn = Callable[[str, dict[str, Any]], list[dict[str, Any]]]


@dataclass(frozen=True)
class SourceJob:
    key: str
    sql: str
    bind_params: dict[str, Any]


@dataclass(frozen=True)
class SourceOutcome:
    key: str
    rows: list[dict[str, Any]] | None
    available: bool


def run_sources_parallel(
    jobs: list[SourceJob],
    execute: ExecuteFn,
    *,
    on_complete: Callable[[SourceOutcome], None] | None = None,
) -> dict[str, SourceOutcome]:
    """Execute each job concurrently on the process-wide query executor.

    A SnowflakeQueryError (incl. the object-unavailable subclass) marks that
    single source unavailable; it never aborts the others. Availability comes
    from the exception type, not row count.
    """
    def _run(job: SourceJob) -> SourceOutcome:
        try:
            rows = execute(job.sql, job.bind_params)
            outcome = SourceOutcome(key=job.key, rows=rows, available=True)
        except SnowflakeQueryError:
            outcome = SourceOutcome(key=job.key, rows=None, available=False)
        if on_complete is not None:
            on_complete(outcome)
        return outcome

    if not jobs:
        return {}
    outcomes = list(get_query_executor().map(_run, jobs))
    return {o.key: o for o in outcomes}
```

- [ ] **Step 4: Run them — expect PASS**

Run: `uv run pytest tests/test_parallel_source_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/parallel_source_runner.py apps/api/tests/test_parallel_source_runner.py
git commit -m "feat(api): parallel source runner with per-source availability"
```

### Task 6: Parallelize AI Cortex branches

**Files:**
- Modify: `apps/api/app/services/ai_consumption.py:128-145` (`fetch_ai_consumption_daily`)
- Test: `apps/api/tests/test_ai_consumption.py` (extend)

**Interfaces:**
- Consumes: `run_sources_parallel`, `SourceJob`. Keeps the existing return
  `tuple[list[dict], list[str]]` (rows, skipped branch ids) so callers/tests are unchanged.

- [ ] **Step 1: Write the failing test (concurrency + preserved skip semantics)**

```python
# tests/test_ai_consumption.py
import time
from app.services import ai_consumption
from app.services.snowflake_client import SnowflakeObjectUnavailableError


def test_ai_branches_run_in_parallel_and_skip_unavailable():
    calls = []

    def execute(sql, params):
        time.sleep(0.1)
        calls.append(sql)
        if "cortex_search" in sql:   # one unavailable branch
            raise SnowflakeObjectUnavailableError("nope")
        return [{"row": 1}]

    start = time.monotonic()
    rows, skipped = ai_consumption.fetch_ai_consumption_daily(execute, window_days=30)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5                      # 10 branches * 0.1 serial = 1s
    assert any("cortex_search" in s for s in skipped)
    assert len(rows) > 0
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_ai_consumption.py -v`
Expected: FAIL (current loop is sequential → elapsed ≈ 1s).

- [ ] **Step 3: Implement**

```python
# ai_consumption.py
from app.services.parallel_source_runner import SourceJob, run_sources_parallel


def fetch_ai_consumption_daily(
    execute: ExecuteFn,
    *,
    window_days: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    bind_params = {"window_days": window_days}
    jobs = [SourceJob(branch.id, branch.sql, bind_params) for branch in AI_CONSUMPTION_BRANCHES]
    outcomes = run_sources_parallel(jobs, execute)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for branch in AI_CONSUMPTION_BRANCHES:        # deterministic order
        outcome = outcomes[branch.id]
        if outcome.available and outcome.rows is not None:
            rows.extend(outcome.rows)
        else:
            skipped.append(branch.id)
    return rows, skipped
```

> Note: this changes skip-detection from catching only `SnowflakeObjectUnavailableError` to any `SnowflakeQueryError` (the runner collapses both to `available=False`). That is acceptable — a hard query error on one branch should skip that branch, not fail the whole AI source. Confirm existing AI tests still pass; adjust any that asserted a raw raise.

- [ ] **Step 4: Run it — expect PASS**

Run: `uv run pytest tests/test_ai_consumption.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/ai_consumption.py apps/api/tests/test_ai_consumption.py
git commit -m "feat(api): run AI Cortex branches in parallel"
```

### Task 7: Rewire `build_snowflake_dashboard_data` to fan out (no dependency edge)

**Files:**
- Modify: `apps/api/app/services/dashboard_datasets.py:42-128` (replace the three sequential `_fetch_source_group` calls + the `_derive_account_locator` pre-query)
- Test: `apps/api/tests/test_dashboard_datasets.py` (extend)

**Interfaces:**
- Consumes: `run_sources_parallel`, `SourceJob`, `connection_config.account_locator`.
- Produces: same `SnowflakeDashboardData` shape. The `account_locator` now comes from `connection_config` (or a one-time `current_account()` fallback when null); `current_account` dataset is synthesized from it. All sources are submitted in one fan-out.

- [ ] **Step 1: Write the failing test (single parallel fan-out, locator from config)**

```python
# tests/test_dashboard_datasets.py
from app.config import Settings
from app.services.dashboard_datasets import build_snowflake_dashboard_data
from app.services.snowflake_client import SnowflakeConnectionConfig


def test_build_uses_config_locator_without_current_account_query():
    executed = []

    def execute(sql, params):
        executed.append(sql)
        # org queries must receive the stored locator, never a pre-query result
        if "organization_usage" in sql:
            assert params.get("account_locator") == "XY12345"
        return []

    data = build_snowflake_dashboard_data(
        Settings(),
        execute=execute,
        connection_config=SnowflakeConnectionConfig(account_locator="XY12345"),
    )
    # current_account() is NOT run as a gating query when the locator is known
    assert not any("current_account()" in sql.lower() for sql in executed)
    assert data.datasets["current_account"] == [{"account_locator": "XY12345"}]
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_dashboard_datasets.py::test_build_uses_config_locator_without_current_account_query -v`
Expected: FAIL (current code calls `_derive_account_locator` which runs `current_account()`).

- [ ] **Step 3: Implement the fan-out**

Replace the locator-derivation + three `_fetch_source_group` calls (lines 71-99) with a single parallel fan-out. Key points:
- `account_locator = connection_config.account_locator if connection_config else None`; if `None`, fall back to one `execute(current_account_sql, {})` call and use its value for this run only. Do not persist this fallback locator here; re-validation/onboarding remains the persistence path.
- Build `SourceJob`s for: the 4 account-usage sources (`bind_params={"window_days": FETCH_WINDOW_DAYS}`), `org_spend_daily` + `rate_sheet_daily` (`bind_params={"window_days": FETCH_WINDOW_DAYS, "account_locator": account_locator}`), and `capacity_balance_daily` (`bind_params={"window_days": FETCH_WINDOW_DAYS}` — **no locator**, it is org-scoped).
- Call `run_sources_parallel(jobs, execute_source)`; map outcomes into `account_datasets` / `org_datasets` / `capacity_datasets`, treating `available=False` as the empty/unavailable group exactly as `_fetch_source_group` did. Preserve the `not org_available and not account_available -> DashboardSourcesUnavailableError` rule.
- Keep the post-fetch transforms (`bound_user_compute_rows`, `derive_account_spend_daily`, `build_top_warehouses_table`) unchanged.
- Synthesize `current_account = [{"account_locator": account_locator}]` from the value (no separate query when it came from config).

```python
# dashboard_datasets.py (sketch of the replacement block)
    account_locator = (
        connection_config.account_locator if connection_config else None
    )
    if account_locator is None:
        # Legacy-row fallback: one-time fetch, used for this run only.
        locator_rows = execute_source(
            registry.sources["current_account"].sql, {}
        )
        account_locator = (
            str(locator_rows[0]["account_locator"]) if locator_rows else None
        )

    window = {"window_days": FETCH_WINDOW_DAYS}
    locator_window = {**window, "account_locator": account_locator}
    jobs: list[SourceJob] = []
    for key, source in account_sources.items():
        jobs.append(SourceJob(key, source.sql, window))
    for key, source in org_sources.items():            # org_spend, rate_sheet
        jobs.append(SourceJob(key, source.sql, locator_window))
    for key, source in optional_org_sources.items():   # capacity — no locator
        jobs.append(SourceJob(key, source.sql, window))

    outcomes = run_sources_parallel(jobs, execute_source)
    # group outcomes back into account/org/capacity dicts + availability flags
    ...
```

> Implementer note: keep `_sources_by_kind`, `OPTIONAL_ORG_SOURCE_IDS`, `_build_metadata`, summary build, and `_json_ready_rows` exactly as today. Only the fetch mechanism changes. `org_sources` here excludes capacity (it's in `optional_org_sources`), so capacity correctly gets the no-locator `window`.

- [ ] **Step 4: Run the dataset tests — expect PASS**

Run: `uv run pytest tests/test_dashboard_datasets.py -v`
Expected: PASS. Fix any existing test that injected `execute` and asserted sequential call order — order is now non-deterministic; assert on the *set* of executed SQL, not the sequence.

- [ ] **Step 5: Run the full api suite**

Run: `uv run pytest -q`
Expected: PASS (POST still returns `completed`; only timing/order changed).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/dashboard_datasets.py apps/api/tests/test_dashboard_datasets.py
git commit -m "feat(api): fan out all base queries in parallel (no current_account pre-query)"
```

---

## Phase 2 — Async run lifecycle (Layer 2 backend)

`POST` returns `202 running`; a background worker writes each dataset as it lands; `/view` serves a partial view with a top-level `section_statuses` map while `running`, and the authoritative completed view once `finalize_run` fires. Builds on Phase 1's `run_sources_parallel` (its `on_complete` hook is the incremental-write seam).

### Task 8: Repository — running-run lifecycle methods

**Files:**
- Modify: `apps/api/app/routes/dashboard_runs.py` (`InMemoryDashboardRunRepository`: add lifecycle methods + a module constant + TTL state)
- Test: `apps/api/tests/test_dashboard_run_repository.py` (create — co-locate with any existing repo tests if present; otherwise new file)

**Interfaces:**
- Consumes: existing `RLock`, `StoredDashboardDataset`, `_source_bounds_for_dataset_rows`, `dataset_is_expired`, and the existing `claim_source` / `complete_source` / `fail_source` source-record lifecycle used by deferred sources.
- Produces/changes:
  ```python
  BASE_RUN_SOURCE_KEYS: tuple[str, ...]   # the 7 base sources gating the run

  def create_running_run(self, *, organization_id: UUID | None, source: str,
      window_days: int, expected_sources: tuple[str, ...], retention_days: int,
  ) -> DashboardRun                        # status="running", all sources "pending"
  def set_dataset(self, run_id: UUID, key: str, rows: list[dict[str, Any]]) -> None
  def finalize_run(self, run_id: UUID, *, status: str, summary: dict[str, Any],
      metadata: dict[str, Any] | None, datasets: dict[str, list[dict[str, Any]]],
      error: str | None = None,
  ) -> None
  ```
  Generalize the existing `claim_source` / `complete_source` / `fail_source`
  methods so they work for the base-query sources in `BASE_RUN_SOURCE_KEYS` as
  well as deferred AI sources. Do not add parallel `mark_source_ready`,
  `mark_source_unavailable`, or `get_source_statuses` methods. Add only the
  missing running-run creation, dataset write, finalize, and TTL pieces. All
  incremental writes re-check `status == "running"` under the lock (staleness
  guard) and no-op otherwise. A wall-clock TTL auto-expires runs stuck
  `running`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_run_repository.py
from datetime import datetime, timedelta, timezone
from uuid import UUID

import app.routes.dashboard_runs as dr
from app.routes.dashboard_runs import (
    BASE_RUN_SOURCE_KEYS,
    InMemoryDashboardRunRepository,
)


def _new_running_repo() -> tuple[InMemoryDashboardRunRepository, UUID]:
    repo = InMemoryDashboardRunRepository()
    run = repo.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    return repo, UUID(run.id)


def test_create_running_run_seeds_pending_sources():
    repo, run_id = _new_running_repo()
    run = repo.get_run(run_id)
    assert run is not None and run.status == "running"
    statuses = _source_statuses_from_existing_records(repo, run_id)
    assert set(statuses) == set(BASE_RUN_SOURCE_KEYS)
    assert all(s == "pending" for s in statuses.values())


def test_set_dataset_and_mark_ready_updates_view_inputs():
    repo, run_id = _new_running_repo()
    repo.set_dataset(run_id, "service_spend_daily", [{"usage_date": "2026-06-01"}])
    repo.complete_source(run_id, "service_spend_daily")
    assert _source_statuses_from_existing_records(repo, run_id)[
        "service_spend_daily"
    ] == "ready"
    view_inputs = repo.get_view_inputs(run_id)
    assert view_inputs is not None
    _run, datasets, _metadata, bounds = view_inputs
    assert datasets["service_spend_daily"] == [{"usage_date": "2026-06-01"}]
    # provisional bounds reflect the only landed usage_date
    assert bounds.source_start_date.isoformat() == "2026-06-01"


def test_fail_source_marks_base_source_unavailable():
    repo, run_id = _new_running_repo()
    repo.fail_source(run_id, "capacity_balance_daily", error="unavailable")
    assert _source_statuses_from_existing_records(repo, run_id)[
        "capacity_balance_daily"
    ] == "unavailable"


def test_finalize_run_sets_completed_and_authoritative_bounds():
    repo, run_id = _new_running_repo()
    repo.finalize_run(
        run_id,
        status="completed",
        summary={"total_credits": 1.0},
        metadata=None,
        datasets={"service_spend_daily": [{"usage_date": "2026-05-01"}]},
    )
    run = repo.get_run(run_id)
    assert run is not None and run.status == "completed"
    view_inputs = repo.get_view_inputs(run_id)
    assert view_inputs is not None
    _run, datasets, _meta, bounds = view_inputs
    assert datasets["service_spend_daily"] == [{"usage_date": "2026-05-01"}]
    assert bounds.source_end_date.isoformat() == "2026-05-01"


def test_writes_after_terminal_state_are_discarded():
    repo, run_id = _new_running_repo()
    repo.finalize_run(run_id, status="completed", summary={}, metadata=None,
                      datasets={"service_spend_daily": [{"usage_date": "2026-05-01"}]})
    # A late worker write must not mutate the finalized run.
    repo.set_dataset(run_id, "service_spend_daily", [{"usage_date": "1999-01-01"}])
    repo.complete_source(run_id, "service_spend_daily")
    _run, datasets, _m, _b = repo.get_view_inputs(run_id)
    assert datasets["service_spend_daily"] == [{"usage_date": "2026-05-01"}]


def test_running_run_ttl_auto_expires(monkeypatch):
    repo, run_id = _new_running_repo()
    # Force the deadline into the past, then read the run.
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    with repo._lock:
        repo._running_deadlines[run_id] = past
    run = repo.get_run(run_id)
    assert run is not None and run.status == "expired"


def _source_statuses_from_existing_records(
    repo: InMemoryDashboardRunRepository, run_id: UUID
) -> dict[str, str]:
    """Use the repo's existing deferred-source record read path in real tests."""
    return {
        key: repo.get_source(run_id, key).status
        for key in BASE_RUN_SOURCE_KEYS
    }
```

- [ ] **Step 2: Run them — expect FAIL**

Run: `uv run pytest tests/test_dashboard_run_repository.py -v`
Expected: FAIL (`cannot import name 'BASE_RUN_SOURCE_KEYS'`; running-run/dataset/finalize/TTL support missing; existing source lifecycle may not yet accept base-query source ids).

- [ ] **Step 3: Implement — add the constant + methods**

Add the module constant near `ACCOUNT_USAGE_DATASET_KEYS` (top of `dashboard_runs.py`):

```python
# Base sources whose readiness gates the progressive view. AI stays deferred
# (its own /sources poll); current_account, account_spend_daily, and
# top_warehouses_table are synthesized/derived at finalize, not streamed.
BASE_RUN_SOURCE_KEYS: tuple[str, ...] = (
    "warehouse_spend_daily",
    "service_spend_daily",
    "query_compute_by_user_daily",
    "database_storage_daily",
    "org_spend_daily",
    "rate_sheet_daily",
    "capacity_balance_daily",
)

# Wall-clock ceiling for a run stuck in "running"; independent of dataset
# retention. A worker that dies without finalizing can never leave a run
# permanently running.
RUNNING_RUN_TTL_SECONDS = 300
```

In `InMemoryDashboardRunRepository.__init__`, add the deadline map:

```python
        self._running_deadlines: dict[UUID, datetime] = {}
```

Add `self._running_deadlines.clear()` to `clear()`, and `self._running_deadlines.pop(run_id, None)` to both `_expire_run_locked` and `delete_run`.

Generalize `claim_source`, `complete_source`, and `fail_source` so
`BASE_RUN_SOURCE_KEYS` are valid source ids and `create_running_run` seeds their
records as `pending`. Add the missing lifecycle methods (place the new methods
near the existing source lifecycle):

```python
    def create_running_run(
        self,
        *,
        organization_id: UUID | None,
        source: str,
        window_days: int,
        expected_sources: tuple[str, ...],
        retention_days: int,
    ) -> DashboardRun:
        now = datetime.now(timezone.utc)
        run_id = uuid4()
        run = DashboardRun(
            id=str(run_id),
            organization_id=organization_id,
            source=source,
            status="running",
            window_days=window_days,
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._runs[run_id] = run
            self._summaries[run_id] = {}
            self._metadata[run_id] = None
            self._datasets[run_id] = {}
            self._source_bounds[run_id] = _source_bounds_for_dataset_rows({})
            self._source_states[run_id] = {
                key: {"status": "pending"} for key in expected_sources
            }
            self._retention_days[run_id] = retention_days
            self._running_deadlines[run_id] = now + timedelta(
                seconds=RUNNING_RUN_TTL_SECONDS
            )
        return run

    def _is_running_locked(self, run_id: UUID) -> bool:
        """Staleness guard: True only if the run is still actively running."""
        run = self._runs.get(run_id)
        if run is None or run.status != "running":
            return False
        deadline = self._running_deadlines.get(run_id)
        if deadline is not None and deadline <= datetime.now(timezone.utc):
            self._expire_run_locked(run_id, run)
            return False
        return True

    def set_dataset(
        self, run_id: UUID, key: str, rows: list[dict[str, Any]]
    ) -> None:
        with self._lock:
            if not self._is_running_locked(run_id):
                return
            retention_days = self._retention_days.get(run_id, 7)
            expires_at = datetime.now(timezone.utc) + timedelta(days=retention_days)
            stored = self._datasets.setdefault(run_id, {})
            stored[key] = StoredDashboardDataset(
                aggregate_dataset=rows, retention_expires_at=expires_at
            )
            # Recompute provisional bounds from everything landed so far.
            self._store_source_bounds(
                run_id,
                {k: d.aggregate_dataset for k, d in stored.items()},
            )

    def finalize_run(
        self,
        run_id: UUID,
        *,
        status: str,
        summary: dict[str, Any],
        metadata: dict[str, Any] | None,
        datasets: dict[str, list[dict[str, Any]]],
        error: str | None = None,
    ) -> None:
        with self._lock:
            # Only finalize a run that is still running; never resurrect a
            # deleted/expired run or re-finalize a completed one.
            if not self._is_running_locked(run_id):
                return
            run = self._runs[run_id]
            now = datetime.now(timezone.utc)
            self._runs[run_id] = run.model_copy(
                update={
                    "status": status,
                    "completed_at": now,
                    "updated_at": now,
                    "error": error,
                }
            )
            self._running_deadlines.pop(run_id, None)
            retention_days = self._retention_days.get(run_id, 7)
            expires_at = now + timedelta(days=retention_days)
            self._summaries[run_id] = summary
            self._metadata[run_id] = metadata
            self._datasets[run_id] = {
                key: StoredDashboardDataset(
                    aggregate_dataset=rows, retention_expires_at=expires_at
                )
                for key, rows in datasets.items()
            }
            self._store_source_bounds(run_id, datasets)
```

Add the retention map to `__init__` and `clear()` (used by the new methods so streamed + finalized datasets inherit the run's retention window):

```python
        # __init__:
        self._retention_days: dict[UUID, int] = {}
        # clear():
        self._retention_days.clear()
```

Also pop it in `_expire_run_locked` and `delete_run`: `self._retention_days.pop(run_id, None)`.

> Implementer note: `DashboardRun` must accept `status="running"`, `completed_at=None`, and an `error` field. Confirm `app/models.py`'s `DashboardRun` already allows these (the run-status `Literal` includes `running` per Global Constraints; `error` is already an optional field used by the view route's `_record...` and the frontend contract). If `error` is absent from the model, add `error: str | None = None`.

> Finalize note: `finalize_run` must call or inline the same TTL check used by `_is_running_locked` before marking a run `completed` or `failed`. If the running-run TTL has expired, treat the run as expired and do not complete it.

- [ ] **Step 4: Run them — expect PASS**

Run: `uv run pytest tests/test_dashboard_run_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full api suite (no regressions to existing repo paths)**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routes/dashboard_runs.py apps/api/tests/test_dashboard_run_repository.py
git commit -m "feat(api): running-run lifecycle methods on dashboard run repository"
```

### Task 9: Background worker + async `POST` (202 running)

**Files:**
- Modify: `apps/api/app/services/dashboard_datasets.py` (`build_snowflake_dashboard_data` gains an `on_source_outcome` hook forwarded to `run_sources_parallel`)
- Modify: `apps/api/app/routes/dashboard_runs.py` (`create_dashboard_run` + `_create_snowflake_dashboard_run` → spawn worker; add `_run_dashboard_worker`)
- Test: `apps/api/tests/test_dashboard_runs_async.py` (create), `apps/api/tests/test_dashboard_datasets.py` (extend)

**Interfaces:**
- Consumes: `run_sources_parallel`/`SourceOutcome` (Task 5), repo lifecycle methods (Task 8), `build_snowflake_dashboard_data`.
- Produces: `build_snowflake_dashboard_data(..., on_source_outcome: Callable[[SourceOutcome], None] | None = None)`; `POST /api/dashboard-runs` returns `202` with a `running` run for snowflake (demo stays `201 completed`). Worker `_run_dashboard_worker(run_id: UUID, settings: Settings, connection_config, summary_window_days: int) -> None` always drives the run to a terminal state.

- [ ] **Step 1: Write the failing worker test (terminal-state guarantee + incremental writes)**

```python
# tests/test_dashboard_runs_async.py
import time
from uuid import UUID

import app.routes.dashboard_runs as dr
from app.config import Settings


def _wait_terminal(run_id: UUID, timeout: float = 2.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = dr.dashboard_run_repository.get_run(run_id)
        if run is not None and run.status in {"completed", "failed", "expired"}:
            return run.status
        time.sleep(0.02)
    raise AssertionError("worker never reached a terminal state")


def test_worker_reaches_completed_and_streams_datasets(monkeypatch):
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None, source="snowflake", window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS, retention_days=7,
    )
    run_id = UUID(run.id)

    # Stub the heavy build to drive the incremental hook then return a snapshot.
    from app.services import dashboard_datasets

    class _Data:
        summary = {"total_credits": 0.0}
        class metadata:  # noqa: N801
            @staticmethod
            def model_dump(mode="json"):
                return None
        datasets = {"service_spend_daily": [{"usage_date": "2026-05-01"}]}

    def fake_build(settings, *, summary_window_days, connection_config,
                   on_source_outcome=None):
        if on_source_outcome is not None:
            on_source_outcome(
                dashboard_datasets_outcome("service_spend_daily",
                                           [{"usage_date": "2026-05-01"}], True)
            )
        return _Data()

    monkeypatch.setattr(dr, "build_snowflake_dashboard_data", fake_build)
    dr._run_dashboard_worker(run_id, Settings(), object(), 30)
    assert _wait_terminal(run_id) == "completed"
    assert dr.dashboard_run_repository.get_source(run_id, "service_spend_daily").status == "ready"


def test_worker_unhandled_exception_finalizes_failed(monkeypatch):
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None, source="snowflake", window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS, retention_days=7,
    )
    run_id = UUID(run.id)

    def boom(*a, **k):
        raise RuntimeError("snowflake exploded")

    monkeypatch.setattr(dr, "build_snowflake_dashboard_data", boom)
    dr._run_dashboard_worker(run_id, Settings(), object(), 30)
    assert _wait_terminal(run_id) == "failed"
```

> Note: replace `dashboard_datasets_outcome(...)` with the real constructor — `from app.services.parallel_source_runner import SourceOutcome` and build `SourceOutcome(key=..., rows=..., available=...)`. (Kept inline-pseudo here only to show intent; use the real import in the test.)

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_dashboard_runs_async.py -v`
Expected: FAIL (`_run_dashboard_worker` / `build_snowflake_dashboard_data` hook do not exist).

- [ ] **Step 3: Add the `on_source_outcome` hook to the builder**

In `dashboard_datasets.py`, thread an optional callback into `build_snowflake_dashboard_data` and forward it to the runner:

```python
from typing import Callable
from app.services.parallel_source_runner import SourceOutcome  # if not already imported

def build_snowflake_dashboard_data(
    settings: Settings,
    *,
    summary_window_days: int,
    connection_config: SnowflakeConnectionConfig | None = None,
    execute: ExecuteFn | None = None,
    on_source_outcome: Callable[[SourceOutcome], None] | None = None,
) -> SnowflakeDashboardData:
    ...
    outcomes = run_sources_parallel(
        jobs, execute_source, on_complete=on_source_outcome
    )
    ...
```

> Implementer note: keep the existing `execute`-injection parameter exactly as Task 7 left it (tests inject it). Only add `on_source_outcome` and pass it as `on_complete`. The callback fires from worker threads as each base source lands — it must be cheap and thread-safe (it only calls the lock-guarded repo).

- [ ] **Step 4: Implement the worker + wire `POST`**

Add the worker and switch the create path to async (top of `dashboard_runs.py` already imports `Settings`; add `from threading import Thread` alongside `RLock`, and import `SourceOutcome`):

```python
def _run_dashboard_worker(
    run_id: UUID,
    settings: Settings,
    connection_config: Any,
    summary_window_days: int,
) -> None:
    """Drive the parallel run to completion, streaming each dataset as it lands.

    Wrapped so the run ALWAYS reaches a terminal state — a crash mid-fetch
    finalizes `failed` rather than leaving the run stuck `running`.
    """
    repo = dashboard_run_repository

    def on_outcome(outcome: SourceOutcome) -> None:
        if outcome.available and outcome.rows is not None:
            repo.set_dataset(run_id, outcome.key, outcome.rows)
            repo.complete_source(run_id, outcome.key)
        else:
            repo.fail_source(run_id, outcome.key, error="unavailable")

    try:
        data = build_snowflake_dashboard_data(
            settings,
            summary_window_days=summary_window_days,
            connection_config=connection_config,
            on_source_outcome=on_outcome,
        )
    except DashboardSourcesUnavailableError:
        repo.finalize_run(
            run_id, status="failed", summary={}, metadata=None, datasets={},
            error="Could not query Snowflake billing or Account Usage data.",
        )
        return
    except Exception as exc:  # noqa: BLE001 — terminal-state guarantee
        repo.finalize_run(
            run_id, status="failed", summary={}, metadata=None, datasets={},
            error=str(exc),
        )
        return
    repo.finalize_run(
        run_id,
        status="completed",
        summary=data.summary,
        metadata=data.metadata.model_dump(mode="json"),
        datasets=data.datasets,
    )
```

Rewrite `_create_snowflake_dashboard_run` to resolve the connection synchronously (so a missing connection still 409s on `POST`), register a `running` run, set `202`, and spawn the worker:

```python
def _create_snowflake_dashboard_run(
    request: DashboardRunCreateRequest,
    settings: Settings,
    response: Response,
) -> DashboardRun:
    from app.services.org_connection_resolver import (
        OrgConnectionNotConfiguredError,
        resolve_snowflake_config,
    )
    from app.services.snowflake_runtime import get_connection_fetcher

    try:
        connection_config = resolve_snowflake_config(
            str(request.organization_id),
            settings,
            fetch_connection=get_connection_fetcher(settings),
        )
    except OrgConnectionNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This organization has no Snowflake connection configured.",
        ) from None

    run = dashboard_run_repository.create_running_run(
        organization_id=request.organization_id,
        source=request.source,
        window_days=FETCH_WINDOW_DAYS,
        expected_sources=BASE_RUN_SOURCE_KEYS,
        retention_days=request.retention_days,
    )
    response.status_code = status.HTTP_202_ACCEPTED
    Thread(
        target=_run_dashboard_worker,
        args=(UUID(run.id), settings, connection_config, request.window_days),
        daemon=True,
    ).start()
    return run
```

Update `create_dashboard_run` to pass the `Response` through:

```python
from fastapi import Response

@router.post("", response_model=DashboardRun, status_code=status.HTTP_201_CREATED)
def create_dashboard_run(
    request: DashboardRunCreateRequest,
    response: Response,
    _auth_context: AuthContext = Depends(require_auth_context),
) -> DashboardRun:
    _require_dashboard_run_membership(_auth_context, request.organization_id)
    settings = Settings()
    if settings.data_source == "snowflake":
        run = _create_snowflake_dashboard_run(request, settings, response)
    else:
        run = _create_demo_dashboard_run(request)   # stays 201 completed
    _record_dashboard_run_created(run)
    return run
```

> Implementer note: the old synchronous `build_snowflake_dashboard_data` + `create_completed_snapshot` call inside `_create_snowflake_dashboard_run` is removed (the worker now owns it). The decorator keeps `201` so demo is unchanged; snowflake overrides to `202` on the `response`. Tests that asserted `POST` snowflake → `completed`/`201` move to Task 9's async tests (poll to `completed`).

- [ ] **Step 5: Run the dataset + async tests — expect PASS**

Run: `uv run pytest tests/test_dashboard_runs_async.py tests/test_dashboard_datasets.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full api suite; fix synchronous-POST assumptions**

Run: `uv run pytest -q`
Expected: PASS. Any existing test that did `POST` (snowflake) and immediately read a `completed` run must now: assert the `POST` returns `running`, then poll `get_run`/`/view` until `completed` (mirror `_wait_terminal`). Demo-mode POST tests are unchanged.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/dashboard_datasets.py apps/api/app/routes/dashboard_runs.py apps/api/tests/test_dashboard_runs_async.py apps/api/tests/test_dashboard_datasets.py
git commit -m "feat(api): async dashboard run worker; POST returns 202 running"
```

### Task 10: Add `section_statuses` to the view response model

**Files:**
- Modify: `apps/api/app/services/dashboard_view_models.py` (`DashboardViewResponse` + a `SectionStatus` literal)
- Test: `apps/api/tests/test_dashboard_view_models.py` (create or extend)

**Interfaces:**
- Produces: `SectionStatus = Literal["pending", "ready", "unavailable"]`; `DashboardViewResponse.section_statuses: dict[str, SectionStatus]` defaulting to all-`ready` (`{"overview": "ready", "warehouse": "ready", "storage": "ready"}`) for legacy/demo payloads. Completed and failed Snowflake views must not blindly use the default: the route should override `section_statuses` from the final source records so any source that ended unavailable is reflected in the completed/failed view.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_view_models.py
from app.services.dashboard_view_models import DashboardViewResponse


def test_section_statuses_defaults_all_ready():
    fields = DashboardViewResponse.model_fields
    assert "section_statuses" in fields
    default = fields["section_statuses"].default_factory()
    assert default == {"overview": "ready", "warehouse": "ready", "storage": "ready"}
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_dashboard_view_models.py::test_section_statuses_defaults_all_ready -v`
Expected: FAIL (`section_statuses` not in `model_fields`).

- [ ] **Step 3: Implement**

In `dashboard_view_models.py`, add the literal (next to `SpendBasis`) and the field:

```python
SectionStatus = Literal["pending", "ready", "unavailable"]


def _all_ready_section_statuses() -> dict[str, "SectionStatus"]:
    return {"overview": "ready", "warehouse": "ready", "storage": "ready"}
```

Add to `DashboardViewResponse` (after `ai_spend_summary`):

```python
    section_statuses: dict[str, SectionStatus] = Field(
        default_factory=_all_ready_section_statuses
    )
```

- [ ] **Step 4: Run it — expect PASS**

Run: `uv run pytest tests/test_dashboard_view_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/dashboard_view_models.py apps/api/tests/test_dashboard_view_models.py
git commit -m "feat(api): add section_statuses map to dashboard view response"
```

### Task 11: Per-section dependency map + partial `/view` for running runs

**Files:**
- Modify: `apps/api/app/routes/dashboard_runs.py` (`read_dashboard_run_view` — serve a provisional view + inject `section_statuses` while `running`)
- Test: `apps/api/tests/test_dashboard_runs_async.py` (extend — dependency map + partial-view transitions)

**Interfaces:**
- Produces:
  ```python
  SECTION_SOURCE_DEPENDENCIES: dict[str, tuple[str, ...]]
  def compute_section_statuses(source_statuses: dict[str, str]) -> dict[str, str]
  ```
  A section is `ready` when every dependency is `ready`; `unavailable` if any
  dependency is `unavailable`; otherwise `pending`.
- Keep `SECTION_SOURCE_DEPENDENCIES` and `compute_section_statuses` inline in
  `dashboard_runs.py` (or the view builder if that is cleaner) until a second
  consumer appears. Do not create a standalone `section_dependencies.py` module.
- Consumes: the existing repository source records generalized in Task 8, `build_dashboard_view`, `StoredSourceBounds`.

- [ ] **Step 1: Write the failing tests for the dependency map**

```python
# tests/test_dashboard_runs_async.py
import app.routes.dashboard_runs as dr


def test_sections_are_overview_warehouse_storage():
    assert set(dr.SECTION_SOURCE_DEPENDENCIES) == {"overview", "warehouse", "storage"}


def test_section_ready_only_when_all_deps_ready():
    all_ready = {key: "ready" for key in _all_dep_sources()}
    statuses = dr.compute_section_statuses(all_ready)
    assert statuses == {
        "overview": "ready",
        "warehouse": "ready",
        "storage": "ready",
    }


def test_pending_dep_keeps_section_pending():
    statuses = dr.compute_section_statuses({"warehouse_spend_daily": "pending"})
    assert statuses["warehouse"] == "pending"


def test_unavailable_dep_marks_section_unavailable():
    base = {key: "ready" for key in _all_dep_sources()}
    base["database_storage_daily"] = "unavailable"
    statuses = dr.compute_section_statuses(base)
    assert statuses["storage"] == "unavailable"


def _all_dep_sources() -> set[str]:
    return {s for deps in dr.SECTION_SOURCE_DEPENDENCIES.values() for s in deps}
```

- [ ] **Step 2: Run them — expect FAIL**

Run: `uv run pytest tests/test_dashboard_runs_async.py -v`
Expected: FAIL (`dashboard_runs` has no inline dependency map/status helper).

- [ ] **Step 3: Implement the dependency map**

```python
# apps/api/app/routes/dashboard_runs.py
# Source→section gating matrix. Each section renders as soon as its PRIMARY
# source(s) land. Secondary inputs the view builder already tolerates when
# empty (capacity_balance_daily, the billed org_spend override, rate_sheet
# currency conversion, the per-user warehouse breakdown) are intentionally
# EXCLUDED so their lag never blocks a section. Verify against
# build_dashboard_view's dataset reads when implementing (Codex findings 6 & 7):
#   - overview  -> total_spend + service breakdown + capacity  => service_spend_daily
#   - warehouse -> warehouse_spend                              => warehouse_spend_daily
#   - storage   -> storage_spend                                => database_storage_daily
SECTION_SOURCE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "overview": ("service_spend_daily",),
    "warehouse": ("warehouse_spend_daily",),
    "storage": ("database_storage_daily",),
}


def compute_section_statuses(source_statuses: dict[str, str]) -> dict[str, str]:
    """Roll per-source readiness up into per-section status.

    ready       — every dependency is "ready"
    unavailable — at least one dependency is "unavailable" or "failed"
    pending     — otherwise (a dependency is still pending/unknown)
    """
    result: dict[str, str] = {}
    for section, deps in SECTION_SOURCE_DEPENDENCIES.items():
        dep_states = [source_statuses.get(dep, "pending") for dep in deps]
        if any(state in {"unavailable", "failed"} for state in dep_states):
            result[section] = "unavailable"
        elif all(state == "ready" for state in dep_states):
            result[section] = "ready"
        else:
            result[section] = "pending"
    return result
```

> Dependency decision point: the map must account for secondary inputs required by converted-currency sections, especially `rate_sheet_daily`. During implementation either add the missing secondary entries to `SECTION_SOURCE_DEPENDENCIES`, or explicitly document that the rate source is omitted and that section readiness may be premature without it. Do not leave this implicit.

- [ ] **Step 4: Run them — expect PASS**

Run: `uv run pytest tests/test_dashboard_runs_async.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing partial-view test**

```python
# tests/test_dashboard_runs_async.py  (append)
from uuid import UUID

import app.routes.dashboard_runs as dr
from app.routes.dashboard_runs import read_dashboard_run_view
from app.auth import AuthContext


def _anon() -> AuthContext:
    # Auth disabled context used elsewhere in these tests; mirror the existing
    # helper in the suite if one exists.
    return AuthContext(auth_required=False, user_id=None, claims={})


def test_running_view_reports_section_statuses():
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None, source="snowflake", window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS, retention_days=7,
    )
    run_id = UUID(run.id)
    # Only warehouse data has landed.
    dr.dashboard_run_repository.set_dataset(
        run_id, "warehouse_spend_daily",
        [{"usage_date": "2026-06-01", "warehouse_name": "WH", "credits_used": 1.0,
          "credits_used_compute": 1.0}],
    )
    dr.dashboard_run_repository.complete_source(run_id, "warehouse_spend_daily")

    payload = read_dashboard_run_view(run_id, None, None, None, _anon())
    assert payload["run"]["status"] == "running"
    assert payload["section_statuses"]["warehouse"] == "ready"
    assert payload["section_statuses"]["overview"] == "pending"
    assert payload["section_statuses"]["storage"] == "pending"
```

- [ ] **Step 6: Run it — expect FAIL**

Run: `uv run pytest tests/test_dashboard_runs_async.py::test_running_view_reports_section_statuses -v`
Expected: FAIL (running view either 404s or omits `section_statuses`).

- [ ] **Step 7: Implement the running branch in `read_dashboard_run_view`**

Replace the body of `read_dashboard_run_view` after `view_inputs` is unpacked with a running-aware build. While `running`, ignore the caller's range (it would 409 against narrow provisional bounds) and build a custom view spanning the landed bounds, then inject `section_statuses`:

```python
    run, datasets, metadata, source_bounds = view_inputs
    datasets = {
        key: datasets.get(key, [])
        for key in EXPECTED_DASHBOARD_DATASET_KEYS
    }
    source_statuses = {
        key: dashboard_run_repository.get_source(run_id, key).status
        for key in BASE_RUN_SOURCE_KEYS
    } if run.source == "snowflake" else None
    if run.status == "running":
        # Provisional view: span whatever has landed; the date axis is
        # provisional until finalize_run. Section gating drives rendering.
        view = _prepared_view_or_http_error(
            run=run,
            datasets=datasets,
            metadata=metadata,
            source_bounds=source_bounds,
            window_days=None,
            start_date=source_bounds.source_start_date,
            end_date=source_bounds.source_end_date,
        )
        view = view.model_copy(
            update={
                "section_statuses": compute_section_statuses(source_statuses or {})
            }
        )
        _record_dashboard_run_view_retrieved(view)
        return view.model_dump(mode="json")

    view = _prepared_view_or_http_error(
        run=run,
        datasets=datasets,
        metadata=metadata,
        source_bounds=source_bounds,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    if source_statuses is not None:
        view = view.model_copy(
            update={"section_statuses": compute_section_statuses(source_statuses)}
        )
    _record_dashboard_run_view_retrieved(view)
    return view.model_dump(mode="json")
```

> Implementer note: before calling the full view builder with a running run's datasets, normalize every expected dataset key to `[]` when absent. Missing keys must not be passed raw to the view builder. Completed and failed views also compute `section_statuses` from the final source records (not all-ready defaults), so a source marked unavailable is visible in the final view. The running custom-range `[source_start, source_end]` always satisfies `resolve_dashboard_view_range`'s bounds check (effective_start == source_start, effective_end == min(end, through) ≤ source_end). When no usage dates have landed, provisional bounds are `today/today` and `build_dashboard_view` returns the empty view (through_date None) — every section stays `pending`, which is correct.

- [ ] **Step 8: Run the async + view suites — expect PASS**

Run: `uv run pytest tests/test_dashboard_runs_async.py tests/test_dashboard_view_models.py -v`
Expected: PASS.

- [ ] **Step 9: Full api suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add apps/api/app/routes/dashboard_runs.py apps/api/tests/test_dashboard_runs_async.py
git commit -m "feat(api): partial dashboard view with per-section status while running"
```

---

## Phase 3 — Progressive frontend (Layer 2, apps/web)

The frontend polls `/view` after the `202`, reveals each section the instant its `section_statuses` entry flips to `ready`, keeps skeletons for `pending`, and stops polling at `completed`/`failed`. The date-range fast path (cached re-derive after completion) is unchanged. Run commands from `apps/web/`.

### Task 12: Contract — parse top-level `section_statuses`

**Files:**
- Modify: `apps/web/src/lib/dashboard-contracts.ts` (`DashboardView` type + `parseDashboardView`)
- Test: `apps/web/src/lib/dashboard-contracts.test.ts` (extend)

**Interfaces:**
- Produces: `DashboardSectionStatus = "pending" | "ready" | "unavailable"`; `DashboardView.sectionStatuses: Record<"overview" | "warehouse" | "storage", DashboardSectionStatus>`. Absent in payload (legacy/demo) → defaults to all-`"ready"`, so existing parsers/components are untouched. Snowflake completed/failed payloads should include the route-computed final statuses from Task 11.

- [ ] **Step 1: Write the failing tests**

```typescript
// dashboard-contracts.test.ts (add)
import { describe, expect, it } from "vitest";
import { parseDashboardView } from "./dashboard-contracts";
import { validDashboardViewPayload } from "./__fixtures__/dashboard-view"; // or the inline fixture this suite already uses

describe("parseDashboardView section_statuses", () => {
  it("defaults to all-ready when absent", () => {
    const view = parseDashboardView(validDashboardViewPayload());
    expect(view.sectionStatuses).toEqual({
      overview: "ready",
      warehouse: "ready",
      storage: "ready",
    });
  });

  it("reads a provisional running payload", () => {
    const view = parseDashboardView({
      ...validDashboardViewPayload(),
      section_statuses: { overview: "pending", warehouse: "ready", storage: "unavailable" },
    });
    expect(view.sectionStatuses).toEqual({
      overview: "pending",
      warehouse: "ready",
      storage: "unavailable",
    });
  });
});
```

> Implementer note: reuse whatever valid-view fixture/builder `dashboard-contracts.test.ts` already defines instead of `validDashboardViewPayload()` if the name differs — do not invent a second fixture.

- [ ] **Step 2: Run them — expect FAIL**

Run: `npx vitest run src/lib/dashboard-contracts.test.ts`
Expected: FAIL (`sectionStatuses` is undefined).

- [ ] **Step 3: Implement**

Add the types (near `DashboardView`):

```typescript
export type DashboardSectionStatus = "pending" | "ready" | "unavailable";

export type DashboardViewSectionKey = "overview" | "warehouse" | "storage";

export type DashboardViewSectionStatuses = Record<
  DashboardViewSectionKey,
  DashboardSectionStatus
>;
```

Add to the `DashboardView` type:

```typescript
  sectionStatuses: DashboardViewSectionStatuses;
```

Add the section keys + status set + a parser, and wire it into `parseDashboardView`'s returned object:

```typescript
const DASHBOARD_VIEW_SECTION_KEYS = [
  "overview",
  "warehouse",
  "storage",
] as const satisfies readonly DashboardViewSectionKey[];

const DASHBOARD_SECTION_STATUSES = [
  "pending",
  "ready",
  "unavailable",
] as const satisfies readonly DashboardSectionStatus[];

const ALL_READY_SECTION_STATUSES: DashboardViewSectionStatuses = {
  overview: "ready",
  warehouse: "ready",
  storage: "ready",
};

function isDashboardSectionStatus(
  value: unknown,
): value is DashboardSectionStatus {
  return (
    typeof value === "string" &&
    (DASHBOARD_SECTION_STATUSES as readonly string[]).includes(value)
  );
}

function parseSectionStatuses(payload: unknown): DashboardViewSectionStatuses {
  // Absent (completed/legacy views) → all ready, preserving today's behavior.
  if (!hasViewValue(payload as Record<string, unknown>, "section_statuses", "sectionStatuses")) {
    return { ...ALL_READY_SECTION_STATUSES };
  }
  const record = readViewRecord(
    payload as Record<string, unknown>,
    "section_statuses",
    "sectionStatuses",
  );
  const result: DashboardViewSectionStatuses = { ...ALL_READY_SECTION_STATUSES };
  for (const key of DASHBOARD_VIEW_SECTION_KEYS) {
    const value = record[key];
    if (value === undefined) {
      continue; // a section the server omitted stays at its ready default
    }
    if (!isDashboardSectionStatus(value)) {
      throwInvalidDashboardView();
    }
    result[key] = value;
  }
  return result;
}
```

In `parseDashboardView`, add the field to the returned object (the `payload` is the record passed in — pass it to the parser):

```typescript
  return {
    schema_version: 1,
    run: parseDashboardViewRun(readViewRecord(payload, "run")),
    ...
    aiSpendSummary: ...,
    sectionStatuses: parseSectionStatuses(payload),
  };
```

- [ ] **Step 4: Run them + typecheck — expect PASS**

Run: `npx vitest run src/lib/dashboard-contracts.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/dashboard-contracts.ts apps/web/src/lib/dashboard-contracts.test.ts
git commit -m "feat(web): parse section_statuses from dashboard view payload"
```

### Task 13: Extend the existing polling helper for `/view`

**Files:**
- Modify: `apps/web/src/lib/dashboard-api.ts`
- Test: `apps/web/src/lib/dashboard-api.test.ts` (extend)

**Interfaces:**
- Consumes: `fetchDashboardView`, `DashboardView`, `DashboardViewRangeRequest`.
- Produces an extension to the existing polling helper rather than a second standalone dashboard-view poller. The helper should accept a fetcher and a terminal predicate, and it should call an optional `onResult`/`onView` callback after each fetch so progressive views can paint. If that is awkward in the existing helper, keep the `/view` polling loop local to `loadSnowflakeRun` in Task 14.
  ```typescript
  type PollUntilOptions<T> = {
    intervalMs?: number;
    maxAttempts?: number;
    onResult?: (result: T) => void;
  };
  function pollUntilTerminal<T>(
    fetcher: () => Promise<T>,
    isTerminal: (result: T) => boolean,
    options?: PollUntilOptions<T>,
  ): Promise<T>
  ```
  For `/view`, the fetcher calls `fetchDashboardView(runId, range, options)`, the terminal predicate checks `view.run.status`, and the callback receives every provisional/final view. Do not export a separate dashboard-view polling function.

**Acceptance criteria:**
- If the helper or inline loop exhausts `maxAttempts` without reaching a terminal status, it throws a timeout error. It must not return the last non-terminal view.

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard-api.test.ts (add)
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchDashboardView, pollUntilTerminal } from "./dashboard-api";
import * as contracts from "./dashboard-contracts";

afterEach(() => vi.restoreAllMocks());

describe("pollUntilTerminal", () => {
  it("polls until completed and reports every provisional view", async () => {
    const running = makeView("running", { overview: "pending", warehouse: "ready", storage: "pending" });
    const done = makeView("completed", { overview: "ready", warehouse: "ready", storage: "ready" });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(running))
      .mockResolvedValueOnce(jsonResponse(done));
    vi.spyOn(contracts, "parseDashboardView").mockImplementation((p) => p as contracts.DashboardView);

    const seen: string[] = [];
    const result = await pollUntilTerminal(
      () => fetchDashboardView("run-1", { windowDays: 30 }),
      (view) => view.run.status === "completed",
      { intervalMs: 0, onResult: (v) => seen.push(v.run.status) },
    );

    expect(seen).toEqual(["running", "completed"]);
    expect(result.run.status).toBe("completed");
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("throws when maxAttempts is exhausted before terminal status", async () => {
    const running = makeView("running", { overview: "pending", warehouse: "pending", storage: "pending" });
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(running));
    vi.spyOn(contracts, "parseDashboardView").mockImplementation((p) => p as contracts.DashboardView);

    await expect(
      pollUntilTerminal(
        () => fetchDashboardView("run-1", { windowDays: 30 }),
        (view) => view.run.status === "completed",
        { intervalMs: 0, maxAttempts: 2 },
      ),
    ).rejects.toThrow(/timed out/i);
  });
});

function makeView(status: string, sectionStatuses: Record<string, string>) {
  return { run: { id: "run-1", status }, sectionStatuses } as unknown as contracts.DashboardView;
}
function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}
```

> Implementer note: match the existing `dashboard-api.test.ts` mocking style — if that suite stubs `resolveApiUrl`/`fetch` a particular way, reuse it rather than the sketch above.

- [ ] **Step 2: Run it — expect FAIL**

Run: `npx vitest run src/lib/dashboard-api.test.ts`
Expected: FAIL (the existing polling helper does not accept a fetcher/terminal predicate/callback, or no generic helper is exported).

- [ ] **Step 3: Implement**

```typescript
// dashboard-api.ts
export type PollUntilOptions<T> = {
  intervalMs?: number;
  maxAttempts?: number;
  onResult?: (result: T) => void;
};

export async function pollUntilTerminal<T>(
  fetcher: () => Promise<T>,
  isTerminal: (result: T) => boolean,
  { intervalMs = 1_500, maxAttempts = 60, onResult }: PollUntilOptions<T> = {},
): Promise<T> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const result = await fetcher();
    onResult?.(result);
    if (isTerminal(result)) {
      return result;
    }
    if (intervalMs > 0) await delay(intervalMs);
  }
  throw new Error("Polling timed out before reaching a terminal status");
}
```

- [ ] **Step 4: Run it + typecheck — expect PASS**

Run: `npx vitest run src/lib/dashboard-api.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/dashboard-api.ts apps/web/src/lib/dashboard-api.test.ts
git commit -m "feat(web): generalize polling helper for progressive view streaming"
```

### Task 14: Per-section readiness in the dashboard UI

**Files:**
- Modify: `apps/web/src/components/dashboard/use-section-statuses.ts`
- Modify: `apps/web/src/components/dashboard/cost-dashboard.tsx` (`loadSnowflakeRun` → poll `/view`; thread real per-section readiness)
- Test: `apps/web/src/components/dashboard/use-section-statuses.test.ts` (extend)

**Interfaces:**
- Consumes: the generalized polling helper from Task 13 (or an equivalent `/view` loop kept local to `loadSnowflakeRun`), `fetchDashboardView`, and `DashboardViewSectionStatuses` (Task 12).
- Produces: `useSectionStatuses` accepts an optional `sectionReadiness?: DashboardViewSectionStatuses` — when provided it drives per-section reveal (mapping `ready→"ready"`, else `"loading"`); when omitted it keeps the existing single-`dataReady` timed-stagger behavior (demo/cached path unchanged). `SectionStatus` (UI) gains no new variant — `unavailable` maps to `"loading"`'s skeleton for now (no explicit unavailable/error UI; the section stays skeletoned).

**Acceptance criteria:**
- `unavailable` sections remain in the loading skeleton state. Do not add an explicit unavailable/error UI in this task.
- After awaiting the `/view` polling helper or inline loop, check `runGenerationRef` immediately, before any cache write, `setState`, `applyDashboardView`, or prefetch. Increment generation only when starting a new load; do not increment it from partial-view callbacks or after the final view resolves.

- [ ] **Step 1: Write the failing test for per-section readiness**

```typescript
// use-section-statuses.test.ts (add)
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useSectionStatuses } from "./use-section-statuses";

describe("useSectionStatuses per-section readiness", () => {
  it("reveals only sections marked ready when sectionReadiness is provided", () => {
    const { result } = renderHook(() =>
      useSectionStatuses({
        dataReady: true,
        instant: true,
        revealGeneration: 1,
        sectionReadiness: { overview: "pending", warehouse: "ready", storage: "unavailable" },
      }),
    );
    expect(result.current).toEqual({
      overview: "loading",
      warehouse: "ready",
      storage: "loading",
    });
  });
});
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `npx vitest run src/components/dashboard/use-section-statuses.test.ts`
Expected: FAIL (`sectionReadiness` is ignored; storage/overview report per the timer path).

- [ ] **Step 3: Implement per-section readiness in the hook**

In `use-section-statuses.ts`, import the contract type and add the optional arg + a short-circuit branch before the timer logic:

```typescript
import type { DashboardViewSectionStatuses } from "../../lib/dashboard-contracts";

type UseSectionStatusesArgs = {
  dataReady: boolean;
  instant: boolean;
  revealGeneration: number;
  // When present (progressive Snowflake runs), reveal is driven by the server's
  // per-section readiness instead of the timed stagger. Absent for demo/cached
  // views, which keep the original stagger.
  sectionReadiness?: DashboardViewSectionStatuses;
};

export function useSectionStatuses({
  dataReady,
  instant,
  revealGeneration,
  sectionReadiness,
}: UseSectionStatusesArgs): DashboardSectionStatuses {
  const [statuses, setStatuses] = useState<DashboardSectionStatuses>(() =>
    dataReady ? ALL_READY : ALL_LOADING,
  );
  // ...existing refs unchanged...

  useEffect(() => {
    generationRef.current = revealGeneration;
    function clearTimers() { /* unchanged */ }
    clearTimers();
    const wasReady = prevDataReadyRef.current;
    prevDataReadyRef.current = dataReady;

    if (!dataReady) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStatuses(ALL_LOADING);
      return clearTimers;
    }

    // Progressive path: map server readiness directly; no timers.
    if (sectionReadiness) {
      setStatuses({
        overview: sectionReadiness.overview === "ready" ? "ready" : "loading",
        warehouse: sectionReadiness.warehouse === "ready" ? "ready" : "loading",
        storage: sectionReadiness.storage === "ready" ? "ready" : "loading",
      });
      return clearTimers;
    }

    if (instant || wasReady) {
      setStatuses(ALL_READY);
      return clearTimers;
    }
    // ...existing stagger loop unchanged...
  }, [dataReady, instant, revealGeneration, sectionReadiness]);

  return statuses;
}
```

> Implementer note: keep the existing `clearTimers`/stagger body verbatim; only add the `sectionReadiness` param, the early progressive branch, and the new effect dependency. `unavailable` deliberately maps to `"loading"` (skeleton) — the spec calls for no new progress UI.

- [ ] **Step 4: Run the hook test — expect PASS**

Run: `npx vitest run src/components/dashboard/use-section-statuses.test.ts`
Expected: PASS.

- [ ] **Step 5: Wire the progressive run path in `cost-dashboard.tsx`**

Replace `loadSnowflakeRun`'s "poll run then fetch view once" body with start → generic `/view` polling, applying each provisional view as it arrives. Add a `sectionReadiness` state and feed it to `useSectionStatuses`.

Add state near the other `useState` hooks:

```typescript
  const [sectionReadiness, setSectionReadiness] =
    useState<DashboardViewSectionStatuses | undefined>(undefined);
```

Update the imports:

```typescript
import { fetchDashboardView, pollUntilTerminal, startDashboardRun /* + existing */ } from "../../lib/dashboard-api";
import { type DashboardViewSectionStatuses /* + existing */ } from "../../lib/dashboard-contracts";
```

Rewrite the success path of `loadSnowflakeRun` (replacing the `pollDashboardRun` + single `fetchDashboardView` block) with:

```typescript
      const run = await startDashboardRun(
        { organizationId: runtime.organizationId, windowDays: FETCH_WINDOW_DAYS },
        options,
      );
      setLoadState((current) => ({
        ...current,
        status: run.status,
        message: run.error ?? run.user_safe_message,
      }));

      const finalView = await pollUntilTerminal(
        () => fetchDashboardView(run.id, DEFAULT_VIEW_RANGE, options),
        (view) => TERMINAL_DASHBOARD_RUN_STATUSES.has(view.run.status),
        {
          intervalMs: 1_500,
          maxAttempts: 60,
          onResult: (view) => {
            if (runGeneration !== runGenerationRef.current) return;
            setSectionReadiness(view.sectionStatuses);
            applyDashboardView(view);   // paints ready sections; pending/unavailable stay skeleton
          },
        },
      );
      if (runGeneration !== runGenerationRef.current) return;
      if (finalView.run.status !== "completed") {
        setLoadState((current) => ({
          ...current,
          status: finalView.run.status,
          message: finalView.run.error ?? finalView.run.user_safe_message,
        }));
        return;
      }
      setSectionReadiness(undefined);   // completed → all sections ready
      cacheView(finalView.run.id, DEFAULT_VIEW_RANGE, finalView);
      applyDashboardView(finalView);
      prefetchRelativeWindows(finalView.run.id, (range) =>
        fetchDashboardView(finalView.run.id, range, options),
      );
```

> Implementer note: increment `runGenerationRef.current` only at the start of a new load, then capture `const runGeneration = runGenerationRef.current;` at the top of `loadSnowflakeRun` (mirroring `loadRange`) so a superseded run's partial callbacks and final awaited result are ignored. Reset `setSectionReadiness(undefined)` at the start of every run/range load (`loadDemoRun`, `loadRange`, the initial demo effect) so the demo/cached/range paths keep the timed-stagger behavior — only the live Snowflake run sets it.

Feed readiness into the hook (existing `useSectionStatuses` call):

```typescript
  const sectionStatuses = useSectionStatuses({
    dataReady,
    instant: reduceMotion,
    revealGeneration,
    sectionReadiness,
  });
```

> Implementer note: `dataReady` currently requires `loadState.status !== "loading"`. A `running` provisional view sets `loadState.status = "running"` via `applyDashboardView`, which is NOT `"loading"`, so `dataReady` becomes true and per-section gating takes over — exactly what we want. Confirm `runInFlight` does not force every section back to skeleton while polling: `loadSnowflakeRun` sets `runInFlight` true for the whole poll, and `dataReady` includes `!runInFlight`. **Change required:** clear `setRunInFlight(false)` as soon as the FIRST provisional view is applied (inside the polling callback on first call), or drop `runInFlight` from `dataReady` and rely on `sectionReadiness`/status. Implement by tracking a `firstViewApplied` flag in the callback and calling `setRunInFlight(false)` once; keep the `finally` `setRunInFlight(false)` as a backstop.

- [ ] **Step 6: Typecheck, lint, and run the web suite**

Run: `npm run typecheck && npm run lint && npx vitest run src/components/dashboard src/lib`
Expected: PASS. Update any existing `cost-dashboard`/`use-section-statuses` test that assumed the single-fetch Snowflake path (now a poll) — stub the generic polling helper or local fetch loop and assert sections reveal per `sectionStatuses`.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/dashboard/use-section-statuses.ts apps/web/src/components/dashboard/cost-dashboard.tsx apps/web/src/components/dashboard/use-section-statuses.test.ts
git commit -m "feat(web): progressive per-section reveal driven by view section_statuses"
```

- [ ] **Step 8: Visual verification (user)**

Ask Kyle to run a Snowflake analysis in his browser and confirm: sections appear one-by-one as their data lands (not all at once after ~2 min), the header/filter bar render immediately, and the completed dashboard matches today's output. (Per user preference, Kyle verifies UI changes visually himself.)

---

## Self-Review

Checked against `docs/superpowers/specs/2026-06-19-parallel-progressive-dashboard-design.md`:

**Spec coverage**
- Prerequisite — persist `account_locator` → Tasks 1–3 (migration + RPC param, capture at validation, resolver threading). ✅
- Layer 1 parallel runner, no dependency edge, exception-typed availability, org-scoped capacity (no locator) → Tasks 5, 7. ✅
- Process-wide module-level executor + `GREYSIGHT_QUERY_CONCURRENCY` → Task 4. ✅
- AI branches parallelized → Task 6. ✅
- Async lifecycle: `POST`→`202 running`, incremental writes, terminal-state guarantee, running-state write guard, TTL → Tasks 8, 9. ✅
- Repository `create_running_run`/`set_dataset`/generalized `claim_source|complete_source|fail_source`/`finalize_run` → Task 8. ✅
- Partial view: top-level `section_statuses` map, provisional bounds, inline per-section dependency matrix, completed/failed final-source statuses → Tasks 10, 11. ✅
- Demo mode returns fully-ready completed view first poll → unchanged demo path (Task 9 keeps demo synchronous `201`); contract defaults all-ready for legacy/demo payloads (Tasks 10, 12). ✅
- Frontend poll `/view`, per-section reveal, stop on terminal, range fast path preserved → Tasks 13, 14. ✅
- Tests enumerated in the spec → covered across Tasks 5–14. ✅

**Placeholder scan:** No `TBD`/"handle edge cases"/"similar to Task N". Code blocks present for every code step. Two intentional "match the existing fixture/mock style" notes (Tasks 12, 13) point at real suite conventions rather than leaving content blank.

**Type consistency:**
- Backend `section_statuses: dict[str, SectionStatus]` (Task 10) ↔ `compute_section_statuses` returns `dict[str, str]` (Task 11) — values are the same three literals; the route injects via `model_copy`. ✅
- Source keys in `BASE_RUN_SOURCE_KEYS` (Task 8) ↔ `SECTION_SOURCE_DEPENDENCIES` deps (Task 11) ↔ dataset keys read by `build_dashboard_view` — all use the canonical source ids (`service_spend_daily`, `warehouse_spend_daily`, `database_storage_daily`, …). ✅
- `on_source_outcome: Callable[[SourceOutcome], None]` (Task 9) ↔ `run_sources_parallel(..., on_complete=...)` `SourceOutcome` (Task 5). ✅
- Frontend `DashboardViewSectionStatuses` (Task 12) ↔ `useSectionStatuses({ sectionReadiness })` (Task 14) ↔ generic `/view` polling callback reading `view.sectionStatuses`. ✅

One dependency nuance is deliberately left as a decision point inline: secondary inputs such as `rate_sheet_daily` must either be added to `SECTION_SOURCE_DEPENDENCIES` or explicitly documented as omitted with premature-readiness risk.
