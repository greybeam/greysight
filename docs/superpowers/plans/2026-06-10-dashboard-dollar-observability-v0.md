# Dashboard Dollar Observability V0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task (chosen workflow for this plan; superpowers:executing-plans is the inline fallback only if subagents are unavailable). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Greysight dashboard from a credit-denominated demo into a dollar-denominated Snowflake cost observability console with billed (Organization Usage), estimated (rate-sheet / configured-price), and demo modes sharing one transform path.

**Architecture:** The API gains three Organization Usage / metadata source datasets (`org_spend_daily`, `rate_sheet_daily`, `current_account`), a versioned dataset response (`schema_version: 1`) with a metadata block (data mode, currency, freshness, source availability), tolerant Organization Usage probing, and payload bounding. All windowing, summary-building, ranking, dollar-mode selection, and view-model construction move into one pure TypeScript transform module (`dashboard-transforms.ts`); React components only render view models. Demo, billed, and estimated modes share that single transform path.

**Tech Stack:** FastAPI + pydantic + snowflake-connector (apps/api, pytest), Next.js 16 + React 18 + @tremor/react + Tailwind (apps/web, Vitest + Testing Library), registry-approved SQL in `sql/`.

**Spec:** `docs/specs/dashboard-dollar-observability-v0.md`

---

## Live Verification Findings (run 2026-06-10, account GOPGUKF-JO19546, locator TU24199, role DEV_ROLE)

These satisfy the spec's "Plan Prerequisites" section. Inspection was read-only (`describe view` + bounded `select`), script at `/tmp/greysight-plan-inspection.py`.

1. **`SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY` is accessible and populated.** Verified columns: `DATE, ORGANIZATION_NAME, CONTRACT_NUMBER, ACCOUNT_NAME, ACCOUNT_LOCATOR, REGION, SERVICE_LEVEL, USAGE_TYPE, CURRENCY, EFFECTIVE_RATE (NUMBER(38,2)), SERVICE_TYPE, RATING_TYPE, BILLING_TYPE, IS_ADJUSTMENT`.
2. **Pinned rate-sheet join mapping** (all columns verified live):
   - Filter: `account_locator = :account_locator AND billing_type = 'CONSUMPTION' AND is_adjustment = false`
   - Join keys to credit datasets: `date` ↔ `usage_date`, `service_type`, `rating_type`, `currency`.
   - Multiple `usage_type` rows can map to one `(date, service_type)` (e.g. `compute` and `overage-compute` both → `WAREHOUSE_METERING`); the base dataset dedupes with `max(effective_rate)` per `(date, service_type, rating_type, currency)`.
   - Warehouse and user credit conversion uses the `service_type = 'WAREHOUSE_METERING'`, `rating_type = 'COMPUTE'` rate for that usage date.
3. **`USAGE_IN_CURRENCY_DAILY` verified columns:** `ORGANIZATION_NAME, CONTRACT_NUMBER, ACCOUNT_NAME, ACCOUNT_LOCATOR, REGION, SERVICE_LEVEL, USAGE_DATE, USAGE_TYPE, USAGE, CURRENCY, USAGE_IN_CURRENCY (NUMBER(38,2)), BALANCE_SOURCE, BILLING_TYPE, RATING_TYPE, SERVICE_TYPE, IS_ADJUSTMENT`.
4. **SPEC CORRECTION — included-cloud-services adjustment flags.** The spec assumed the negative included-cloud-services row carries `is_adjustment = false`. **Live data disproves this**: rows like `usage_type='overage-adj for incl cloud services'` carry `billing_type='CONSUMPTION'` and **`IS_ADJUSTMENT=TRUE`** with negative `usage_in_currency`. Per the spec's own fallback instruction, the V0 billed filter is therefore **`billing_type = 'CONSUMPTION'` only** (both `is_adjustment` values kept), so invoice-matching negative rows stay in billed totals. Dimension values are UPPERCASE in live data (`'CONSUMPTION'`, `'COMPUTE'`, `'STORAGE'`).
5. **Observed `rating_type` values (last 60 days):** `COMPUTE`, `STORAGE`, `AI_COMPUTE`, `AI_INFERENCE`, `DATA_TRANSFER` — all `billing_type='CONSUMPTION'`. Storage predicate `rating_type = 'STORAGE'` is valid and sufficient.
6. **`WAREHOUSE_METERING_HISTORY.CREDITS_USED_COMPUTE` exists** (NUMBER(38,9)), alongside `CREDITS_USED` and `CREDITS_USED_CLOUD_SERVICES`. **`QUERY_ATTRIBUTION_HISTORY.CREDITS_ATTRIBUTED_COMPUTE` exists** (FLOAT).
7. **Account locator must be derived, not configured:** `current_account()` returns `TU24199`, which differs from the `SNOWFLAKE_ACCOUNT` env value (`GOPGUKF-JO19546`). The org has **9 account locators** in `USAGE_IN_CURRENCY_DAILY` — unfiltered org queries would mix accounts. Single currency `USD`; billing data fresh through `2026-06-10`.
8. **Dev account access confirmed:** `DEV_ROLE` can already query both Organization Usage views in `GOPGUKF-JO19546`. Fresh deployments need `GRANT DATABASE ROLE SNOWFLAKE.ORGANIZATION_BILLING_VIEWER TO ROLE <greysight_role>;` (documented in Task 16).

---

## File Structure

**API (apps/api):**
| File | Action | Responsibility |
| --- | --- | --- |
| `app/config.py` | Modify | Add `estimated_credit_price_usd` setting |
| `sql/snowflake/org_spend_daily.sql` | Create | Billed dollars base dataset (Organization Usage) |
| `sql/snowflake/rate_sheet_daily.sql` | Create | Effective-rate base dataset (Organization Usage) |
| `sql/snowflake/current_account.sql` | Create | Account locator metadata query |
| `sql/dashboard_sources.yml` | Modify | Register new sources with new `kind` values |
| `app/services/dashboard_registry.py` | Modify | Validate allowed source kinds |
| `app/services/snowflake_client.py` | Modify | Allow `account_locator` bind param; generalized user-safe query error |
| `sql/snowflake/warehouse_spend_daily.sql` | Modify | Add `credits_used_compute` |
| `sql/snowflake/query_compute_by_user_daily.sql` | Modify | Rename output to `credits_attributed_compute` |
| `app/services/dataset_bounds.py` | Create | Top-100-users + `Other` rollup |
| `app/models.py` | Modify | New dataset keys/fields, `schema_version`, `DashboardDatasetMetadata` |
| `app/services/dashboard_datasets.py` | Create | Snowflake run orchestration: locator derivation, tolerant org probe, mode/freshness/currency metadata |
| `app/routes/dashboard_runs.py` | Modify | Use orchestrator; store/serve metadata |
| `app/services/demo_data.py` | Rewrite | Deterministic 100-day dollar fixtures + metadata |

**Web (apps/web/src):**
| File | Action | Responsibility |
| --- | --- | --- |
| `lib/dashboard-contracts.ts` | Modify | `schema_version` + metadata validation, new dataset types |
| `lib/dashboard-transforms.ts` | Create | THE pure transform module (windowing, conversion tiers, view models) |
| `lib/demo-dashboard-data.ts` | Rewrite | Small typed fixture matching the new contract (test fallback data) |
| `components/dashboard/dashboard-header.tsx` | Create | Product/mode/account/freshness header + run action |
| `components/dashboard/filter-bar.tsx` | Create | 7/30/90 segmented control + currency |
| `components/dashboard/spend-sections.tsx` | Create | Total/Compute/Storage/Service sections (render view models) |
| `components/dashboard/detail-tables.tsx` | Create | Dense capped detail tables |
| `components/dashboard/section-empty-state.tsx` | Create | Per-section empty states |
| `components/dashboard/cost-dashboard.tsx` | Rewrite | Orchestration: fetch, hold data in memory, window state, render |
| `components/dashboard/dashboard-runtime-shell.tsx` | Modify | Pass mode label (`Demo` / `Local Snowflake` / `Authenticated Snowflake`) |

**Docs:** `docs/snowflake-setup.md` (org usage grants), `.env.example` (`ESTIMATED_CREDIT_PRICE_USD`).

### Pinned cross-task contract names

Use these EXACTLY in every task: Python — `Settings.estimated_credit_price_usd`, `FETCH_WINDOW_DAYS = 100`, `SCHEMA_VERSION = 1`, `SourceAvailability`, `DashboardDatasetMetadata`, `bound_user_compute_rows`, `build_snowflake_dashboard_data`, `DashboardSourcesUnavailableError`, `build_top_warehouses_table`. Dataset row fields — `org_spend_daily`: `usage_date, service_type, rating_type, billing_type, is_adjustment, currency, spend`; `rate_sheet_daily`: `usage_date, service_type, rating_type, currency, effective_rate`; `current_account`: `account_locator`; `warehouse_spend_daily` adds `credits_used_compute`; `query_compute_by_user_daily` uses `credits_attributed_compute` (replaces `credits_used`). TypeScript — `SCHEMA_VERSION`, `DashboardMetadata`, `WindowDays`, `buildDashboardViewModel`, `DashboardViewModel`, `formatCurrency`, `buildRateIndex`, `creditsToDollars`.

---

## Phase 1 — API: settings, sources, contract, orchestration

### Task 1: `ESTIMATED_CREDIT_PRICE_USD` setting

**Files:**
- Modify: `apps/api/app/config.py`
- Test: `apps/api/tests/test_config.py`

- [ ] **Step 1: Write the failing tests** (append to `apps/api/tests/test_config.py`)

```python
def test_estimated_credit_price_defaults_to_three_usd() -> None:
    settings = Settings()
    assert settings.estimated_credit_price_usd == 3.0


def test_estimated_credit_price_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATED_CREDIT_PRICE_USD", "2.25")
    assert Settings().estimated_credit_price_usd == 2.25


def test_estimated_credit_price_empty_env_uses_default(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATED_CREDIT_PRICE_USD", "")
    assert Settings().estimated_credit_price_usd == 3.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'estimated_credit_price_usd'`

- [ ] **Step 3: Implement** (in `apps/api/app/config.py`)

Add below `DEFAULT_STORAGE_PRICE_USD_PER_TB_MONTH = 23.0`:

```python
DEFAULT_ESTIMATED_CREDIT_PRICE_USD = 3.0
```

Add the field inside `Settings` (next to `storage_price_usd_per_tb_month`):

```python
    estimated_credit_price_usd: float = Field(
        default=DEFAULT_ESTIMATED_CREDIT_PRICE_USD,
        gt=0,
        validation_alias=AliasChoices("ESTIMATED_CREDIT_PRICE_USD"),
    )
```

And extend the existing empty-string validator to cover both price fields (replace the existing `default_empty_storage_price` validator):

```python
    @field_validator(
        "storage_price_usd_per_tb_month", "estimated_credit_price_usd", mode="before"
    )
    @classmethod
    def default_empty_price(cls, value: object, info) -> object:
        if value == "":
            if info.field_name == "estimated_credit_price_usd":
                return DEFAULT_ESTIMATED_CREDIT_PRICE_USD
            return DEFAULT_STORAGE_PRICE_USD_PER_TB_MONTH
        return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_config.py -v`
