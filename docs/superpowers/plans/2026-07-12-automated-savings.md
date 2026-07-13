# Automated Savings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an opt-in control loop that suspends idle enrolled Snowflake warehouses once they pass the 60-second billing floor, reclaiming the idle tail between the prepaid minute and the customer's `AUTO_SUSPEND`.

**Architecture:** A standalone Python worker (`apps/auto-savings/`, its own Railway service) polls `SHOW WAREHOUSES` per tenant on a warm persistent Snowflake session, reconciles config drift, and force-suspends idle warehouses by lowering `AUTO_SUSPEND=1` behind a durable Supabase "restore-intent" row (never a hard `SUSPEND`). The API (`apps/api/`) exposes UI-facing read/write endpoints (opt-in, warehouse list, enroll/config), writing only *intent* to Supabase — the worker owns every Snowflake `ALTER`. The web app (`apps/web/`) adds a top nav and an `/automated-savings` page. Shared Snowflake/Supabase connection code is extracted into an installable `greysight-connect` package both Python apps import.

**Tech Stack:** Python 3.12 + `uv` (worker + API), FastAPI + raw-httpx Supabase (API), `snowflake-connector-python` 3.12.4, `asyncio` + bounded `ThreadPoolExecutor` (worker), Supabase/Postgres migrations with RLS + `SECURITY DEFINER` RPCs, Next.js 16 + React 18 + Tremor 3.18 + Vitest (web), pytest (Python).

> **Revision R2.2 (2026-07-12) — second Codex pass.** Fixed 4 blocking items: (1) unenroll/disable **never writes an intent** (only clears `enabled`; worker drains any pre-existing one) — Task 15; (2) `created_on`-mismatch branch **explicitly deletes the stale intent** + test seeds one — Task 8; (3) added the **idempotent "already restored" terminal branch** (`live == restore_to` → clear intent + cooldown, no ALTER) so a failed `delete_intent` can't strand an org in `worker_tenants()` forever — Task 8; (4) fixed the stale **300s cooldown assertion → 60s** in the Task 8 test. Plus mediums: `write_intent(set_at=…)` for deterministic age tests; `WorkerConfig.__post_init__` enforces `socket_timeout < poll_timeout` + non-vacuous connector-kwargs test; supervisor **closes sessions** on tenant removal; `shared/connect` gets pytest dev deps; web shell reads camelCase `grantPresent`; GRANT-SQL role-name escaping; RLS `to authenticated`; page/shell `OrgShell` boundary aligned to the dashboard.
>
> **Revision R2.1 (2026-07-12):** deviation #4 **accepted**; `cooldown_seconds` default lowered **300s → 60s** (it's a stored `cooldown_ts` + per-tick comparison, no timer); added **orphaned-intent cleanup** for fully-dropped warehouses (#10, Task 8 branch 0); Task 0 now hard-gates `max_intent_hold_ticks` at **≥ 2–3× measured suspend latency** (the 15s default is a placeholder).
>
> **Revision R2 (2026-07-12) — folded in Codex + review-agent findings.** Key changes from R1: added **Task 0 verification spike** (SHOW WAREHOUSES timestamp type + suspend latency); shared package is now a **uv path dependency, not a workspace** (Task 1); **kill switch made functional** (worker re-reads `global_enabled`, `worker_tenants()` unions outstanding intents, supervisor re-enumerates — Tasks 3/9/12); **single live `managed_auto_suspend`** restore target + drift baseline, `stored_default` immutable (Tasks 3/8/9/14); **timestamp coercion** in the parser (Task 5); **next-tick restore is now conditional** — SUSPENDED / busy / held-with-intent-age-backstop (Task 8, ⚠️ spec deviation, needs sign-off); **connector socket timeout** is the real thread-unblock, not `close()` (Tasks 4/11/12); reconcile drains regardless of `enabled`, `created_on`-mismatch invalidation, failed-ALTER-retry, reconcile-accept=false enqueues intent (Task 8/14); **snake→camel contract transform** (Task 16); service-role auth-off gating + SHOW GRANTS escaping + shim `snowflake` re-export.

## Global Constraints