Expected: PASS (all, including pre-existing storage-price tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/config.py apps/api/tests/test_config.py
git commit -m "feat: add ESTIMATED_CREDIT_PRICE_USD compute estimate setting"
```

---

### Task 2: V0 SQL assets + registry kind validation (sources are REGISTERED in Task 7)

> **Sequencing note:** the new sources must not be added to `sql/dashboard_sources.yml`
> yet — the current run route executes every registered source with only
> `window_days` bound, so registering Organization Usage sources before the
> Task 7 orchestrator would break `test_snowflake_dashboard_run.py`. The SQL
> files created here are inert until registered; the full suite stays green.

**Files:**
- Create: `sql/snowflake/org_spend_daily.sql`, `sql/snowflake/rate_sheet_daily.sql`, `sql/snowflake/current_account.sql`
- Modify: `apps/api/app/services/dashboard_registry.py`
- Test: `apps/api/tests/test_dashboard_registry.py`

- [ ] **Step 1: Write the failing test** (append to `apps/api/tests/test_dashboard_registry.py`)

```python
def test_registry_rejects_unknown_source_kind(tmp_path, monkeypatch) -> None:
    import app.services.dashboard_registry as registry_module

    registry_path = tmp_path / "sql" / "dashboard_sources.yml"
    sql_path = tmp_path / "sql" / "snowflake" / "bad.sql"
    sql_path.parent.mkdir(parents=True)
    sql_path.write_text("select 1", encoding="utf-8")
    registry_path.write_text(
        "sources:\n"
        "  - id: bad_source\n"
        "    kind: snowflake_anything\n"
        "    sql_path: sql/snowflake/bad.sql\n"
        "    grain:\n"
        "      - usage_date\n"
        "derived_datasets: []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module, "_ROOT_PATH", tmp_path)
    monkeypatch.setattr(registry_module, "_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(registry_module, "_SQL_ROOT", tmp_path / "sql")

    with pytest.raises(ValueError, match="kind"):
        registry_module.load_dashboard_registry()
```

Add `import pytest` at the top of the test file if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_dashboard_registry.py -v`
Expected: FAIL — the loader currently accepts `snowflake_anything` (no kind validation)

- [ ] **Step 3: Create the SQL assets**

`sql/snowflake/org_spend_daily.sql` — daily-grain billed dollars for the current account. Billed filter rationale: live data shows the invoice-matching negative included-cloud-services row has `billing_type='CONSUMPTION'`, `is_adjustment=true`, so we keep ALL rows here and let the frontend default to `billing_type = 'CONSUMPTION'` (both adjustment flags) for billed summaries:

```sql
select
    usage_date,
    service_type,
    rating_type,
    billing_type,
    is_adjustment,
    currency,
    sum(usage_in_currency) as spend
from snowflake.organization_usage.usage_in_currency_daily
where account_locator = %(account_locator)s
  and usage_date >= dateadd(
      day,
      -%(window_days)s,
      convert_timezone('UTC', current_timestamp())::date
  )
  and usage_date < convert_timezone('UTC', current_timestamp())::date
group by
    usage_date,
    service_type,
    rating_type,
    billing_type,
    is_adjustment,
    currency
order by
    usage_date,
    service_type,
    rating_type
```

`sql/snowflake/rate_sheet_daily.sql` — effective rates, deduped with `max()` because multiple `usage_type` rows (e.g. `compute` and `overage-compute`) map to one `(date, service_type)`:

```sql
select
    date as usage_date,
    service_type,
    rating_type,
    currency,
    max(effective_rate) as effective_rate
from snowflake.organization_usage.rate_sheet_daily
where account_locator = %(account_locator)s
  and billing_type = 'CONSUMPTION'
  and is_adjustment = false
  and date >= dateadd(
      day,
      -%(window_days)s,
      convert_timezone('UTC', current_timestamp())::date
  )
  and date < convert_timezone('UTC', current_timestamp())::date
group by
    usage_date,
    service_type,
    rating_type,
    currency
order by
    usage_date,
    service_type,
    rating_type
```

`sql/snowflake/current_account.sql`:

```sql
select current_account() as account_locator
```

- [ ] **Step 4: Validate kinds in the registry loader** (in `apps/api/app/services/dashboard_registry.py`)

Add near the top, below the path constants:

```python
ALLOWED_SOURCE_KINDS = frozenset(
    {
        "snowflake_account_usage",
        "snowflake_organization_usage",
        "snowflake_metadata",
    }
)
```

In `_load_sources`, after `source_id = _required_str(raw_source, "id")`, add:

```python
        kind = _required_str(raw_source, "kind")
        if kind not in ALLOWED_SOURCE_KINDS:
            raise ValueError(
                f"Dashboard source {source_id} has unknown kind: {kind}"
            )
```

and pass `kind=kind` in the `DashboardSource(...)` constructor instead of `kind=_required_str(raw_source, "kind")`.

- [ ] **Step 5: Run the full API suite to verify it is green**

Run: `cd apps/api && uv run pytest tests/ -q`
Expected: PASS — the new SQL files are not registered yet, so nothing executes them.

- [ ] **Step 6: Commit**

```bash
git add sql/snowflake/ apps/api/app/services/dashboard_registry.py apps/api/tests/test_dashboard_registry.py
git commit -m "feat: add v0 organization usage sql assets and registry kind validation"
```

---

### Task 3: Snowflake client — `account_locator` bind param + generalized error copy

The spec requires the derived locator to be **bound**, never interpolated, and forbids hardcoded `Account Usage` error copy for Organization Usage / metadata failures.

**Files:**
- Modify: `apps/api/app/services/snowflake_client.py`
- Test: `apps/api/tests/test_snowflake_client.py`

- [ ] **Step 1: Write the failing tests** (append to `apps/api/tests/test_snowflake_client.py`, reusing that file's existing fake-connection helpers)

```python
def test_execute_source_query_accepts_account_locator_bind() -> None:
    # Reuse the existing fake connection/monkeypatch pattern in this file to
    # capture bind params; assert the call succeeds and params pass through.
    captured: dict[str, object] = {}

    class _Cursor:
        description = [("account_locator",)]

        def execute(self, sql: str, params: dict[str, object]) -> None:
            captured.update(params)

        def fetchall(self):
            return [("TU24199",)]

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Connection:
        def cursor(self):
            return _Cursor()

        def close(self) -> None:
            return None

    config = SnowflakeConnectionConfig()
    rows = execute_source_query(
        "select 1",
        {"window_days": 100, "account_locator": "TU24199"},
        config=config,
        connect=lambda _config: _Connection(),
    )
    assert rows == [{"account_locator": "TU24199"}]
    assert captured == {"window_days": 100, "account_locator": "TU24199"}


def test_execute_source_query_rejects_malformed_account_locator() -> None:
    with pytest.raises(ValueError, match="account_locator"):
        execute_source_query(
            "select 1",
            {"window_days": 100, "account_locator": "BAD;DROP"},
        )


def test_execute_source_query_rejects_unknown_bind_keys() -> None:
    with pytest.raises(ValueError, match="bind"):
        execute_source_query("select 1", {"window_days": 100, "foo": 1})


def test_execute_source_query_allows_empty_bind_params() -> None:
    class _Cursor:
        description = [("account_locator",)]

        def execute(self, sql: str, params: dict[str, object]) -> None:
            return None

        def fetchall(self):
            return [("TU24199",)]

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Connection:
        def cursor(self):
            return _Cursor()

        def close(self) -> None:
            return None

    rows = execute_source_query(
        "select current_account() as account_locator",
        {},
        connect=lambda _config: _Connection(),
    )
    assert rows == [{"account_locator": "TU24199"}]


def test_execute_source_query_error_message_is_source_neutral() -> None:
    class _Connection:
        def cursor(self):
            raise RuntimeError("boom")

        def close(self) -> None:
            return None

    with pytest.raises(SnowflakeQueryError, match="Could not query Snowflake."):
        execute_source_query(
            "select 1", {"window_days": 100}, connect=lambda _config: _Connection()
        )
```

If the existing tests in this file monkeypatch `snowflake.connector.connect` instead of passing a `connect` callable, follow the file's established pattern — the behavior under test (bind validation, neutral error) is what matters. Adapt any existing test that asserts `window_days` is REQUIRED (empty params are now allowed) and any test asserting the old `"Could not query Snowflake Account Usage."` message.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_snowflake_client.py -v`
Expected: FAIL — unknown `connect` kwarg / `ValueError: window_days must be an integer between 1 and 365` for `{}` params

- [ ] **Step 3: Implement** (in `apps/api/app/services/snowflake_client.py`)

Add `import re` at the top. Replace `_validate_window_params` with:

```python
_ALLOWED_BIND_KEYS = frozenset({"window_days", "account_locator"})
_ACCOUNT_LOCATOR_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _validate_bind_params(bind_params: dict[str, Any]) -> None:
    unknown_keys = sorted(set(bind_params) - _ALLOWED_BIND_KEYS)
    if unknown_keys:
        raise ValueError(f"Unknown bind params: {', '.join(unknown_keys)}")

    if "window_days" in bind_params:
        window_days = bind_params["window_days"]
        if not isinstance(window_days, int) or not 1 <= window_days <= 365:
            raise ValueError("window_days must be an integer between 1 and 365")

    if "account_locator" in bind_params:
        account_locator = bind_params["account_locator"]
        if not isinstance(account_locator, str) or not _ACCOUNT_LOCATOR_PATTERN.match(
            account_locator
        ):
            raise ValueError("account_locator must match ^[A-Za-z0-9_]{1,64}$")
```

Replace `execute_source_query` with:

```python
def execute_source_query(
    sql: str,
    bind_params: dict[str, Any],
    config: SnowflakeConnectionConfig | None = None,
    *,
    connect: Any = None,
) -> list[dict[str, Any]]:
    _validate_bind_params(bind_params)
    connection = (connect or _connect)(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, bind_params)
            columns = [_column_name(column) for column in cursor.description or ()]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    except Exception:
        raise SnowflakeQueryError("Could not query Snowflake.") from None
    finally:
        connection.close()
```

Do NOT touch `_validation_queries()` — the spec forbids adding Organization Usage probes to setup validation.

- [ ] **Step 4: Run the API suite**

Run: `cd apps/api && uv run pytest tests/test_snowflake_client.py tests/test_snowflake_validation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/snowflake_client.py apps/api/tests/test_snowflake_client.py
git commit -m "feat: bind account_locator in snowflake client with source-neutral errors"
```

---

### Task 4: Account Usage base-field updates (`credits_used_compute`, `credits_attributed_compute`)

**Files:**
- Modify: `sql/snowflake/warehouse_spend_daily.sql`, `sql/snowflake/query_compute_by_user_daily.sql`
- Modify: `apps/api/app/models.py` (`SAFE_DATASET_ROW_FIELDS` for these two keys), `apps/api/app/services/cost_metrics.py` (`WarehouseSpendDaily`)
- Test: `apps/api/tests/test_cost_metrics.py`, plus fixture updates in `apps/api/tests/test_dataset_retention.py`, `apps/api/tests/test_snowflake_dashboard_run.py`, `apps/api/tests/test_audit_events.py` (wherever dataset row dicts are posted)

- [ ] **Step 1: Write the failing test** (append to `apps/api/tests/test_cost_metrics.py`)

```python
def test_warehouse_spend_daily_accepts_credits_used_compute() -> None:
    row = WarehouseSpendDaily.model_validate(
        {
            "usage_date": date(2026, 6, 5),
            "warehouse_name": "BI_WH",
            "credits_used": 10.0,
            "credits_used_compute": 9.2,
        }
    )
    assert row.credits_used_compute == 9.2


def test_warehouse_spend_daily_defaults_credits_used_compute_to_zero() -> None:
    row = WarehouseSpendDaily.model_validate(
        {
            "usage_date": date(2026, 6, 5),
            "warehouse_name": "BI_WH",
            "credits_used": 10.0,
        }
    )
    assert row.credits_used_compute == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_cost_metrics.py -v`
Expected: FAIL — `credits_used_compute` attribute missing

- [ ] **Step 3: Implement the model + SQL changes**

In `apps/api/app/services/cost_metrics.py`, extend `WarehouseSpendDaily`:

```python
class WarehouseSpendDaily(BaseModel):
    usage_date: date
    warehouse_name: str
    credits_used: float
    credits_used_compute: float = 0.0
```

In `sql/snowflake/warehouse_spend_daily.sql`, replace the select list line `sum(credits_used) as credits_used` block with:

```sql
    sum(credits_used) as credits_used,
    sum(credits_used_compute) as credits_used_compute
```

(`CREDITS_USED_COMPUTE` verified live in `WAREHOUSE_METERING_HISTORY`.)

In `sql/snowflake/query_compute_by_user_daily.sql`, change the aliased aggregate to preserve source semantics:

```sql
    sum(credits_attributed_compute) as credits_attributed_compute
```

In `apps/api/app/models.py`, update `SAFE_DATASET_ROW_FIELDS` entries:

```python
    "warehouse_spend_daily": frozenset(
        {"usage_date", "warehouse_name", "credits_used", "credits_used_compute"}
    ),
    "query_compute_by_user_daily": frozenset(
        {"usage_date", "user_name", "warehouse_name", "credits_attributed_compute"}
    ),
```

- [ ] **Step 4: Update test fixtures that post these dataset shapes**

Run: `cd apps/api && uv run pytest tests/ -x -q` and fix each failing fixture: every posted/faked `warehouse_spend_daily` row gains `"credits_used_compute": <0.9 * credits_used>`, every `query_compute_by_user_daily` row renames `credits_used` → `credits_attributed_compute`. `apps/api/app/services/demo_data.py` rows for these two keys must also be updated now (add `"credits_used_compute"` to each warehouse row; rename the user-row key) so the demo endpoints stay consistent — the full demo rewrite happens in Task 8.

- [ ] **Step 5: Run the full API suite**

Run: `cd apps/api && uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add sql/snowflake/ apps/api/
git commit -m "feat: add credits_used_compute and preserve credits_attributed_compute base fields"
```

---

### Task 5: Payload bounds — top-100 users + `Other` rollup

**Files:**
- Create: `apps/api/app/services/dataset_bounds.py`
- Test: `apps/api/tests/test_dataset_bounds.py`

- [ ] **Step 1: Write the failing tests** (create `apps/api/tests/test_dataset_bounds.py`)

```python
from datetime import date

from app.services.dataset_bounds import OTHER_USER_NAME, bound_user_compute_rows


def _row(day: int, user: str, warehouse: str, credits: float) -> dict[str, object]:
    return {
        "usage_date": date(2026, 6, day),
        "user_name": user,
        "warehouse_name": warehouse,
        "credits_attributed_compute": credits,
    }


def test_keeps_all_rows_when_under_limit() -> None:
    rows = [_row(1, "A", "WH1", 5.0), _row(2, "B", "WH1", 3.0)]
    assert bound_user_compute_rows(rows, top_n=100) == rows


def test_rolls_tail_users_into_per_day_other_rows() -> None:
    rows = [
        _row(1, "HEAVY", "WH1", 100.0),
        _row(1, "TAIL_1", "WH1", 1.0),
        _row(1, "TAIL_2", "WH1", 2.0),
        _row(2, "TAIL_1", "WH2", 4.0),
    ]
    bounded = bound_user_compute_rows(rows, top_n=1)

    head = [row for row in bounded if row["user_name"] == "HEAVY"]
    other = [row for row in bounded if row["user_name"] == OTHER_USER_NAME]
    assert head == [rows[0]]
    assert other == [
        _row(1, OTHER_USER_NAME, "WH1", 3.0),
        _row(2, OTHER_USER_NAME, "WH2", 4.0),
    ]


def test_top_users_ranked_by_total_credits_across_window() -> None:
    rows = [
        _row(1, "SMALL_DAILY", "WH1", 2.0),
        _row(2, "SMALL_DAILY", "WH1", 2.0),
        _row(1, "ONE_BIG_DAY", "WH1", 3.0),
    ]
    bounded = bound_user_compute_rows(rows, top_n=1)
    names = {row["user_name"] for row in bounded}
    assert names == {"SMALL_DAILY", OTHER_USER_NAME}


def test_other_totals_preserve_window_sum() -> None:
    rows = [_row(d, f"U{i}", "WH1", float(i)) for d in (1, 2) for i in range(5)]
    bounded = bound_user_compute_rows(rows, top_n=2)
    assert sum(r["credits_attributed_compute"] for r in bounded) == sum(
        r["credits_attributed_compute"] for r in rows
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_dataset_bounds.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.dataset_bounds`

- [ ] **Step 3: Implement** (create `apps/api/app/services/dataset_bounds.py`)

```python
from collections import defaultdict
from typing import Any

TOP_USER_COUNT = 100
OTHER_USER_NAME = "Other"


def bound_user_compute_rows(
    rows: list[dict[str, Any]], *, top_n: int = TOP_USER_COUNT
) -> list[dict[str, Any]]:
    """Keep user-by-warehouse-by-day rows for the top-N users by total
    compute credits over the fetch window; roll remaining users into
    per-day (per-warehouse) `Other` rows so local 7/30/90-day filters
    keep full fidelity without unbounded payloads."""
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        totals[str(row["user_name"])] += float(row["credits_attributed_compute"])

    top_users = set(
        sorted(totals, key=lambda user: (-totals[user], user))[:top_n]
    )
    if len(totals) <= top_n:
        return list(rows)

    head_rows = [row for row in rows if row["user_name"] in top_users]
    other_credits: dict[tuple[Any, Any], float] = defaultdict(float)
    for row in rows:
        if row["user_name"] in top_users:
            continue
        key = (row["usage_date"], row["warehouse_name"])
        other_credits[key] += float(row["credits_attributed_compute"])

    other_rows = [
        {
            "usage_date": usage_date,
            "user_name": OTHER_USER_NAME,
            "warehouse_name": warehouse_name,
            "credits_attributed_compute": credits,
        }
        for (usage_date, warehouse_name), credits in sorted(
            other_credits.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        )
    ]
    return head_rows + other_rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_dataset_bounds.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/dataset_bounds.py apps/api/tests/test_dataset_bounds.py
git commit -m "feat: bound query_compute_by_user_daily to top 100 users plus Other"
```

---

### Task 6: Versioned response contract — `schema_version`, `DashboardDatasetMetadata`, new safe dataset keys

> **Contract compatibility decision (intentional breaking change):** V0 dataset
> responses require `schema_version: 1` and all nine dataset keys; client-posted
> snapshots (`POST /api/dashboard-runs` with a `datasets` body) that carry the
> legacy six keys are intentionally rejected by the `REQUIRED_DATASET_KEYS`
> exact-match validator. The run repository is in-memory (wiped on restart), so
> there are no stored legacy snapshots to migrate, and the V0 frontend never
> posts datasets (it posts an empty body and lets the server build them). Do
> NOT split `SAFE_DATASET_ROW_FIELDS` from the required-keys set in this slice;
> revisit only if a later slice adds optional live-only datasets.

**Files:**
- Modify: `apps/api/app/models.py`
- Test: `apps/api/tests/test_dashboard_models.py` (create)

- [ ] **Step 1: Write the failing tests** (create `apps/api/tests/test_dashboard_models.py`)

```python
from datetime import date

import pytest

from app.models import (
    SAFE_DATASET_ROW_FIELDS,
    SCHEMA_VERSION,
    DashboardDatasetMetadata,
    DashboardDatasetResponse,
    SourceAvailability,
)


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1


def test_safe_fields_include_v0_dataset_keys() -> None:
    assert SAFE_DATASET_ROW_FIELDS["org_spend_daily"] == frozenset(
        {
            "usage_date",
            "service_type",
            "rating_type",
            "billing_type",
            "is_adjustment",
            "currency",
            "spend",
        }
    )
    assert SAFE_DATASET_ROW_FIELDS["rate_sheet_daily"] == frozenset(
        {"usage_date", "service_type", "rating_type", "currency", "effective_rate"}
    )
    assert SAFE_DATASET_ROW_FIELDS["current_account"] == frozenset(
        {"account_locator"}
    )


def test_metadata_round_trips() -> None:
    metadata = DashboardDatasetMetadata(
        data_mode="billed",
        account_locator="TU24199",
        currency="USD",
        billing_through_date=date(2026, 6, 8),
        account_usage_through_date=date(2026, 6, 9),
        estimated_credit_price_usd=3.0,
        storage_price_usd_per_tb_month=23.0,
        organization_usage=SourceAvailability(available=True),
        account_usage=SourceAvailability(available=True),
    )
    assert metadata.unsupported_reason is None
    dumped = metadata.model_dump(mode="json")
    assert dumped["billing_through_date"] == "2026-06-08"
    assert DashboardDatasetMetadata.model_validate(dumped) == metadata


def test_metadata_rejects_unknown_data_mode() -> None:
    with pytest.raises(ValueError):
        DashboardDatasetMetadata(
            data_mode="invoiced",
            account_locator=None,
            currency=None,
            billing_through_date=None,
            account_usage_through_date=None,
            estimated_credit_price_usd=3.0,
            storage_price_usd_per_tb_month=23.0,
            organization_usage=SourceAvailability(available=False),
            account_usage=SourceAvailability(available=True),
        )


def test_dataset_response_defaults_schema_version() -> None:
    response = DashboardDatasetResponse(
        run={
            "id": "r1",
            "status": "completed",
            "source": "snowflake",
            "window_days": 100,
        },
        summary={},
        datasets={},
    )
    assert response.schema_version == SCHEMA_VERSION
    assert response.metadata is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_dashboard_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'SCHEMA_VERSION'`

- [ ] **Step 3: Implement** (in `apps/api/app/models.py`)

Add `from datetime import date` to the imports (datetime already imported). Add below the Literal aliases:

```python
SCHEMA_VERSION = 1

DashboardDataMode = Literal["demo", "billed", "estimated"]
UnsupportedReason = Literal["mixed_currency"]
```

Extend `SAFE_DATASET_ROW_FIELDS` with the three new keys (exact field sets as in the test above). `REQUIRED_DATASET_KEYS` already derives from it — client-posted snapshots must now post all nine keys, which Task 4/this task's fixture updates cover.

Add the metadata models above `DashboardDatasetResponse`:

```python
class SourceAvailability(BaseModel):
    available: bool
    detail: str | None = None


class DashboardDatasetMetadata(BaseModel):
    data_mode: DashboardDataMode
    account_locator: str | None
    currency: str | None
    billing_through_date: date | None
    account_usage_through_date: date | None
    estimated_credit_price_usd: float
    storage_price_usd_per_tb_month: float
    unsupported_reason: UnsupportedReason | None = None
    organization_usage: SourceAvailability
    account_usage: SourceAvailability
```

Extend `DashboardDatasetResponse`:

```python
class DashboardDatasetResponse(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: DashboardRun
    summary: dict[str, Any]
    metadata: DashboardDatasetMetadata | None = None
    datasets: dict[str, list[dict[str, Any]]]
```

- [ ] **Step 4: Run tests and fix client-posted dataset fixtures**

Run: `cd apps/api && uv run pytest tests/ -q`
Any test that POSTs a full `datasets` payload now needs the three new keys, e.g. add to those fixtures:

```python
"org_spend_daily": [
    {
        "usage_date": "2026-06-05",
        "service_type": "WAREHOUSE_METERING",
        "rating_type": "COMPUTE",
        "billing_type": "CONSUMPTION",
        "is_adjustment": False,
        "currency": "USD",
        "spend": 18.0,
    }
],
"rate_sheet_daily": [
    {
        "usage_date": "2026-06-05",
        "service_type": "WAREHOUSE_METERING",
        "rating_type": "COMPUTE",
        "currency": "USD",
        "effective_rate": 2.25,
    }
],
"current_account": [{"account_locator": "TU24199"}],
```

Expected after fixes: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/
git commit -m "feat: version dashboard dataset response and add metadata contract"
```

---

### Task 7: Register V0 sources + Snowflake run orchestration — tolerant org probe, locator derivation, freshness, mixed currency

> Registering the Organization Usage / metadata sources and teaching the run
> path to bind them happen in THIS one commit, so the API suite is green
> before and after (see the Task 2 sequencing note).

**Files:**
- Create: `apps/api/app/services/dashboard_datasets.py`
- Modify: `sql/dashboard_sources.yml`, `apps/api/app/routes/dashboard_runs.py`
- Test: `apps/api/tests/test_dashboard_datasets.py` (create), `apps/api/tests/test_dashboard_registry.py` (extend), `apps/api/tests/test_snowflake_dashboard_run.py` (update)

- [ ] **Step 1: Write the failing tests** (create `apps/api/tests/test_dashboard_datasets.py`)

```python
from datetime import date

import pytest

from app.config import Settings
from app.services.dashboard_datasets import (
    FETCH_WINDOW_DAYS,
    DashboardSourcesUnavailableError,
    build_snowflake_dashboard_data,
)
from app.services.snowflake_client import SnowflakeQueryError


def _fake_execute(org_fails: bool = False, account_fails: bool = False):
    def execute(sql: str, bind_params: dict[str, object]):
        lowered = sql.lower()
        if "current_account()" in lowered:
            return [{"account_locator": "TU24199"}]
        if "organization_usage" in lowered:
            if org_fails:
                raise SnowflakeQueryError("Could not query Snowflake.")
            assert bind_params == {
                "window_days": FETCH_WINDOW_DAYS,
                "account_locator": "TU24199",
            }
            if "rate_sheet_daily" in lowered:
                return [
                    {
                        "usage_date": date(2026, 6, 5),
                        "service_type": "WAREHOUSE_METERING",
                        "rating_type": "COMPUTE",
                        "currency": "USD",
                        "effective_rate": 2.25,
                    }
                ]
            return [
                {
                    "usage_date": date(2026, 6, 5),
                    "service_type": "WAREHOUSE_METERING",
                    "rating_type": "COMPUTE",
                    "billing_type": "CONSUMPTION",
                    "is_adjustment": False,
                    "currency": "USD",
                    "spend": 18.0,
                },
                {
                    "usage_date": date(2026, 6, 8),
                    "service_type": "CLOUD_SERVICES",
                    "rating_type": "COMPUTE",
                    "billing_type": "CONSUMPTION",
                    "is_adjustment": True,
                    "currency": "USD",
                    "spend": -0.79,
                },
            ]
        if account_fails:
            raise SnowflakeQueryError("Could not query Snowflake.")
        assert bind_params == {"window_days": FETCH_WINDOW_DAYS}
        if "metering_daily_history" in lowered:
            return [
                {
                    "usage_date": date(2026, 6, 9),
                    "service_type": "WAREHOUSE_METERING",
                    "credits_used": 8.0,
                }
            ]
        if "warehouse_metering_history" in lowered:
            return [
                {
                    "usage_date": date(2026, 6, 9),
                    "warehouse_name": "BI_WH",
                    "credits_used": 8.0,
                    "credits_used_compute": 7.4,
                }
            ]
        if "query_attribution_history" in lowered:
            return [
                {
                    "usage_date": date(2026, 6, 9),
                    "user_name": "ANALYST",
                    "warehouse_name": "BI_WH",
                    "credits_attributed_compute": 6.0,
                }
            ]
        if "database_storage_usage_history" in lowered:
            return [
                {
                    "usage_date": date(2026, 6, 9),
                    "database_name": "RAW",
                    "average_database_bytes": 1_000.0,
                    "average_failsafe_bytes": 10.0,
                }
            ]
        raise AssertionError(f"Unexpected SQL: {sql}")

    return execute


def test_billed_mode_with_full_sources() -> None:
    data = build_snowflake_dashboard_data(Settings(), execute=_fake_execute())

    assert data.metadata.data_mode == "billed"
    assert data.metadata.account_locator == "TU24199"
    assert data.metadata.currency == "USD"
    assert data.metadata.billing_through_date == date(2026, 6, 8)
    assert data.metadata.account_usage_through_date == date(2026, 6, 9)
    assert data.metadata.unsupported_reason is None
    assert data.metadata.organization_usage.available is True
    assert data.metadata.account_usage.available is True
    # Negative consumption adjustment rows are preserved.
    spends = [row["spend"] for row in data.datasets["org_spend_daily"]]
    assert -0.79 in spends
    assert data.datasets["current_account"] == [{"account_locator": "TU24199"}]
    # JSON-ready: dates are isoformat strings.
    assert data.datasets["org_spend_daily"][0]["usage_date"] == "2026-06-05"


def test_estimated_mode_when_org_usage_unavailable() -> None:
    data = build_snowflake_dashboard_data(
        Settings(), execute=_fake_execute(org_fails=True)
    )

    assert data.metadata.data_mode == "estimated"
    assert data.metadata.organization_usage.available is False
    assert data.metadata.billing_through_date is None
    assert data.datasets["org_spend_daily"] == []
    assert data.datasets["rate_sheet_daily"] == []
    # Account usage datasets still present.
    assert data.datasets["warehouse_spend_daily"]


def test_billed_mode_when_account_usage_unavailable() -> None:
    data = build_snowflake_dashboard_data(
        Settings(), execute=_fake_execute(account_fails=True)
    )

    assert data.metadata.data_mode == "billed"
    assert data.metadata.account_usage.available is False
    assert data.datasets["warehouse_spend_daily"] == []


def test_fails_when_both_source_groups_unavailable() -> None:
    with pytest.raises(DashboardSourcesUnavailableError):
        build_snowflake_dashboard_data(
            Settings(), execute=_fake_execute(org_fails=True, account_fails=True)
        )


def test_mixed_currency_marks_unsupported() -> None:
    base = _fake_execute()

    def execute(sql: str, bind_params: dict[str, object]):
        rows = base(sql, bind_params)
        if "usage_in_currency_daily" in sql.lower():
            return rows + [
                {
                    "usage_date": date(2026, 6, 5),
                    "service_type": "STORAGE",
                    "rating_type": "STORAGE",
                    "billing_type": "CONSUMPTION",
                    "is_adjustment": False,
                    "currency": "EUR",
                    "spend": 4.0,
                }
            ]
        return rows

    data = build_snowflake_dashboard_data(Settings(), execute=execute)
    assert data.metadata.unsupported_reason == "mixed_currency"
    assert data.metadata.currency is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_dashboard_datasets.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.dashboard_datasets`

- [ ] **Step 3: Register the V0 sources** (append to `sources:` in `sql/dashboard_sources.yml`, and append the registration test — moved here from Task 2 — to `apps/api/tests/test_dashboard_registry.py`)

```yaml
  - id: org_spend_daily
    kind: snowflake_organization_usage
    sql_path: sql/snowflake/org_spend_daily.sql
    grain:
      - usage_date
      - service_type
      - rating_type
      - billing_type
      - is_adjustment
      - currency
  - id: rate_sheet_daily
    kind: snowflake_organization_usage
    sql_path: sql/snowflake/rate_sheet_daily.sql
    grain:
      - usage_date
      - service_type
      - rating_type
      - currency
  - id: current_account
    kind: snowflake_metadata
    sql_path: sql/snowflake/current_account.sql
    grain:
      - account_locator
```

```python
def test_registry_includes_organization_usage_and_metadata_sources() -> None:
    registry = load_dashboard_registry()

    org_spend = registry.sources["org_spend_daily"]
    assert org_spend.kind == "snowflake_organization_usage"
    assert "usage_in_currency_daily" in org_spend.sql.lower()
    assert "%(account_locator)s" in org_spend.sql

    rate_sheet = registry.sources["rate_sheet_daily"]
    assert rate_sheet.kind == "snowflake_organization_usage"
    assert "rate_sheet_daily" in rate_sheet.sql.lower()
    assert "%(account_locator)s" in rate_sheet.sql

    current_account = registry.sources["current_account"]
    assert current_account.kind == "snowflake_metadata"
    assert "current_account()" in current_account.sql.lower()
```

- [ ] **Step 4: Implement** (create `apps/api/app/services/dashboard_datasets.py`)

```python
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from pydantic import BaseModel

from app.config import Settings
from app.models import DashboardDatasetMetadata, SourceAvailability
from app.services.cost_metrics import derive_account_spend_daily
from app.services.dashboard_registry import DashboardSource, load_dashboard_registry
from app.services.dataset_bounds import bound_user_compute_rows
from app.services.snowflake_client import (
    SnowflakeConfigurationError,
    SnowflakeQueryError,
    execute_source_query,
)

FETCH_WINDOW_DAYS = 100

ExecuteFn = Callable[[str, dict[str, Any]], list[dict[str, Any]]]


class DashboardSourcesUnavailableError(RuntimeError):
    """Raised when neither Organization Usage nor Account Usage is queryable."""


class SnowflakeDashboardData(BaseModel):
    datasets: dict[str, list[dict[str, Any]]]
    metadata: DashboardDatasetMetadata


def build_snowflake_dashboard_data(
    settings: Settings, *, execute: ExecuteFn = execute_source_query
) -> SnowflakeDashboardData:
    registry = load_dashboard_registry()
    sources = registry.sources
    account_sources = _sources_of_kind(sources, "snowflake_account_usage")
    org_sources = _sources_of_kind(sources, "snowflake_organization_usage")

    account_locator, locator_detail = _derive_account_locator(
        sources["current_account"], execute
    )

    org_datasets, org_availability = _fetch_source_group(
        org_sources,
        execute,
        bind_params={
            "window_days": FETCH_WINDOW_DAYS,
            "account_locator": account_locator or "",
        },
        skip_detail=locator_detail,
        skip=account_locator is None,
    )
    account_datasets, account_availability = _fetch_source_group(
        account_sources,
        execute,
        bind_params={"window_days": FETCH_WINDOW_DAYS},
    )

    if not org_availability.available and not account_availability.available:
        raise DashboardSourcesUnavailableError(
            "Could not query Snowflake billing or Account Usage data."
        )

    account_datasets["query_compute_by_user_daily"] = bound_user_compute_rows(
        account_datasets.get("query_compute_by_user_daily", [])
    )

    datasets: dict[str, list[dict[str, Any]]] = {
        **account_datasets,
        **org_datasets,
        "current_account": (
            [{"account_locator": account_locator}] if account_locator else []
        ),
    }
    datasets["account_spend_daily"] = [
        row.model_dump()
        for row in derive_account_spend_daily(
            datasets.get("service_spend_daily", [])
        )
    ]
    datasets["top_warehouses_table"] = build_top_warehouses_table(
        datasets.get("warehouse_spend_daily", [])
    )

    currencies = sorted(
        {str(row["currency"]) for row in datasets.get("org_spend_daily", [])}
    )
    unsupported_reason = "mixed_currency" if len(currencies) > 1 else None

    metadata = DashboardDatasetMetadata(
        data_mode="billed" if org_availability.available else "estimated",
        account_locator=account_locator,
        currency=currencies[0] if len(currencies) == 1 else None,
        billing_through_date=_max_usage_date(datasets.get("org_spend_daily", [])),
        account_usage_through_date=_max_usage_date(
            [
                row
                for dataset_key in (
                    "service_spend_daily",
                    "warehouse_spend_daily",
                    "query_compute_by_user_daily",
                    "database_storage_daily",
                )
                for row in datasets.get(dataset_key, [])
            ]
        ),
        estimated_credit_price_usd=settings.estimated_credit_price_usd,
        storage_price_usd_per_tb_month=settings.storage_price_usd_per_tb_month,
        unsupported_reason=unsupported_reason,
        organization_usage=org_availability,
        account_usage=account_availability,
    )
    return SnowflakeDashboardData(
        datasets={key: _json_ready_rows(rows) for key, rows in datasets.items()},
        metadata=metadata,
    )


def build_top_warehouses_table(
    warehouse_spend_daily: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Legacy server-side ranking kept for contract compatibility only.
    V0 selected-window rankings are built in the frontend transform layer."""
    credits_by_warehouse: dict[str, float] = {}
    for row in warehouse_spend_daily:
        warehouse_name = str(row["warehouse_name"])
        credits_by_warehouse[warehouse_name] = credits_by_warehouse.get(
            warehouse_name, 0.0
        ) + float(row["credits_used"])
    return [
        {"warehouse_name": warehouse_name, "credits_used": credits_used}
        for warehouse_name, credits_used in sorted(
            credits_by_warehouse.items(), key=lambda item: item[1], reverse=True
        )[:10]
    ]


def _sources_of_kind(
    sources: dict[str, DashboardSource], kind: str
) -> dict[str, DashboardSource]:
    return {key: source for key, source in sources.items() if source.kind == kind}


def _derive_account_locator(
    current_account_source: DashboardSource, execute: ExecuteFn
) -> tuple[str | None, str | None]:
    try:
        rows = execute(current_account_source.sql, {})
    except (SnowflakeQueryError, SnowflakeConfigurationError):
        return None, "Could not determine the current Snowflake account."
    if not rows or not rows[0].get("account_locator"):
        return None, "Could not determine the current Snowflake account."
    return str(rows[0]["account_locator"]), None


def _fetch_source_group(
    sources: dict[str, DashboardSource],
    execute: ExecuteFn,
    *,
    bind_params: dict[str, Any],
    skip: bool = False,
    skip_detail: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], SourceAvailability]:
    empty = {key: [] for key in sources}
    if skip:
        return empty, SourceAvailability(available=False, detail=skip_detail)
    datasets: dict[str, list[dict[str, Any]]] = {}
    for dataset_key, source in sources.items():
        try:
            datasets[dataset_key] = execute(source.sql, bind_params)
        except (SnowflakeQueryError, SnowflakeConfigurationError) as exc:
            return empty, SourceAvailability(available=False, detail=str(exc))
    return datasets, SourceAvailability(available=True)


def _max_usage_date(rows: list[dict[str, Any]]) -> date | None:
    usage_dates = [
        _as_date(row["usage_date"]) for row in rows if row.get("usage_date")
    ]
    return max(usage_dates) if usage_dates else None


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _json_ready_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: value.isoformat() if isinstance(value, date) else value
            for key, value in row.items()
        }
        for row in rows
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_dashboard_datasets.py tests/test_dashboard_registry.py -v`
Expected: PASS

- [ ] **Step 6: Wire the route** (in `apps/api/app/routes/dashboard_runs.py`)

Replace `_build_snowflake_datasets` and `_build_top_warehouses_table` (delete both) and rewrite `_create_snowflake_dashboard_run`:

```python
def _create_snowflake_dashboard_run(
    request: DashboardRunCreateRequest, settings: Settings
) -> DashboardRun:
    try:
        dashboard_data = build_snowflake_dashboard_data(settings)
    except DashboardSourcesUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not query Snowflake billing or Account Usage data.",
        ) from None
    except (SnowflakeConfigurationError, SnowflakeQueryError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not query Snowflake.",
        ) from None

    datasets = dashboard_data.datasets
    summary = build_dashboard_summary(
        account_spend_daily=datasets["account_spend_daily"],
        warehouse_spend_daily=datasets["warehouse_spend_daily"],
        database_storage_daily=datasets["database_storage_daily"],
        current_usage_date=datetime.now(timezone.utc).date(),
        window_days=request.window_days,
        storage_price_usd_per_tb_month=settings.storage_price_usd_per_tb_month,
    ).model_dump(mode="json")

    return dashboard_run_repository.create_completed_snapshot(
        organization_id=request.organization_id,
        source=request.source,
        window_days=FETCH_WINDOW_DAYS,
        summary=summary,
        datasets=datasets,
        metadata=dashboard_data.metadata.model_dump(mode="json"),
        retention_days=request.retention_days,
    )
```

Update imports in `dashboard_runs.py`: remove `derive_account_spend_daily` and `execute_source_query` imports; add

```python
from app.services.dashboard_datasets import (
    FETCH_WINDOW_DAYS,
    DashboardSourcesUnavailableError,
    build_snowflake_dashboard_data,
)
```

Repository changes in the same file: add `metadata: dict[str, Any] | None = None` keyword arg to `create_completed_snapshot`, store it in a new `self._metadata: dict[UUID, dict[str, Any] | None] = {}` map (set in `__init__`, cleared in `clear()`, popped in `delete_run`), and include it in `get_dataset_response`:

```python
            stored_metadata = self._metadata.get(run_id)
            return DashboardDatasetResponse(
                run=run,
                summary=self._summaries.get(run_id, {}),
                metadata=(
                    DashboardDatasetMetadata.model_validate(stored_metadata)
                    if stored_metadata is not None
                    else None
                ),
                datasets={
                    dataset_key: stored_dataset.aggregate_dataset
                    for dataset_key, stored_dataset in stored_datasets.items()
                },
            )
```

(import `DashboardDatasetMetadata` from `app.models`).

- [ ] **Step 7: Update `tests/test_snowflake_dashboard_run.py`**

- Import `build_top_warehouses_table` from `app.services.dashboard_datasets` instead of `_build_top_warehouses_table` from the route.
- Extend the monkeypatched `execute` fakes to answer the new SQL (mirror `_fake_execute` from Step 1: respond to `current_account()`, `usage_in_currency_daily`, `rate_sheet_daily`).
- Monkeypatch target moves: patch `app.services.dashboard_datasets.execute_source_query` (the orchestrator's default), not the route module.
- Assert the dataset response now includes `schema_version == 1`, a `metadata` object with `data_mode` in `{"billed", "estimated"}`, and the three new dataset keys.
- The both-sources-fail test asserts 502 with detail `"Could not query Snowflake billing or Account Usage data."`.

- [ ] **Step 8: Run the full API suite**

Run: `cd apps/api && uv run pytest tests/ -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add sql/dashboard_sources.yml apps/api/
git commit -m "feat: orchestrate billed/estimated snowflake dashboard runs with metadata"
```

---

### Task 8: Demo data rewrite — deterministic 100-day dollar fixtures + metadata

**Files:**
- Rewrite: `apps/api/app/services/demo_data.py`
- Modify: `apps/api/app/routes/dashboard_runs.py` (demo snapshot stores metadata)
- Test: `apps/api/tests/test_demo_data.py`, `apps/api/tests/test_demo_dashboard_run.py` (update)

- [ ] **Step 1: Write the failing tests** (replace assertions in `apps/api/tests/test_demo_data.py` with)

```python
from datetime import date

from app.models import SAFE_DATASET_ROW_FIELDS, SCHEMA_VERSION
from app.services.demo_data import (
    DEMO_ACCOUNT_LOCATOR,
    DEMO_ACCOUNT_USAGE_THROUGH,
    DEMO_BILLING_THROUGH,
    DEMO_FETCH_DAYS,
    build_demo_dashboard_dataset,
)


def test_demo_payload_has_versioned_contract_and_metadata() -> None:
    payload = build_demo_dashboard_dataset()

    assert payload.schema_version == SCHEMA_VERSION
    assert payload.metadata.data_mode == "demo"
    assert payload.metadata.account_locator == DEMO_ACCOUNT_LOCATOR
    assert payload.metadata.currency == "USD"
    assert payload.metadata.billing_through_date == DEMO_BILLING_THROUGH
    assert payload.metadata.account_usage_through_date == DEMO_ACCOUNT_USAGE_THROUGH
    assert payload.metadata.organization_usage.available is True
    assert payload.metadata.account_usage.available is True


def test_demo_datasets_cover_all_keys_with_safe_fields() -> None:
    payload = build_demo_dashboard_dataset()

    assert set(payload.datasets) == set(SAFE_DATASET_ROW_FIELDS)
    for dataset_key, rows in payload.datasets.items():
        assert rows, f"{dataset_key} must not be empty"
        for row in rows:
            assert set(row) == SAFE_DATASET_ROW_FIELDS[dataset_key], dataset_key


def test_demo_org_spend_covers_full_fetch_window() -> None:
    payload = build_demo_dashboard_dataset()
    dates = sorted({row["usage_date"] for row in payload.datasets["org_spend_daily"]})

    assert len(dates) == DEMO_FETCH_DAYS
    assert dates[-1] == DEMO_BILLING_THROUGH.isoformat()
    # 90-day local filter must show meaningful data.
    assert len(dates) >= 90


def test_demo_includes_negative_consumption_adjustment_rows() -> None:
    payload = build_demo_dashboard_dataset()
    negatives = [
        row
        for row in payload.datasets["org_spend_daily"]
        if row["spend"] < 0
    ]
    assert negatives
    assert all(row["billing_type"] == "CONSUMPTION" for row in negatives)
    assert all(row["is_adjustment"] is True for row in negatives)


def test_demo_data_is_deterministic() -> None:
    first = build_demo_dashboard_dataset()
    second = build_demo_dashboard_dataset()
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_demo_data.py -v`
Expected: FAIL — imports missing

- [ ] **Step 3: Rewrite** `apps/api/app/services/demo_data.py`

```python
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

from app.models import (
    SCHEMA_VERSION,
    DashboardDatasetMetadata,
    SourceAvailability,
)
from app.services.cost_metrics import (
    DashboardSummary,
    build_dashboard_summary,
    derive_account_spend_daily,
)

DEMO_FETCH_DAYS = 100
DEMO_TODAY = date(2026, 6, 10)
DEMO_BILLING_THROUGH = date(2026, 6, 8)
DEMO_ACCOUNT_USAGE_THROUGH = date(2026, 6, 9)
DEMO_ACCOUNT_LOCATOR = "DEMO123"
DEMO_CREDIT_RATE_USD = 2.25
DEMO_STORAGE_RATE_USD = 25.0

# (service_type, rating_type, base daily credits)
_DEMO_SERVICES = (
    ("WAREHOUSE_METERING", "COMPUTE", 38.0),
    ("CLOUD_SERVICES", "COMPUTE", 4.0),
    ("AUTO_CLUSTERING", "COMPUTE", 1.5),
)
# (warehouse_name, share of metered compute credits)
_DEMO_WAREHOUSES = (("BI_WH", 0.5), ("ETL_WH", 0.35), ("ADHOC_WH", 0.15))
# (user_name, warehouse_name, share of attributed credits)
_DEMO_USERS = (
    ("ANALYST_A", "BI_WH", 0.34),
    ("ANALYST_B", "ADHOC_WH", 0.22),
    ("DATA_ENGINEER", "ETL_WH", 0.30),
    ("AIRFLOW_SVC", "ETL_WH", 0.14),
)
# (database_name, base terabytes)
_DEMO_DATABASES = (("RAW", 3.6), ("ANALYTICS", 2.3))


class DemoRun(BaseModel):
    id: str
    status: str
    source: str
    window_days: int
    started_at: datetime
    completed_at: datetime
    error: str | None = None


class DashboardDatasetPayload(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: DemoRun
    summary: DashboardSummary
    metadata: DashboardDatasetMetadata
    datasets: dict[str, list[dict[str, Any]]]


def _daily_factor(day_index: int) -> float:
    """Deterministic weekly wobble: weekdays heavier than weekends."""
    return 1.0 + 0.18 * ((day_index % 7) - 3) / 3.0


def _dates(through: date, days: int) -> list[date]:
    return [through - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _round(value: float) -> float:
    return round(value, 2)


def build_demo_dashboard_dataset() -> DashboardDatasetPayload:
    org_spend_daily: list[dict[str, Any]] = []
    rate_sheet_daily: list[dict[str, Any]] = []
    for index, usage_date in enumerate(_dates(DEMO_BILLING_THROUGH, DEMO_FETCH_DAYS)):
        factor = _daily_factor(index)
        for service_type, rating_type, base_credits in _DEMO_SERVICES:
            org_spend_daily.append(
                {
                    "usage_date": usage_date,
                    "service_type": service_type,
                    "rating_type": rating_type,
                    "billing_type": "CONSUMPTION",
                    "is_adjustment": False,
                    "currency": "USD",
                    "spend": _round(base_credits * factor * DEMO_CREDIT_RATE_USD),
                }
            )
            rate_sheet_daily.append(
                {
                    "usage_date": usage_date,
                    "service_type": service_type,
                    "rating_type": rating_type,
                    "currency": "USD",
                    "effective_rate": DEMO_CREDIT_RATE_USD,
                }
            )
        # Invoice-matching negative included-cloud-services adjustment row
        # (billing_type CONSUMPTION, is_adjustment true — as verified live).
        org_spend_daily.append(
            {
                "usage_date": usage_date,
                "service_type": "CLOUD_SERVICES",
                "rating_type": "COMPUTE",
                "billing_type": "CONSUMPTION",
                "is_adjustment": True,
                "currency": "USD",
                "spend": _round(-0.6 * factor),
            }
        )
        # Billed storage dollars.
        storage_tb = sum(tb for _, tb in _DEMO_DATABASES) * (1 + index / 800.0)
        org_spend_daily.append(
            {
                "usage_date": usage_date,
                "service_type": "STORAGE",
                "rating_type": "STORAGE",
                "billing_type": "CONSUMPTION",
                "is_adjustment": False,
                "currency": "USD",
                "spend": _round(storage_tb * DEMO_STORAGE_RATE_USD / 30.0),
            }
        )
        rate_sheet_daily.append(
            {
                "usage_date": usage_date,
                "service_type": "STORAGE",
                "rating_type": "STORAGE",
                "currency": "USD",
                "effective_rate": DEMO_STORAGE_RATE_USD,
            }
        )

    service_spend_daily: list[dict[str, Any]] = []
    warehouse_spend_daily: list[dict[str, Any]] = []
    user_rows: list[dict[str, Any]] = []
    database_storage_daily: list[dict[str, Any]] = []
    for index, usage_date in enumerate(
        _dates(DEMO_ACCOUNT_USAGE_THROUGH, DEMO_FETCH_DAYS)
    ):
        factor = _daily_factor(index)
        for service_type, _rating_type, base_credits in _DEMO_SERVICES:
            service_spend_daily.append(
                {
                    "usage_date": usage_date,
                    "service_type": service_type,
                    "credits_used": _round(base_credits * factor),
                }
            )
        metered_credits = _DEMO_SERVICES[0][2] * factor
        for warehouse_name, share in _DEMO_WAREHOUSES:
            credits_used = metered_credits * share
            warehouse_spend_daily.append(
                {
                    "usage_date": usage_date,
                    "warehouse_name": warehouse_name,
                    "credits_used": _round(credits_used),
                    "credits_used_compute": _round(credits_used * 0.92),
                }
            )
        for user_name, warehouse_name, share in _DEMO_USERS:
            user_rows.append(
                {
                    "usage_date": usage_date,
                    "user_name": user_name,
                    "warehouse_name": warehouse_name,
                    "credits_attributed_compute": _round(
                        metered_credits * 0.9 * share
                    ),
                }
            )
        for database_name, base_tb in _DEMO_DATABASES:
            grown_bytes = base_tb * 1_000_000_000_000 * (1 + index / 800.0)
            database_storage_daily.append(
                {
                    "usage_date": usage_date,
                    "database_name": database_name,
                    "average_database_bytes": round(grown_bytes),
                    "average_failsafe_bytes": round(grown_bytes * 0.1),
                }
            )

    account_spend_daily = derive_account_spend_daily(service_spend_daily)
    summary = build_dashboard_summary(
        account_spend_daily=account_spend_daily,
        warehouse_spend_daily=warehouse_spend_daily,
        database_storage_daily=database_storage_daily,
        current_usage_date=DEMO_TODAY,
        window_days=30,
        storage_price_usd_per_tb_month=None,
    )

    datasets = {
        "account_spend_daily": _dump_rows(account_spend_daily),
        "warehouse_spend_daily": _dump_rows(warehouse_spend_daily),
        "service_spend_daily": _dump_rows(service_spend_daily),
        "query_compute_by_user_daily": _dump_rows(user_rows),
        "database_storage_daily": _dump_rows(database_storage_daily),
        "top_warehouses_table": _top_warehouse_rows(warehouse_spend_daily),
        "org_spend_daily": _dump_rows(org_spend_daily),
        "rate_sheet_daily": _dump_rows(rate_sheet_daily),
        "current_account": [{"account_locator": DEMO_ACCOUNT_LOCATOR}],
    }

    metadata = DashboardDatasetMetadata(
        data_mode="demo",
        account_locator=DEMO_ACCOUNT_LOCATOR,
        currency="USD",
        billing_through_date=DEMO_BILLING_THROUGH,
        account_usage_through_date=DEMO_ACCOUNT_USAGE_THROUGH,
        estimated_credit_price_usd=3.0,
        storage_price_usd_per_tb_month=23.0,
        organization_usage=SourceAvailability(available=True),
        account_usage=SourceAvailability(available=True),
    )

    return DashboardDatasetPayload(
        run=DemoRun(
            id="demo-run",
            status="completed",
            source="demo",
            window_days=DEMO_FETCH_DAYS,
            started_at=datetime(2026, 6, 10, 0, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 6, 10, 0, 0, 1, tzinfo=timezone.utc),
        ),
        summary=summary,
        metadata=metadata,
        datasets=datasets,
    )


def _top_warehouse_rows(
    warehouse_spend_daily: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    credits_by_warehouse: dict[str, float] = {}
    for row in warehouse_spend_daily:
        warehouse_name = str(row["warehouse_name"])
        credits_by_warehouse[warehouse_name] = credits_by_warehouse.get(
            warehouse_name, 0.0
        ) + float(row["credits_used"])
    return [
        {"warehouse_name": warehouse_name, "credits_used": _round(credits_used)}
        for warehouse_name, credits_used in sorted(
            credits_by_warehouse.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def _dump_rows(rows: list[Any]) -> list[dict[str, Any]]:
    dumped_rows: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, BaseModel):
            row = row.model_dump()
        dumped_rows.append(
            {
                key: value.isoformat() if isinstance(value, date) else value
                for key, value in row.items()
            }
        )
    return dumped_rows
```

- [ ] **Step 4: Pass demo metadata through the demo run snapshot** (in `apps/api/app/routes/dashboard_runs.py`, `_create_demo_dashboard_run`)

```python
    demo_payload = build_demo_dashboard_dataset()
    return dashboard_run_repository.create_completed_snapshot(
        organization_id=request.organization_id,
        source=request.source,
        window_days=demo_payload.run.window_days,
        summary=demo_payload.summary.model_dump(mode="json"),
        datasets=demo_payload.datasets,
        metadata=demo_payload.metadata.model_dump(mode="json"),
        retention_days=request.retention_days,
    )
```

- [ ] **Step 5: Run the full API suite and fix demo-shape assertions**

Run: `cd apps/api && uv run pytest tests/ -q`
`tests/test_demo_dashboard_run.py` assertions about specific row values/dates need updating to the new deterministic fixtures (assert on structure: `schema_version == 1`, `metadata["data_mode"] == "demo"`, all nine dataset keys present, `run["window_days"] == 100`).
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api/
git commit -m "feat: deterministic 100-day dollar demo fixtures with metadata"
```

---

## Phase 2 — Frontend contract and the pure transform module

### Task 9: Frontend contracts — `schema_version`, metadata, new dataset types

**Files:**
- Modify: `apps/web/src/lib/dashboard-contracts.ts`
- Test: `apps/web/src/lib/dashboard-contracts.test.ts`

- [ ] **Step 1: Write the failing tests** (append to `apps/web/src/lib/dashboard-contracts.test.ts`; reuse/extend the file's existing valid-payload fixture builder if one exists)

```ts
const validMetadata = {
  data_mode: "billed",
  account_locator: "TU24199",
  currency: "USD",
  billing_through_date: "2026-06-08",
  account_usage_through_date: "2026-06-09",
  estimated_credit_price_usd: 3,
  storage_price_usd_per_tb_month: 23,
  unsupported_reason: null,
  organization_usage: { available: true, detail: null },
  account_usage: { available: true, detail: null },
};

const validV0Datasets = {
  account_spend_daily: [{ usage_date: "2026-06-05", credits_used: 41.5 }],
  warehouse_spend_daily: [
    {
      usage_date: "2026-06-05",
      warehouse_name: "BI_WH",
      credits_used: 18,
      credits_used_compute: 16.5,
    },
  ],
  service_spend_daily: [
    { usage_date: "2026-06-05", service_type: "WAREHOUSE_METERING", credits_used: 37.5 },
  ],
  query_compute_by_user_daily: [
    {
      usage_date: "2026-06-05",
      user_name: "ANALYST_A",
      warehouse_name: "BI_WH",
      credits_attributed_compute: 12,
    },
  ],
  database_storage_daily: [
    {
      usage_date: "2026-06-05",
      database_name: "RAW",
      average_database_bytes: 1000,
      average_failsafe_bytes: 100,
    },
  ],
  top_warehouses_table: [{ warehouse_name: "BI_WH", credits_used: 18 }],
  org_spend_daily: [
    {
      usage_date: "2026-06-05",
      service_type: "WAREHOUSE_METERING",
      rating_type: "COMPUTE",
      billing_type: "CONSUMPTION",
      is_adjustment: false,
      currency: "USD",
      spend: 40.5,
    },
  ],
  rate_sheet_daily: [
    {
      usage_date: "2026-06-05",
      service_type: "WAREHOUSE_METERING",
      rating_type: "COMPUTE",
      currency: "USD",
      effective_rate: 2.25,
    },
  ],
  current_account: [{ account_locator: "TU24199" }],
};

const validV0Payload = {
  schema_version: 1,
  run: { id: "r1", status: "completed", source: "snowflake", window_days: 100 },
  summary: {
    total_credits: 1,
    average_daily_credits: 1,
    estimated_monthly_credits: 30,
    storage_bytes: 0,
  },
  metadata: validMetadata,
  datasets: validV0Datasets,
};

describe("v0 contract", () => {
  it("parses a versioned payload with metadata and new dataset keys", () => {
    const data = parseDashboardDatasets(validV0Payload);
    expect(data.schema_version).toBe(1);
    expect(data.metadata.data_mode).toBe("billed");
    expect(data.metadata.billing_through_date).toBe("2026-06-08");
    expect(data.datasets.org_spend_daily[0].spend).toBe(40.5);
    expect(data.datasets.rate_sheet_daily[0].effective_rate).toBe(2.25);
    expect(data.datasets.current_account[0].account_locator).toBe("TU24199");
  });

  it("rejects a missing or wrong schema_version", () => {
    expect(() =>
      parseDashboardDatasets({ ...validV0Payload, schema_version: 2 }),
    ).toThrow(/schema_version/);
    const { schema_version: _ignored, ...withoutVersion } = validV0Payload;
    expect(() => parseDashboardDatasets(withoutVersion)).toThrow(/schema_version/);
  });

  it("rejects missing metadata", () => {
    const { metadata: _ignored, ...withoutMetadata } = validV0Payload;
    expect(() => parseDashboardDatasets(withoutMetadata)).toThrow(/metadata/);
  });

  it("rejects an invalid data_mode", () => {
    expect(() =>
      parseDashboardDatasets({
        ...validV0Payload,
        metadata: { ...validMetadata, data_mode: "invoiced" },
      }),
    ).toThrow(/data_mode/);
  });
});
```

Existing tests in this file that build payloads without `schema_version`/`metadata`/new keys must be updated to use the new valid payload shape (the parser is now stricter by design).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/web && npx vitest run src/lib/dashboard-contracts.test.ts`
Expected: FAIL — parser accepts wrong versions / metadata missing

- [ ] **Step 3: Implement** (in `apps/web/src/lib/dashboard-contracts.ts`)

Add at the top:

```ts
export const SCHEMA_VERSION = 1;
export const FETCH_WINDOW_DAYS = 100;

export type DashboardDataMode = "demo" | "billed" | "estimated";

export type SourceAvailability = {
  available: boolean;
  detail?: string | null;
};

export type DashboardMetadata = {
  data_mode: DashboardDataMode;
  account_locator: string | null;
  currency: string | null;
  billing_through_date: string | null;
  account_usage_through_date: string | null;
  estimated_credit_price_usd: number;
  storage_price_usd_per_tb_month: number;
  unsupported_reason: "mixed_currency" | null;
  organization_usage: SourceAvailability;
  account_usage: SourceAvailability;
};
```

Replace/extend the dataset types:

```ts
export type WarehouseSpendDaily = AccountSpendDaily & {
  warehouse_name: string;
  credits_used_compute: number;
};

export type QueryComputeByUserDaily = {
  usage_date: string;
  user_name: string;
  warehouse_name: string;
  credits_attributed_compute: number;
};

export type OrgSpendDaily = {
  usage_date: string;
  service_type: string;
  rating_type: string;
  billing_type: string;
  is_adjustment: boolean;
  currency: string;
  spend: number;
};

export type RateSheetDaily = {
  usage_date: string;
  service_type: string;
  rating_type: string;
  currency: string;
  effective_rate: number;
};

export type CurrentAccount = {
  account_locator: string;
};
```

Extend `DashboardDatasets` and `REQUIRED_DATASET_KEYS` with `org_spend_daily: OrgSpendDaily[]`, `rate_sheet_daily: RateSheetDaily[]`, `current_account: CurrentAccount[]`, and extend `DashboardData`:

```ts
export type DashboardData = {
  schema_version: number;
  run: DashboardRun;
  summary: DashboardSummary;
  metadata: DashboardMetadata;
  datasets: DashboardDatasets;
};
```

In `parseDashboardDatasets`, after the `isRecord(payload)` check add:

```ts
  if (payload.schema_version !== SCHEMA_VERSION) {
    throw new Error(
      `Unsupported dashboard schema_version: ${String(payload.schema_version)}`,
    );
  }

  const metadata = parseDashboardMetadata(payload.metadata);
```

and include `schema_version: SCHEMA_VERSION, metadata` in the returned object. Add the metadata parser:

```ts
const DATA_MODES = ["demo", "billed", "estimated"] as const;

function parseDashboardMetadata(payload: unknown): DashboardMetadata {
  if (!isRecord(payload)) {
    throw new Error("Dashboard metadata is required");
  }
  if (!(DATA_MODES as readonly string[]).includes(String(payload.data_mode))) {
    throw new Error("Dashboard metadata data_mode is invalid");
  }
  if (
    !isFiniteNumber(payload.estimated_credit_price_usd) ||
    !isFiniteNumber(payload.storage_price_usd_per_tb_month)
  ) {
    throw new Error("Dashboard metadata prices must be numbers");
  }
  return {
    data_mode: payload.data_mode as DashboardDataMode,
    account_locator: readNullableString(payload, "account_locator"),
    currency: readNullableString(payload, "currency"),
    billing_through_date: readNullableString(payload, "billing_through_date"),
    account_usage_through_date: readNullableString(
      payload,
      "account_usage_through_date",
    ),
    estimated_credit_price_usd: payload.estimated_credit_price_usd,
    storage_price_usd_per_tb_month: payload.storage_price_usd_per_tb_month,
    unsupported_reason:
      payload.unsupported_reason === "mixed_currency" ? "mixed_currency" : null,
    organization_usage: parseSourceAvailability(payload.organization_usage),
    account_usage: parseSourceAvailability(payload.account_usage),
  };
}

function parseSourceAvailability(payload: unknown): SourceAvailability {
  if (!isRecord(payload) || typeof payload.available !== "boolean") {
    throw new Error("Dashboard metadata source availability is invalid");
  }
  const detail = payload.detail;
  if (detail !== undefined && detail !== null && typeof detail !== "string") {
    throw new Error("Dashboard metadata source availability is invalid");
  }
  return { available: payload.available, detail: detail ?? null };
}

function readNullableString(
  payload: Record<string, unknown>,
  key: string,
): string | null {
  const value = payload[key];
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "string") {
    throw new Error(`Dashboard metadata ${key} must be a string or null`);
  }
  return value;
}
```

- [ ] **Step 4: Rewrite the web demo fixture** (`apps/web/src/lib/demo-dashboard-data.ts`) — generated, 100 days, mirrors the API demo generator so transform/component tests have realistic data:

```ts
import type { DashboardData, OrgSpendDaily, RateSheetDaily } from "./dashboard-contracts";

const FETCH_DAYS = 100;
const BILLING_THROUGH = "2026-06-08";
const ACCOUNT_USAGE_THROUGH = "2026-06-09";
const CREDIT_RATE_USD = 2.25;
const STORAGE_RATE_USD = 25;

const SERVICES: Array<[string, string, number]> = [
  ["WAREHOUSE_METERING", "COMPUTE", 38],
  ["CLOUD_SERVICES", "COMPUTE", 4],
  ["AUTO_CLUSTERING", "COMPUTE", 1.5],
];
const WAREHOUSES: Array<[string, number]> = [
  ["BI_WH", 0.5],
  ["ETL_WH", 0.35],
  ["ADHOC_WH", 0.15],
];
const USERS: Array<[string, string, number]> = [
  ["ANALYST_A", "BI_WH", 0.34],
  ["ANALYST_B", "ADHOC_WH", 0.22],
  ["DATA_ENGINEER", "ETL_WH", 0.3],
  ["AIRFLOW_SVC", "ETL_WH", 0.14],
];
const DATABASES: Array<[string, number]> = [
  ["RAW", 3.6],
  ["ANALYTICS", 2.3],
];

function datesEnding(through: string, days: number): string[] {
  const [year, month, day] = through.split("-").map(Number);
  const end = Date.UTC(year, month - 1, day);
  return Array.from({ length: days }, (_, index) =>
    new Date(end - (days - 1 - index) * 86_400_000).toISOString().slice(0, 10),
  );
}

const wobble = (index: number) => 1 + (0.18 * ((index % 7) - 3)) / 3;
const round2 = (value: number) => Math.round(value * 100) / 100;

const orgSpendDaily: OrgSpendDaily[] = [];
const rateSheetDaily: RateSheetDaily[] = [];
datesEnding(BILLING_THROUGH, FETCH_DAYS).forEach((usage_date, index) => {
  const factor = wobble(index);
  for (const [service_type, rating_type, baseCredits] of SERVICES) {
    orgSpendDaily.push({
      usage_date,
      service_type,
      rating_type,
      billing_type: "CONSUMPTION",
      is_adjustment: false,
      currency: "USD",
      spend: round2(baseCredits * factor * CREDIT_RATE_USD),
    });
    rateSheetDaily.push({
      usage_date,
      service_type,
      rating_type,
      currency: "USD",
      effective_rate: CREDIT_RATE_USD,
    });
  }
  orgSpendDaily.push({
    usage_date,
    service_type: "CLOUD_SERVICES",
    rating_type: "COMPUTE",
    billing_type: "CONSUMPTION",
    is_adjustment: true,
    currency: "USD",
    spend: round2(-0.6 * factor),
  });
  const storageTb = DATABASES.reduce((sum, [, tb]) => sum + tb, 0) * (1 + index / 800);
  orgSpendDaily.push({
    usage_date,
    service_type: "STORAGE",
    rating_type: "STORAGE",
    billing_type: "CONSUMPTION",
    is_adjustment: false,
    currency: "USD",
    spend: round2((storageTb * STORAGE_RATE_USD) / 30),
  });
  rateSheetDaily.push({
    usage_date,
    service_type: "STORAGE",
    rating_type: "STORAGE",
    currency: "USD",
    effective_rate: STORAGE_RATE_USD,
  });
});

const serviceSpendDaily: DashboardData["datasets"]["service_spend_daily"] = [];
const accountSpendDaily: DashboardData["datasets"]["account_spend_daily"] = [];
const warehouseSpendDaily: DashboardData["datasets"]["warehouse_spend_daily"] = [];
const userComputeDaily: DashboardData["datasets"]["query_compute_by_user_daily"] = [];
const databaseStorageDaily: DashboardData["datasets"]["database_storage_daily"] = [];
datesEnding(ACCOUNT_USAGE_THROUGH, FETCH_DAYS).forEach((usage_date, index) => {
  const factor = wobble(index);
  let dayCredits = 0;
  for (const [service_type, , baseCredits] of SERVICES) {
    const credits_used = round2(baseCredits * factor);
    dayCredits += credits_used;
    serviceSpendDaily.push({ usage_date, service_type, credits_used });
  }
  accountSpendDaily.push({ usage_date, credits_used: round2(dayCredits) });
  const meteredCredits = SERVICES[0][2] * factor;
  for (const [warehouse_name, share] of WAREHOUSES) {
    warehouseSpendDaily.push({
      usage_date,
      warehouse_name,
      credits_used: round2(meteredCredits * share),
      credits_used_compute: round2(meteredCredits * share * 0.92),
    });
  }
  for (const [user_name, warehouse_name, share] of USERS) {
    userComputeDaily.push({
      usage_date,
      user_name,
      warehouse_name,
      credits_attributed_compute: round2(meteredCredits * 0.9 * share),
    });
  }
  for (const [database_name, baseTb] of DATABASES) {
    const bytes = Math.round(baseTb * 1_000_000_000_000 * (1 + index / 800));
    databaseStorageDaily.push({
      usage_date,
      database_name,
      average_database_bytes: bytes,
      average_failsafe_bytes: Math.round(bytes * 0.1),
    });
  }
});

const demoDashboardDatasets: DashboardData = {
  schema_version: 1,
  run: {
    id: "demo-run",
    status: "completed",
    source: "demo",
    window_days: FETCH_DAYS,
    started_at: "2026-06-10T00:00:00Z",
    completed_at: "2026-06-10T00:00:01Z",
    error: null,
  },
  summary: {
    total_credits: 1305,
    average_daily_credits: 43.5,
    estimated_monthly_credits: 1305,
    storage_bytes: 6_700_000_000_000,
    estimated_monthly_storage_cost_usd: null,
  },
  metadata: {
    data_mode: "demo",
    account_locator: "DEMO123",
    currency: "USD",
    billing_through_date: BILLING_THROUGH,
    account_usage_through_date: ACCOUNT_USAGE_THROUGH,
    estimated_credit_price_usd: 3,
    storage_price_usd_per_tb_month: 23,
    unsupported_reason: null,
    organization_usage: { available: true, detail: null },
    account_usage: { available: true, detail: null },
  },
  datasets: {
    account_spend_daily: accountSpendDaily,
    warehouse_spend_daily: warehouseSpendDaily,
    service_spend_daily: serviceSpendDaily,
    query_compute_by_user_daily: userComputeDaily,
    database_storage_daily: databaseStorageDaily,
    top_warehouses_table: [
      { warehouse_name: "BI_WH", credits_used: 1900 },
      { warehouse_name: "ETL_WH", credits_used: 1330 },
      { warehouse_name: "ADHOC_WH", credits_used: 570 },
    ],
    org_spend_daily: orgSpendDaily,
    rate_sheet_daily: rateSheetDaily,
    current_account: [{ account_locator: "DEMO123" }],
  },
};

export default demoDashboardDatasets;
```

- [ ] **Step 5: Run web tests and fix dependent fixtures**

Run: `cd apps/web && npm run typecheck && npx vitest run src/lib/`
`dashboard-api.test.ts` and any test feeding `parseDashboardDatasets` need the new payload shape (reuse `demoDashboardDatasets` or `validV0Payload`).
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/
git commit -m "feat: versioned dashboard contract with metadata and v0 dataset types"
```

---

### Task 10: Transform module core — windowing, clamping, money, conversion tiers

**Files:**
- Create: `apps/web/src/lib/dashboard-transforms.ts`
- Test: `apps/web/src/lib/dashboard-transforms.test.ts`

- [ ] **Step 1: Write the failing tests** (create `apps/web/src/lib/dashboard-transforms.test.ts`)

```ts
import { describe, expect, it } from "vitest";

import type { DashboardMetadata } from "./dashboard-contracts";
import {
  buildRateIndex,
  creditsToDollars,
  formatCurrency,
  storageBytesToDailyDollars,
  throughDateFor,
  windowStartFor,
} from "./dashboard-transforms";

const metadata = (overrides: Partial<DashboardMetadata> = {}): DashboardMetadata => ({
  data_mode: "billed",
  account_locator: "TU24199",
  currency: "USD",
  billing_through_date: "2026-06-08",
  account_usage_through_date: "2026-06-09",
  estimated_credit_price_usd: 3,
  storage_price_usd_per_tb_month: 23,
  unsupported_reason: null,
  organization_usage: { available: true, detail: null },
  account_usage: { available: true, detail: null },
  ...overrides,
});

describe("throughDateFor", () => {
  it("clamps billed and demo modes to the billing-through date", () => {
    expect(throughDateFor(metadata())).toBe("2026-06-08");
    expect(throughDateFor(metadata({ data_mode: "demo" }))).toBe("2026-06-08");
  });

  it("uses account usage freshness in estimated mode", () => {
    expect(
      throughDateFor(
        metadata({
          data_mode: "estimated",
          billing_through_date: null,
          organization_usage: { available: false, detail: null },
        }),
      ),
    ).toBe("2026-06-09");
  });
});

describe("windowStartFor", () => {
  it("returns an inclusive start so the window has exactly N days", () => {
    expect(windowStartFor("2026-06-08", 7)).toBe("2026-06-02");
    expect(windowStartFor("2026-06-08", 30)).toBe("2026-05-10");
    expect(windowStartFor("2026-03-02", 90)).toBe("2025-12-03");
  });
});

describe("creditsToDollars", () => {
  const rates = buildRateIndex([
    {
      usage_date: "2026-06-05",
      service_type: "WAREHOUSE_METERING",
      rating_type: "COMPUTE",
      currency: "USD",
      effective_rate: 2.25,
    },
  ]);

  it("tier 2: uses the matching rate-sheet row", () => {
    expect(
      creditsToDollars(10, "2026-06-05", "WAREHOUSE_METERING", rates, metadata()),
    ).toBe(22.5);
  });

  it("tier 3: falls back to the configured USD credit price", () => {
    expect(
      creditsToDollars(10, "2026-06-06", "WAREHOUSE_METERING", rates, metadata()),
    ).toBe(30);
  });

  it("returns null instead of mixing currencies for non-USD billing", () => {
    expect(
      creditsToDollars(
        10,
        "2026-06-06",
        "WAREHOUSE_METERING",
        rates,
        metadata({ currency: "EUR" }),
      ),
    ).toBeNull();
  });
});

describe("storageBytesToDailyDollars", () => {
  it("converts bytes through TB-month price to a daily dollar figure", () => {
    expect(storageBytesToDailyDollars(2_000_000_000_000, 30)).toBeCloseTo(2.0);
  });
});

describe("formatCurrency", () => {
  it("formats dollars with the active currency", () => {
    expect(formatCurrency(1234.5, "USD")).toBe("$1,234.50");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/web && npx vitest run src/lib/dashboard-transforms.test.ts`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement** (create `apps/web/src/lib/dashboard-transforms.ts`)

```ts
// THE shared pure transform module (spec: Base/Transform Boundary).
// All windowing, summary-building, ranking, dollar-mode selection, and
// chart/table view-model construction lives here. React components only
// render the view models this module returns. Demo, billed, and estimated
// modes all flow through this one code path.
import type {
  DashboardMetadata,
  RateSheetDaily,
} from "./dashboard-contracts";

export type WindowDays = 7 | 30 | 90;
export const WINDOW_OPTIONS: readonly WindowDays[] = [7, 30, 90] as const;
export const DEFAULT_WINDOW_DAYS: WindowDays = 30;

export type SpendBasis = "billed" | "estimated";

export function formatCurrency(value: number, currency: string | null): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency ?? "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatUsageDate(usageDate: string): string {
  const [year, month, day] = usageDate.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

// Billed mode clamps every dollar section to the billing-through date so
// fresher Account Usage rows never leak past billed freshness (spec:
// Freshness Policy — one rule for V0). Estimated mode clamps to Account
// Usage freshness instead.
export function throughDateFor(metadata: DashboardMetadata): string | null {
  if (metadata.data_mode === "estimated") {
    return metadata.account_usage_through_date;
  }
  return metadata.billing_through_date ?? metadata.account_usage_through_date;
}

export function windowStartFor(throughDate: string, windowDays: number): string {
  const [year, month, day] = throughDate.split("-").map(Number);
  const start = new Date(Date.UTC(year, month - 1, day));
  start.setUTCDate(start.getUTCDate() - (windowDays - 1));
  return start.toISOString().slice(0, 10);
}

// ISO yyyy-mm-dd strings order lexicographically, so plain string
// comparison is a correct date comparison here.
export function filterWindow<T extends { usage_date: string }>(
  rows: readonly T[],
  throughDate: string | null,
  windowDays: number,
): T[] {
  if (!throughDate) {
    return [];
  }
  const startDate = windowStartFor(throughDate, windowDays);
  return rows.filter(
    (row) => row.usage_date >= startDate && row.usage_date <= throughDate,
  );
}

export type RateIndex = Map<string, number>;

export function buildRateIndex(rows: readonly RateSheetDaily[]): RateIndex {
  const index: RateIndex = new Map();
  for (const row of rows) {
    index.set(`${row.usage_date}|${row.service_type}`, row.effective_rate);
  }
  return index;
}

// Estimated dollar conversion tiers (spec: Estimated Dollar Conversion):
// 1. matching rate-sheet row; 2. configured USD credit price (USD billing
// currency only); otherwise null — never mix currencies.
export function creditsToDollars(
  credits: number,
  usageDate: string,
  serviceType: string,
  rates: RateIndex,
  metadata: DashboardMetadata,
): number | null {
  const effectiveRate = rates.get(`${usageDate}|${serviceType}`);
  if (effectiveRate !== undefined) {
    return credits * effectiveRate;
  }
  if (metadata.currency === null || metadata.currency === "USD") {
    return credits * metadata.estimated_credit_price_usd;
  }
  return null;
}

export function storageBytesToDailyDollars(
  bytes: number,
  pricePerTbMonth: number,
): number {
  return (bytes / 1_000_000_000_000) * (pricePerTbMonth / 30);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/web && npx vitest run src/lib/dashboard-transforms.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/dashboard-transforms.ts apps/web/src/lib/dashboard-transforms.test.ts
git commit -m "feat: transform core with windowing, clamping, and dollar conversion tiers"
```

---

### Task 11: Transform module — full dashboard view model

**Files:**
- Modify: `apps/web/src/lib/dashboard-transforms.ts`
- Test: `apps/web/src/lib/dashboard-transforms.test.ts`

- [ ] **Step 1: Write the failing tests** (append to `dashboard-transforms.test.ts`)

```ts
import demoDashboardData from "./demo-dashboard-data";
import type { DashboardData } from "./dashboard-contracts";
import { buildDashboardViewModel } from "./dashboard-transforms";

function dataWith(overrides: {
  metadata?: Partial<DashboardMetadata>;
  datasets?: Partial<DashboardData["datasets"]>;
}): DashboardData {
  return {
    ...demoDashboardData,
    metadata: { ...demoDashboardData.metadata, ...overrides.metadata },
    datasets: { ...demoDashboardData.datasets, ...overrides.datasets },
  };
}

describe("buildDashboardViewModel", () => {
  it("renders billed totals from org consumption rows, keeping negative adjustments", () => {
    const vm = buildDashboardViewModel(demoDashboardData, 30);
    expect(vm.header.dataModeLabel).toBe("Demo");
    expect(vm.unsupported).toBeNull();
    expect(vm.totalSpend.basis).toBe("billed");
    expect(vm.totalSpend.dailySeries).toHaveLength(30);
    // Per-day total = sum of ALL consumption rows incl. negative adjustment.
    const day = demoDashboardData.datasets.org_spend_daily.filter(
      (row) => row.usage_date === vm.totalSpend.dailySeries.at(-1)!.date,
    );
    expect(vm.totalSpend.dailySeries.at(-1)!.spend).toBeCloseTo(
      day.reduce((sum, row) => sum + row.spend, 0),
    );
  });

  it("hides account usage rows after the billing-through date in billed mode", () => {
    const vm = buildDashboardViewModel(demoDashboardData, 30);
    const through = demoDashboardData.metadata.billing_through_date!;
    for (const row of vm.detailTables.users) {
      expect(row.name).toBeTruthy();
    }
    // The compute daily series must not extend past billing-through.
    expect(vm.computeSpend.dailySeries.at(-1)!.date <= through).toBe(true);
  });

  it("windows locally without changing underlying data", () => {
    const seven = buildDashboardViewModel(demoDashboardData, 7);
    const ninety = buildDashboardViewModel(demoDashboardData, 90);
    expect(seven.totalSpend.dailySeries).toHaveLength(7);
    expect(ninety.totalSpend.dailySeries).toHaveLength(90);
  });

  it("always projects from the latest 30 days when available", () => {
    const seven = buildDashboardViewModel(demoDashboardData, 7);
    const thirty = buildDashboardViewModel(demoDashboardData, 30);
    expect(seven.totalSpend.projectedMonthlyLabel).toBe(
      thirty.totalSpend.projectedMonthlyLabel,
    );
    expect(seven.totalSpend.projectionBasisLabel).toContain("30-day");
  });

  it("switches to estimated mode when organization usage is unavailable", () => {
    const vm = buildDashboardViewModel(
      dataWith({
        metadata: {
          data_mode: "estimated",
          billing_through_date: null,
          organization_usage: { available: false, detail: "denied" },
        },
        datasets: { org_spend_daily: [], rate_sheet_daily: [] },
      }),
      30,
    );
    expect(vm.header.dataModeLabel).toBe("Estimated");
    expect(vm.header.estimatedRateLabel).toBe(
      "Estimated spend at $3.00/credit — billed data unavailable",
    );
    expect(vm.totalSpend.basis).toBe("estimated");
    expect(vm.totalSpend.dailySeries.length).toBeGreaterThan(0);
  });

  it("ranked warehouses and users are estimated, ordered by spend, with secondary credits", () => {
    const vm = buildDashboardViewModel(demoDashboardData, 30);
    const spends = vm.computeSpend.rankedWarehouses.map((row) => row.spend);
    expect(spends).toEqual([...spends].sort((a, b) => b - a));
    expect(vm.computeSpend.rankedWarehouses[0].credits).not.toBeNull();
    expect(vm.computeSpend.rankedUsers.length).toBeGreaterThan(0);
  });

  it("renders a mixed-currency unsupported state from metadata", () => {
    const vm = buildDashboardViewModel(
      dataWith({ metadata: { unsupported_reason: "mixed_currency", currency: null } }),
      30,
    );
    expect(vm.unsupported).not.toBeNull();
    expect(vm.unsupported!.title).toMatch(/currency/i);
  });

  it("marks sections empty when org usage is accessible but has zero rows", () => {
    const vm = buildDashboardViewModel(
      dataWith({
        datasets: { org_spend_daily: [] },
        metadata: { billing_through_date: null },
      }),
      30,
    );
    expect(vm.totalSpend.basis).toBe("billed");
    expect(vm.totalSpend.isEmpty).toBe(true);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/web && npx vitest run src/lib/dashboard-transforms.test.ts`
Expected: FAIL — `buildDashboardViewModel` not exported

- [ ] **Step 3: Implement** (append to `apps/web/src/lib/dashboard-transforms.ts`; extend the import from `./dashboard-contracts` with `DashboardData`, `OrgSpendDaily`)

```ts
export type DollarPoint = { date: string; spend: number };
export type ServicePoint = Record<string, number | string>;

export type RankedSpendRow = {
  name: string;
  spend: number;
  spendLabel: string;
  credits: number | null;
};

export type HeaderViewModel = {
  dataModeLabel: "Billed" | "Estimated" | "Demo";
  accountLocator: string | null;
  currency: string;
  freshnessLabel: string | null;
  estimatedRateLabel: string | null;
};

export type TotalSpendViewModel = {
  basis: SpendBasis;
  totalLabel: string;
  averageDailyLabel: string;
  projectedMonthlyLabel: string;
  projectionBasisLabel: string;
  dailySeries: DollarPoint[];
  topDriver: { name: string; spendLabel: string } | null;
  isEmpty: boolean;
};

export type ComputeSpendViewModel = {
  computeBasis: SpendBasis;
  dailySeries: DollarPoint[];
  rankedWarehouses: RankedSpendRow[];
  rankedUsers: RankedSpendRow[];
  isEmpty: boolean;
};

export type StorageSpendViewModel = {
  basis: SpendBasis;
  dailySeries: DollarPoint[];
  databases: { name: string; monthlySpendLabel: string | null; bytes: number }[];
  isEmpty: boolean;
};

export type ServiceSpendViewModel = {
  basis: SpendBasis;
  dailySeries: ServicePoint[];
  serviceNames: string[];
  rankedServices: RankedSpendRow[];
  isEmpty: boolean;
};

export type DetailTablesViewModel = {
  services: { name: string; spendLabel: string; credits: number | null }[];
  warehouses: {
    name: string;
    spendLabel: string;
    creditsCompute: number;
    creditsTotal: number;
  }[];
  users: { name: string; spendLabel: string; credits: number }[];
  storage: { name: string; monthlySpendLabel: string | null; bytes: number }[];
};

export type UnsupportedViewModel = { title: string; detail: string };

export type DashboardViewModel = {
  windowDays: WindowDays;
  header: HeaderViewModel;
  unsupported: UnsupportedViewModel | null;
  totalSpend: TotalSpendViewModel;
  computeSpend: ComputeSpendViewModel;
  storageSpend: StorageSpendViewModel;
  serviceSpend: ServiceSpendViewModel;
  detailTables: DetailTablesViewModel;
};

const WAREHOUSE_SERVICE_TYPE = "WAREHOUSE_METERING";

export function buildDashboardViewModel(
  data: DashboardData,
  windowDays: WindowDays,
): DashboardViewModel {
  const metadata = data.metadata;
  const billed = metadata.data_mode !== "estimated";
  const currency = metadata.currency ?? "USD";
  const throughDate = throughDateFor(metadata);
  const header = buildHeader(metadata, billed);

  if (metadata.unsupported_reason === "mixed_currency") {
    return {
      windowDays,
      header,
      unsupported: {
        title: "Mixed currencies are not supported",
        detail:
          "This account's billing data contains more than one currency. " +
          "Greysight V0 supports one currency per dashboard.",
      },
      ...emptySections(currency),
    };
  }

  const rates = buildRateIndex(data.datasets.rate_sheet_daily);
  const billedRows = filterWindow(
    data.datasets.org_spend_daily.filter(
      (row) => row.billing_type === "CONSUMPTION",
    ),
    throughDate,
    windowDays,
  );
  const fullBilledRows = data.datasets.org_spend_daily.filter(
    (row) =>
      row.billing_type === "CONSUMPTION" &&
      (throughDate === null || row.usage_date <= throughDate),
  );
  const serviceCredits = filterWindow(
    data.datasets.service_spend_daily,
    throughDate,
    windowDays,
  );
  const warehouseRows = filterWindow(
    data.datasets.warehouse_spend_daily,
    throughDate,
    windowDays,
  );
  const userRows = filterWindow(
    data.datasets.query_compute_by_user_daily,
    throughDate,
    windowDays,
  );
  const storageRows = filterWindow(
    data.datasets.database_storage_daily,
    throughDate,
    windowDays,
  );

  const convert = (credits: number, usageDate: string, serviceType: string) =>
    creditsToDollars(credits, usageDate, serviceType, rates, metadata) ?? 0;

  // ---- Daily total spend (and full clamped history for projection) ----
  const dailyTotals = billed
    ? sumByDate(billedRows.map((row) => [row.usage_date, row.spend]))
    : sumByDate(
        serviceCredits.map((row) => [
          row.usage_date,
          convert(row.credits_used, row.usage_date, row.service_type),
        ]),
      );
  const fullDailyTotals = billed
    ? sumByDate(fullBilledRows.map((row) => [row.usage_date, row.spend]))
    : sumByDate(
        data.datasets.service_spend_daily
          .filter((row) => throughDate === null || row.usage_date <= throughDate)
          .map((row) => [
            row.usage_date,
            convert(row.credits_used, row.usage_date, row.service_type),
          ]),
      );

  const totalSpend = buildTotalSpend(
    dailyTotals,
    fullDailyTotals,
    billedRows,
    serviceCredits,
    billed,
    currency,
    convert,
  );

  // ---- Compute ----
  const computeDaily = billed
    ? sumByDate(
        billedRows
          .filter((row) => row.rating_type === "COMPUTE")
          .map((row) => [row.usage_date, row.spend]),
      )
    : sumByDate(
        warehouseRows.map((row) => [
          row.usage_date,
          convert(row.credits_used_compute, row.usage_date, WAREHOUSE_SERVICE_TYPE),
        ]),
      );

  const warehouseSpend = new Map<string, { spend: number; compute: number; total: number }>();
  for (const row of warehouseRows) {
    const entry = warehouseSpend.get(row.warehouse_name) ?? {
      spend: 0,
      compute: 0,
      total: 0,
    };
    entry.spend += convert(
      row.credits_used_compute,
      row.usage_date,
      WAREHOUSE_SERVICE_TYPE,
    );
    entry.compute += row.credits_used_compute;
    entry.total += row.credits_used;
    warehouseSpend.set(row.warehouse_name, entry);
  }
  const rankedWarehouses = [...warehouseSpend.entries()]
    .map(([name, entry]) => ({
      name,
      spend: entry.spend,
      spendLabel: formatCurrency(entry.spend, currency),
      credits: entry.compute,
    }))
    .sort((a, b) => b.spend - a.spend || a.name.localeCompare(b.name));

  const userSpend = new Map<string, { spend: number; credits: number }>();
  for (const row of userRows) {
    const entry = userSpend.get(row.user_name) ?? { spend: 0, credits: 0 };
    entry.spend += convert(
      row.credits_attributed_compute,
      row.usage_date,
      WAREHOUSE_SERVICE_TYPE,
    );
    entry.credits += row.credits_attributed_compute;
    userSpend.set(row.user_name, entry);
  }
  const rankedUsers = [...userSpend.entries()]
    .map(([name, entry]) => ({
      name,
      spend: entry.spend,
      spendLabel: formatCurrency(entry.spend, currency),
      credits: entry.credits,
    }))
    .sort((a, b) => b.spend - a.spend || a.name.localeCompare(b.name));

  const computeSpend: ComputeSpendViewModel = {
    computeBasis: billed ? "billed" : "estimated",
    dailySeries: computeDaily,
    rankedWarehouses,
    rankedUsers,
    isEmpty:
      computeDaily.length === 0 &&
      rankedWarehouses.length === 0 &&
      rankedUsers.length === 0,
  };

  // ---- Storage ----
  const latestBytesByDate = sumByDate(
    storageRows.map((row) => [
      row.usage_date,
      row.average_database_bytes + row.average_failsafe_bytes,
    ]),
  );
  const storageDaily = billed
    ? sumByDate(
        billedRows
          .filter((row) => row.rating_type === "STORAGE")
          .map((row) => [row.usage_date, row.spend]),
      )
    : latestBytesByDate.map((point) => ({
        date: point.date,
        spend: storageBytesToDailyDollars(
          point.spend,
          metadata.storage_price_usd_per_tb_month,
        ),
      }));
  const latestStorageDate = storageRows.at(-1)?.usage_date ?? null;
  const databases = storageRows
    .filter((row) => row.usage_date === latestStorageDate)
    .map((row) => {
      const bytes = row.average_database_bytes + row.average_failsafe_bytes;
      const monthly =
        (bytes / 1_000_000_000_000) * metadata.storage_price_usd_per_tb_month;
      return {
        name: row.database_name ?? "Unknown",
        monthlySpendLabel: formatCurrency(monthly, currency),
        bytes,
      };
    })
    .sort((a, b) => b.bytes - a.bytes);

  const storageSpend: StorageSpendViewModel = {
    basis: billed ? "billed" : "estimated",
    dailySeries: storageDaily,
    databases,
    isEmpty: storageDaily.length === 0 && databases.length === 0,
  };

  // ---- Services ----
  const serviceTotals = new Map<string, number>();
  const servicePoints = new Map<string, ServicePoint>();
  const serviceRows: Array<[string, string, number]> = billed
    ? billedRows.map((row) => [row.usage_date, row.service_type, row.spend])
    : serviceCredits.map((row) => [
        row.usage_date,
        row.service_type,
        convert(row.credits_used, row.usage_date, row.service_type),
      ]);
  for (const [usageDate, serviceType, spend] of serviceRows) {
    serviceTotals.set(serviceType, (serviceTotals.get(serviceType) ?? 0) + spend);
    const point = servicePoints.get(usageDate) ?? { date: usageDate };
    point[serviceType] = Number(point[serviceType] ?? 0) + spend;
    servicePoints.set(usageDate, point);
  }
  const serviceNames = [...serviceTotals.keys()].sort(
    (a, b) => (serviceTotals.get(b) ?? 0) - (serviceTotals.get(a) ?? 0),
  );
  const creditsByService = new Map<string, number>();
  for (const row of serviceCredits) {
    creditsByService.set(
      row.service_type,
      (creditsByService.get(row.service_type) ?? 0) + row.credits_used,
    );
  }
  const rankedServices = serviceNames.map((name) => ({
    name,
    spend: serviceTotals.get(name) ?? 0,
    spendLabel: formatCurrency(serviceTotals.get(name) ?? 0, currency),
    credits: creditsByService.get(name) ?? null,
  }));

  const serviceSpend: ServiceSpendViewModel = {
    basis: billed ? "billed" : "estimated",
    dailySeries: [...servicePoints.values()].sort((a, b) =>
      String(a.date).localeCompare(String(b.date)),
    ),
    serviceNames,
    rankedServices,
    isEmpty: rankedServices.length === 0,
  };

  return {
    windowDays,
    header,
    unsupported: null,
    totalSpend,
    computeSpend,
    storageSpend,
    serviceSpend,
    detailTables: {
      services: rankedServices.map(({ name, spendLabel, credits }) => ({
        name,
        spendLabel,
        credits,
      })),
      warehouses: [...warehouseSpend.entries()]
        .map(([name, entry]) => ({
          name,
          spendLabel: formatCurrency(entry.spend, currency),
          creditsCompute: entry.compute,
          creditsTotal: entry.total,
        }))
        .sort((a, b) => b.creditsCompute - a.creditsCompute),
      users: rankedUsers.map(({ name, spendLabel, credits }) => ({
        name,
        spendLabel,
        credits: credits ?? 0,
      })),
      storage: databases,
    },
  };
}

function buildHeader(
  metadata: DashboardMetadata,
  billed: boolean,
): HeaderViewModel {
  const dataModeLabel =
    metadata.data_mode === "demo" ? "Demo" : billed ? "Billed" : "Estimated";
  const freshnessLabel = billed
    ? metadata.billing_through_date
      ? `Billing data through ${formatUsageDate(metadata.billing_through_date)}`
      : null
    : metadata.account_usage_through_date
      ? `Account Usage data through ${formatUsageDate(metadata.account_usage_through_date)}`
      : null;
  const estimatedRateLabel = billed
    ? null
    : `Estimated spend at ${formatCurrency(
        metadata.estimated_credit_price_usd,
        "USD",
      )}/credit — billed data unavailable`;
  return {
    dataModeLabel,
    accountLocator: metadata.account_locator,
    currency: metadata.currency ?? "USD",
    freshnessLabel,
    estimatedRateLabel,
  };
}

function buildTotalSpend(
  dailySeries: DollarPoint[],
  fullDailySeries: DollarPoint[],
  billedRows: OrgSpendDaily[],
  serviceCredits: { usage_date: string; service_type: string; credits_used: number }[],
  billed: boolean,
  currency: string,
  convert: (credits: number, usageDate: string, serviceType: string) => number,
): TotalSpendViewModel {
  const total = dailySeries.reduce((sum, point) => sum + point.spend, 0);
  const averageDaily = dailySeries.length > 0 ? total / dailySeries.length : 0;

  // Projection rule (spec: Windowing And Filters): always base projected
  // monthly spend on the latest available 30 days, even when the visible
  // filter is 7 days; label by basis when fewer than 30 days exist.
  const projectionPoints = fullDailySeries.slice(-30);
  const basisDays = projectionPoints.length;
  const projected =
    basisDays > 0
      ? (projectionPoints.reduce((sum, point) => sum + point.spend, 0) /
          basisDays) *
        30
      : 0;

  const driverTotals = new Map<string, number>();
  if (billed) {
    for (const row of billedRows) {
      driverTotals.set(
        row.service_type,
        (driverTotals.get(row.service_type) ?? 0) + row.spend,
      );
    }
  } else {
    for (const row of serviceCredits) {
      driverTotals.set(
        row.service_type,
        (driverTotals.get(row.service_type) ?? 0) +
          convert(row.credits_used, row.usage_date, row.service_type),
      );
    }
  }
  const topDriverEntry = [...driverTotals.entries()].sort(
    (a, b) => b[1] - a[1],
  )[0];

  return {
    basis: billed ? "billed" : "estimated",
    totalLabel: formatCurrency(total, currency),
    averageDailyLabel: formatCurrency(averageDaily, currency),
    projectedMonthlyLabel: formatCurrency(projected, currency),
    projectionBasisLabel:
      basisDays >= 30 ? "30-day basis" : `${basisDays}-day basis`,
    dailySeries,
    topDriver: topDriverEntry
      ? {
          name: topDriverEntry[0],
          spendLabel: formatCurrency(topDriverEntry[1], currency),
        }
      : null,
    isEmpty: dailySeries.length === 0,
  };
}

function sumByDate(pairs: Array<[string, number]>): DollarPoint[] {
  const totals = new Map<string, number>();
  for (const [usageDate, spend] of pairs) {
    totals.set(usageDate, (totals.get(usageDate) ?? 0) + spend);
  }
  return [...totals.entries()]
    .map(([date, spend]) => ({ date, spend }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

function emptySections(currency: string): Omit<
  DashboardViewModel,
  "windowDays" | "header" | "unsupported"
> {
  const empty = {
    basis: "billed" as const,
    dailySeries: [] as DollarPoint[],
    isEmpty: true,
  };
  return {
    totalSpend: {
      ...empty,
      totalLabel: formatCurrency(0, currency),
      averageDailyLabel: formatCurrency(0, currency),
      projectedMonthlyLabel: formatCurrency(0, currency),
      projectionBasisLabel: "0-day basis",
      topDriver: null,
    },
    computeSpend: {
      computeBasis: "billed",
      dailySeries: [],
      rankedWarehouses: [],
      rankedUsers: [],
      isEmpty: true,
    },
    storageSpend: { ...empty, databases: [] },
    serviceSpend: {
      ...empty,
      serviceNames: [],
      rankedServices: [],
    },
    detailTables: { services: [], warehouses: [], users: [], storage: [] },
  };
}
```

Note: `storageRows.at(-1)` relies on `filterWindow` preserving the API's date-sorted row order; the registry SQL orders by `usage_date`, and demo fixtures are generated in date order.

- [ ] **Step 4: Run tests + typecheck**

Run: `cd apps/web && npx vitest run src/lib/dashboard-transforms.test.ts && npm run typecheck`
Expected: PASS. If the demo fixture from Task 9 doesn't satisfy a test (e.g. fewer than 90 days), extend the fixture, not the transform.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/
git commit -m "feat: dashboard view-model transforms for billed and estimated dollars"
```

---

## Phase 3 — Dashboard UI

### Task 12: Header, filter bar, and empty-state components

**Files:**
- Create: `apps/web/src/components/dashboard/dashboard-header.tsx`, `apps/web/src/components/dashboard/filter-bar.tsx`, `apps/web/src/components/dashboard/section-empty-state.tsx`
- Test: `apps/web/src/components/dashboard/dashboard-header.test.tsx`, `apps/web/src/components/dashboard/filter-bar.test.tsx`

- [ ] **Step 1: Write the failing tests**

`dashboard-header.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DashboardHeader from "./dashboard-header";

const headerViewModel = {
  dataModeLabel: "Billed" as const,
  accountLocator: "TU24199",
  currency: "USD",
  freshnessLabel: "Billing data through Jun 8, 2026",
  estimatedRateLabel: null,
};

describe("DashboardHeader", () => {
  it("shows product, mode, data mode, account, and freshness", () => {
    render(
      <DashboardHeader
        header={headerViewModel}
        modeLabel="Local Snowflake"
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );
    expect(screen.getByText("Greysight")).toBeInTheDocument();
    expect(screen.getByText("Local Snowflake")).toBeInTheDocument();
    expect(screen.getByText("Billed")).toBeInTheDocument();
    expect(screen.getByText("TU24199")).toBeInTheDocument();
    expect(
      screen.getByText("Billing data through Jun 8, 2026"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run analysis" }),
    ).toBeInTheDocument();
  });

  it("shows the estimated-rate assumption in estimated mode", () => {
    render(
      <DashboardHeader
        header={{
          ...headerViewModel,
          dataModeLabel: "Estimated",
          freshnessLabel: "Account Usage data through Jun 9, 2026",
          estimatedRateLabel:
            "Estimated spend at $3.00/credit — billed data unavailable",
        }}
        modeLabel="Local Snowflake"
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Estimated spend at $3.00/credit — billed data unavailable"),
    ).toBeInTheDocument();
  });
});
```

`filter-bar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import FilterBar from "./filter-bar";

describe("FilterBar", () => {
  it("renders 7/30/90 options, currency, and reports local changes", async () => {
    const onWindowChange = vi.fn();
    render(
      <FilterBar windowDays={30} currency="USD" onWindowChange={onWindowChange} />,
    );
    expect(screen.getByText("USD")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "90 days" }));
    expect(onWindowChange).toHaveBeenCalledWith(90);
  });

  it("marks the active window", () => {
    render(<FilterBar windowDays={7} currency="USD" onWindowChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
```

If `@testing-library/user-event` is not installed, use `fireEvent.click` from `@testing-library/react` instead — follow existing component tests' idiom.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/web && npx vitest run src/components/dashboard/`
Expected: FAIL — components do not exist

- [ ] **Step 3: Implement the components**

`dashboard-header.tsx`:

```tsx
"use client";

import { Badge } from "@tremor/react";

import type { HeaderViewModel } from "../../lib/dashboard-transforms";

type DashboardHeaderProps = {
  header: HeaderViewModel | null;
  modeLabel: "Demo" | "Local Snowflake" | "Authenticated Snowflake";
  runDisabled: boolean;
  onRun: () => void;
};

export default function DashboardHeader({
  header,
  modeLabel,
  runDisabled,
  onRun,
}: DashboardHeaderProps) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold text-slate-950">Greysight</h1>
        <Badge color="slate">{modeLabel}</Badge>
        {header ? (
          <>
            <Badge color={header.dataModeLabel === "Estimated" ? "amber" : "blue"}>
              {header.dataModeLabel}
            </Badge>
            {header.accountLocator ? (
              <span className="font-mono text-xs text-slate-500">
                {header.accountLocator}
              </span>
            ) : null}
            {header.freshnessLabel ? (
              <span className="text-xs text-slate-500">{header.freshnessLabel}</span>
            ) : null}
          </>
        ) : null}
      </div>
      <div className="flex items-center gap-3">
        {header?.estimatedRateLabel ? (
          <span className="text-xs font-medium text-amber-700">
            {header.estimatedRateLabel}
          </span>
        ) : null}
        <button
          className="h-9 rounded-md bg-slate-950 px-3 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
          disabled={runDisabled}
          type="button"
          onClick={onRun}
        >
          Run analysis
        </button>
      </div>
    </header>
  );
}
```

`filter-bar.tsx`:

```tsx
"use client";

import type { WindowDays } from "../../lib/dashboard-transforms";
import { WINDOW_OPTIONS } from "../../lib/dashboard-transforms";

type FilterBarProps = {
  windowDays: WindowDays;
  currency: string;
  onWindowChange: (windowDays: WindowDays) => void;
};

export default function FilterBar({
  windowDays,
  currency,
  onWindowChange,
}: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="inline-flex rounded-md border border-slate-200 bg-white p-0.5">
        {WINDOW_OPTIONS.map((option) => (
          <button
            key={option}
            aria-pressed={option === windowDays}
            className={
              option === windowDays
                ? "rounded bg-slate-950 px-3 py-1 text-xs font-semibold text-white"
                : "rounded px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
            }
            type="button"
            onClick={() => onWindowChange(option)}
          >
            {option} days
          </button>
        ))}
      </div>
      <span className="text-xs font-medium text-slate-500">{currency}</span>
    </div>
  );
}
```

`section-empty-state.tsx`:

```tsx
type SectionEmptyStateProps = {
  message: string;
};

export default function SectionEmptyState({ message }: SectionEmptyStateProps) {
  return (
    <div className="flex min-h-32 items-center justify-center rounded-md border border-dashed border-slate-200 bg-slate-50">
      <p className="text-sm text-slate-500">{message}</p>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/web && npx vitest run src/components/dashboard/ && npm run typecheck`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/dashboard/
git commit -m "feat: dashboard header, filter bar, and section empty state"
```

---

### Task 13: Spend sections and detail tables

**Files:**
- Create: `apps/web/src/components/dashboard/spend-sections.tsx`, `apps/web/src/components/dashboard/detail-tables.tsx`
- Test: `apps/web/src/components/dashboard/spend-sections.test.tsx`

- [ ] **Step 1: Write the failing tests** (create `spend-sections.test.tsx`)

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import demoDashboardData from "../../lib/demo-dashboard-data";
import { buildDashboardViewModel } from "../../lib/dashboard-transforms";
import {
  ComputeSpendSection,
  ServiceSpendSection,
  StorageSpendSection,
  TotalSpendSection,
} from "./spend-sections";

const viewModel = buildDashboardViewModel(demoDashboardData, 30);

describe("spend sections", () => {
  it("renders total spend dollars with projection basis", () => {
    render(<TotalSpendSection viewModel={viewModel.totalSpend} />);
    expect(screen.getByText("Total spend")).toBeInTheDocument();
    expect(screen.getByText(viewModel.totalSpend.totalLabel)).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(viewModel.totalSpend.projectionBasisLabel)),
    ).toBeInTheDocument();
  });

  it("labels estimated warehouse and user rankings", () => {
    render(<ComputeSpendSection viewModel={viewModel.computeSpend} />);
    expect(screen.getByText("Compute spend")).toBeInTheDocument();
    expect(screen.getAllByText(/Estimated/).length).toBeGreaterThan(0);
    expect(
      screen.getByText(viewModel.computeSpend.rankedWarehouses[0].name),
    ).toBeInTheDocument();
  });

  it("renders a storage empty state when storage data is missing", () => {
    render(
      <StorageSpendSection
        viewModel={{ basis: "billed", dailySeries: [], databases: [], isEmpty: true }}
      />,
    );
    expect(screen.getByText("No storage spend data")).toBeInTheDocument();
  });

  it("renders ranked services", () => {
    render(<ServiceSpendSection viewModel={viewModel.serviceSpend} />);
    expect(screen.getByText("Service spend")).toBeInTheDocument();
    expect(
      screen.getByText(viewModel.serviceSpend.rankedServices[0].name),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/web && npx vitest run src/components/dashboard/spend-sections.test.tsx`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement** `spend-sections.tsx` (one file, four section components sharing small helpers — keeps cohesion; split if it exceeds ~400 lines)

```tsx
"use client";

import { BarChart, Card, LineChart, Metric, Text, Title } from "@tremor/react";

import type {
  ComputeSpendViewModel,
  RankedSpendRow,
  ServiceSpendViewModel,
  StorageSpendViewModel,
  TotalSpendViewModel,
} from "../../lib/dashboard-transforms";
import SectionEmptyState from "./section-empty-state";

function EstimatedBadge() {
  return (
    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-800">
      Estimated
    </span>
  );
}

function RankedBars({ rows, max }: { rows: RankedSpendRow[]; max?: number }) {
  const shown = rows.slice(0, max ?? 8);
  const top = shown[0]?.spend || 1;
  return (
    <ul className="mt-3 grid gap-1.5">
      {shown.map((row) => (
        <li key={row.name} className="grid grid-cols-[8rem_1fr_auto] items-center gap-2">
          <span className="truncate text-xs text-slate-600">{row.name}</span>
          <span className="h-2 rounded bg-slate-200">
            <span
              className="block h-2 rounded bg-blue-600"
              style={{ width: `${Math.max(0, (row.spend / top) * 100)}%` }}
            />
          </span>
          <span className="text-xs font-semibold tabular-nums text-slate-900">
            {row.spendLabel}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function TotalSpendSection({ viewModel }: { viewModel: TotalSpendViewModel }) {
  return (
    <section aria-label="Total spend" className="grid gap-3">
      <Title>Total spend</Title>
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No total spend data" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-[18rem_1fr]">
          <div className="grid gap-3">
            <Card className="p-4">
              <Text>Total spend {viewModel.basis === "estimated" ? <EstimatedBadge /> : null}</Text>
              <Metric>{viewModel.totalLabel}</Metric>
            </Card>
            <Card className="p-4">
              <Text>Average daily</Text>
              <Metric className="text-xl">{viewModel.averageDailyLabel}</Metric>
            </Card>
            <Card className="p-4">
              <Text>Projected monthly ({viewModel.projectionBasisLabel})</Text>
              <Metric className="text-xl">{viewModel.projectedMonthlyLabel}</Metric>
            </Card>
            {viewModel.topDriver ? (
              <Card className="p-4">
                <Text>Top driver</Text>
                <p className="text-sm font-semibold text-slate-900">
                  {viewModel.topDriver.name} · {viewModel.topDriver.spendLabel}
                </p>
              </Card>
            ) : null}
          </div>
          <Card className="p-4">
            <Text>Daily spend</Text>
            <LineChart
              className="mt-2 h-44"
              data={viewModel.dailySeries}
              index="date"
              categories={["spend"]}
              colors={["blue"]}
              yAxisWidth={56}
            />
          </Card>
        </div>
      )}
    </section>
  );
}

export function ComputeSpendSection({ viewModel }: { viewModel: ComputeSpendViewModel }) {
  return (
    <section aria-label="Compute spend" className="grid gap-3">
      <Title>Compute spend</Title>
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No compute spend data" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-3">
          <Card className="p-4 lg:col-span-1">
            <Text>
              Daily compute{" "}
              {viewModel.computeBasis === "estimated" ? <EstimatedBadge /> : null}
            </Text>
            <LineChart
              className="mt-2 h-40"
              data={viewModel.dailySeries}
              index="date"
              categories={["spend"]}
              colors={["blue"]}
              yAxisWidth={56}
            />
          </Card>
          <Card className="p-4">
            <Text>
              Warehouses <EstimatedBadge />
            </Text>
            <RankedBars rows={viewModel.rankedWarehouses} />
          </Card>
          <Card className="p-4">
            <Text>
              Users <EstimatedBadge />
            </Text>
            <RankedBars rows={viewModel.rankedUsers} />
          </Card>
        </div>
      )}
    </section>
  );
}

export function StorageSpendSection({ viewModel }: { viewModel: StorageSpendViewModel }) {
  return (
    <section aria-label="Storage spend" className="grid gap-3">
      <Title>Storage spend</Title>
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No storage spend data" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <Card className="p-4">
            <Text>
              Daily storage{" "}
              {viewModel.basis === "estimated" ? <EstimatedBadge /> : null}
            </Text>
            <LineChart
              className="mt-2 h-40"
              data={viewModel.dailySeries}
              index="date"
              categories={["spend"]}
              colors={["emerald"]}
              yAxisWidth={56}
            />
          </Card>
          <Card className="p-4">
            <Text>Latest storage by database (est. monthly)</Text>
            <RankedBars
              rows={viewModel.databases.map((database) => ({
                name: database.name,
                spend: database.bytes,
                spendLabel: database.monthlySpendLabel ?? "—",
                credits: null,
              }))}
            />
          </Card>
        </div>
      )}
    </section>
  );
}

export function ServiceSpendSection({ viewModel }: { viewModel: ServiceSpendViewModel }) {
  return (
    <section aria-label="Service spend" className="grid gap-3">
      <Title>Service spend</Title>
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No service spend data" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <Card className="p-4">
            <Text>
              Daily by service{" "}
              {viewModel.basis === "estimated" ? <EstimatedBadge /> : null}
            </Text>
            <BarChart
              className="mt-2 h-44"
              data={viewModel.dailySeries}
              index="date"
              categories={viewModel.serviceNames}
              stack
              yAxisWidth={56}
            />
          </Card>
          <Card className="p-4">
            <Text>Ranked services</Text>
            <RankedBars rows={viewModel.rankedServices} />
          </Card>
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Implement** `detail-tables.tsx`

```tsx
"use client";

import {
  Card,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  Text,
} from "@tremor/react";

import type { DetailTablesViewModel } from "../../lib/dashboard-transforms";

const MAX_ROWS = 50;

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
}

type DetailTableProps = {
  title: string;
  headers: string[];
  rows: Array<Array<string | number>>;
};

function DetailTable({ title, headers, rows }: DetailTableProps) {
  return (
    <Card className="p-4">
      <Text>{title}</Text>
      <div className="mt-2 max-h-72 overflow-y-auto">
        <Table>
          <TableHead>
            <TableRow>
              {headers.map((header) => (
                <TableHeaderCell key={header}>{header}</TableHeaderCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.slice(0, MAX_ROWS).map((row, index) => (
              <TableRow key={`${String(row[0])}-${index}`}>
                {row.map((cell, cellIndex) => (
                  <TableCell key={cellIndex} className="py-1.5 text-xs">
                    {typeof cell === "number" ? formatNumber(cell) : cell}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Card>
  );
}

export default function DetailTables({
  viewModel,
}: {
  viewModel: DetailTablesViewModel;
}) {
  return (
    <section aria-label="Detail tables" className="grid gap-3 lg:grid-cols-2">
      <DetailTable
        title="Service spend"
        headers={["Service", "Spend", "Credits"]}
        rows={viewModel.services.map((row) => [
          row.name,
          row.spendLabel,
          row.credits ?? 0,
        ])}
      />
      <DetailTable
        title="Warehouse spend"
        headers={["Warehouse", "Est. spend", "Compute credits", "Total credits"]}
        rows={viewModel.warehouses.map((row) => [
          row.name,
          row.spendLabel,
          row.creditsCompute,
          row.creditsTotal,
        ])}
      />
      <DetailTable
        title="User compute spend"
        headers={["User", "Est. spend", "Credits"]}
        rows={viewModel.users.map((row) => [row.name, row.spendLabel, row.credits])}
      />
      <DetailTable
        title="Storage by database"
        headers={["Database", "Est. monthly spend", "Bytes"]}
        rows={viewModel.storage.map((row) => [
          row.name,
          row.monthlySpendLabel ?? "—",
          row.bytes,
        ])}
      />
    </section>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/web && npx vitest run src/components/dashboard/ && npm run typecheck`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/dashboard/
git commit -m "feat: dollar spend sections and dense detail tables"
```

---

### Task 14: Wire the dashboard — local windowing, states, mode labels

**Files:**
- Rewrite: `apps/web/src/components/dashboard/cost-dashboard.tsx`
- Modify: `apps/web/src/components/dashboard/dashboard-runtime-shell.tsx`
- Test: `apps/web/src/components/dashboard/cost-dashboard.test.tsx` (update), `apps/web/src/components/dashboard/dashboard-runtime-shell.test.tsx` (update)

- [ ] **Step 1: Write/extend the failing tests** (add to `cost-dashboard.test.tsx`). The file already mocks `fetch` for the demo endpoints — if its existing helper differs from `mockDemoFetch` below, use the file's helper; the assertions are what matter.

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import demoDashboardData from "../../lib/demo-dashboard-data";
import CostDashboard from "./cost-dashboard";

function mockDemoFetch(payload: unknown = demoDashboardData) {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CostDashboard v0", () => {
  it("changes the local window without another Snowflake round trip", async () => {
    const fetchMock = mockDemoFetch();
    render(<CostDashboard demoMode />);
    await screen.findByText("Total spend");
    const callsAfterLoad = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "7 days" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    expect(fetchMock.mock.calls.length).toBe(callsAfterLoad);
  });

  it("shows billed freshness and account locator in the header", async () => {
    mockDemoFetch();
    render(<CostDashboard demoMode />);
    expect(
      await screen.findByText("Billing data through Jun 8, 2026"),
    ).toBeInTheDocument();
    expect(screen.getByText("DEMO123")).toBeInTheDocument();
  });

  it("renders the mixed-currency unsupported state from metadata", async () => {
    mockDemoFetch({
      ...demoDashboardData,
      metadata: {
        ...demoDashboardData.metadata,
        unsupported_reason: "mixed_currency",
        currency: null,
      },
    });
    render(<CostDashboard demoMode />);
    expect(
      await screen.findByText(/Mixed currencies are not supported/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Total spend")).not.toBeInTheDocument();
  });

  it("disables the run action and shows placeholders while loading", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => undefined)),
    );
    render(<CostDashboard demoMode />);
    expect(screen.getByRole("button", { name: "Run analysis" })).toBeDisabled();
    expect(screen.getByLabelText("Loading dashboard")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/web && npx vitest run src/components/dashboard/cost-dashboard.test.tsx`
Expected: FAIL

- [ ] **Step 3: Rewrite** `cost-dashboard.tsx`

Keep the existing load-state machine (`loadDemoRun`, `loadSnowflakeRun`, `startRun`, initial-demo `useEffect`, `runDisabled`) exactly as is, with these changes:

```tsx
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchDashboardDatasets,
  fetchDemoDashboardDatasets,
  pollDashboardRun,
  startDashboardRun,
} from "../../lib/dashboard-api";
import type { DashboardData, DashboardRunStatus } from "../../lib/dashboard-contracts";
import { FETCH_WINDOW_DAYS } from "../../lib/dashboard-contracts";
import {
  DEFAULT_WINDOW_DAYS,
  buildDashboardViewModel,
  type WindowDays,
} from "../../lib/dashboard-transforms";
import DashboardHeader from "./dashboard-header";
import DetailTables from "./detail-tables";
import FilterBar from "./filter-bar";
import RunStatus from "./run-status";
import SectionEmptyState from "./section-empty-state";
import {
  ComputeSpendSection,
  ServiceSpendSection,
  StorageSpendSection,
  TotalSpendSection,
} from "./spend-sections";

export type CostDashboardRuntime = {
  accessToken: string | null;
  organizationId: string;
  organizationName: string;
};

export type DashboardModeLabel = "Demo" | "Local Snowflake" | "Authenticated Snowflake";

type CostDashboardProps = {
  data?: DashboardData;
  demoMode?: boolean;
  runtime?: CostDashboardRuntime | null;
  modeLabel?: DashboardModeLabel;
};
```

Inside the component: `const [windowDays, setWindowDays] = useState<WindowDays>(DEFAULT_WINDOW_DAYS);`, replace the `windowDays = 30` prop with `modeLabel` (default `"Demo"` when `shouldUseDemo`, else `"Local Snowflake"`), pass `windowDays: FETCH_WINDOW_DAYS` to `startDashboardRun` (the API fetch window — local filters never re-query), and build the view model:

```tsx
  const dashboardData = loadState.data ?? data;
  const viewModel = useMemo(
    () => (dashboardData ? buildDashboardViewModel(dashboardData, windowDays) : null),
    [dashboardData, windowDays],
  );
```

Render:

```tsx
  return (
    <main className="min-h-screen bg-slate-50">
      <DashboardHeader
        header={viewModel?.header ?? null}
        modeLabel={modeLabel ?? (shouldUseDemo ? "Demo" : "Local Snowflake")}
        runDisabled={runDisabled}
        onRun={() => {
          void startRun();
        }}
      />
      <RunStatus status={loadState.status} message={loadState.message} />
      <div className="mx-auto grid w-full max-w-7xl gap-5 px-4 py-4">
        {viewModel ? (
          viewModel.unsupported ? (
            <SectionEmptyState
              message={`${viewModel.unsupported.title}. ${viewModel.unsupported.detail}`}
            />
          ) : (
            <>
              <FilterBar
                windowDays={windowDays}
                currency={viewModel.header.currency}
                onWindowChange={setWindowDays}
              />
              <TotalSpendSection viewModel={viewModel.totalSpend} />
              <ComputeSpendSection viewModel={viewModel.computeSpend} />
              <StorageSpendSection viewModel={viewModel.storageSpend} />
              <ServiceSpendSection viewModel={viewModel.serviceSpend} />
              <DetailTables viewModel={viewModel.detailTables} />
            </>
          )
        ) : (
          <section
            aria-label="Loading dashboard"
            className="grid min-h-96 gap-4"
          >
            {[0, 1, 2].map((placeholder) => (
              <div
                key={placeholder}
                className="h-40 animate-pulse rounded-lg border border-slate-200 bg-white"
              />
            ))}
          </section>
        )}
      </div>
    </main>
  );
```

Delete the old `DashboardSections` and `formatNumber` from this file. Authenticated billing data stays in component memory only (`loadState`) — no persistence (spec: Browser Analytics / Security).

- [ ] **Step 4: Update** `dashboard-runtime-shell.tsx` to pass the mode label

```tsx
  const modeLabel =
    authRequired ? "Authenticated Snowflake"
    : dataSource === "snowflake" ? "Local Snowflake"
    : "Demo";
```

and pass `modeLabel={modeLabel}` to `<CostDashboard …/>`. Update its tests: local snowflake mode must show `Local Snowflake` (the spec forbids labeling it `Demo mode`).

- [ ] **Step 5: Run the full web suite, fix remaining assertions**

Run: `cd apps/web && npm run test && npm run typecheck && npm run lint`
`page.test.tsx`, `page.auth-mode.test.tsx`, and `dashboard-runtime-shell.integration.test.tsx` will need updated expectations (new header text, new fetch payload shape from Task 9's fixture). Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/
git commit -m "feat: dollar dashboard with local 7/30/90 windowing and mode-aware header"
```

---

## Phase 4 — Docs and live verification

### Task 15: Documentation and environment examples

**Files:**
- Modify: `docs/snowflake-setup.md`, `.env.example`

- [ ] **Step 1: Document Organization Usage grants** (append a section to `docs/snowflake-setup.md`)

````markdown
## Organization Usage access (billed dollars)

Greysight V0 reads billed spend from `SNOWFLAKE.ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY`
and effective rates from `SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY`. These views
require an Organization Usage grant on the Greysight role:

```sql
grant database role SNOWFLAKE.ORGANIZATION_BILLING_VIEWER to role <GREYSIGHT_ROLE>;
```

Notes:

- The account locator used to filter Organization Usage rows is derived at run
  time via `select current_account()` — no extra environment variable is needed.
- If the grant is missing, Greysight degrades to estimated dollars computed from
  Account Usage credits and `ESTIMATED_CREDIT_PRICE_USD`; setup validation does
  not require Organization Usage access.
- Verified populated in the Greybeam dev account `GOPGUKF-JO19546`
  (locator `TU24199`) on 2026-06-10.
````

- [ ] **Step 2: Add the new env var to `.env.example`** (next to `STORAGE_PRICE_USD_PER_TB_MONTH`)

```bash
# USD price per credit used for estimated dollar conversion when
# Organization Usage billing/rate data is unavailable.
ESTIMATED_CREDIT_PRICE_USD=3.00
```

- [ ] **Step 3: Commit**

```bash
git add docs/snowflake-setup.md .env.example
git commit -m "docs: organization usage grants and estimated credit price setting"
```

---

### Task 16: Manual live verification (not hermetic CI)

**Files:** none (checklist; record results in the PR description)

- [ ] **Step 1: Run the stack against live Snowflake**

```bash
npm run dev   # .env already has DATA_SOURCE=snowflake for the dev account
```

Open `http://localhost:3000/dashboard`, click `Run analysis`.

- [ ] **Step 2: Verify billed mode end to end**

- Header shows `Local Snowflake`, `Billed`, locator `TU24199`, and `Billing data through <date>`.
- Total/Compute/Storage/Service sections show dollars; negative included-cloud-services rows are reflected (daily totals can dip), not hidden.
- 7/30/90 switches re-render instantly with no new API call (check the network tab).
- Detail tables show credits only as secondary columns.

- [ ] **Step 3: Invoice month reconciliation (spec: Billing Row Inclusion)**

Pick one known invoice month and compare the dashboard's Organization Usage
window total (filter `billing_type = 'CONSUMPTION'`, all `is_adjustment` values,
locator `TU24199`) against Snowflake's billing page for that month. Document the
match (or any delta with explanation) in the PR.

- [ ] **Step 4: Verify estimated-mode degradation**

Temporarily point `SNOWFLAKE_ROLE` at a role without the Organization Usage
grant (or revoke in a sandbox), re-run: the dashboard must render estimated
dollars with the `Estimated spend at $3.00/credit — billed data unavailable`
banner instead of failing; restore the role afterwards.

- [ ] **Step 5: Verify demo mode parity**

```bash
DATA_SOURCE=demo AUTH_REQUIRED=false npm run dev
```

Demo dashboard renders the same layout/sections with the 90-day filter showing
meaningful data and the header labeled `Demo`.

---

## Self-review checklist (for the plan executor, after the final task)

- `npm run test && npm run lint && npm run typecheck` green at repo root.
- Grep the diff for the word `billed` in UI copy: it must appear only on Organization-Usage-sourced figures.
- Grep components for windowing/ranking/conversion logic: none allowed outside `dashboard-transforms.ts`.
- No Organization Usage SQL added to `_validation_queries()` in `snowflake_client.py`.
- No access tokens or billing data written to any browser storage API.