- **Snowflake writes only ever set `AUTO_SUSPEND`.** Never `ALTER … SUSPEND`, never mutate `MIN_CLUSTER_COUNT`/`MAX_CLUSTER_COUNT`. Force-suspend = lower `AUTO_SUSPEND` to `1` and let Snowflake's race-free idle accounting decide.
- **The worker owns every Snowflake `ALTER WAREHOUSE`.** The API writes intent to Supabase only; the worker applies it on its next tick.
- **Ownership of an `AUTO_SUSPEND=1` sentinel is proven by our durable restore-intent row, never by the live value alone.**
- **Uptime is never persisted.** Always derived live as `now(timezone.utc) - resumed_on` in Python. Never compute it via any SQL `SELECT`/`RESULT_SCAN` (that resumes the session warehouse). `SHOW WAREHOUSES` is pure metadata and needs no running warehouse.
- **One `SHOW WAREHOUSES` snapshot per cycle.** Reconcile first (heal/restore), then decide. Never re-query between the two.
- **Reconcile (drain intents) runs every cycle regardless of `enabled` or `global_enabled`; only the *decide* step is gated on `global_enabled`.** An outstanding restore-intent is always drained — a disabled/unenrolled/kill-switched warehouse must never be stranded at `AUTO_SUSPEND=1`.
- **Restore is next-tick, driven by the intent row (reachable from every state) — but conditional on live state to avoid guillotining Snowflake's own in-flight suspend:** `SUSPENDED` → restore + start cooldown; `STARTED & busy` (`running`/`queued > 0`) → restore, **no** cooldown (a query landed, back off); `STARTED & idle & live == 1` → **hold** the intent (suspend just hasn't landed yet), bounded by intent age — force-restore after `max_intent_hold_ticks` to preserve C1 anti-stranding. Never gated on *catching* `SUSPENDED`.
- **The kill switch / global switch actually stops automation.** The worker re-reads `global_enabled` every cycle (or via periodic re-enumeration); flipping it off halts the decide step immediately while reconcile still drains outstanding sentinels. `worker_tenants()` returns any org with `global_enabled` **or** an outstanding restore-intent (so a kill-switched org is still polled until drained).
- **One live restore-target column.** `managed_auto_suspend` is the editable value the worker restores to **and** the drift baseline; `stored_default_auto_suspend` is the immutable opt-in capture kept for reference/audit only. `restore_to` = `managed_auto_suspend`; drift = `live ∉ {managed_auto_suspend, 1}`.
- **A restore/heal `ALTER` must succeed before its intent row is deleted.** Order is always `apply_alter(...)` → (on success) `delete_intent(...)` → `set_cooldown(...)`; a failed `ALTER` leaves the intent so the next tick retries.
- **Timestamps from `SHOW WAREHOUSES` are coerced to tz-aware UTC in the parser** (the connector may return them as strings or tz-naive). Never subtract a raw/naive value.
- **Blocking Snowflake calls are bounded at the socket layer.** The connector is configured with `network_timeout`/`socket_timeout` shorter than the per-tenant `poll_timeout_seconds` watchdog — that OS-level read timeout, not `close()`, is what actually frees a pool thread. `close_hard()` is cleanup after.
- **Eligibility filter is `type == 'STANDARD'` only.** No cluster-count restriction; the `started_clusters == min_cluster_count` gate keeps multi-cluster warehouses safe. Snowpark-optimized (`type != STANDARD`) warehouses are excluded and auto-paused if an enrolled warehouse becomes non-STANDARD.
- **Hardcoded guardrails (not user-editable):** 60s billing floor (worker uses a `>= 62s` safety margin), cooldown duration. UI enforces a **floor of 60** on the managed default so a human cannot produce `AUTO_SUSPEND=1`.
- **Enroll rejects a captured default `∈ {0, 1, NULL}`** (always-on / already-sentinel warehouses are out of scope, never silently reconfigured). Enforced at the route AND by a DB check constraint.
- **Tenant discovery is not startup-only.** A supervisor loop re-enumerates `worker_tenants()` on an interval so orgs that opt in (or get kill-switched) after startup are picked up (or drained) without a restart.
- **RLS never widened:** members read; only owners/admins mutate opt-in, toggles, and reconcile. The worker uses the Supabase **service role**, mirroring `SupabaseConnectionFetcher`.
- **Parse `SHOW WAREHOUSES` by column name**, tolerating absence/case — never positionally.
- **Single worker process in v1.** `hash(tenant_id) % NUM_REPLICAS == REPLICA_INDEX` sharding is present but dormant. Do NOT build the bounded-LRU pool / dual-cadence scheme.
- **The `AUTO_SUSPEND=1` resume-storm window is bounded on three axes.** (1) *Fast-poll while any intent is outstanding:* `run_cycle` reports whether an intent remains; the tenant loop then polls at `intent_poll_interval_seconds` (default 1s, ±15% jitter) instead of `poll_interval_seconds`, shrinking the live window so at most ~1 resume occurs before we observe-and-restore. (2) *Busy-restore cooldown:* the `STARTED & busy` restore branch now sets a cooldown — a warehouse that resumed under our sentinel proved it is bursty, so we back it off. (3) *Resume-aware restore:* intents persist `baseline_resumed_on` (the warehouse's `resumed_on` captured at set-time); the `STARTED & idle & live == 1` HOLD branch restores early if `resumed_on` advanced past the baseline (a suspend→resume cycle completed under the sentinel), guarded both-non-None so a `resumed_on=None` snapshot still holds until aged. `intent_hold_seconds` stays pinned to the **normal** `poll_interval_seconds` (× `max_intent_hold_ticks`) — never the fast interval — so the anti-stranding backstop does not shrink.
- **TDD, no tests that restate the implementation** (no copy/label/render assertions). Failing-first test for every behavior change. Frequent, focused commits.

---

## File Structure

**New shared package** (`shared/connect/` — installable `greysight-connect`, consumed by each app via a `[tool.uv.sources]` **path dependency** — NOT a uv workspace, so per-app `uv.lock` files are preserved and the existing API Dockerfile lock-copy keeps working):
- `shared/connect/pyproject.toml` — package metadata + `[build-system]`.
- `shared/connect/src/greysight_connect/__init__.py` — public re-exports.
- `shared/connect/src/greysight_connect/snowflake_account.py` — moved from `apps/api/app/services/`.
- `shared/connect/src/greysight_connect/snowflake_client.py` — moved; adds `SHOW WAREHOUSES` support (`execute_metadata_query`).
- `shared/connect/src/greysight_connect/org_connection_resolver.py` — moved (`OrgConnectionRow`, `resolve_snowflake_config`, `SupabaseConnectionFetcher`).
- `shared/connect/tests/` — moved unit tests for the above.

**Worker app** (`apps/auto-savings/` — new Railway service):
- `apps/auto-savings/pyproject.toml`, `uv.lock`, `Dockerfile`, `dev.py`.
- `src/auto_savings/config.py` — env settings (cadence, cooldown, sharding, Supabase creds).
- `src/auto_savings/warehouse_snapshot.py` — parse `SHOW WAREHOUSES` rows → `WarehouseSnapshot`, compute uptime.
- `src/auto_savings/decision.py` — pure suspend-decision truth table.
- `src/auto_savings/store.py` — Supabase service-role reads/writes (enrollment, intent rows, drift, cooldown).
- `src/auto_savings/reconcile.py` — restore-intent + drift reconciliation (pure, given a snapshot + store state).
- `src/auto_savings/engine.py` — per-tenant cycle: one snapshot → reconcile → decide → act.
- `src/auto_savings/snowflake_session.py` — warm persistent session, watchdog/force-close, backoff reconnect.
- `src/auto_savings/tenant_loop.py` — per-tenant `asyncio` loop + lock + timeout.
- `src/auto_savings/sharding.py` — `owns_tenant(tenant_id)` predicate.
- `src/auto_savings/main.py` — process bootstrap: enumerate tenants, spawn loops, bounded executor.
- `tests/` — hermetic pytest (mock the Snowflake session + store).

**Database** (`supabase/migrations/`):
- `supabase/migrations/202607120001_automated_savings.sql` — settings, enrollment, intent tables + RLS + RPCs.

**API** (`apps/api/`):
- `app/routes/automated_savings.py` — new route module.
- `app/services/automated_savings_store.py` — service-role Supabase reads/writes for opt-in + enrollment.
- `app/services/warehouse_directory.py` — live `SHOW WAREHOUSES` + `SHOW GRANTS TO ROLE` reads, joined with enrollment.
- `app/main.py` — mount router + wire the store singleton (modify).

**Web** (`apps/web/`):
- `src/app/automated-savings/page.tsx` — server component (env → client shell).
- `src/components/automated-savings/automated-savings-shell.tsx` — client shell (gate vs. dashboard branching).
- `src/components/automated-savings/opt-in-gate.tsx` — explainer + agree + GRANT SQL.
- `src/components/automated-savings/warehouse-table.tsx` — enrollment table.
- `src/components/dashboard/app-nav.tsx` — shared top nav (Home / Automated Savings).
- `src/lib/automated-savings-api.ts` — fetch wrappers + hand-written parsers.
- `src/components/dashboard/dashboard-header.tsx` — render `AppNav` (modify).

**Docs** (`docs/`):
- `docs/automated-savings.md` — cloud-services cost note, cadence env, operational runbook.

---

## Phase 0 — Verification spike + shared package extraction

### Task 0: Verify the two load-bearing Snowflake facts BEFORE building on them

**Files:**
- Create: `docs/superpowers/plans/2026-07-12-automated-savings-spike-notes.md` (record the findings)

**Why this task exists (adversarial-review findings #3, and Codex "unverified timestamp coercion"):** The entire uptime design assumes `SHOW WAREHOUSES` returns `resumed_on`/`created_on` as usable timestamps. In practice the connector often returns SHOW-command timestamps as **strings** (and sometimes tz-naive). If so, every uptime subtraction throws and the poll churns. The refined restore logic (finding #4) also depends on real **suspend latency vs. the 3s cadence**. Both are cheap to check against a real account and must be confirmed before Tasks 5/8 are built on them.

- [ ] **Step 1: Capture a real `SHOW WAREHOUSES` result and record the exact types**

Using an authorized Snowflake connection (the Snowflake MCP tool, or a scratch script with a dev account's key-pair), run `SHOW WAREHOUSES` and record, for `resumed_on`, `created_on`, `auto_suspend`, `running`, `queued`, `started_clusters`, `min_cluster_count`, `max_cluster_count`, `auto_resume`, `type`: the **Python type** the connector returns (str vs datetime; tz-aware vs naive), and the exact string format if a string. Write it to the spike notes.

- [ ] **Step 2: Measure suspend latency**

On a test warehouse, `ALTER WAREHOUSE … SET AUTO_SUSPEND = 1` while idle, then poll `SHOW WAREHOUSES` every ~1s and record how many seconds/ticks until `state == SUSPENDED`. This calibrates `max_intent_hold_ticks` (finding #4) and confirms the 3–5s cadence is compatible.

- [ ] **Step 3: Record the decisions that fall out**

In the spike notes, state: (a) whether `parse_warehouses` must string-parse `resumed_on` (it almost certainly must — Task 5 assumes so), and the format to parse; (b) the observed suspend latency and the resulting `max_intent_hold_ticks`.

**⚠️ This is the one number that gates shipping (both reviewers flagged it as load-bearing).** `intent_hold_seconds = max_intent_hold_ticks × poll_interval_seconds` MUST be **≥ 2–3× the observed suspend latency**. The R2 default is `5 × 3s = 15s` — a **placeholder, not a real value**. If real suspend latency exceeds `intent_hold_seconds`, the age-backstop force-restores *before* Snowflake finishes suspending — silently re-introducing the exact guillotine finding #4 set out to kill. Set `AUTO_SAVINGS_MAX_INTENT_HOLD_TICKS` from the measurement (e.g. observed latency 8s, poll 3s → hold ≥ 24s → ticks ≥ 8). If the connection can't be authorized in this environment, mark Task 0 **blocked** and surface it — do NOT proceed to Task 5/8 on the placeholder.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-07-12-automated-savings-spike-notes.md
git commit -m "docs: spike notes — SHOW WAREHOUSES types + suspend latency"
```

---

### Task 1: Create the `greysight-connect` package as a uv path dependency

**Files:**
- Create: `shared/connect/pyproject.toml`
- Create: `shared/connect/src/greysight_connect/__init__.py`
- Move: `apps/api/app/services/snowflake_account.py` → `shared/connect/src/greysight_connect/snowflake_account.py`
- Move: `apps/api/app/services/snowflake_client.py` → `shared/connect/src/greysight_connect/snowflake_client.py`
- Move: `apps/api/app/services/org_connection_resolver.py` → `shared/connect/src/greysight_connect/org_connection_resolver.py`
- Create (shims): `apps/api/app/services/snowflake_account.py`, `snowflake_client.py`, `org_connection_resolver.py`
- Modify: `apps/api/pyproject.toml` (add `greysight-connect` dependency + `[tool.uv.sources]` **path** entry)
- Move: `apps/api/tests/test_snowflake_client.py`, `test_snowflake_account.py`, `test_snowflake_validation.py`, `test_org_connection_resolver.py` → `shared/connect/tests/`

**Packaging decision (revised — Codex finding on workspace/Docker ordering):** Do **NOT** introduce a repo-root uv workspace. A uv workspace uses a single **root** lockfile, which conflicts with the existing per-app `apps/api/uv.lock` the Dockerfile copies, and would force listing `apps/auto-savings` as a member before it exists (Task 4). Instead, each consuming app declares a **path dependency**: `greysight-connect = { path = "../../shared/connect", editable = true }` under `[tool.uv.sources]`. Per-app lockfiles are preserved; no ordering trap; the API Dockerfile changes are minimal (copy `shared/connect/` before `uv sync`).

**Interfaces:**
- Consumes: nothing (first task).
- Produces: importable module `greysight_connect` re-exporting `SnowflakeConnectionConfig`, `SnowflakeConfigurationError`, `SnowflakeValidationError`, `SnowflakeQueryError`, `SnowflakeObjectUnavailableError`, `execute_source_query`, `validate_snowflake_connection`, `validate_account_identifier`, `InvalidSnowflakeAccountError`, `OrgConnectionRow`, `OrgConnectionNotConfiguredError`, `resolve_snowflake_config`, `SupabaseConnectionFetcher`, `FetchConnection`. The `resolve_snowflake_config` signature is unchanged: `resolve_snowflake_config(organization_id: str, settings, *, fetch_connection: FetchConnection) -> SnowflakeConnectionConfig` where it reads only `settings.auth_required` and `settings.query_timeout_seconds`.

**Design note (decision locked here):** The API currently has no `[build-system]` and imports `app.services.*` from CWD. Moving the three modules into an installable package, then leaving **thin re-export shims** at the old `app.services.*` paths, keeps all ~8 existing API import sites and their tests green with zero churn while the new worker imports `greysight_connect` directly. This is the DRY, low-blast-radius path. `resolve_snowflake_config` already takes discrete params and only reads `settings.auth_required` + `settings.query_timeout_seconds` off the settings object (duck-typed) — so the package does **not** import `app.config.Settings`; any object exposing those two attributes works.

- [ ] **Step 1: Create the package skeleton and move the three modules verbatim**

Move the files unchanged (`git mv`) so history is preserved:

```bash
mkdir -p shared/connect/src/greysight_connect shared/connect/tests
git mv apps/api/app/services/snowflake_account.py shared/connect/src/greysight_connect/snowflake_account.py
git mv apps/api/app/services/snowflake_client.py shared/connect/src/greysight_connect/snowflake_client.py
git mv apps/api/app/services/org_connection_resolver.py shared/connect/src/greysight_connect/org_connection_resolver.py
git mv apps/api/tests/test_snowflake_client.py shared/connect/tests/test_snowflake_client.py
git mv apps/api/tests/test_snowflake_account.py shared/connect/tests/test_snowflake_account.py
git mv apps/api/tests/test_snowflake_validation.py shared/connect/tests/test_snowflake_validation.py
git mv apps/api/tests/test_org_connection_resolver.py shared/connect/tests/test_org_connection_resolver.py
```

Then fix the intra-package imports in the moved files (they currently import each other via `app.services.*`):
- In `shared/connect/src/greysight_connect/snowflake_client.py`: change `from app.services.snowflake_account import validate_account_identifier` → `from greysight_connect.snowflake_account import validate_account_identifier`.
- In `shared/connect/src/greysight_connect/org_connection_resolver.py`: change `from app.services.snowflake_client import SnowflakeConnectionConfig` → `from greysight_connect.snowflake_client import SnowflakeConnectionConfig`, and remove `from app.config import Settings` — replace the type hint on `settings` with a `typing.Protocol` defined inline:

```python
from typing import Protocol

class ResolverSettings(Protocol):
    auth_required: bool
    query_timeout_seconds: int
```

Update `resolve_snowflake_config`'s annotation from `settings: Settings` to `settings: ResolverSettings`.

Update the moved test files' imports from `app.services.X` → `greysight_connect.X`.

- [ ] **Step 2: Write the package `__init__.py` (public surface)**

`shared/connect/src/greysight_connect/__init__.py`:

```python
from greysight_connect.snowflake_account import (
    InvalidSnowflakeAccountError,
    validate_account_identifier,
)
from greysight_connect.snowflake_client import (
    SnowflakeConfigurationError,
    SnowflakeConnectionConfig,
    SnowflakeObjectUnavailableError,
    SnowflakeQueryError,
    SnowflakeValidationError,
    execute_source_query,
    validate_snowflake_connection,
)
from greysight_connect.org_connection_resolver import (
    FetchConnection,
    OrgConnectionNotConfiguredError,
    OrgConnectionRow,
    ResolverSettings,
    SupabaseConnectionFetcher,
    resolve_snowflake_config,
)

__all__ = [
    "InvalidSnowflakeAccountError",
    "validate_account_identifier",
    "SnowflakeConfigurationError",
    "SnowflakeConnectionConfig",
    "SnowflakeObjectUnavailableError",
    "SnowflakeQueryError",
    "SnowflakeValidationError",
    "execute_source_query",
    "validate_snowflake_connection",
    "FetchConnection",
    "OrgConnectionNotConfiguredError",
    "OrgConnectionRow",
    "ResolverSettings",
    "SupabaseConnectionFetcher",
    "resolve_snowflake_config",
]
```

- [ ] **Step 3: Write `shared/connect/pyproject.toml`**

```toml
[project]
name = "greysight-connect"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "snowflake-connector-python==3.12.4",
    "httpx==0.28.1",
    "pyopenssl",
    "cryptography",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/greysight_connect"]

[dependency-groups]
dev = ["pytest", "pytest-cov"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Wire the API to the package via a path source**

In `apps/api/pyproject.toml`, add `"greysight-connect"` to `[project.dependencies]`, and add (no root `pyproject.toml`, no workspace):

```toml
[tool.uv.sources]
greysight-connect = { path = "../../shared/connect", editable = true }
```

- [ ] **Step 5: Add re-export shims (preserving name-based connector patching)**

Create the three shim modules so existing `app.services.*` imports keep working. **The shim must re-export the `snowflake` submodule** so tests that do `patch("app.services.snowflake_client.snowflake.connector.connect")` still resolve — a bare `from … import *` does NOT (verified against `apps/api/tests/test_snowflake_client.py:36`). `apps/api/app/services/snowflake_client.py`:

```python
"""Re-export shim — implementation moved to the greysight_connect package."""
from greysight_connect import snowflake_client as _impl
from greysight_connect.snowflake_client import (  # noqa: F401  explicit for name-based patching
    SnowflakeConfigurationError,
    SnowflakeConnectionConfig,
    SnowflakeObjectUnavailableError,
    SnowflakeQueryError,
    SnowflakeValidationError,
    execute_source_query,
    validate_snowflake_connection,
)

# `execute_metadata_query` is added to the package in Task 2; add it to this import list then.
# Re-export the `snowflake` module attribute so `patch("app.services.snowflake_client.snowflake...")`
# targets the same object the implementation uses.
snowflake = _impl.snowflake
```

> Because the shim and `greysight_connect.snowflake_client` now reference the **same** `snowflake` module object, patching either path patches the connector for both. The moved package tests (Step 1) should still be repointed to `greysight_connect.snowflake_client.*` for clarity; any *remaining* API test that patches `app.services.snowflake_client.snowflake...` keeps working via this re-export.

`apps/api/app/services/snowflake_account.py`:

```python
"""Re-export shim — implementation moved to the greysight_connect package."""
from greysight_connect.snowflake_account import (  # noqa: F401
    InvalidSnowflakeAccountError,
    validate_account_identifier,
)
```

`apps/api/app/services/org_connection_resolver.py`:

```python
"""Re-export shim — implementation moved to the greysight_connect package."""
from greysight_connect.org_connection_resolver import (  # noqa: F401
    FetchConnection,
    OrgConnectionNotConfiguredError,
    OrgConnectionRow,
    SupabaseConnectionFetcher,
    resolve_snowflake_config,
)
```

- [ ] **Step 6: Sync (regenerating the API lockfile) and run both test suites**

The path dependency changes `apps/api/uv.lock`, so sync without `--frozen` first to regenerate it, then verify:
```bash
uv sync --directory shared/connect
uv run --directory shared/connect pytest
uv sync --directory apps/api          # regenerates apps/api/uv.lock with the path dep
uv run --directory apps/api pytest
```
Expected: package tests PASS (moved Snowflake/resolver tests green under `greysight_connect.*`); API suite PASS (shims resolve, connector patching works via the re-exported `snowflake` attr). Commit the regenerated `apps/api/uv.lock`.

- [ ] **Step 7: Update the API Dockerfile to copy the path-dep package before sync**

Modify `apps/api/Dockerfile`. Build context is already the repo root. Copy `shared/connect/` before `uv sync` so the editable path source resolves (no root `pyproject.toml` needed):

```dockerfile
WORKDIR /app/apps/api
COPY shared/connect/ /app/shared/connect/
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-install-project
COPY apps/api/ ./
COPY sql/ /app/sql/
COPY shared/ /app/shared/
```

(The final `COPY shared/` still brings `free-email-domains.json`; the earlier `COPY shared/connect/` is what the path source needs.)

- [ ] **Step 8: Verify the API image still builds, then commit**

Run: `docker build -f apps/api/Dockerfile -t api-test .` (from repo root); expected: `uv sync --frozen` resolves the path dep and the build completes. Then:

```bash
git add shared/connect apps/api/pyproject.toml apps/api/uv.lock apps/api/Dockerfile apps/api/app/services shared/connect/tests
git commit -m "refactor: extract Snowflake/Supabase connection code into greysight-connect package"
```

---

### Task 2: Add `SHOW WAREHOUSES` / `SHOW GRANTS` metadata execution to the package

**Files:**
- Modify: `shared/connect/src/greysight_connect/snowflake_client.py`
- Test: `shared/connect/tests/test_metadata_query.py`

**Interfaces:**
- Consumes: `SnowflakeConnectionConfig` (Task 1).
- Produces: `execute_metadata_query(sql: str, *, config: SnowflakeConnectionConfig | None = None, connect=None) -> list[dict[str, Any]]` — runs a metadata `SHOW …` statement and returns rows as dicts keyed by **lowercased** column name. Unlike `execute_source_query`, it takes **no bind params** (SHOW statements are literal) and does not go through `_validate_window_params`. It maps errors to `SnowflakeQueryError` the same way.

**Design note:** `SHOW WAREHOUSES` and `SHOW GRANTS TO ROLE` are cloud-services metadata calls — no warehouse compute, sub-second, and crucially they do **not** resume a warehouse. This is a new command class the client must support; it is *not* an `ACCOUNT_USAGE` registry query, so the file-based registry / `_validate_window_params` allowlist does not apply.

- [ ] **Step 1: Write the failing test**

`shared/connect/tests/test_metadata_query.py`:

```python
from unittest.mock import Mock, patch

from greysight_connect.snowflake_client import (
    SnowflakeConnectionConfig,
    execute_metadata_query,
)


def _config() -> SnowflakeConnectionConfig:
    return SnowflakeConnectionConfig(
        account="ab12345", user="svc", role="GREYSIGHT_RL", warehouse="WH",
        database="SNOWFLAKE", schema="ACCOUNT_USAGE",
        private_key_path=None, private_key_pem="pem", private_key_passphrase=None,
    )


def test_execute_metadata_query_returns_lowercased_dicts():
    cursor = Mock()
    cursor.description = [("name",), ("state",), ("auto_suspend",)]
    cursor.fetchall.return_value = [("WH1", "STARTED", 300)]
    connection = Mock()
    connection.cursor.return_value = cursor

    with patch("greysight_connect.snowflake_client.snowflake.connector.connect", return_value=connection), \
         patch.object(SnowflakeConnectionConfig, "_load_private_key_der", return_value=b"key"):
        rows = execute_metadata_query("SHOW WAREHOUSES", config=_config())

    assert rows == [{"name": "WH1", "state": "STARTED", "auto_suspend": 300}]
    assert cursor.execute.call_args[0][0] == "SHOW WAREHOUSES"
    # No bind params passed for metadata statements.
    assert len(cursor.execute.call_args[0]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory shared/connect pytest tests/test_metadata_query.py -v`
Expected: FAIL with `ImportError: cannot import name 'execute_metadata_query'`.

- [ ] **Step 3: Implement `execute_metadata_query`**

Add to `shared/connect/src/greysight_connect/snowflake_client.py` (reuse the existing `_connect`, `_column_name`, and error-mapping helpers):

```python
def execute_metadata_query(
    sql: str,
    *,
    config: SnowflakeConnectionConfig | None = None,
    connect=None,
) -> list[dict[str, Any]]:
    """Run a metadata SHOW statement (e.g. SHOW WAREHOUSES). No warehouse compute,
    no bind params, and — unlike a SELECT — never resumes a warehouse."""
    resolved = config or SnowflakeConnectionConfig.from_environment()
    connection = (connect or _connect)(resolved)
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            columns = [_column_name(meta, index) for index, meta in enumerate(cursor.description)]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001  mapped to SnowflakeQueryError
            raise SnowflakeQueryError(_user_safe_message(exc)) from exc
        finally:
            cursor.close()
    finally:
        connection.close()
```

Add `execute_metadata_query` to `__init__.py`'s imports and `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory shared/connect pytest tests/test_metadata_query.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/connect/src/greysight_connect/snowflake_client.py shared/connect/src/greysight_connect/__init__.py shared/connect/tests/test_metadata_query.py
git commit -m "feat: add execute_metadata_query for SHOW statements to greysight-connect"
```

---

## Phase 1 — Database

### Task 3: Automated-savings migration (settings, enrollment, intent) with RLS + service-role RPCs

**Files:**
- Create: `supabase/migrations/202607120001_automated_savings.sql`
- Test: `apps/api/tests/test_automated_savings_migration.py` (mirror the existing `test_supabase_migration.py` string-assertion style)

**Interfaces:**
- Consumes: existing `organizations`, `organization_memberships`, `is_organization_member`, `is_organization_admin`, `set_updated_at` (from `202606080001_initial_org_membership.sql`).
- Produces these tables/columns (final names — every later task references them):
  - `automated_savings_settings(organization_id uuid pk → organizations, agreed_at timestamptz, global_enabled boolean not null default false, grant_present boolean not null default false, grant_checked_at timestamptz, created_at, updated_at)`.
  - `automated_savings_warehouses(organization_id uuid, warehouse_name text, enabled boolean not null default false, managed_auto_suspend integer, stored_default_auto_suspend integer, warehouse_created_on timestamptz, cooldown_ts timestamptz, drift_state text not null default 'ok' check (drift_state in ('ok','drifted','unsupported')), drifted_value integer, updated_at, primary key (organization_id, warehouse_name))`. **`managed_auto_suspend` is the live restore target + drift baseline (editable, floor 60); `stored_default_auto_suspend` is the immutable opt-in capture, reference only.** DB check constraints: `managed_auto_suspend >= 60` and `stored_default_auto_suspend not in (0, 1)` (defense-in-depth for the enroll reject).
  - `automated_savings_restore_intents(organization_id uuid, warehouse_name text, restore_to integer not null, set_at timestamptz not null default now(), primary key (organization_id, warehouse_name))` — one outstanding intent per warehouse. `set_at` drives the intent-age bound for the held-suspend case (finding #4).
  - Service-role RPC `automated_savings_worker_tenants()` (`SECURITY DEFINER`, `revoke … from public`, `grant execute … to service_role`) returns every org that either has `global_enabled` true **or** has an outstanding restore-intent — so a kill-switched org is still polled until its sentinels drain (finding #1). The worker otherwise reads/writes these tables directly via PostgREST with the service-role key, same as `SupabaseRunCacheStore`.

- [ ] **Step 1: Write the failing migration-shape test**

`apps/api/tests/test_automated_savings_migration.py`:

```python
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "202607120001_automated_savings.sql"
).read_text()


def test_tables_created():
    for table in (
        "automated_savings_settings",
        "automated_savings_warehouses",
        "automated_savings_restore_intents",
    ):
        assert f"create table {table}" in MIGRATION


def test_enabled_and_global_default_false():
    # Nothing is automated at opt-in.
    assert "global_enabled boolean not null default false" in MIGRATION
    assert "enabled boolean not null default false" in MIGRATION


def test_rls_members_read_admins_mutate():
    assert "enable row level security" in MIGRATION
    assert "is_organization_member" in MIGRATION
    assert "is_organization_admin" in MIGRATION


def test_one_restore_intent_per_warehouse():
    assert "primary key (organization_id, warehouse_name)" in MIGRATION


def test_drift_state_constraint():
    assert "check (drift_state in ('ok','drifted','unsupported'))" in MIGRATION


def test_managed_default_floor_and_stored_default_constraints():
    assert "managed_auto_suspend >= 60" in MIGRATION
    assert "stored_default_auto_suspend not in (0, 1)" in MIGRATION


def test_worker_tenants_includes_outstanding_intents():
    # A kill-switched org with an outstanding sentinel must still be enumerated so it drains.
    assert "automated_savings_restore_intents" in MIGRATION.split(
        "function automated_savings_worker_tenants"
    )[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/api pytest tests/test_automated_savings_migration.py -v`
Expected: FAIL — migration file does not exist.

- [ ] **Step 3: Write the migration**

`supabase/migrations/202607120001_automated_savings.sql` (follow the conventions in `202606080001` and `202606160001`: `set_updated_at` triggers, RLS via the `is_organization_*` helpers, service-role-only RPCs):

```sql
-- Automated Savings: opt-in settings, per-warehouse enrollment, restore-intent sentinel.

create table automated_savings_settings (
    organization_id uuid primary key references organizations(id) on delete cascade,
    agreed_at timestamptz,
    global_enabled boolean not null default false,
    grant_present boolean not null default false,
    grant_checked_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table automated_savings_warehouses (
    organization_id uuid not null references organizations(id) on delete cascade,
    warehouse_name text not null,
    enabled boolean not null default false,
    managed_auto_suspend integer check (managed_auto_suspend is null or managed_auto_suspend >= 60),
    stored_default_auto_suspend integer check (stored_default_auto_suspend is null or stored_default_auto_suspend not in (0, 1)),
    warehouse_created_on timestamptz,
    cooldown_ts timestamptz,
    drift_state text not null default 'ok' check (drift_state in ('ok','drifted','unsupported')),
    drifted_value integer,
    updated_at timestamptz not null default now(),
    primary key (organization_id, warehouse_name)
);
-- managed_auto_suspend: live restore target + drift baseline (editable via API, floor 60).
-- stored_default_auto_suspend: immutable opt-in capture, reference/audit only.

create table automated_savings_restore_intents (
    organization_id uuid not null references organizations(id) on delete cascade,
    warehouse_name text not null,
    restore_to integer not null,
    set_at timestamptz not null default now(),
    primary key (organization_id, warehouse_name)
);

create trigger set_automated_savings_settings_updated_at
    before update on automated_savings_settings
    for each row execute function set_updated_at();

create trigger set_automated_savings_warehouses_updated_at
    before update on automated_savings_warehouses
    for each row execute function set_updated_at();

alter table automated_savings_settings enable row level security;
alter table automated_savings_warehouses enable row level security;
alter table automated_savings_restore_intents enable row level security;

-- Members read; only owners/admins mutate. Restore-intents are worker-only
-- (service role bypasses RLS); members may read them for status display.
create policy automated_savings_settings_read on automated_savings_settings
    for select to authenticated using (is_organization_member(organization_id));
create policy automated_savings_settings_write on automated_savings_settings
    for all to authenticated using (is_organization_admin(organization_id))
    with check (is_organization_admin(organization_id));

create policy automated_savings_warehouses_read on automated_savings_warehouses
    for select to authenticated using (is_organization_member(organization_id));
create policy automated_savings_warehouses_write on automated_savings_warehouses
    for all to authenticated using (is_organization_admin(organization_id))
    with check (is_organization_admin(organization_id));

create policy automated_savings_intents_read on automated_savings_restore_intents
    for select to authenticated using (is_organization_member(organization_id));
-- No authenticated write policy: intents are written only by the worker (service role).

-- Worker tenant enumeration: orgs with the global switch on and >=1 enrolled warehouse,
-- UNION orgs with any outstanding restore-intent (so a kill-switched org still drains).
create or replace function automated_savings_worker_tenants()
returns table (organization_id uuid)
language sql
security definer
set search_path = public
as $$
    select s.organization_id
    from automated_savings_settings s
    where s.global_enabled
      and exists (
          select 1 from automated_savings_warehouses w
          where w.organization_id = s.organization_id and w.enabled
      )
    union
    select i.organization_id
    from automated_savings_restore_intents i;
$$;

revoke all on function automated_savings_worker_tenants() from public;
grant execute on function automated_savings_worker_tenants() to service_role;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/api pytest tests/test_automated_savings_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Apply the migration locally and smoke-check**

Run (if the Supabase CLI/local stack is available): `supabase db reset` (or `supabase migration up`) and confirm no SQL errors. If no local stack, note in the commit that the migration is validated by shape-test only and must be applied in the Supabase project before deploy.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/202607120001_automated_savings.sql apps/api/tests/test_automated_savings_migration.py
git commit -m "feat: add automated-savings migration (settings, enrollment, restore-intent)"
```

---

## Phase 2 — Worker engine

> Build the worker as pure, hermetic units first (snapshot parsing → decision → reconciliation), then wire the impure edges (store, session, loops). Every engine test mocks the Snowflake session and the store — no network.

### Task 4: Worker app scaffold + config

**Files:**
- Create: `apps/auto-savings/pyproject.toml`, `apps/auto-savings/dev.py`
- Create: `apps/auto-savings/src/auto_savings/__init__.py`
- Create: `apps/auto-savings/src/auto_savings/config.py`
- Test: `apps/auto-savings/tests/test_config.py`

**Interfaces:**
- Produces: `WorkerConfig` dataclass (frozen) with `from_environment()`, fields: `poll_interval_seconds: float = 3.0`, `poll_timeout_seconds: float = 20.0` (watchdog), `socket_timeout_seconds: int = 15` (connector network/socket read timeout — MUST be < `poll_timeout_seconds`; this is what actually frees a wedged thread), `cooldown_seconds: int = 60` (anti-thrash guard after a restore — one billing minute; env-tunable), `uptime_floor_seconds: int = 62`, `max_intent_hold_ticks: int = 5` (finding #4 — how many ticks to hold an intent while a slow suspend lands before force-restoring), `orphan_grace_seconds: int = 120` (finding #10 — how long an intent whose warehouse has vanished from the snapshot survives before it and its enrollment are cleaned up), `tenant_refresh_seconds: int = 30` (supervisor re-enumeration), `num_replicas: int = 1`, `replica_index: int = 0`, `max_workers: int = 64`, `supabase_url: str`, `supabase_service_role_key: str`, `auth_required: bool = True`, `query_timeout_seconds: int = 120`. It also satisfies the `ResolverSettings` protocol (has `auth_required` + `query_timeout_seconds`) so it can be passed straight to `resolve_snowflake_config`.

- [ ] **Step 1: Write the failing test**

`apps/auto-savings/tests/test_config.py`:

```python
from auto_savings.config import WorkerConfig


def test_from_environment_reads_cadence_and_sharding(monkeypatch):
    monkeypatch.setenv("AUTO_SAVINGS_POLL_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("AUTO_SAVINGS_NUM_REPLICAS", "3")
    monkeypatch.setenv("AUTO_SAVINGS_REPLICA_INDEX", "2")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    config = WorkerConfig.from_environment()

    assert config.poll_interval_seconds == 5.0
    assert config.num_replicas == 3
    assert config.replica_index == 2
    assert config.uptime_floor_seconds == 62  # hardcoded guardrail default


def test_defaults_are_safe(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    config = WorkerConfig.from_environment()
    assert config.poll_interval_seconds == 3.0
    assert config.cooldown_seconds == 60
    assert config.num_replicas == 1


def test_socket_timeout_must_be_below_poll_timeout():
    import pytest
    with pytest.raises(ValueError):
        WorkerConfig(supabase_url="u", supabase_service_role_key="k",
                     socket_timeout_seconds=20, poll_timeout_seconds=20)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/auto-savings pytest tests/test_config.py -v`
Expected: FAIL — `auto_savings.config` does not exist. (First create `pyproject.toml` — Step 3 — then this runs.)

- [ ] **Step 3: Write `pyproject.toml` and the config module**

`apps/auto-savings/pyproject.toml`:

```toml
[project]
name = "greysight-auto-savings"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "greysight-connect",
    "httpx==0.28.1",
    "snowflake-connector-python==3.12.4",
    "python-dotenv",
]

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "pytest-cov", "ruff"]

[tool.uv.sources]
greysight-connect = { path = "../../shared/connect", editable = true }

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
asyncio_mode = "auto"
```

`apps/auto-savings/src/auto_savings/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class WorkerConfig:
    supabase_url: str
    supabase_service_role_key: str
    poll_interval_seconds: float = 3.0
    poll_timeout_seconds: float = 20.0
    socket_timeout_seconds: int = 15
    cooldown_seconds: int = 60
    uptime_floor_seconds: int = 62
    max_intent_hold_ticks: int = 5
    orphan_grace_seconds: int = 120
    tenant_refresh_seconds: int = 30
    num_replicas: int = 1
    replica_index: int = 0
    max_workers: int = 64
    auth_required: bool = True
    query_timeout_seconds: int = 120

    def __post_init__(self) -> None:
        # The socket read timeout MUST fire before the watchdog, or the watchdog trips
        # while the pool thread is still blocked → thread leak (Codex R2.1 MED).
        if self.socket_timeout_seconds >= self.poll_timeout_seconds:
            raise ValueError(
                f"socket_timeout_seconds ({self.socket_timeout_seconds}) must be < "
                f"poll_timeout_seconds ({self.poll_timeout_seconds})"
            )

    @classmethod
    def from_environment(cls) -> "WorkerConfig":
        return cls(
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            poll_interval_seconds=_float("AUTO_SAVINGS_POLL_INTERVAL_SECONDS", 3.0),
            poll_timeout_seconds=_float("AUTO_SAVINGS_POLL_TIMEOUT_SECONDS", 20.0),
            socket_timeout_seconds=_int("AUTO_SAVINGS_SOCKET_TIMEOUT_SECONDS", 15),
            cooldown_seconds=_int("AUTO_SAVINGS_COOLDOWN_SECONDS", 60),
            uptime_floor_seconds=_int("AUTO_SAVINGS_UPTIME_FLOOR_SECONDS", 62),
            max_intent_hold_ticks=_int("AUTO_SAVINGS_MAX_INTENT_HOLD_TICKS", 5),
            orphan_grace_seconds=_int("AUTO_SAVINGS_ORPHAN_GRACE_SECONDS", 120),
            tenant_refresh_seconds=_int("AUTO_SAVINGS_TENANT_REFRESH_SECONDS", 30),
            num_replicas=_int("AUTO_SAVINGS_NUM_REPLICAS", 1),
            replica_index=_int("AUTO_SAVINGS_REPLICA_INDEX", 0),
            max_workers=_int("AUTO_SAVINGS_MAX_WORKERS", 64),
            query_timeout_seconds=_int("GREYSIGHT_QUERY_TIMEOUT_SECONDS", 120),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv sync --directory apps/auto-savings && uv run --directory apps/auto-savings pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/auto-savings/pyproject.toml apps/auto-savings/uv.lock apps/auto-savings/src/auto_savings/__init__.py apps/auto-savings/src/auto_savings/config.py apps/auto-savings/tests/test_config.py
git commit -m "feat: scaffold auto-savings worker app + config"
```

---

### Task 5: Warehouse snapshot parsing + uptime derivation

**Files:**
- Create: `apps/auto-savings/src/auto_savings/warehouse_snapshot.py`
- Test: `apps/auto-savings/tests/test_warehouse_snapshot.py`

**Interfaces:**
- Consumes: raw `list[dict]` rows from `execute_metadata_query("SHOW WAREHOUSES", …)` (lowercased column keys).
- Produces:
  - `@dataclass(frozen=True) class WarehouseSnapshot` with fields `name: str`, `state: str`, `type: str`, `size: str | None`, `started_clusters: int`, `min_cluster_count: int`, `max_cluster_count: int`, `running: int`, `queued: int`, `auto_suspend: int | None`, `auto_resume: bool`, `resumed_on: datetime | None`, `created_on: datetime | None`.
  - `parse_warehouses(rows: list[dict], *, now: datetime) -> list[WarehouseSnapshot]` — parses **by column name**, tolerating absence/case (`row.get(key.lower())`), and **coerces `resumed_on`/`created_on` to tz-aware UTC datetimes** (finding #3: the connector may hand back SHOW timestamps as strings or tz-naive). `_coerce_ts` handles `None`, `datetime` (naive → assume UTC; aware → `astimezone(utc)`), and `str` (parse the format the Task 0 spike recorded).
  - `uptime_seconds(snapshot: WarehouseSnapshot, *, now: datetime) -> float | None` — returns `(now - resumed_on).total_seconds()` when `resumed_on` is set (already coerced tz-aware by the parser); `None` when `resumed_on is None`. `now` must be tz-aware (`datetime.now(timezone.utc)`).

- [ ] **Step 1: Write the failing tests**

`apps/auto-savings/tests/test_warehouse_snapshot.py`:

```python
from datetime import datetime, timedelta, timezone

from auto_savings.warehouse_snapshot import parse_warehouses, uptime_seconds

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _row(**overrides):
    base = {
        "name": "WH1", "state": "STARTED", "type": "STANDARD", "size": "X-Small",
        "started_clusters": 1, "min_cluster_count": 1, "max_cluster_count": 1,
        "running": 0, "queued": 0, "auto_suspend": 300, "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90), "created_on": NOW - timedelta(days=5),
    }
    base.update(overrides)
    return base


def test_parse_maps_columns_by_name():
    [wh] = parse_warehouses([_row()], now=NOW)
    assert wh.name == "WH1"
    assert wh.type == "STANDARD"
    assert wh.auto_resume is True
    assert wh.started_clusters == 1


def test_parse_tolerates_missing_columns_and_case():
    [wh] = parse_warehouses([{"NAME": "WH2", "state": "SUSPENDED", "type": "STANDARD"}], now=NOW)
    assert wh.name == "WH2"
    assert wh.running == 0  # missing → default
    assert wh.resumed_on is None


def test_uptime_from_tz_aware_resumed_on():
    [wh] = parse_warehouses([_row()], now=NOW)
    assert uptime_seconds(wh, now=NOW) == 90.0


def test_uptime_none_when_never_resumed():
    [wh] = parse_warehouses([_row(resumed_on=None)], now=NOW)
    assert uptime_seconds(wh, now=NOW) is None


def test_string_resumed_on_is_coerced_to_tz_aware():
    # SHOW WAREHOUSES often returns timestamps as strings — the parser must coerce,
    # not pass them through (finding #3). Use the exact format the Task 0 spike recorded.
    [wh] = parse_warehouses([_row(resumed_on="2026-07-12 11:58:30.000 -0000")], now=NOW)
    assert wh.resumed_on is not None
    assert wh.resumed_on.tzinfo is not None
    assert uptime_seconds(wh, now=NOW) == 90.0


def test_naive_resumed_on_is_assumed_utc():
    [wh] = parse_warehouses([_row(resumed_on=datetime(2026, 7, 12, 11, 58, 30))], now=NOW)
    assert wh.resumed_on.tzinfo is not None
    assert uptime_seconds(wh, now=NOW) == 90.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/auto-savings pytest tests/test_warehouse_snapshot.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the parser**

`apps/auto-savings/src/auto_savings/warehouse_snapshot.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class WarehouseSnapshot:
    name: str
    state: str
    type: str
    size: str | None
    started_clusters: int
    min_cluster_count: int
    max_cluster_count: int
    running: int
    queued: int
    auto_suspend: int | None
    auto_resume: bool
    resumed_on: datetime | None
    created_on: datetime | None


def _ci_get(row: dict, key: str, default=None):
    if key in row:
        return row[key]
    lowered = {str(k).lower(): v for k, v in row.items()}
    return lowered.get(key.lower(), default)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1", "y")


def _coerce_ts(value) -> datetime | None:
    """Coerce a SHOW WAREHOUSES timestamp (str | datetime | None) to tz-aware UTC.
    The connector may return these as strings or tz-naive datetimes (finding #3)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    text = str(value).strip()
    # Snowflake SHOW timestamps look like "2026-07-12 11:58:30.000 -0000"
    # (exact format confirmed by the Task 0 spike). Try fromisoformat first, then fallback formats.
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unparseable SHOW WAREHOUSES timestamp: {value!r}")


def parse_warehouses(rows: list[dict], *, now: datetime) -> list[WarehouseSnapshot]:
    snapshots: list[WarehouseSnapshot] = []
    for row in rows:
        auto_suspend_raw = _ci_get(row, "auto_suspend")
        snapshots.append(
            WarehouseSnapshot(
                name=str(_ci_get(row, "name", "")),
                state=str(_ci_get(row, "state", "")),
                type=str(_ci_get(row, "type", "")),
                size=_ci_get(row, "size"),
                started_clusters=_as_int(_ci_get(row, "started_clusters"), 0),
                min_cluster_count=_as_int(_ci_get(row, "min_cluster_count"), 1),
                max_cluster_count=_as_int(_ci_get(row, "max_cluster_count"), 1),
                running=_as_int(_ci_get(row, "running"), 0),
                queued=_as_int(_ci_get(row, "queued"), 0),
                auto_suspend=None if auto_suspend_raw in (None, "") else _as_int(auto_suspend_raw, 0),
                auto_resume=_as_bool(_ci_get(row, "auto_resume", False)),
                resumed_on=_coerce_ts(_ci_get(row, "resumed_on")),
                created_on=_coerce_ts(_ci_get(row, "created_on")),
            )
        )
    return snapshots


def uptime_seconds(snapshot: WarehouseSnapshot, *, now: datetime) -> float | None:
    if snapshot.resumed_on is None:
        return None
    return (now - snapshot.resumed_on).total_seconds()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/auto-savings pytest tests/test_warehouse_snapshot.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/auto-savings/src/auto_savings/warehouse_snapshot.py apps/auto-savings/tests/test_warehouse_snapshot.py
git commit -m "feat: parse SHOW WAREHOUSES snapshot + derive uptime tz-safely"
```

---

### Task 6: The suspend decision (pure truth table)

**Files:**
- Create: `apps/auto-savings/src/auto_savings/decision.py`
- Test: `apps/auto-savings/tests/test_decision.py`

**Interfaces:**
- Consumes: `WarehouseSnapshot` + `uptime_seconds` (Task 5), and a small `EnrollmentState` view produced by the store (Task 7). To keep this unit pure and decoupled, `should_force_suspend` takes primitives, not the store row:
- Produces: `should_force_suspend(snapshot: WarehouseSnapshot, *, now: datetime, uptime_floor_seconds: int, in_cooldown: bool, is_drifted: bool, has_outstanding_intent: bool) -> bool`. Returns True only when **all** hold: `type == "STANDARD"`, `state == "STARTED"`, `started_clusters == min_cluster_count`, uptime is not None and `>= uptime_floor_seconds`, `running == 0`, `queued == 0`, `auto_resume is True`, `not in_cooldown`, `not is_drifted`, `not has_outstanding_intent`.

- [ ] **Step 1: Write the failing truth-table test**

`apps/auto-savings/tests/test_decision.py`:

```python
from datetime import datetime, timedelta, timezone

from auto_savings.decision import should_force_suspend
from auto_savings.warehouse_snapshot import WarehouseSnapshot

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _wh(**overrides) -> WarehouseSnapshot:
    base = dict(
        name="WH1", state="STARTED", type="STANDARD", size="X-Small",
        started_clusters=1, min_cluster_count=1, max_cluster_count=1,
        running=0, queued=0, auto_suspend=300, auto_resume=True,
        resumed_on=NOW - timedelta(seconds=90), created_on=NOW - timedelta(days=1),
    )
    base.update(overrides)
    return WarehouseSnapshot(**base)


def _decide(wh, **kw):
    defaults = dict(now=NOW, uptime_floor_seconds=62, in_cooldown=False,
                    is_drifted=False, has_outstanding_intent=False)
    defaults.update(kw)
    return should_force_suspend(wh, **defaults)


def test_fires_when_all_conditions_hold():
    assert _decide(_wh()) is True


def test_each_precondition_individually_blocks():
    assert _decide(_wh(type="SNOWPARK-OPTIMIZED")) is False
    assert _decide(_wh(state="SUSPENDED")) is False
    assert _decide(_wh(state="RESUMING")) is False
    assert _decide(_wh(started_clusters=2, min_cluster_count=1, max_cluster_count=4)) is False
    assert _decide(_wh(resumed_on=NOW - timedelta(seconds=30))) is False  # uptime < floor
    assert _decide(_wh(resumed_on=None)) is False                          # never resumed
    assert _decide(_wh(running=1)) is False
    assert _decide(_wh(queued=1)) is False
    assert _decide(_wh(auto_resume=False)) is False
    assert _decide(_wh(), in_cooldown=True) is False
    assert _decide(_wh(), is_drifted=True) is False
    assert _decide(_wh(), has_outstanding_intent=True) is False


def test_maximized_fires_when_all_clusters_idle_at_floor():
    # min == max == N, started at N, idle → acts.
    assert _decide(_wh(started_clusters=3, min_cluster_count=3, max_cluster_count=3)) is True


def test_autoscale_above_floor_does_not_fire():
    assert _decide(_wh(started_clusters=3, min_cluster_count=1, max_cluster_count=4)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/auto-savings pytest tests/test_decision.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the decision**

`apps/auto-savings/src/auto_savings/decision.py`:

```python
from __future__ import annotations

from datetime import datetime

from auto_savings.warehouse_snapshot import WarehouseSnapshot, uptime_seconds


def should_force_suspend(
    snapshot: WarehouseSnapshot,
    *,
    now: datetime,
    uptime_floor_seconds: int,
    in_cooldown: bool,
    is_drifted: bool,
    has_outstanding_intent: bool,
) -> bool:
    if snapshot.type != "STANDARD":
        return False
    if snapshot.state != "STARTED":
        return False
    if snapshot.started_clusters != snapshot.min_cluster_count:
        return False
    uptime = uptime_seconds(snapshot, now=now)
    if uptime is None or uptime < uptime_floor_seconds:
        return False
    if snapshot.running != 0 or snapshot.queued != 0:
        return False
    if not snapshot.auto_resume:
        return False
    if in_cooldown or is_drifted or has_outstanding_intent:
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/auto-savings pytest tests/test_decision.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/auto-savings/src/auto_savings/decision.py apps/auto-savings/tests/test_decision.py
git commit -m "feat: pure suspend-decision truth table"
```

---

### Task 7: Supabase store (service-role reads/writes) with an in-memory fake

**Files:**
- Create: `apps/auto-savings/src/auto_savings/store.py`
- Test: `apps/auto-savings/tests/test_store.py`

**Interfaces:**
- Consumes: `WorkerConfig` (Task 4).
- Produces:
  - `@dataclass(frozen=True) class EnrollmentRow` — `organization_id, warehouse_name, enabled, managed_auto_suspend, stored_default_auto_suspend, warehouse_created_on, cooldown_ts, drift_state, drifted_value`. **`managed_auto_suspend` is the live restore target + drift baseline; `stored_default_auto_suspend` is the immutable reference.**
  - `@dataclass(frozen=True) class SettingsRow` — `organization_id, agreed_at, global_enabled, grant_present, grant_checked_at`.
  - `@dataclass(frozen=True) class RestoreIntent` — `organization_id, warehouse_name, restore_to, set_at`.
  - A `Store` protocol with methods the engine calls (all keyed by `organization_id`):
    - `get_settings(org_id) -> SettingsRow | None` — the engine reads `global_enabled` every cycle from this (finding #1).
    - `list_enrollments(org_id) -> list[EnrollmentRow]`
    - `list_intents(org_id) -> list[RestoreIntent]`
    - `write_intent(org_id, warehouse_name, restore_to, *, set_at=None) -> None` — `set_at` defaults to `now(timezone.utc)`; the engine/reconcile pass the cycle's `now` so intent-age math is deterministic (and testable). `InMemoryStore` records it verbatim.
    - `delete_intent(org_id, warehouse_name) -> None`
    - `set_cooldown(org_id, warehouse_name, cooldown_ts) -> None`
    - `mark_drifted(org_id, warehouse_name, drifted_value) -> None`
    - `mark_unsupported(org_id, warehouse_name) -> None`
    - `clear_enrollment(org_id, warehouse_name) -> None` — used when a `created_on` mismatch invalidates a stale enrollment (finding M2/#8).
    - `worker_tenants() -> list[str]` — returns orgs with `global_enabled` OR an outstanding intent (matches the RPC).
  - `SupabaseStore(config)` — the real impl (raw httpx, service-role headers, PostgREST + the `automated_savings_worker_tenants` RPC), mirroring `SupabaseRunCacheStore`.
  - `InMemoryStore()` — a test double implementing the same protocol, plus a `seed_enrollment(row)` / `seed_settings(row)` helper for tests (used by all engine/reconcile tests).

**Design note:** The engine is written against the `Store` protocol so every engine test uses `InMemoryStore`. Only this task's `SupabaseStore` tests touch httpx (via a mock transport).

- [ ] **Step 1: Write the failing test (InMemoryStore contract + SupabaseStore request shape)**

`apps/auto-savings/tests/test_store.py`:

```python
from datetime import datetime, timezone

import httpx

from auto_savings.config import WorkerConfig
from auto_savings.store import InMemoryStore, SupabaseStore


def test_in_memory_intent_lifecycle():
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    [intent] = store.list_intents("org-1")
    assert intent.restore_to == 300
    store.delete_intent("org-1", "WH1")
    assert store.list_intents("org-1") == []


def test_supabase_store_writes_intent_via_postgrest():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(201, json=[])

    config = WorkerConfig(supabase_url="https://x.supabase.co", supabase_service_role_key="svc")
    store = SupabaseStore(config, transport=httpx.MockTransport(handler))
    store.write_intent("org-1", "WH1", restore_to=300)

    assert "automated_savings_restore_intents" in seen["url"]
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer svc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/auto-savings pytest tests/test_store.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `store.py`**

Implement `EnrollmentRow`, `RestoreIntent`, the `Store` `Protocol`, `InMemoryStore` (dict-backed), and `SupabaseStore`. `SupabaseStore` uses `httpx.Client(base_url=f"{supabase_url}/rest/v1", headers={"apikey": key, "authorization": f"Bearer {key}", "content-type": "application/json"}, transport=transport)`. `write_intent` POSTs to `/automated_savings_restore_intents` with `Prefer: resolution=merge-duplicates` (upsert on the composite PK); `delete_intent` DELETEs with `organization_id=eq.&warehouse_name=eq.` filters; `list_*` GETs with `select=*&organization_id=eq.`; `worker_tenants` POSTs `/rpc/automated_savings_worker_tenants`. Follow the header/error conventions in `apps/api/app/services/dashboard_run_cache.py` (`SupabaseRunCacheStore`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/auto-savings pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/auto-savings/src/auto_savings/store.py apps/auto-savings/tests/test_store.py
git commit -m "feat: auto-savings Supabase store + in-memory fake"
```

---

### Task 8: Reconciliation (restore-intent + drift), pure over one snapshot

**Files:**
- Create: `apps/auto-savings/src/auto_savings/reconcile.py`
- Test: `apps/auto-savings/tests/test_reconcile.py`

**Interfaces:**
- Consumes: `WarehouseSnapshot` (Task 5), `EnrollmentRow` + `RestoreIntent` + `Store` (Task 7), `WorkerConfig` (Task 4).
- Produces: `reconcile(org_id, snapshots, enrollments, intents, store, *, now, cooldown_seconds, intent_hold_seconds, orphan_grace_seconds) -> set[str]` — the **reconcile-then-decide** first half over a single snapshot. (`intent_hold_seconds` = `max_intent_hold_ticks × poll_interval_seconds`, computed by the engine.) Iterates the **union of enrolled and intent-bearing warehouse names**, looking each up in the snapshot (which may be absent). Runs for **every** managed warehouse **regardless of `enabled`** (finding #5: draining an outstanding sentinel must not depend on the warehouse still being enabled — an unenroll/kill mid-suspend must still restore). Returns the "settled this tick, skip in decide" set. Behavior per managed warehouse, in order:
  0. **Warehouse absent from the snapshot entirely (customer dropped it; finding #10)** — if a managed/intent-bearing warehouse name is **not** in the snapshot: if it has an outstanding intent whose age (`now - set_at`) exceeds `orphan_grace_seconds`, the warehouse is gone for good → `store.delete_intent(…)` + `store.clear_enrollment(…)` so the org can finally leave `worker_tenants()` (otherwise the loop never exits). Within the grace window (transient/partial snapshot), leave it. Either way, skip — no `ALTER` (there's nothing to alter). This runs **before** the created_on/intent branches, which all assume a live snapshot row.
  1. **`created_on` mismatch (drop+recreate reuse of the name; finding M2/#8)** — if the enrollment has a `warehouse_created_on` and the live `created_on` differs, this is a *different* warehouse reusing the name → **explicitly `store.delete_intent(…)` (if one exists) AND `store.clear_enrollment(…)`** — the outstanding intent's `restore_to` belongs to the *dropped* warehouse and must not be applied to the new one. Do **not** `apply_alter`. Add to skip-set. (Codex R2.1 HIGH: don't leave the stale intent.)
  2. **Outstanding restore-intent exists → we own it.** Re-check live `auto_suspend`:
     - **`live == intent.restore_to` → already restored (idempotent completion).** A prior tick's `apply_alter` succeeded but its `delete_intent` failed (or the warehouse resumed on its own to the restored value). Just `store.delete_intent(…)` + `store.set_cooldown(…, now + cooldown_seconds)`, no `ALTER`. **This terminal case is required** — without it, a stuck intent whose live value already equals `restore_to` matches no other subcase and keeps the tenant in `worker_tenants()` forever (Codex R2.1 HIGH). Skip.
     - `live ∉ {1, intent.restore_to}` → customer edited mid-suspend → `store.mark_drifted(…, drifted_value=live)`, `store.delete_intent(…)`, skip. (Do not stomp their edit.)
     - Else (`live == 1`) decide restore vs. **hold** by live state (finding #4 — refined; NOT unconditional):
       - `state == SUSPENDED` → **restore**: `apply_alter(name, restore_to)` **then on success** `store.delete_intent(…)` + `store.set_cooldown(…, now + cooldown_seconds)`. Savings captured.
       - `state == STARTED and (running > 0 or queued > 0)` → a query landed → **restore, NO cooldown**: `apply_alter(name, restore_to)` then `store.delete_intent(…)` (backed off).
       - `state == STARTED and idle and live == 1` → suspend just hasn't landed yet → **HOLD**: do nothing this tick, UNLESS the intent age (`now - intent.set_at`) exceeds `intent_hold_seconds` → then force-restore (anti-stranding backstop), `apply_alter(name, restore_to)` + `store.delete_intent(…)` + cooldown. In all restore branches, **`apply_alter` must succeed before `delete_intent`** (finding #9) — a failed `ALTER` leaves the intent so the next tick retries. Add to skip-set (held or restored, the decide step must not touch it).
  3. **No intent, warehouse became non-STANDARD** → `store.mark_unsupported(…)`, skip (auto-paused).
  4. **No intent, `live ∉ {managed_auto_suspend, 1}`** (drift baseline is the LIVE restore target `managed_auto_suspend`, not the immutable capture — finding #2) → `store.mark_drifted(…, drifted_value=live)`, skip.
  5. **No intent, `live == 1`** (independent sentinel, not ours) → left untouched, skip (not flagged, not acted on).

  `reconcile` never issues `AUTO_SUSPEND=1` itself; it only restores/heals. `apply_alter(warehouse_name, auto_suspend_value)` is injected so tests assert the calls without Snowflake; it may raise (failed ALTER) — reconcile must NOT delete the intent when it does.

- [ ] **Step 1: Write the failing tests**

`apps/auto-savings/tests/test_reconcile.py`:

```python
from datetime import datetime, timedelta, timezone

from auto_savings.reconcile import reconcile
from auto_savings.store import EnrollmentRow, InMemoryStore, RestoreIntent
from auto_savings.warehouse_snapshot import WarehouseSnapshot

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _wh(name="WH1", auto_suspend=1, state="SUSPENDED", type="STANDARD"):
    return WarehouseSnapshot(
        name=name, state=state, type=type, size="X-Small",
        started_clusters=1, min_cluster_count=1, max_cluster_count=1,
        running=0, queued=0, auto_suspend=auto_suspend, auto_resume=True,
        resumed_on=None, created_on=NOW - timedelta(days=1),
    )


def _enroll(name="WH1", managed=300, stored=300, created=None, enabled=True):
    return EnrollmentRow(
        organization_id="org-1", warehouse_name=name, enabled=enabled,
        managed_auto_suspend=managed, stored_default_auto_suspend=stored,
        warehouse_created_on=created or (NOW - timedelta(days=1)), cooldown_ts=None,
        drift_state="ok", drifted_value=None,
    )


def _reconcile(store, snaps, enrolls, **kw):
    for e in enrolls:
        store.seed_enrollment(e)
    defaults = dict(now=NOW, cooldown_seconds=60, intent_hold_seconds=15.0, orphan_grace_seconds=120.0)
    defaults.update(kw)
    calls = []
    skip = reconcile("org-1", snaps, enrolls, store.list_intents("org-1"), store,
                     apply_alter=lambda name, val: calls.append((name, val)), **defaults)
    return skip, calls


def test_intent_restores_and_cools_down_when_suspended():
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    skip, calls = _reconcile(store, [_wh(auto_suspend=1, state="SUSPENDED")], [_enroll()])
    assert calls == [("WH1", 300)]  # restore target (managed default)
    assert store.list_intents("org-1") == []
    assert store.list_enrollments("org-1")[0].cooldown_ts == NOW + timedelta(seconds=60)  # cooldown_seconds=60
    assert "WH1" in skip


def test_started_busy_restores_without_cooldown():
    # A query landed → back off, restore default, but do NOT burn cooldown.
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    busy = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                             started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                             running=1, queued=0, auto_suspend=1, auto_resume=True,
                             resumed_on=None, created_on=NOW - timedelta(days=1))
    _, calls = _reconcile(store, [busy], [_enroll()])
    assert calls == [("WH1", 300)]
    assert store.list_enrollments("org-1")[0].cooldown_ts is None  # not cooled down


def test_started_idle_still_one_holds_intent_until_age_exceeds_bound():
    # Suspend hasn't landed yet — HOLD, don't guillotine it (finding #4).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW)  # deterministic age
    idle_started = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                                     started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                                     running=0, queued=0, auto_suspend=1, auto_resume=True,
                                     resumed_on=None, created_on=NOW - timedelta(days=1))
    # Fresh intent → held, no ALTER.
    _, calls = _reconcile(store, [idle_started], [_enroll()], now=NOW)
    assert calls == []
    assert store.list_intents("org-1") != []  # still held
    # Age it past intent_hold_seconds → force-restore (anti-stranding backstop).
    _, calls2 = _reconcile(store, [idle_started], [_enroll()],
                           now=NOW + timedelta(seconds=30), intent_hold_seconds=15.0)
    assert calls2 == [("WH1", 300)]


def test_intent_restore_detects_customer_edit_mid_suspend():
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    _, calls = _reconcile(store, [_wh(auto_suspend=120, state="STARTED")], [_enroll()])
    assert calls == []  # not stomped
    assert store.list_enrollments("org-1")[0].drift_state == "drifted"


def test_failed_alter_leaves_intent_for_retry():
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    for e in [_enroll()]:
        store.seed_enrollment(e)

    def boom(name, val):
        raise RuntimeError("ALTER failed")

    try:
        reconcile("org-1", [_wh(auto_suspend=1, state="SUSPENDED")], [_enroll()],
                  store.list_intents("org-1"), store, now=NOW, cooldown_seconds=60,
                  intent_hold_seconds=15.0, orphan_grace_seconds=120.0, apply_alter=boom)
    except RuntimeError:
        pass
    assert store.list_intents("org-1") != []  # NOT deleted — next tick retries


def test_dropped_warehouse_with_stale_intent_is_cleaned_up():
    # Warehouse fully dropped: absent from snapshot + intent older than the grace →
    # delete intent + enrollment so the org can leave worker_tenants() (finding #10).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW)  # deterministic age
    # Empty snapshot (WH1 dropped); evaluate well past the grace window.
    _, calls = _reconcile(store, [], [_enroll()],
                          now=NOW + timedelta(seconds=200), orphan_grace_seconds=120.0)
    assert calls == []                       # nothing to ALTER
    assert store.list_intents("org-1") == []  # intent cleaned
    assert store.list_enrollments("org-1") == []  # enrollment cleared


def test_dropped_warehouse_within_grace_is_left_alone():
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW)
    _, _ = _reconcile(store, [], [_enroll()],
                      now=NOW + timedelta(seconds=10), orphan_grace_seconds=120.0)
    assert store.list_intents("org-1") != []  # transient absence — keep


def test_already_restored_intent_is_cleared_idempotently():
    # apply_alter succeeded but delete_intent failed last tick → live already == restore_to.
    # No matching subcase would leave the intent stuck forever (Codex R2.1 HIGH).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    live_restored = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                                      started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                                      running=0, queued=0, auto_suspend=300, auto_resume=True,
                                      resumed_on=None, created_on=NOW - timedelta(days=1))
    _, calls = _reconcile(store, [live_restored], [_enroll()])
    assert calls == []                             # no ALTER — already at restore_to
    assert store.list_intents("org-1") == []       # intent cleared idempotently
    assert store.list_enrollments("org-1")[0].cooldown_ts == NOW + timedelta(seconds=60)


def test_drain_runs_even_when_disabled():
    # Unenroll mid-suspend: enabled=False but intent must still drain (finding #5).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    _, calls = _reconcile(store, [_wh(auto_suspend=1, state="SUSPENDED")],
                          [_enroll(enabled=False)])
    assert calls == [("WH1", 300)]
    assert store.list_intents("org-1") == []


def test_created_on_mismatch_invalidates_stale_enrollment_and_intent():
    # Name reused by a recreated warehouse (finding M2/#8); a stale intent from the OLD
    # warehouse must be deleted, never applied to the new one (Codex R2.1 HIGH).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)  # belongs to the dropped warehouse
    fresh = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                              started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                              running=0, queued=0, auto_suspend=300, auto_resume=True,
                              resumed_on=None, created_on=NOW)  # created just now
    skip, calls = _reconcile(store, [fresh],
                             [_enroll(created=NOW - timedelta(days=30))])  # old enrollment
    assert calls == []                            # stale intent NOT applied to the new warehouse
    assert store.list_intents("org-1") == []      # stale intent deleted
    assert store.list_enrollments("org-1") == []  # stale enrollment dropped
    assert "WH1" in skip


def test_independent_one_without_intent_is_left_untouched():
    store = InMemoryStore()
    skip, calls = _reconcile(store, [_wh(auto_suspend=1, state="SUSPENDED")], [_enroll()])
    assert calls == []
    assert store.list_enrollments("org-1")[0].drift_state == "ok"  # not flagged
    assert "WH1" in skip


def test_drift_baseline_is_managed_default_not_stored_capture():
    # managed edited to 90; live at 90 is CORRECT, not drift. Live at 120 IS drift.
    store = InMemoryStore()
    _, _ = _reconcile(store, [_wh(auto_suspend=90, state="SUSPENDED")],
                      [_enroll(managed=90, stored=300)])
    assert store.list_enrollments("org-1")[0].drift_state == "ok"
    store2 = InMemoryStore()
    _reconcile(store2, [_wh(auto_suspend=120, state="SUSPENDED")],
               [_enroll(managed=90, stored=300)])
    assert store2.list_enrollments("org-1")[0].drift_state == "drifted"


def test_non_standard_marked_unsupported():
    store = InMemoryStore()
    _reconcile(store, [_wh(type="SNOWPARK-OPTIMIZED", auto_suspend=300)], [_enroll()])
    assert store.list_enrollments("org-1")[0].drift_state == "unsupported"
```

> `InMemoryStore.mark_drifted`/`mark_unsupported`/`set_cooldown`/`clear_enrollment` mutate the stored `EnrollmentRow` **immutably** (replace with a new frozen row / remove). `seed_enrollment(row)` (Task 7) preloads the fake.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/auto-savings pytest tests/test_reconcile.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `reconcile.py`**

Implement per the interface contract above, iterating the union of enrolled + intent-bearing names, in this branch order: (0) **absent from snapshot** → orphan cleanup past grace, else leave; (1) `created_on` mismatch → clear; (2) outstanding intent → restore/hold by live state, with `apply_alter`-before-`delete_intent`; (3) non-STANDARD → unsupported; (4) drift vs. `managed_auto_suspend`; (5) independent `live == 1` → skip untouched. Runs for every managed warehouse **regardless of `enabled`**. Every store mutation goes through the injected `store`; every `ALTER` through `apply_alter` (which may raise — do not delete the intent then). Return the skip-set.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/auto-savings pytest tests/test_reconcile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/auto-savings/src/auto_savings/reconcile.py apps/auto-savings/tests/test_reconcile.py apps/auto-savings/src/auto_savings/store.py
git commit -m "feat: reconcile restore-intent + drift over one snapshot"
```

---

### Task 9: Engine cycle (snapshot → reconcile → decide → act) with force-suspend lifecycle

**Files:**
- Create: `apps/auto-savings/src/auto_savings/engine.py`
- Test: `apps/auto-savings/tests/test_engine.py`

**Interfaces:**
- Consumes: everything in Tasks 5–8 + `WorkerConfig`.
- Produces: `run_cycle(org_id, *, rows, store, config, now, apply_alter) -> None` — the full per-tenant cycle over one `SHOW WAREHOUSES` result (`rows`):
  1. `snapshots = parse_warehouses(rows, now=now)`.
  2. **Reconcile always** (drains intents regardless of the switch): `skip = reconcile(org_id, snapshots, enrollments, intents, store, now=now, cooldown_seconds=config.cooldown_seconds, intent_hold_seconds=config.max_intent_hold_ticks * config.poll_interval_seconds, orphan_grace_seconds=config.orphan_grace_seconds, apply_alter=apply_alter)`.
  3. **Decide only when the switch is on.** Read `settings = store.get_settings(org_id)`; if `settings is None or not settings.global_enabled`, **skip the decide step entirely** (the kill switch — finding #1). Otherwise for each enrolled, **enabled** warehouse **not** in `skip`: compute `in_cooldown = enrollment.cooldown_ts is not None and enrollment.cooldown_ts > now`; `is_drifted = enrollment.drift_state != "ok"`; `has_outstanding_intent = name in {i.warehouse_name for i in intents}`. If `should_force_suspend(snapshot, …)`: **write the restore-intent row first** (`store.write_intent(org_id, name, restore_to=enrollment.managed_auto_suspend, set_at=now)` — the live restore target, finding #2; `set_at=now` keeps intent-age deterministic), **then** `apply_alter(name, 1)`. Never in the other order (durability before mutation).
  - `apply_alter(name, value)` performs `ALTER WAREHOUSE <name> SET AUTO_SUSPEND = <value>` via the session; injected so the engine test is hermetic.

- [ ] **Step 1: Write the failing lifecycle tests**

`apps/auto-savings/tests/test_engine.py`:

```python
from datetime import datetime, timedelta, timezone

from auto_savings.config import WorkerConfig
from auto_savings.engine import run_cycle
from auto_savings.store import EnrollmentRow, InMemoryStore, SettingsRow

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = WorkerConfig(supabase_url="u", supabase_service_role_key="k",
                      cooldown_seconds=300, uptime_floor_seconds=62,
                      poll_interval_seconds=3.0, max_intent_hold_ticks=5)


def _seed_settings(store, global_enabled=True):
    store.seed_settings(SettingsRow(organization_id="org-1", agreed_at=NOW,
                                    global_enabled=global_enabled, grant_present=True,
                                    grant_checked_at=NOW))


def _rows(**overrides):
    row = {
        "name": "WH1", "state": "STARTED", "type": "STANDARD", "size": "X-Small",
        "started_clusters": 1, "min_cluster_count": 1, "max_cluster_count": 1,
        "running": 0, "queued": 0, "auto_suspend": 300, "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90),
    }
    row.update(overrides)
    return [row]


def _seed(store, cooldown_ts=None, drift_state="ok"):
    store.seed_enrollment(EnrollmentRow(
        organization_id="org-1", warehouse_name="WH1", enabled=True,
        managed_auto_suspend=300, stored_default_auto_suspend=300,
        warehouse_created_on=NOW - timedelta(days=1), cooldown_ts=cooldown_ts,
        drift_state=drift_state, drifted_value=None))


def test_idle_warehouse_gets_intent_then_alter_in_order():
    store = InMemoryStore()
    _seed(store); _seed_settings(store)
    calls = []
    run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls.append((n, v)))
    # Intent restore target is the LIVE managed default; intent written before the ALTER.
    assert store.list_intents("org-1")[0].restore_to == 300
    assert calls == [("WH1", 1)]


def test_kill_switch_off_stops_decide_but_still_drains():
    # global_enabled False → no new suspends, but an outstanding intent still restores.
    store = InMemoryStore()
    _seed(store); _seed_settings(store, global_enabled=False)
    store.write_intent("org-1", "WH1", restore_to=300)
    calls = []
    run_cycle("org-1", rows=_rows(state="SUSPENDED", auto_suspend=1, resumed_on=None),
              store=store, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls.append((n, v)))
    assert calls == [("WH1", 300)]          # drained
    # A fresh idle warehouse is NOT suspended while the switch is off.
    store2 = InMemoryStore()
    _seed(store2); _seed_settings(store2, global_enabled=False)
    calls2 = []
    run_cycle("org-1", rows=_rows(), store=store2, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls2.append((n, v)))
    assert calls2 == []


def test_busy_warehouse_not_touched():
    store = InMemoryStore()
    _seed(store); _seed_settings(store)
    calls = []
    run_cycle("org-1", rows=_rows(running=1), store=store, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls.append((n, v)))
    assert calls == []
    assert store.list_intents("org-1") == []


def test_next_tick_restores_and_sets_cooldown():
    store = InMemoryStore()
    _seed(store); _seed_settings(store)
    calls = []
    # Tick 1: set sentinel.
    run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls.append((n, v)))
    # Tick 2: warehouse now suspended; intent outstanding → restore + cooldown.
    later = NOW + timedelta(seconds=3)
    run_cycle("org-1", rows=_rows(state="SUSPENDED", auto_suspend=1, resumed_on=None),
              store=store, config=CONFIG, now=later,
              apply_alter=lambda n, v: calls.append((n, v)))
    assert calls == [("WH1", 1), ("WH1", 300)]  # set, then restore
    assert store.list_intents("org-1") == []
    assert store.list_enrollments("org-1")[0].cooldown_ts == later + timedelta(seconds=300)


def test_cooldown_blocks_reacquire():
    store = InMemoryStore()
    _seed(store, cooldown_ts=NOW + timedelta(seconds=100)); _seed_settings(store)
    calls = []
    run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls.append((n, v)))
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/auto-savings pytest tests/test_engine.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `engine.py`**

Implement `run_cycle` per the interface. Fetch `enrollments = store.list_enrollments(org_id)` and `intents = store.list_intents(org_id)` **once** at the top (single snapshot of store state for the cycle). **Reconcile always**; then read `get_settings` and run the decide step **only when `global_enabled`**. Restore target for new intents is `managed_auto_suspend`. Intent-before-ALTER ordering is mandatory.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/auto-savings pytest tests/test_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/auto-savings/src/auto_savings/engine.py apps/auto-savings/tests/test_engine.py apps/auto-savings/src/auto_savings/store.py
git commit -m "feat: engine cycle — reconcile, decide, force-suspend lifecycle"
```

---

### Task 10: Sharding predicate

**Files:**
- Create: `apps/auto-savings/src/auto_savings/sharding.py`
- Test: `apps/auto-savings/tests/test_sharding.py`

**Interfaces:**
- Produces: `owns_tenant(tenant_id: str, *, num_replicas: int, replica_index: int) -> bool` — `True` when `num_replicas <= 1` (single process owns all), else `hash_of(tenant_id) % num_replicas == replica_index`. Use a **stable** hash (`hashlib.sha256(tenant_id.encode()).digest()` → int), never Python's salted `hash()`.

- [ ] **Step 1: Write the failing test**

```python
from auto_savings.sharding import owns_tenant


def test_single_replica_owns_all():
    assert owns_tenant("any", num_replicas=1, replica_index=0) is True


def test_partition_is_stable_and_disjoint():
    ids = [f"org-{i}" for i in range(50)]
    r0 = {t for t in ids if owns_tenant(t, num_replicas=3, replica_index=0)}
    r1 = {t for t in ids if owns_tenant(t, num_replicas=3, replica_index=1)}
    r2 = {t for t in ids if owns_tenant(t, num_replicas=3, replica_index=2)}
    assert r0 | r1 | r2 == set(ids)     # every tenant owned once
    assert r0 & r1 == set() and r1 & r2 == set() and r0 & r2 == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/auto-savings pytest tests/test_sharding.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
from __future__ import annotations

import hashlib


def owns_tenant(tenant_id: str, *, num_replicas: int, replica_index: int) -> bool:
    if num_replicas <= 1:
        return True
    digest = hashlib.sha256(tenant_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % num_replicas == replica_index
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/auto-savings pytest tests/test_sharding.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/auto-savings/src/auto_savings/sharding.py apps/auto-savings/tests/test_sharding.py
git commit -m "feat: stable tenant sharding predicate (dormant single-process default)"
```

---

### Task 11: Warm persistent session + watchdog force-close + backoff reconnect

**Files:**
- Create: `apps/auto-savings/src/auto_savings/snowflake_session.py`
- Test: `apps/auto-savings/tests/test_snowflake_session.py`

**Interfaces:**
- Consumes: `SnowflakeConnectionConfig`, `execute_metadata_query` (Task 2), `WorkerConfig` (Task 4).
- Produces:
  - `class TenantSession` wrapping one persistent connection: `__init__(config, *, socket_timeout_seconds: int, connect=None)`; `show_warehouses() -> list[dict]` (runs `SHOW WAREHOUSES` on the warm connection); `alter_auto_suspend(name: str, value: int) -> None` (runs `ALTER WAREHOUSE "<name>" SET AUTO_SUSPEND = <value>`); `close_hard() -> None` (best-effort close for cleanup); `ensure_connected() -> None` (lazy connect with `client_session_keep_alive=True`).
  - **The actual wedge-escape mechanism is connector-level socket timeouts, NOT `close()` (finding #6).** `ensure_connected` passes `network_timeout=socket_timeout_seconds` and `socket_timeout=socket_timeout_seconds` into the connector kwargs so a blocking socket read raises on its own at the OS layer — `close_hard()` from another thread cannot interrupt a C-level `recv()`. `socket_timeout_seconds` MUST be `< poll_timeout_seconds`.
  - `next_backoff(attempt: int, *, base: float = 0.5, cap: float = 30.0, jitter: Callable[[], float]) -> float` — jittered exponential backoff (`min(cap, base * 2**attempt)` scaled by a `[0.5, 1.5)` jitter). `jitter` injected for determinism in tests.
  - Warehouse names are quoted/escaped for the `ALTER` to prevent identifier injection: wrap in double quotes and double any embedded `"`.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import Mock

from auto_savings.snowflake_session import TenantSession, next_backoff


def test_show_warehouses_reuses_one_connection():
    cursor = Mock()
    cursor.description = [("name",), ("state",)]
    cursor.fetchall.return_value = [("WH1", "STARTED")]
    connection = Mock()
    connection.cursor.return_value = cursor
    connects = []

    def fake_connect(_config):
        connects.append(1)
        return connection

    session = TenantSession(config=object(), socket_timeout_seconds=15, connect=fake_connect)
    session.show_warehouses()
    session.show_warehouses()
    assert len(connects) == 1  # warm reuse, not reconnect-per-poll


def test_connector_kwargs_include_socket_timeouts():
    # The real wedge-escape: connector network/socket timeouts, not close() (finding #6).
    class Cfg:
        def connector_kwargs(self):
            return {"account": "ab12345", "user": "svc"}

    session = TenantSession(config=Cfg(), socket_timeout_seconds=15, connect=lambda c: Mock())
    kwargs = session._connector_kwargs()  # the dict the default connect passes to snowflake.connector.connect
    assert kwargs["network_timeout"] == 15
    assert kwargs["socket_timeout"] == 15
    assert kwargs["client_session_keep_alive"] is True


def test_alter_quotes_identifier():
    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(config=object(), socket_timeout_seconds=15, connect=lambda c: connection)
    session.alter_auto_suspend('weird"name', 1)
    sql = cursor.execute.call_args[0][0]
    assert '"weird""name"' in sql
    assert "SET AUTO_SUSPEND = 1" in sql


def test_close_hard_closes_connection():
    connection = Mock()
    session = TenantSession(config=object(), socket_timeout_seconds=15, connect=lambda c: connection)
    session.ensure_connected()
    session.close_hard()
    connection.close.assert_called_once()


def test_backoff_is_bounded_and_jittered():
    assert next_backoff(0, base=0.5, cap=30.0, jitter=lambda: 1.0) == 0.5
    assert next_backoff(10, base=0.5, cap=30.0, jitter=lambda: 1.0) == 30.0  # capped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/auto-savings pytest tests/test_snowflake_session.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `snowflake_session.py`**

Implement `TenantSession` with a `_connector_kwargs()` helper that returns `config.connector_kwargs()` plus `client_session_keep_alive=True`, `network_timeout=self.socket_timeout_seconds`, `socket_timeout=self.socket_timeout_seconds` — these OS-level read timeouts are what free a wedged thread (finding #6). `ensure_connected` lazily calls `connect(...)` with that dict. `show_warehouses` (cursor execute `SHOW WAREHOUSES`, return dicts by lowercased column), `alter_auto_suspend` (quoted identifier), `close_hard` (best-effort `connection.close()` guarded by try/except; set `self._connection = None`), and `next_backoff`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/auto-savings pytest tests/test_snowflake_session.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/auto-savings/src/auto_savings/snowflake_session.py apps/auto-savings/tests/test_snowflake_session.py
git commit -m "feat: warm persistent tenant session + backoff + hard close"
```

---

### Task 12: Per-tenant async loop (lock + timeout + reconnect) and process bootstrap

**Files:**
- Create: `apps/auto-savings/src/auto_savings/tenant_loop.py`
- Create: `apps/auto-savings/src/auto_savings/main.py`
- Test: `apps/auto-savings/tests/test_tenant_loop.py`

**Interfaces:**
- Consumes: `run_cycle` (Task 9), `TenantSession` + `next_backoff` (Task 11), `owns_tenant` (Task 10), `SupabaseStore` + `resolve_snowflake_config` (Tasks 7, 1), `WorkerConfig`.
- Produces:
  - `async def run_tenant_once(org_id, *, session, store, config, executor, now_fn) -> None` — one guarded tick: acquire the tenant's `asyncio.Lock`; dispatch the blocking `session.show_warehouses()` to `executor` via `loop.run_in_executor` wrapped in `asyncio.wait_for(timeout=config.poll_timeout_seconds)`; on success call `run_cycle(org_id, rows=rows, store=store, config=config, now=now_fn(), apply_alter=session.alter_auto_suspend)`; on **any** exception (including `TimeoutError`) call `session.close_hard()` (cleanup) and **re-raise** so the caller backs off. The thread itself is freed by the connector socket timeout (Task 11), which is set `< poll_timeout_seconds`. A **per-tenant lock** ensures a slow tenant never overlaps its own polls.
  - `async def tenant_loop(org_id, *, session, store, config, executor, sleep=asyncio.sleep, stop=None)` — until `stop` is set: `run_tenant_once`; on success sleep `config.poll_interval_seconds` and reset the backoff attempt; on failure sleep `next_backoff(attempt, …)` and increment attempt.
  - `async def supervisor(*, store, config, executor, sleep=asyncio.sleep)` (finding #1/#3 — NOT startup-only) — every `config.tenant_refresh_seconds`, re-enumerate `store.worker_tenants()`, filter by `owns_tenant(...)`, **start** a `tenant_loop` task for any newly-appearing owned tenant, and **signal stop** to any running loop whose tenant disappeared from the set (its sentinels have drained, so it can exit cleanly). On stop, **`await` the loop task and call `session.close_hard()`** so the tenant's warm Snowflake session is released — otherwise removed tenants leak sessions (Codex R2.1 MED). Holds the `dict[str, (task, stop_event, session)]` of running loops.
  - `async def main()` (in `main.py`) — build `WorkerConfig.from_environment()`, `SupabaseStore(config)`, a bounded `ThreadPoolExecutor(max_workers=config.max_workers)`, construct a `TenantSession` per owned tenant on demand (resolving each tenant's `SnowflakeConnectionConfig` lazily), and run `supervisor(...)` forever.

- [ ] **Step 1: Write the failing tests** (drive the loop with a fake session/store; assert lock + reconnect-on-error)

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from auto_savings.config import WorkerConfig
from auto_savings.store import EnrollmentRow, InMemoryStore
from auto_savings.tenant_loop import run_tenant_once

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = WorkerConfig(supabase_url="u", supabase_service_role_key="k")


class FakeSession:
    def __init__(self, rows=None, raise_exc=None):
        self._rows = rows or []
        self._raise = raise_exc
        self.closed = False
        self.alters = []

    def show_warehouses(self):
        if self._raise:
            raise self._raise
        return self._rows

    def alter_auto_suspend(self, name, value):
        self.alters.append((name, value))

    def close_hard(self):
        self.closed = True


@pytest.mark.asyncio
async def test_successful_tick_runs_cycle():
    store = InMemoryStore()
    store.seed_enrollment(EnrollmentRow(
        organization_id="org-1", warehouse_name="WH1", enabled=True,
        managed_auto_suspend=300, stored_default_auto_suspend=300,
        warehouse_created_on=NOW, cooldown_ts=None, drift_state="ok", drifted_value=None))
    rows = [{"name": "WH1", "state": "STARTED", "type": "STANDARD",
             "started_clusters": 1, "min_cluster_count": 1, "max_cluster_count": 1,
             "running": 0, "queued": 0, "auto_suspend": 300, "auto_resume": "true",
             "resumed_on": NOW.replace(hour=11, minute=58)}]
    session = FakeSession(rows=rows)
    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_tenant_once("org-1", session=session, store=store, config=CONFIG,
                              executor=executor, now_fn=lambda: NOW)
    assert session.alters == [("WH1", 1)]


@pytest.mark.asyncio
async def test_wedged_session_is_force_closed():
    session = FakeSession(raise_exc=RuntimeError("boom"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(RuntimeError):
            await run_tenant_once("org-1", session=session, store=InMemoryStore(),
                                  config=CONFIG, executor=executor, now_fn=lambda: NOW)
    assert session.closed is True


@pytest.mark.asyncio
async def test_genuinely_blocking_call_times_out_and_frees_the_loop():
    # A show_warehouses that blocks past poll_timeout must not wedge the loop.
    # (The connector socket timeout frees the pool thread in prod; here we prove the
    #  wait_for path raises and close_hard() runs.)
    import threading

    class BlockingSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()

        def show_warehouses(self):
            self.started.set()
            threading.Event().wait(5)  # blocks well past the 0.2s timeout
            return []

    cfg = WorkerConfig(supabase_url="u", supabase_service_role_key="k", poll_timeout_seconds=0.2)
    session = BlockingSession()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await run_tenant_once("org-1", session=session, store=InMemoryStore(),
                                  config=cfg, executor=executor, now_fn=lambda: NOW)
    assert session.closed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/auto-savings pytest tests/test_tenant_loop.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `tenant_loop.py`, `supervisor`, and `main.py`**

Implement per the interface. `run_tenant_once` wraps the executor future in `asyncio.wait_for(..., config.poll_timeout_seconds)`, calls `close_hard()` in a `finally`/`except`, and re-raises. One `asyncio.Lock` per org. `supervisor` re-enumerates `worker_tenants()` every `tenant_refresh_seconds`, starting/stopping `tenant_loop` tasks so post-startup opt-ins are picked up and kill-switched/drained orgs exit (findings #1, #3). `main.py` wires the real `SupabaseStore`, resolves each tenant's `SnowflakeConnectionConfig` via `resolve_snowflake_config(org_id, config, fetch_connection=SupabaseConnectionFetcher(...))`, and builds a `TenantSession(config=snowflake_cfg, socket_timeout_seconds=config.socket_timeout_seconds)` per owned tenant.

> Note on the blocking test: with a single-thread executor, a truly blocked `show_warehouses` occupies the pool thread until it returns — the `wait_for` raising does NOT free the thread. That is exactly why Task 11 sets the connector socket timeout (< `poll_timeout_seconds`): in production the blocking `recv` raises on its own and the thread returns. The test proves the loop escapes; the socket timeout proves the thread escapes. Document both.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/auto-savings pytest tests/test_tenant_loop.py -v`
Expected: PASS.

- [ ] **Step 5: Full worker suite + commit**

Run: `uv run --directory apps/auto-savings pytest`
Expected: all worker tests PASS.

```bash
git add apps/auto-savings/src/auto_savings/tenant_loop.py apps/auto-savings/src/auto_savings/main.py apps/auto-savings/tests/test_tenant_loop.py
git commit -m "feat: per-tenant async loop with lock, timeout, reconnect + process bootstrap"
```

---

### Task 13: Worker Dockerfile + Railway service

**Files:**
- Create: `apps/auto-savings/Dockerfile`
- Create: `apps/auto-savings/railway.json`

**Interfaces:**
- Consumes: the built worker app + the `greysight-connect` path-dependency package.
- Produces: a deployable image whose `CMD` runs `python -m auto_savings.main` (i.e. `asyncio.run(main())`).

- [ ] **Step 1: Write the Dockerfile** (mirror `apps/api/Dockerfile`; build context is the repo root; no root `pyproject.toml` — path dep, not workspace)

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app/apps/auto-savings
COPY shared/connect/ /app/shared/connect/
COPY apps/auto-savings/pyproject.toml apps/auto-savings/uv.lock ./
RUN uv sync --frozen --no-install-project
COPY apps/auto-savings/ ./
ENV PATH="/app/apps/auto-savings/.venv/bin:$PATH"
CMD ["python", "-m", "auto_savings.main"]
```

- [ ] **Step 2: Write `apps/auto-savings/railway.json`**

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": { "builder": "DOCKERFILE", "dockerfilePath": "apps/auto-savings/Dockerfile" },
  "deploy": { "restartPolicyType": "ON_FAILURE", "restartPolicyMaxRetries": 3 }
}
```

- [ ] **Step 3: Verify the image builds**

Run: `docker build -f apps/auto-savings/Dockerfile -t auto-savings-test .` (from repo root)
Expected: build succeeds through `uv sync` and the final stage. (If Docker is unavailable in the environment, note this must be verified in CI/deploy.)

- [ ] **Step 4: Commit**

```bash
git add apps/auto-savings/Dockerfile apps/auto-savings/railway.json
git commit -m "feat: auto-savings worker Dockerfile + Railway service config"
```

---

## Phase 3 — API additions

### Task 14: API store + warehouse directory services

**Files:**
- Create: `apps/api/app/services/automated_savings_store.py`
- Create: `apps/api/app/services/warehouse_directory.py`
- Test: `apps/api/tests/test_automated_savings_store.py`, `apps/api/tests/test_warehouse_directory.py`

**Interfaces:**
- Consumes: `Settings`, `resolve_snowflake_config` + `get_connection_fetcher` (existing), `execute_metadata_query` (Task 2).
- Produces:
  - In `automated_savings_store.py`: `SupabaseAutomatedSavingsStore(supabase_url, service_role_key, *, transport=None)` with `get_settings(org_id) -> SettingsRow`, `upsert_agreement(org_id, agreed_at)`, `set_global_enabled(org_id, enabled)`, `set_grant_status(org_id, present, checked_at)`, `list_warehouses(org_id) -> list[EnrollmentRow]`, `upsert_enrollment(org_id, warehouse_name, *, enabled, stored_default, managed_default, warehouse_created_on)`, `set_managed_default(org_id, warehouse_name, value)` (**writes `managed_auto_suspend` — the live restore target the worker reads, finding #2**), `unenroll(org_id, warehouse_name)`, `reconcile(org_id, warehouse_name, *, accept: bool)`. All raw-httpx service-role, gated on URL + key (same pattern as `SupabaseRunCacheStore`). Notes:
    - **`unenroll` does not itself `ALTER` Snowflake** — it clears `enabled`; the worker drains any outstanding intent (`stored_default`/`managed` kept until drained). The `worker_tenants()` union-on-intents (Task 3) guarantees the org is still polled to completion.
    - **`reconcile(accept=True)`** adopts the drifted value → `managed_auto_suspend = drifted_value` (if `>= 60`), clears drift. **`reconcile(accept=False)`** ("re-apply old default") must actually re-apply it on Snowflake — the API can't `ALTER`, so it **enqueues a restore-intent** (`restore_to = managed_auto_suspend`) and clears drift; the worker applies the `ALTER` on its next tick (finding #7). Neither path leaves the warehouse at the drifted value with drift silently cleared.
  - In `warehouse_directory.py`:
    - `list_live_warehouses(config) -> list[dict]` — `execute_metadata_query("SHOW WAREHOUSES", config=config)`.
    - `check_manage_warehouses_grant(config, role_name) -> bool` — validate/escape `role_name` (quote-double any `"`, reject if it isn't a valid Snowflake identifier) before interpolating, then `execute_metadata_query(f'SHOW GRANTS TO ROLE "{escaped}"', config=config)`; returns True if any row has `privilege == "MANAGE WAREHOUSES"` (case-insensitive), parsed by column name (finding: identifier escaping, consistent with the warehouse quoting in Task 11).
    - `join_warehouse_view(live, enrollments) -> list[WarehouseView]` — merge live `SHOW WAREHOUSES` rows with enrollment rows into the UI contract (see Task 15), computing `supported = (type == "STANDARD")` and `auto_resume_ok`.

- [ ] **Step 1: Write failing tests**

`test_warehouse_directory.py` — inject the metadata executor so no Snowflake is touched:

```python
from app.services import warehouse_directory


def test_grant_present_detected(monkeypatch):
    monkeypatch.setattr(warehouse_directory, "execute_metadata_query",
                        lambda sql, config=None: [{"privilege": "manage warehouses"}])  # case-insensitive
    assert warehouse_directory.check_manage_warehouses_grant(config=object(), role_name="RL") is True


def test_grant_absent(monkeypatch):
    monkeypatch.setattr(warehouse_directory, "execute_metadata_query",
                        lambda sql, config=None: [{"privilege": "USAGE"}])
    assert warehouse_directory.check_manage_warehouses_grant(config=object(), role_name="RL") is False


def test_grant_role_identifier_is_escaped(monkeypatch):
    seen = {}
    monkeypatch.setattr(warehouse_directory, "execute_metadata_query",
                        lambda sql, config=None: seen.setdefault("sql", sql) or [])
    warehouse_directory.check_manage_warehouses_grant(config=object(), role_name='weird"role')
    assert '"weird""role"' in seen["sql"]  # embedded quote doubled, not injected


def test_join_marks_non_standard_unsupported():
    live = [{"name": "SP1", "type": "SNOWPARK-OPTIMIZED", "state": "STARTED",
             "auto_resume": "true", "auto_suspend": 300, "min_cluster_count": 1,
             "max_cluster_count": 1, "started_clusters": 1, "size": "MEDIUM"}]
    [view] = warehouse_directory.join_warehouse_view(live, [])
    assert view.supported is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/api pytest tests/test_warehouse_directory.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement both service modules** per the interface (store follows `SupabaseRunCacheStore`; directory imports `execute_metadata_query` at module scope so it is monkeypatchable).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --directory apps/api pytest tests/test_warehouse_directory.py tests/test_automated_savings_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/automated_savings_store.py apps/api/app/services/warehouse_directory.py apps/api/tests/test_automated_savings_store.py apps/api/tests/test_warehouse_directory.py
git commit -m "feat: API automated-savings store + warehouse directory services"
```

---

### Task 15: API route module + mounting (opt-in, warehouse list, enroll/config/reconcile)

**Files:**
- Create: `apps/api/app/routes/automated_savings.py`
- Modify: `apps/api/app/main.py` (import + `include_router` + configure store singleton)
- Test: `apps/api/tests/test_automated_savings_route.py`

**Interfaces:**
- Consumes: Task 14 services, `require_auth_context` / `require_org_membership` / `require_org_admin` (existing `app.auth`), `resolve_snowflake_config` + `get_connection_fetcher`.
- Produces `router = APIRouter(prefix="/api/automated-savings", tags=["automated-savings"])` with:
  - `GET /{organization_id}/status` (member) → `{agreed: bool, global_enabled: bool, grant_present: bool, grant_checked_at: str | null}`.
  - `POST /{organization_id}/agree` (admin) → records `agreed_at`.
  - `POST /{organization_id}/global-switch` (admin) body `{enabled: bool}` → sets `global_enabled` (master gate + kill switch).
  - `GET /{organization_id}/warehouses` (member) → live `SHOW WAREHOUSES` joined with enrollment: `[{name, size, state, type, supported, min_cluster_count, max_cluster_count, started_clusters, auto_resume_ok, managed_default, stored_default, enabled, drift_state, drifted_value, cooldown_ts, status}]`. `status ∈ {idle, mid_suspend, in_cooldown, drifted, unsupported}`.
  - `POST /{organization_id}/warehouses/{warehouse_name}/toggle` (admin) body `{enabled: bool}` → enroll (captures current `AUTO_SUSPEND` as `stored_default` **and** seeds `managed_default = stored_default`; **reject if captured default ∈ {0, 1, NULL}** with 422) / unenroll (**only clears `enabled` — NEVER writes an intent**; the worker's reconcile drains any *already-outstanding* intent on its next tick, regardless of `enabled`. Writing an intent here would wrongly claim ownership of a customer-set `AUTO_SUSPEND=1` that has no intent — finding, Codex R2.1 HIGH).
  - `POST /{organization_id}/warehouses/{warehouse_name}/managed-default` (admin) body `{value: int}` → **server-side floor 60**, 422 if `< 60`.
  - `POST /{organization_id}/warehouses/{warehouse_name}/reconcile` (admin) body `{accept: bool}`.
  - `POST /{organization_id}/check-access` (member) → runs `check_manage_warehouses_grant`, persists via `set_grant_status`, returns `{grant_present, grant_checked_at}`.
- Follows the `organizations.py` convention: inject `AuthContext`, call `require_org_membership` / `require_org_admin` imperatively; service calls via module-level seam functions for monkeypatching.

- [ ] **Step 1: Write failing route tests** (auth/RLS guards + the floor/reject guards — the behavior, not the wiring)

`apps/api/tests/test_automated_savings_route.py`:

```python
from fastapi.testclient import TestClient

from app.auth import AuthContext, require_auth_context
from app.main import app
from app.services.membership_directory import Organization
from app.routes import automated_savings


def _admin_ctx():
    return AuthContext(user_id="u", auth_required=True, memberships=frozenset({"org-1"}),
                       organizations=(Organization(id="org-1", name="Acme", role="owner"),))


def _member_ctx():
    return AuthContext(user_id="u", auth_required=True, memberships=frozenset({"org-1"}),
                       organizations=(Organization(id="org-1", name="Acme", role="member"),))


def test_member_cannot_flip_global_switch():
    app.dependency_overrides[require_auth_context] = _member_ctx
    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/global-switch", json={"enabled": True})
    app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_managed_default_below_floor_rejected(monkeypatch):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/warehouses/WH1/managed-default",
                       json={"value": 45})
    app.dependency_overrides.clear()
    assert resp.status_code == 422


def test_enroll_rejects_sentinel_default(monkeypatch):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    # Live warehouse captured at AUTO_SUSPEND=1 → cannot enroll.
    monkeypatch.setattr(automated_savings, "capture_stored_default", lambda **kw: 1)
    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/warehouses/WH1/toggle", json={"enabled": True})
    app.dependency_overrides.clear()
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory apps/api pytest tests/test_automated_savings_route.py -v`
Expected: FAIL — route not mounted / module missing.

- [ ] **Step 3: Implement the route module + mount + wire the store**

Create `automated_savings.py` per the interface. In `main.py`: add `from app.routes.automated_savings import router as automated_savings_router` (near lines 14–19), `app.include_router(automated_savings_router)` (near lines 191–196), and a `_configure_automated_savings_store(settings)` helper that constructs `SupabaseAutomatedSavingsStore` and assigns the module-level singleton — mirroring `_configure_*` for the cache stores. **Gate exactly like `main.py:84-111`: the service-role store bypasses RLS, so only wire it when `settings.auth_required` is true (plus `supabase_url` + `supabase_service_role_key` present); when `auth_required` is false, leave the singleton unset so the route-layer membership check remains the tenant boundary** (finding: service-role auth-off guard). The route's `reconcile` handler with `accept=false` must call `store.reconcile(..., accept=False)` which enqueues a restore-intent (Task 14) — verify the intent is written, not just drift cleared.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory apps/api pytest tests/test_automated_savings_route.py -v`
Expected: PASS.

- [ ] **Step 5: Full API suite + commit**

Run: `uv run --directory apps/api pytest`
Expected: PASS (no regression from the Task 1 extraction).

```bash
git add apps/api/app/routes/automated_savings.py apps/api/app/main.py apps/api/tests/test_automated_savings_route.py
git commit -m "feat: automated-savings API routes (opt-in, warehouses, enroll/config/reconcile)"
```

---

## Phase 4 — Web page

### Task 16: API client wrapper + parsers

**Files:**
- Create: `apps/web/src/lib/automated-savings-api.ts`
- Test: `apps/web/src/lib/automated-savings-api.test.ts`

**Interfaces:**
- Consumes: `resolveApiUrl`, `authHeaders` (existing `src/lib/api-client.ts`).
- Produces (hand-written parsers, no zod):
  - Types `AutomatedSavingsStatus`, `WarehouseRow` (**camelCase** UI types, matching the rest of the web codebase), `SavingsStatus = "idle" | "mid_suspend" | "in_cooldown" | "drifted" | "unsupported"`.
  - **Explicit contract transform (Codex finding #5):** the API returns **snake_case** JSON (`min_cluster_count`, `auto_resume_ok`, `managed_default`, `stored_default`, `drift_state`, `cooldown_ts`); `fetchJson` returns it raw. A hand-written `parseWarehouseRow(raw: unknown): WarehouseRow` maps each snake_case field to its camelCase type field (and coerces types) — the UI never touches raw JSON keys. `parseStatus` does the same for the status contract. This transform is the single boundary; Tasks 19/20 consume only the camelCase `WarehouseRow`.
  - `fetchStatus(orgId, { accessToken }): Promise<AutomatedSavingsStatus>` (via `parseStatus`)
  - `fetchWarehouses(orgId, { accessToken }): Promise<WarehouseRow[]>` (maps `parseWarehouseRow` over the array)
  - `agree(orgId, { accessToken })`, `setGlobalSwitch(orgId, enabled, { accessToken })`, `toggleWarehouse(orgId, name, enabled, { accessToken })`, `setManagedDefault(orgId, name, value, { accessToken })`, `reconcileWarehouse(orgId, name, accept, { accessToken })`, `checkAccess(orgId, { accessToken })`.
  - Each throws on `!response.ok` (reuse the `fetchJson` shape from `dashboard-api.ts:288-309`); `setManagedDefault` surfaces a typed `ManagedDefaultFloorError` on 422 so the UI can show the floor message.

- [ ] **Step 1: Write the failing test** (assert bearer header + floor error mapping)

```ts
import { describe, expect, it, vi } from "vitest";
import { fetchWarehouses, setManagedDefault, ManagedDefaultFloorError } from "./automated-savings-api";

describe("automated-savings-api", () => {
  it("sends the bearer token", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }));
    await fetchWarehouses("org-1", { accessToken: "tok" });
    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer tok");
  });

  it("maps 422 on managed-default to a floor error", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ detail: "floor" }), { status: 422 }));
    await expect(setManagedDefault("org-1", "WH1", 45, { accessToken: "t" }))
      .rejects.toBeInstanceOf(ManagedDefaultFloorError);
  });

  it("maps snake_case API JSON to camelCase WarehouseRow", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([{
      name: "WH1", size: "X-Small", state: "STARTED", type: "STANDARD", supported: true,
      min_cluster_count: 1, max_cluster_count: 2, started_clusters: 1, auto_resume_ok: true,
      managed_default: 300, stored_default: 300, enabled: true, drift_state: "ok",
      drifted_value: null, cooldown_ts: null, status: "idle",
    }]), { status: 200 }));
    const [row] = await fetchWarehouses("org-1", { accessToken: "t" });
    expect(row.minClusterCount).toBe(1);
    expect(row.autoResumeOk).toBe(true);
    expect(row.managedDefault).toBe(300);
    expect(row.driftState).toBe("ok");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --workspace apps/web run test -- automated-savings-api`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the client** per the interface (copy the `fetchJson` wrapper pattern; add the `ManagedDefaultFloorError` class and 422 mapping in `setManagedDefault`).

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --workspace apps/web run test -- automated-savings-api`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/automated-savings-api.ts apps/web/src/lib/automated-savings-api.test.ts
git commit -m "feat: web automated-savings API client + parsers"
```

---

### Task 17: Top nav (Home / Automated Savings) in the shared header

**Files:**
- Create: `apps/web/src/components/dashboard/app-nav.tsx`
- Modify: `apps/web/src/components/dashboard/dashboard-header.tsx` (render `<AppNav />` next to the wordmark)
- Test: `apps/web/src/components/dashboard/app-nav.test.tsx`

**Interfaces:**
- Produces: `AppNav` — a client component (`"use client"`) rendering `next/link` tabs **Home** (`/dashboard`) and **Automated Savings** (`/automated-savings`), using `usePathname()` for active state. Active tab: `bg-chart-purple text-white`; inactive: `text-slate-400 hover:bg-white/5` (the `FilterBar` toggle convention). `aria-current="page"` on the active link.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/automated-savings" }));

import { AppNav } from "./app-nav";

describe("AppNav", () => {
  it("marks the active route", () => {
    render(<AppNav />);
    const active = screen.getByRole("link", { name: /automated savings/i });
    expect(active).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /home/i })).not.toHaveAttribute("aria-current");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --workspace apps/web run test -- app-nav`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `AppNav`** and render it in `dashboard-header.tsx` inside the left cluster (`dashboard-header.tsx:46-52`, after `<AccountSwitcher />`).

```tsx
"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/dashboard", label: "Home" },
  { href: "/automated-savings", label: "Automated Savings" },
] as const;

export function AppNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Primary" className="inline-flex h-8 rounded-md border border-hairline bg-surface p-0.5">
      {TABS.map((tab) => {
        const active = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={active
              ? "h-full rounded bg-chart-purple px-4 text-xs font-semibold leading-7 text-white"
              : "h-full rounded px-4 text-xs font-medium leading-7 text-slate-400 hover:bg-white/5"}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --workspace apps/web run test -- app-nav`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/dashboard/app-nav.tsx apps/web/src/components/dashboard/dashboard-header.tsx apps/web/src/components/dashboard/app-nav.test.tsx
git commit -m "feat: top nav (Home / Automated Savings) in dashboard header"
```

---

### Task 18: Opt-in gate (explainer + agree + GRANT SQL)

**Files:**
- Create: `apps/web/src/components/automated-savings/opt-in-gate.tsx`
- Test: `apps/web/src/components/automated-savings/opt-in-gate.test.tsx`

**Interfaces:**
- Consumes: `useAccountChrome()` (existing), `agree` + `checkAccess` (Task 16).
- Produces: `OptInGate({ orgId, roleName, onAgreed })` — renders the explainer (what it does, **Experimental feature** notice + link to this repo), the copyable `GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE "<escapedRoleName>";` SQL (escape via a local `quoteIdent(role) = '"' + role.replace(/"/g, '""') + '"'` so a role containing a quote can't break/inject the rendered SQL — Codex R2.1 MED), and an **Agree** button. The Agree button is **owner/admin only** — read the active org role from `useAccountChrome()` and render the button disabled (with an explanatory note) for `member`. On agree, calls `agree(orgId, …)` then `onAgreed()`.

- [ ] **Step 1: Write the failing test** (GRANT SQL contains the role; Agree disabled for members)

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AccountChromeProvider } from "../../lib/account-context";
import { OptInGate } from "./opt-in-gate";

function withRole(role: "owner" | "member") {
  return {
    email: "u@acme.com", onSignOut: () => {}, signOutError: null,
    organizations: [{ id: "org-1", name: "Acme", role, accountLocator: null, connectionStatus: null }],
    activeOrganizationId: "org-1", setActiveOrganization: () => {}, openAddAccount: () => {},
    accessToken: "tok",
  };
}

describe("OptInGate", () => {
  it("shows GRANT SQL with the role name", () => {
    render(<AccountChromeProvider value={withRole("owner")}>
      <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
    </AccountChromeProvider>);
    expect(screen.getByText(/GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE "GREYSIGHT_RL"/)).toBeInTheDocument();
  });

  it("disables Agree for members", () => {
    render(<AccountChromeProvider value={withRole("member")}>
      <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
    </AccountChromeProvider>);
    expect(screen.getByRole("button", { name: /agree/i })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --workspace apps/web run test -- opt-in-gate`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `OptInGate`** per the interface (dark theme tokens; copyable SQL block reusing the pattern in `org/snowflake-setup-sql.tsx`).

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --workspace apps/web run test -- opt-in-gate`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/automated-savings/opt-in-gate.tsx apps/web/src/components/automated-savings/opt-in-gate.test.tsx
git commit -m "feat: automated-savings opt-in gate (explainer + agree + GRANT SQL)"
```

---

### Task 19: Warehouse enrollment table + shell

**Files:**
- Create: `apps/web/src/components/automated-savings/warehouse-table.tsx`
- Create: `apps/web/src/components/automated-savings/automated-savings-shell.tsx`
- Test: `apps/web/src/components/automated-savings/warehouse-table.test.tsx`, `automated-savings-shell.test.tsx`

**Interfaces:**
- Consumes: Task 16 client, `useAccountChrome()`, design-system primitives (`DetailTable` or a hand-rolled `<table>` matching `detail-tables.tsx`), Tremor `Badge`.
- Produces:
  - `WarehouseTable({ orgId, warehouses, isAdmin, onChange })` — columns **name · size · # clusters · state · AUTO_SUSPEND (managed default) · AUTO_RESUME health · status · toggle**. Rules:
    - Managed-default cell: editable number, **floor 60** enforced in the input (`min={60}`, reject `< 60` before calling `setManagedDefault`), warning tooltip at/near 60; tooltip copy: *"the AUTO_SUSPEND we restore this warehouse to — defaults to the value captured at opt-in; edit to change it."* (It is the live `managedDefault`, editable; the immutable opt-in capture is `storedDefault`, not shown here.)
    - AUTO_RESUME health: `Badge color="emerald"` when ok, `color="rose"` + "AUTO_RESUME off — can't automate safely" when false; toggle **disabled** in that row.
    - `type != STANDARD`: toggle **disabled**, tooltip "Snowpark-optimized warehouses aren't supported yet"; status badge `unsupported`.
    - status badge: `idle | mid_suspend | in_cooldown | drifted (with Reconcile action) | unsupported`.
    - Toggles/edits are admin-only (`isAdmin` gates interactivity; non-admins see read-only).
  - `AutomatedSavingsShell({ authRequired })` — the client entry: **wraps its content in `OrgShell` (like `DashboardRuntimeShell`)** and renders an inner content component that gets `orgId`/`accessToken`/role via `useAccountChrome()`, fetches status; **not agreed → `OptInGate`**; agreed → fetch warehouses and render the **global switch** (master gate + bulk enable/disable) + `WarehouseTable` + "Check access / Refresh" button (calls `checkAccess`; if the parsed **`grantPresent === false`** — camelCase from the Task 16 parser, never the raw `grant_present`, Codex R2.1 MED — show a **"grant missing"** banner re-displaying the GRANT SQL). Global switch reflects all-on / all-off / mixed and, when flipped, forces all toggles.

- [ ] **Step 1: Write the failing tests** (behavioral: floor guard, disabled-when-auto-resume-off, gate branching)

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WarehouseTable } from "./warehouse-table";

const base = {
  name: "WH1", size: "X-Small", state: "STARTED", type: "STANDARD", supported: true,
  minClusterCount: 1, maxClusterCount: 1, startedClusters: 1, autoResumeOk: true,
  managedDefault: 300, storedDefault: 300, enabled: true, driftState: "ok",
  driftedValue: null, cooldownTs: null, status: "idle" as const,
};

describe("WarehouseTable", () => {
  it("disables the toggle when AUTO_RESUME is off", () => {
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[{ ...base, autoResumeOk: false }]} onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: /WH1/i })).toBeDisabled();
  });

  it("enforces the 60 floor on the managed-default input", () => {
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[base]} onChange={() => {}} />);
    const input = screen.getByLabelText(/WH1 auto_suspend/i) as HTMLInputElement;
    expect(input.min).toBe("60");
  });

  it("surfaces Reconcile when drifted", () => {
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[{ ...base, driftState: "drifted", status: "drifted" }]} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /reconcile/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --workspace apps/web run test -- warehouse-table`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `WarehouseTable` and `AutomatedSavingsShell`** per the interface. Use the `<input type="checkbox" role="switch">` hand-rolled toggle (accent `chart-purple`) with `disabled` when `!isAdmin || !autoResumeOk || !supported`. Managed-default is a controlled `<input type="number" min={60}>`; on blur/enter, reject `< 60` locally (show the floor warning) else call `setManagedDefault`. Shell branches on `status.agreed`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --workspace apps/web run test -- warehouse-table automated-savings-shell`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/automated-savings/warehouse-table.tsx apps/web/src/components/automated-savings/automated-savings-shell.tsx apps/web/src/components/automated-savings/warehouse-table.test.tsx apps/web/src/components/automated-savings/automated-savings-shell.test.tsx
git commit -m "feat: automated-savings warehouse table + shell (global switch, grant banner)"
```

---

### Task 20: The `/automated-savings` page route

**Files:**
- Create: `apps/web/src/app/automated-savings/page.tsx`
- Test: `apps/web/src/app/automated-savings/page.test.tsx`

**Interfaces:**
- Consumes: `AutomatedSavingsShell` (Task 19), `getAuthMode` (existing), `OrgShell` (existing auth gate).
- Produces: a server component mirroring `dashboard/page.tsx` — reads `AUTH_REQUIRED`/`NEXT_PUBLIC_AUTH_REQUIRED` via `getAuthMode` and renders `AutomatedSavingsShell`. **Match the dashboard boundary exactly (Codex R2.1 LOW):** `dashboard/page.tsx` delegates to a runtime shell that itself owns `OrgShell` — so `AutomatedSavingsShell` should wrap its content in `OrgShell` internally (like `DashboardRuntimeShell`), and the page just renders the shell. Do not wrap `OrgShell` at the page level while the shell also consumes `useAccountChrome()`.

- [ ] **Step 1: Write the failing test** (mirror `dashboard/page.auth-mode.test.tsx`: mock the shell, assert props)

```tsx
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../components/automated-savings/automated-savings-shell", () => ({
  AutomatedSavingsShell: ({ authRequired }: { authRequired: boolean }) =>
    <div data-testid="shell" data-auth={String(authRequired)} />,
}));

import AutomatedSavingsPage from "./page";

describe("AutomatedSavingsPage", () => {
  const original = process.env.AUTH_REQUIRED;
  afterEach(() => { process.env.AUTH_REQUIRED = original; });

  it("passes authRequired from env", () => {
    process.env.AUTH_REQUIRED = "true";
    render(AutomatedSavingsPage());
    expect(screen.getByTestId("shell")).toHaveAttribute("data-auth", "true");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --workspace apps/web run test -- automated-savings/page`
Expected: FAIL — page missing.

- [ ] **Step 3: Implement `page.tsx`** (copy the shape of `dashboard/page.tsx`; wrap the shell in `OrgShell` when `authRequired`).

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --workspace apps/web run test -- automated-savings/page`
Expected: PASS.

- [ ] **Step 5: Full web suite + commit**

Run: `npm --workspace apps/web run test`
Expected: PASS (coverage thresholds 80% hold).

```bash
git add apps/web/src/app/automated-savings/page.tsx apps/web/src/app/automated-savings/page.test.tsx
git commit -m "feat: /automated-savings page route"
```

---

## Phase 5 — Docs

### Task 21: Operational docs (cloud-services cost note, cadence env, runbook)

**Files:**
- Create: `docs/automated-savings.md`

**Interfaces:**
- Consumes: nothing (documentation).
- Produces: the `docs/` note the spec requires — the cloud-services cost note (`SHOW WAREHOUSES` at 3–5s accrues against the tenant's cloud-services daily allowance, billed only above 10% of daily compute; cadence is env-configurable via `AUTO_SAVINGS_POLL_INTERVAL_SECONDS`), the opt-in GRANT (`MANAGE WAREHOUSES`), the worker env vars (cadence, cooldown, sharding: `AUTO_SAVINGS_NUM_REPLICAS`/`AUTO_SAVINGS_REPLICA_INDEX`), and a short runbook (grant-missing → paused; drift → Reconcile; crash recovery via restore-intent rows; single-worker v1 with dormant sharding).

- [ ] **Step 1: Write `docs/automated-savings.md`** covering all of the above (prose; no code steps).

- [ ] **Step 2: Commit**

```bash
git add docs/automated-savings.md
git commit -m "docs: automated-savings operational + cost notes"
```

---

## Self-Review

**Spec coverage:**
- Worker standalone app + own Railway service → Tasks 4, 13. ✓
- Opt-in gated on customer GRANT → Tasks 3 (settings), 15 (agree/check-access), 18 (GRANT SQL). ✓
- Dashboard page: per-warehouse toggle + managed `AUTO_SUSPEND` → Tasks 19, 20. ✓
- Shared package extraction → Tasks 1–2. ✓
- Idle signal `SHOW WAREHOUSES` (metadata, no resume) → Task 2 (`execute_metadata_query`), 5 (parse), 11 (session). ✓
- Suspend decision truth table (all gates) → Task 6. ✓
- Uptime in Python from tz-aware `resumed_on`, NULL ineligible, no SELECT → Tasks 5, 6. ✓
- Force-suspend lifecycle: intent-before-ALTER, next-tick **conditional** restore (SUSPENDED/busy/held), cooldown → Tasks 8, 9. ✓
- Sentinel ownership by intent row; reject enroll default ∈ {0,1,NULL} (route + DB constraint) → Tasks 3, 8, 15. ✓
- Single writer (worker owns all ALTERs; API writes intent; reconcile accept=false enqueues intent) → Tasks 12, 14, Global Constraints. ✓
- Crash recovery / reconciliation off one snapshot, reconcile-then-decide, drain-regardless-of-enabled → Tasks 8, 9. ✓
- Multi-cluster: `started_clusters == min_cluster_count` gate, no MIN/MAX mutation → Task 6 (tests cover single/auto-scale/maximized). ✓
- Connection mgmt: warm session, per-tenant lock, **connector socket timeout** (real wedge escape), backoff, sharding, supervisor re-enumeration → Tasks 10, 11, 12. ✓
- Kill switch actually stops automation + drains → Tasks 3 (worker_tenants union), 9 (decide gated on global_enabled), 12 (supervisor). ✓
- Managed default = live restore target + drift baseline; stored default = immutable capture → Tasks 3, 8, 9, 14. ✓
- Drift: not state-gated, pause + flag, Reconcile (accept re-applies via intent) → Tasks 8, 14, 15, 19. ✓
- Type filter re-checked; non-STANDARD auto-paused → Tasks 6, 8, 14, 19. ✓
- Drop+recreate (`created_on` mismatch) invalidation → Task 8 (explicit branch + test). ✓
- Durable state tables + RLS + service role → Task 3. ✓
- API endpoints (status, agree, kill switch, warehouse list, toggle, managed-default, reconcile, check-access) → Task 15. ✓
- Web: top nav, explainer gate, dashboard table, global switch, check-access banner, snake→camel transform, out-of-scope firing chart → Tasks 16–20. ✓
- Cloud-services cost note in docs → Task 21. ✓

**Adversarial-review findings mapped (Codex + review agents, R2):**
- Kill switch non-functional/freezes (CRIT) → Tasks 3 (worker_tenants union), 9 (decide gated + reconcile always drains), 12 (supervisor). 
- Managed default wired to nothing + false drift (HIGH) → Tasks 3/8/9/14 (single live `managed_auto_suspend` restore target + baseline).
- `resumed_on` coercion / H1 resurface (HIGH) → Task 0 spike + Task 5 `_coerce_ts` + string/naive tests.
- Unconditional restore guillotines slow suspend (HIGH, spec deviation) → Task 8 refined tick-2 (SUSPENDED restore / busy restore-no-cooldown / idle-still-1 hold w/ intent-age backstop).
- Drain-on-unenroll unproven (HIGH) → Task 8 enabled-independence branch + test; Task 3 worker_tenants union.
- Watchdog + `poll_timeout_seconds` missing (MED-HIGH) → Task 4 fields, Task 11 connector socket timeout (the real unblock), Task 12 blocking test.
- reconcile accept=false no-ops (should-fix) → Task 14 enqueues restore-intent.
- created_on invalidation as prose only (should-fix) → Task 8 explicit branch + test.
- Restore-ALTER-before-delete-intent untested (should-fix) → Task 8 failed-ALTER test.
- Orphaned intent when a warehouse is fully dropped, loop never exits (LOW #10) → Task 8 branch 0 (orphan cleanup past `orphan_grace_seconds`) + 2 tests; Task 4 `orphan_grace_seconds` field.
- uv workspace/Docker ordering (Codex HIGH) → Task 1 path-dep (no workspace), Tasks 7/13 Dockerfiles.
- snake/camel contract drift (Codex HIGH) → Task 16 explicit transform + test.
- service-role auth-off gating (Codex MED) → Task 15 gate mirrors `main.py:84-111`.
- SHOW GRANTS role escaping (Codex MED) → Task 14 identifier escape + test.
- shim `snowflake` attr for patching (Codex MED) → Task 1 shim re-exports `snowflake` submodule.

**Open items resolved in-plan:** shared-package mechanics = `greysight-connect` **path dependency** + API re-export shims (Task 1, NOT a uv workspace); opt-in lives in a new `automated_savings_settings` table (Task 3); grant detection = escaped `SHOW GRANTS TO ROLE` probe (Task 14); cooldown 60s, cadence 3s, `poll_timeout` 20s, `socket_timeout` 15s, `max_intent_hold_ticks` 5 (placeholder — set from the Task 0 spike), `orphan_grace_seconds` 120s (Task 4); global switch = master gate + bulk (Task 19).

**Spec deviation — ACCEPTED (Kyle, 2026-07-12):** the spec's "next-tick **unconditional** restore" is implemented as **conditional** (finding #4) to avoid guillotining Snowflake's in-flight suspend. Still C1-safe (anti-stranding preserved via the intent-age backstop). The only remaining gate is that `max_intent_hold_ticks` MUST come from Task 0's real suspend-latency measurement (≥ 2–3× observed), not the `15s` placeholder default — see Task 0 Step 3.

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" left; the summarizing impl steps (Task 7 Step 3, Task 14 Step 3) name every method and the pattern file to copy — the failing tests pin the contract; show real code when executing.

**Type consistency:** `should_force_suspend` params match Task 6 def ↔ Task 9 call; `EnrollmentRow`/`SettingsRow`/`RestoreIntent` fields consistent across Tasks 7–9; `reconcile(..., intent_hold_seconds=...)` signature consistent Task 8 def ↔ Task 9 call; `execute_metadata_query` consistent Task 2 → 11/14; `WarehouseRow` camelCase UI shape consistent Task 16 (transform) ↔ 19 (table), snake_case only at the API/transform boundary. ✓
