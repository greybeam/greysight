# Dashboard Backend View Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move dashboard analytics from frontend TypeScript transforms into FastAPI prepared dashboard views while preserving current billed, estimated, demo, projection, and filtering behavior.

**Architecture:** The existing Snowflake source registry remains the only Snowflake execution path. FastAPI stores bounded source datasets per run, builds render-ready dashboard views for relative and in-bounds custom ranges, and records view retrieval audit events. The frontend fetches prepared views, caches them by `(run_id, range)`, prefetches 7/30/90 views, and renders without Snowflake billing semantics.

**Tech Stack:** FastAPI + Pydantic + pytest (`apps/api`), Next.js + React + TypeScript + Vitest (`apps/web`), existing registry-approved Snowflake SQL in `sql/`.

**Spec:** `docs/superpowers/specs/2026-06-11-dashboard-backend-view-builder-design.md`

---

## Subagent Execution Notes

- Execute tasks sequentially. Later tasks depend on names and contracts introduced by earlier tasks.
- Give each implementer only the current task, this plan, and the design spec.
- After each task, run the task's targeted verification, commit, then dispatch a spec-compliance reviewer and a code-quality reviewer before continuing.
- Do not start slice 2 explicit Snowflake source date binds in this plan.
- Do not introduce DuckDB, browser DuckDB, IndexedDB, or Supabase persistence in this plan.

## File Structure

| File | Action | Responsibility |
| --- | --- | --- |
| `apps/api/app/services/dashboard_view_models.py` | Create | Pydantic models for prepared dashboard views and range metadata. |
| `apps/api/app/services/dashboard_view_builder.py` | Create | Pure backend builder for range resolution, projection, conversion, ranking, caps, labels, and prepared views. |
| `apps/api/tests/test_dashboard_view_builder.py` | Create | Unit tests for backend view semantics ported from `dashboard-transforms.ts`. |
| `apps/api/app/routes/dashboard_runs.py` | Modify | Store source bounds, expose `/view`, expose `/demo/view`, and record view audit events. |
| `apps/api/tests/test_demo_dashboard_run.py` | Modify | Route tests for demo prepared views and persisted source bounds. |
| `apps/api/tests/test_snowflake_dashboard_run.py` | Modify | Route tests for Snowflake prepared views, range validation, and source bounds. |
| `apps/api/tests/test_audit_events.py` | Modify | Audit coverage for `dashboard_run.view_retrieved`. |
| `apps/web/src/lib/dashboard-contracts.ts` | Modify | Add prepared-view types and parser; keep dataset parser for debug compatibility. |
| `apps/web/src/lib/dashboard-api.ts` | Modify | Add `fetchDashboardView` and `fetchDemoDashboardView`; keep dataset calls for debug compatibility. |
| `apps/web/src/lib/dashboard-api.test.ts` | Modify | API client tests for prepared view endpoints and auth. |
| `apps/web/src/lib/demo-dashboard-view.ts` | Create | Typed demo prepared-view fixture for frontend tests. |
| `apps/web/src/components/dashboard/filter-bar.tsx` | Modify | Add relative filters and date range controls without analytics logic. |
| `apps/web/src/components/dashboard/cost-dashboard.tsx` | Rewrite | Fetch prepared views, cache by `(run_id, range)`, prefetch relative views, render prepared models. |
| `apps/web/src/components/dashboard/*.tsx` | Modify | Import prepared-view types from `dashboard-contracts.ts`, not `dashboard-transforms.ts`. |
| `apps/web/src/components/dashboard/*.test.tsx` | Modify | Update tests for prepared-view API, caching, prefetch, and date range behavior. |
| `apps/web/src/lib/dashboard-transforms.ts` | Reduce or delete | Remove dashboard analytics from normal rendering once backend view path is active. |
| `apps/web/src/lib/dashboard-transforms.test.ts` | Reduce or delete | Keep only presentation helper tests if helpers remain; otherwise remove. |

---

## Task 1: Backend Prepared-View Models and Range Primitives

**Files:**
- Create: `apps/api/app/services/dashboard_view_models.py`
- Create: `apps/api/app/services/dashboard_view_builder.py`
- Create: `apps/api/tests/test_dashboard_view_builder.py`

- [ ] **Step 1: Write failing range and model tests**

Create `apps/api/tests/test_dashboard_view_builder.py` with these initial tests:

```python
from datetime import date
from uuid import UUID

import pytest

from app.models import DashboardRun
from app.services.dashboard_view_builder import (
    DEFAULT_VIEW_WINDOW_DAYS,
    DashboardRangeOutOfBoundsError,
    resolve_dashboard_view_range,
)
from app.services.dashboard_view_models import DashboardViewRange


RUN = DashboardRun(
    id="00000000-0000-4000-8000-000000000001",
    source="snowflake",
    status="completed",
    window_days=100,
    organization_id=UUID("00000000-0000-4000-8000-000000000001"),
)


def test_resolves_default_relative_range_from_through_date() -> None:
    view_range = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
    )

    assert DEFAULT_VIEW_WINDOW_DAYS == 30
    assert view_range == DashboardViewRange(
        mode="relative",
        window_days=30,
        start_date=date(2026, 5, 10),
        end_date=date(2026, 6, 8),
    )


def test_resolves_supported_relative_windows_from_through_date() -> None:
    seven = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
        window_days=7,
    )
    ninety = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
        window_days=90,
    )

    assert seven.start_date == date(2026, 6, 2)
    assert seven.end_date == date(2026, 6, 8)
    assert ninety.start_date == date(2026, 3, 11)
    assert ninety.end_date == date(2026, 6, 8)


def test_clamps_custom_end_date_to_through_date() -> None:
    view_range = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 11),
    )

    assert view_range == DashboardViewRange(
        mode="custom",
        window_days=None,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 8),
    )


def test_rejects_ranges_older_than_stored_source_bounds() -> None:
    with pytest.raises(DashboardRangeOutOfBoundsError) as exc_info:
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            start_date=date(2026, 2, 28),
            end_date=date(2026, 3, 5),
        )

    assert exc_info.value.source_start_date == date(2026, 3, 1)
    assert exc_info.value.source_end_date == date(2026, 6, 8)


def test_rejects_ambiguous_relative_and_custom_range() -> None:
    with pytest.raises(ValueError, match="exactly one range mode"):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            window_days=7,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 8),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_dashboard_view_builder.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `app.services.dashboard_view_builder` or missing model names.

- [ ] **Step 3: Create prepared-view models**

Create `apps/api/app/services/dashboard_view_models.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.models import DashboardRun, SCHEMA_VERSION

DashboardRangeMode = Literal["relative", "custom"]
DashboardDataModeLabel = Literal["Billed", "Estimated", "Demo"]
SpendBasis = Literal["billed", "estimated"]


class DashboardViewRange(BaseModel):
    mode: DashboardRangeMode
    window_days: int | None
    start_date: date
    end_date: date


class DashboardProjectionRange(BaseModel):
    start_date: date
    end_date: date


class DollarPoint(BaseModel):
    date: str
    spend: float
    spend_label: str


class ServicePoint(BaseModel):
    date: str
    values: dict[str, float]


class RankedSpendRow(BaseModel):
    name: str
    spend: float
    spend_label: str
    credits: float | None


class RankedBarRow(RankedSpendRow):
    bar_width_percent: float


class HeaderViewModel(BaseModel):
    data_mode_label: DashboardDataModeLabel
    account_locator: str | None
    currency: str
    through_date: str | None
    through_date_label: str | None
    freshness_label: str | None
    estimated_credit_price_label: str
    storage_price_label: str


class TotalSpendViewModel(BaseModel):
    basis: SpendBasis
    total: float
    total_label: str
    average_daily: float
    average_daily_label: str
    projected_monthly: float
    projected_monthly_label: str
    projection_basis_label: str
    daily_series: list[DollarPoint]
    top_driver: RankedSpendRow | None
    is_empty: bool


class ComputeSpendViewModel(BaseModel):
    compute_basis: SpendBasis
    daily_series: list[DollarPoint]
    ranked_warehouses: list[RankedSpendRow]
    ranked_users: list[RankedSpendRow]
    warehouse_bars: list[RankedBarRow]
    user_bars: list[RankedBarRow]
    is_empty: bool


class StorageDatabaseRow(BaseModel):
    name: str
    bytes: float
    monthly_spend: float
    monthly_spend_label: str


class StorageSpendViewModel(BaseModel):
    basis: SpendBasis
    database_basis: SpendBasis
    daily_series: list[DollarPoint]
    databases: list[StorageDatabaseRow]
    database_bars: list[RankedBarRow]
    is_empty: bool


class ServiceSpendViewModel(BaseModel):
    basis: SpendBasis
    daily_series: list[ServicePoint]
    service_names: list[str]
    ranked_services: list[RankedSpendRow]
    service_bars: list[RankedBarRow]
    is_empty: bool


class WarehouseDetailRow(RankedSpendRow):
    credits_compute: float
    credits_total: float


class UserDetailRow(RankedSpendRow):
    warehouse_name: str


class DetailTablesViewModel(BaseModel):
    services: list[RankedSpendRow]
    warehouses: list[WarehouseDetailRow]
    users: list[UserDetailRow]
    storage: list[StorageDatabaseRow]


class UnsupportedViewModel(BaseModel):
    title: str
    detail: str


class DashboardViewResponse(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: DashboardRun
    range: DashboardViewRange
    projection_range: DashboardProjectionRange
    header: HeaderViewModel
    unsupported: UnsupportedViewModel | None
    total_spend: TotalSpendViewModel
    compute_spend: ComputeSpendViewModel
    storage_spend: StorageSpendViewModel
    service_spend: ServiceSpendViewModel
    detail_tables: DetailTablesViewModel
```

- [ ] **Step 4: Implement range primitives**

Create `apps/api/app/services/dashboard_view_builder.py` with these initial primitives:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.services.dashboard_view_models import DashboardViewRange

DEFAULT_VIEW_WINDOW_DAYS = 30
SUPPORTED_VIEW_WINDOW_DAYS = frozenset({7, 30, 90})


@dataclass(frozen=True)
class DashboardRangeOutOfBoundsError(ValueError):
    source_start_date: date
    source_end_date: date

    def __str__(self) -> str:
        return "Requested dashboard range is outside stored source bounds."


def window_start_for(through_date: date, window_days: int) -> date:
    return through_date - timedelta(days=window_days - 1)


def projection_range_for(through_date: date) -> tuple[date, date]:
    return window_start_for(through_date, DEFAULT_VIEW_WINDOW_DAYS), through_date


def resolve_dashboard_view_range(
    *,
    through_date: date,
    source_start_date: date,
    source_end_date: date,
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DashboardViewRange:
    has_relative = window_days is not None
    has_custom = start_date is not None or end_date is not None
    if has_relative and has_custom:
        raise ValueError("Dashboard view accepts exactly one range mode.")
    if has_custom and (start_date is None or end_date is None):
        raise ValueError("Custom dashboard range requires start_date and end_date.")

    if not has_relative and not has_custom:
        window_days = DEFAULT_VIEW_WINDOW_DAYS
        has_relative = True

    if has_relative:
        if window_days not in SUPPORTED_VIEW_WINDOW_DAYS:
            raise ValueError("Unsupported dashboard window_days.")
        effective_start = window_start_for(through_date, int(window_days))
        effective_end = through_date
        mode = "relative"
        effective_window_days = int(window_days)
    else:
        assert start_date is not None
        assert end_date is not None
        if start_date > end_date:
            raise ValueError("Custom dashboard range start_date must be before end_date.")
        effective_start = start_date
        effective_end = min(end_date, through_date)
        mode = "custom"
        effective_window_days = None

    if effective_start < source_start_date or effective_end > source_end_date:
        raise DashboardRangeOutOfBoundsError(
            source_start_date=source_start_date,
            source_end_date=source_end_date,
        )
    if effective_start > effective_end:
        raise DashboardRangeOutOfBoundsError(
            source_start_date=source_start_date,
            source_end_date=source_end_date,
        )

    return DashboardViewRange(
        mode=mode,
        window_days=effective_window_days,
        start_date=effective_start,
        end_date=effective_end,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_dashboard_view_builder.py -v
```

Expected: PASS for the initial range tests.

- [ ] **Step 6: Commit**

```bash
rtk git add apps/api/app/services/dashboard_view_models.py apps/api/app/services/dashboard_view_builder.py apps/api/tests/test_dashboard_view_builder.py
rtk git commit -m "feat: add dashboard view range models"
```

---

## Task 2: Backend View Builder Analytics Port

**Files:**
- Modify: `apps/api/app/services/dashboard_view_builder.py`
- Modify: `apps/api/tests/test_dashboard_view_builder.py`

- [ ] **Step 1: Add failing builder tests for current transform behavior**

Append these tests to `apps/api/tests/test_dashboard_view_builder.py`:

```python
from copy import deepcopy

from app.models import DashboardDatasetMetadata
from app.services.demo_data import build_demo_dashboard_dataset
from app.services.dashboard_view_builder import build_dashboard_view


def _demo_run() -> DashboardRun:
    payload = build_demo_dashboard_dataset()
    return DashboardRun.model_validate(payload.run.model_dump(mode="json"))


def _demo_datasets() -> dict[str, list[dict[str, object]]]:
    return deepcopy(build_demo_dashboard_dataset().datasets)


def _demo_metadata() -> DashboardDatasetMetadata:
    return build_demo_dashboard_dataset().metadata


def _source_bounds(datasets: dict[str, list[dict[str, object]]]) -> tuple[date, date]:
    dates = [
        date.fromisoformat(str(row["usage_date"]))
        for rows in datasets.values()
        for row in rows
        if "usage_date" in row
    ]
    return min(dates), max(dates)


def _sum_org_spend(
    rows: list[dict[str, object]], start_date: date, end_date: date
) -> float:
    return sum(
        float(row["spend"])
        for row in rows
        if row["billing_type"] == "CONSUMPTION"
        and start_date.isoformat() <= str(row["usage_date"]) <= end_date.isoformat()
    )


def test_builds_demo_view_with_billed_like_totals_and_labels() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=30,
    )

    expected_total = _sum_org_spend(
        datasets["org_spend_daily"],
        view.range.start_date,
        view.range.end_date,
    )
    assert view.header.data_mode_label == "Demo"
    assert view.header.account_locator == "DEMO123"
    assert view.header.freshness_label == "Demo data through Jun 8, 2026"
    assert view.total_spend.basis == "billed"
    assert view.total_spend.total == pytest.approx(expected_total, abs=0.01)
    assert view.total_spend.total_label.startswith("$")
    assert view.unsupported is None


def test_builds_billed_view_with_negative_adjustments_included() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    baseline = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )
    adjusted_datasets = deepcopy(datasets)
    adjusted_datasets["org_spend_daily"].append(
        {
            "usage_date": "2026-06-08",
            "service_type": "CLOUD_SERVICES",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": True,
            "currency": "USD",
            "spend": -10.0,
        }
    )

    adjusted = build_dashboard_view(
        run=_demo_run(),
        datasets=adjusted_datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )

    assert adjusted.total_spend.total == pytest.approx(
        baseline.total_spend.total - 10,
        abs=0.01,
    )


def test_projection_uses_latest_30_days_regardless_of_selected_range() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)

    seven = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )
    thirty = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=30,
    )
    custom = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 8),
    )

    assert seven.total_spend.projected_monthly == pytest.approx(
        thirty.total_spend.projected_monthly,
        abs=0.01,
    )
    assert custom.total_spend.projected_monthly == pytest.approx(
        thirty.total_spend.projected_monthly,
        abs=0.01,
    )
    assert seven.total_spend.projection_basis_label == "latest 30 days"


def test_estimated_mode_uses_account_usage_through_date_and_estimated_basis() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "organization_usage": {"available": False, "detail": "org unavailable"},
        }
    )
    datasets["org_spend_daily"] = []
    datasets["rate_sheet_daily"] = []

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )

    assert view.header.data_mode_label == "Estimated"
    assert view.header.through_date == "2026-06-08"
    assert view.total_spend.basis == "estimated"
    assert view.compute_spend.compute_basis == "estimated"
    assert view.compute_spend.ranked_warehouses


def test_mixed_currency_returns_prepared_unsupported_view() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={"currency": None, "unsupported_reason": "mixed_currency"}
    )

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=30,
    )

    assert view.unsupported is not None
    assert view.unsupported.title == "Mixed currencies are not supported"
    assert view.total_spend.is_empty is True


def test_capped_bars_and_detail_rows_match_dashboard_limits() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    service_rows = []
    for index in range(55):
        service_number = index + 1
        service_rows.append(
            {
                "usage_date": "2026-06-08",
                "service_type": f"SERVICE_{service_number:02}",
                "rating_type": "COMPUTE",
                "billing_type": "CONSUMPTION",
                "is_adjustment": False,
                "currency": "USD",
                "spend": float(service_number),
            }
        )
    datasets["org_spend_daily"] = service_rows

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )

    assert len(view.service_spend.ranked_services) == 55
    assert len(view.service_spend.service_bars) == 8
    assert view.service_spend.service_bars[0].name == "SERVICE_55"
    assert view.service_spend.service_bars[0].bar_width_percent == 100
    assert len(view.detail_tables.services) == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_dashboard_view_builder.py -v
```

Expected: FAIL because `build_dashboard_view` is not implemented.

- [ ] **Step 3: Implement the pure builder**

Modify `apps/api/app/services/dashboard_view_builder.py`. Port the behavior from `apps/web/src/lib/dashboard-transforms.ts` into Python with these exact public names:

```python
DASHBOARD_RANKED_BAR_LIMIT = 8
DASHBOARD_DETAIL_ROW_LIMIT = 50

def build_dashboard_view(
    *,
    run: DashboardRun,
    datasets: dict[str, list[dict[str, Any]]],
    metadata: DashboardDatasetMetadata,
    source_start_date: date,
    source_end_date: date,
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DashboardViewResponse:
    through_date = _through_date_for(metadata)
    currency = metadata.currency or "USD"
    header = _build_header_view_model(metadata, currency, through_date)
    if through_date is None:
        return _empty_dashboard_view(
            run=run,
            view_range=DashboardViewRange(
                mode="relative",
                window_days=DEFAULT_VIEW_WINDOW_DAYS,
                start_date=source_end_date,
                end_date=source_end_date,
            ),
            projection_range=DashboardProjectionRange(
                start_date=source_end_date,
                end_date=source_end_date,
            ),
            header=header,
            currency=currency,
            unsupported=None,
        )
    view_range = resolve_dashboard_view_range(
        through_date=through_date,
        source_start_date=source_start_date,
        source_end_date=source_end_date,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    projection_start, projection_end = projection_range_for(through_date)
    return _build_dashboard_view_for_ranges(
        run=run,
        datasets=datasets,
        metadata=metadata,
        header=header,
        currency=currency,
        view_range=view_range,
        projection_range=DashboardProjectionRange(
            start_date=projection_start,
            end_date=projection_end,
        ),
    )
```

Implement these helper groups in the same file. Keep them private unless tests need direct access:

```python
def _through_date_for(metadata: DashboardDatasetMetadata) -> date | None:
    if metadata.data_mode == "estimated":
        return metadata.account_usage_through_date
    return metadata.billing_through_date or metadata.account_usage_through_date


def _format_currency(value: float, currency: str | None) -> str:
    return f"{value:,.2f}".join(("$", "")) if (currency or "USD") == "USD" else f"{value:,.2f} {currency}"


def _format_usage_date(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"
```

Use the same semantics as the TypeScript transform:

- Billed and demo totals use `org_spend_daily` rows with `billing_type == "CONSUMPTION"`.
- Estimated totals use `service_spend_daily` credits converted by rate sheet or `metadata.estimated_credit_price_usd`.
- Service-only conversion prefers the `rating_type == "COMPUTE"` rate when available.
- Warehouse and user compute spend always render as estimated and use `WAREHOUSE_METERING` / `COMPUTE`.
- Billed storage daily series uses billed Organization Usage storage rows; database breakdown remains estimated from storage bytes.
- `projected_monthly` uses the latest 30 days ending at the through-date, independent of selected range.
- Detail rows are capped at 50; ranked bars are capped at 8.
- Empty and unsupported views still return a complete `DashboardViewResponse`.

Use these implementation details to avoid drift:

```python
def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _rows_in_window(
    rows: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if start_date <= _as_date(row["usage_date"]) <= end_date
    ]


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
```

When `_format_currency()` is implemented, verify it returns strings matching the frontend examples:

```python
assert _format_currency(1234.5, "USD") == "$1,234.50"
assert _format_currency(0, "USD") == "$0.00"
```

- [ ] **Step 4: Run tests to verify builder behavior**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_dashboard_view_builder.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add apps/api/app/services/dashboard_view_builder.py apps/api/tests/test_dashboard_view_builder.py
rtk git commit -m "feat: build backend dashboard prepared views"
```

---

## Task 3: Store Source Bounds on Completed Runs

**Files:**
- Modify: `apps/api/app/routes/dashboard_runs.py`
- Test: `apps/api/tests/test_demo_dashboard_run.py`

- [ ] **Step 1: Add failing source-bound repository tests**

Append to `apps/api/tests/test_demo_dashboard_run.py`:

```python
def test_completed_run_persists_source_bounds() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    create_response = client.post(
        "/api/dashboard-runs",
        json=_complete_create_payload(),
    )
    run_id = create_response.json()["id"]

    bounds = dashboard_run_repository.get_source_bounds(UUID(run_id))

    assert bounds is not None
    assert bounds.source_start_date.isoformat() == "2026-03-01"
    assert bounds.source_end_date.isoformat() == "2026-06-08"


def test_expired_run_removes_source_bounds() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    create_response = client.post(
        "/api/dashboard-runs",
        json=_complete_create_payload(),
    )
    run_id = UUID(create_response.json()["id"])
    dashboard_run_repository.expire_run_datasets(run_id)

    response = client.get(f"/api/dashboard-runs/{run_id}/datasets")

    assert response.status_code == 404
    assert dashboard_run_repository.get_source_bounds(run_id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_demo_dashboard_run.py -v
```

Expected: FAIL because `get_source_bounds` does not exist.

- [ ] **Step 3: Implement source bounds storage**

Modify `apps/api/app/routes/dashboard_runs.py`.

Add this model near `StoredDashboardDataset`:

```python
class StoredSourceBounds(BaseModel):
    source_start_date: date
    source_end_date: date
```

Add storage in `InMemoryDashboardRunRepository.__init__`:

```python
self._source_bounds: dict[UUID, StoredSourceBounds] = {}
```

Clear it in `clear()`:

```python
self._source_bounds.clear()
```

Add helpers inside `InMemoryDashboardRunRepository`:

```python
def get_source_bounds(self, run_id: UUID) -> StoredSourceBounds | None:
    with self._lock:
        return self._source_bounds.get(run_id)


def _store_source_bounds(
    self,
    run_id: UUID,
    datasets: dict[str, list[dict[str, Any]]],
) -> None:
    usage_dates: list[date] = []
    for rows in datasets.values():
        for row in rows:
            value = row.get("usage_date")
            if value is None:
                continue
            parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
            usage_dates.append(parsed)

    if not usage_dates:
        now = datetime.now(timezone.utc).date()
        self._source_bounds[run_id] = StoredSourceBounds(
            source_start_date=now,
            source_end_date=now,
        )
        return

    self._source_bounds[run_id] = StoredSourceBounds(
        source_start_date=min(usage_dates),
        source_end_date=max(usage_dates),
    )
```

Call `_store_source_bounds()` in `create_completed_snapshot()` immediately after `_datasets` is assigned.

Remove source bounds when datasets are removed:

```python
self._source_bounds.pop(run_id, None)
```

Apply that line in the expiration path and delete path.

- [ ] **Step 4: Run tests to verify they pass**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_demo_dashboard_run.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add apps/api/app/routes/dashboard_runs.py apps/api/tests/test_demo_dashboard_run.py
rtk git commit -m "feat: persist dashboard source bounds"
```

---

## Task 4: Prepared View Routes and Audit Events

**Files:**
- Modify: `apps/api/app/routes/dashboard_runs.py`
- Modify: `apps/api/tests/test_demo_dashboard_run.py`
- Modify: `apps/api/tests/test_snowflake_dashboard_run.py`
- Modify: `apps/api/tests/test_audit_events.py`

- [ ] **Step 1: Add failing route tests**

Append to `apps/api/tests/test_demo_dashboard_run.py`:

```python
def test_demo_view_route_returns_default_prepared_view() -> None:
    client = TestClient(app)

    response = client.get("/api/dashboard-runs/demo/view")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["run"]["id"] == "demo-run"
    assert body["range"] == {
        "mode": "relative",
        "window_days": 30,
        "start_date": "2026-05-10",
        "end_date": "2026-06-08",
    }
    assert body["header"]["data_mode_label"] == "Demo"
    assert body["header"]["freshness_label"] == "Demo data through Jun 8, 2026"
    assert body["total_spend"]["projection_basis_label"] == "latest 30 days"


def test_demo_view_clamps_custom_end_date_to_through_date() -> None:
    response = TestClient(app).get(
        "/api/dashboard-runs/demo/view",
        params={"start_date": "2026-06-01", "end_date": "2026-06-11"},
    )

    assert response.status_code == 200
    assert response.json()["range"] == {
        "mode": "custom",
        "window_days": None,
        "start_date": "2026-06-01",
        "end_date": "2026-06-08",
    }
```

Append to `apps/api/tests/test_snowflake_dashboard_run.py`:

```python
def test_snowflake_dashboard_run_view_route_returns_prepared_view(monkeypatch) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")

    def execute(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if "current_account()" in lowered:
            return [{"account_locator": "TU24199"}]
        if "organization_usage" in lowered:
            if "usage_in_currency_daily" in lowered:
                return [
                    {
                        "usage_date": date(2026, 6, 5),
                        "service_type": "WAREHOUSE_METERING",
                        "rating_type": "COMPUTE",
                        "billing_type": "CONSUMPTION",
                        "is_adjustment": False,
                        "currency": "USD",
                        "spend": 24.0,
                    }
                ]
            return [
                {
                    "usage_date": date(2026, 6, 5),
                    "service_type": "WAREHOUSE_METERING",
                    "rating_type": "COMPUTE",
                    "currency": "USD",
                    "effective_rate": 3.0,
                }
            ]
        return _source_rows(_source_key_for_sql(lowered))

    monkeypatch.setattr("app.services.dashboard_datasets.execute_source_query", execute)
    client = TestClient(app)
    run_response = client.post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    )
    run_id = run_response.json()["id"]

    view_response = client.get(f"/api/dashboard-runs/{run_id}/view")

    assert view_response.status_code == 200
    body = view_response.json()
    assert body["run"]["id"] == run_id
    assert body["header"]["data_mode_label"] == "Billed"
    assert body["header"]["account_locator"] == "TU24199"
    assert body["total_spend"]["basis"] == "billed"


def test_snowflake_dashboard_view_rejects_too_old_custom_range(monkeypatch) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")
    monkeypatch.setattr(
        "app.services.dashboard_datasets.execute_source_query",
        lambda sql, bind_params: [{"account_locator": "TU24199"}]
        if "current_account()" in sql.lower()
        else _source_rows(_source_key_for_sql(sql.lower()))
        if "organization_usage" not in sql.lower()
        else [],
    )
    client = TestClient(app)
    run_response = client.post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    )
    run_id = run_response.json()["id"]

    response = client.get(
        f"/api/dashboard-runs/{run_id}/view",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "range_out_of_bounds"
    assert response.json()["detail"]["source_start_date"] <= response.json()["detail"]["source_end_date"]
```

Modify `apps/api/tests/test_audit_events.py` in `test_org_dashboard_run_lifecycle_records_sanitized_audit_events()`:

```python
view_response = client.get(f"/api/dashboard-runs/{run_id}/view")
```

Insert it between `datasets_response` and `delete_response`, assert status `200`, and update expected event names:

```python
assert [event["event_name"] for event in events] == [
    "dashboard_run.created",
    "dashboard_run.dataset_retrieved",
    "dashboard_run.view_retrieved",
    "dashboard_run.deleted",
]
```

Assert the view event payload:

```python
assert events[2]["payload"] == {
    "run_id": run_id,
    "range_mode": "relative",
    "start_date": "2026-05-10",
    "end_date": "2026-06-08",
    "window_days": 30,
}
```

- [ ] **Step 2: Run route tests to verify they fail**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_demo_dashboard_run.py tests/test_snowflake_dashboard_run.py tests/test_audit_events.py -v
```

Expected: FAIL with 404 for `/view` routes and missing audit event.

- [ ] **Step 3: Implement `/view` routes**

Modify `apps/api/app/routes/dashboard_runs.py`.

Import builder and models:

```python
from app.services.dashboard_view_builder import (
    DashboardRangeOutOfBoundsError,
    build_dashboard_view,
)
```

Add a repository method:

```python
def get_view_inputs(
    self, run_id: UUID
) -> tuple[DashboardRun, dict[str, list[dict[str, Any]]], dict[str, Any], StoredSourceBounds] | None:
    with self._lock:
        run = self._runs.get(run_id)
        datasets = self._datasets.get(run_id)
        metadata = self._metadata.get(run_id)
        source_bounds = self._source_bounds.get(run_id)
        if run is None or datasets is None or metadata is None or source_bounds is None:
            return None
        return (
            run,
            {
                dataset_key: stored_dataset.aggregate_dataset
                for dataset_key, stored_dataset in datasets.items()
            },
            metadata,
            source_bounds,
        )
```

Add route handlers before `/{run_id}` routes so `demo/view` is not captured:

```python
@router.get("/demo/view")
def read_demo_dashboard_view(
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    payload = build_demo_dashboard_dataset()
    run = DashboardRun.model_validate(payload.run.model_dump(mode="json"))
    bounds = _source_bounds_for_dataset_rows(payload.datasets)
    view = build_dashboard_view(
        run=run,
        datasets=payload.datasets,
        metadata=payload.metadata,
        source_start_date=bounds.source_start_date,
        source_end_date=bounds.source_end_date,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    return view.model_dump(mode="json")
```

Add persisted route:

```python
@router.get("/{run_id}/view")
def read_dashboard_run_view(
    run_id: UUID,
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    auth_context: AuthContext = Depends(require_auth_context),
) -> dict[str, Any]:
    view_inputs = dashboard_run_repository.get_view_inputs(run_id)
    if view_inputs is None:
        raise HTTPException(status_code=404, detail="Dashboard view not found")
    run, datasets, metadata, source_bounds = view_inputs
    _require_dashboard_run_membership(auth_context, run.organization_id)
    try:
        view = build_dashboard_view(
            run=run,
            datasets=datasets,
            metadata=DashboardDatasetMetadata.model_validate(metadata),
            source_start_date=source_bounds.source_start_date,
            source_end_date=source_bounds.source_end_date,
            window_days=window_days,
            start_date=start_date,
            end_date=end_date,
        )
    except DashboardRangeOutOfBoundsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "range_out_of_bounds",
                "message": "Broader date ranges are not supported yet.",
                "source_start_date": exc.source_start_date.isoformat(),
                "source_end_date": exc.source_end_date.isoformat(),
            },
        ) from None
    _record_dashboard_run_view_retrieved(view)
    return view.model_dump(mode="json")
```

Add helpers:

```python
def _source_bounds_for_dataset_rows(
    datasets: dict[str, list[dict[str, Any]]]
) -> StoredSourceBounds:
    usage_dates = [
        row["usage_date"] if isinstance(row["usage_date"], date) else date.fromisoformat(str(row["usage_date"]))
        for rows in datasets.values()
        for row in rows
        if row.get("usage_date") is not None
    ]
    if not usage_dates:
        now = datetime.now(timezone.utc).date()
        return StoredSourceBounds(source_start_date=now, source_end_date=now)
    return StoredSourceBounds(
        source_start_date=min(usage_dates),
        source_end_date=max(usage_dates),
    )


def _record_dashboard_run_view_retrieved(response) -> None:
    audit_event_recorder.record_org_event(
        "dashboard_run.view_retrieved",
        organization_id=response.run.organization_id,
        payload={
            "run_id": response.run.id,
            "range_mode": response.range.mode,
            "start_date": response.range.start_date.isoformat(),
            "end_date": response.range.end_date.isoformat(),
            "window_days": response.range.window_days,
        },
    )
```

- [ ] **Step 4: Run route tests to verify they pass**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_demo_dashboard_run.py tests/test_snowflake_dashboard_run.py tests/test_audit_events.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full API suite**

Run from `apps/api`:

```bash
rtk uv run pytest tests/ -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
rtk git add apps/api/app/routes/dashboard_runs.py apps/api/tests/test_demo_dashboard_run.py apps/api/tests/test_snowflake_dashboard_run.py apps/api/tests/test_audit_events.py
rtk git commit -m "feat: expose prepared dashboard views"
```

---

## Task 5: Frontend Prepared-View Contract and API Client

**Files:**
- Modify: `apps/web/src/lib/dashboard-contracts.ts`
- Modify: `apps/web/src/lib/dashboard-api.ts`
- Modify: `apps/web/src/lib/dashboard-api.test.ts`
- Create: `apps/web/src/lib/demo-dashboard-view.ts`

- [ ] **Step 1: Add failing API client and parser tests**

Modify `apps/web/src/lib/dashboard-api.test.ts` imports:

```ts
import {
  fetchDashboardDatasets,
  fetchDashboardView,
  fetchDemoDashboardDatasets,
  fetchDemoDashboardView,
  pollDashboardRun,
  startDashboardRun,
} from "./dashboard-api";
import demoDashboardView from "./demo-dashboard-view";
```

Append tests:

```ts
it("fetches demo prepared dashboard view", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(demoDashboardView), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );

  const view = await fetchDemoDashboardView({ windowDays: 30 });

  expect(view.header.dataModeLabel).toBe("Demo");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/dashboard-runs/demo/view?window_days=30",
    expect.objectContaining({ cache: "no-store" }),
  );
});

it("fetches run prepared view with bearer auth and custom range", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(demoDashboardView), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );

  await fetchDashboardView(
    "run-123",
    { startDate: "2026-06-01", endDate: "2026-06-08" },
    { accessToken: "token-123" },
  );

  const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    "/api/dashboard-runs/run-123/view?start_date=2026-06-01&end_date=2026-06-08",
  );
  expect(new Headers(init.headers).get("authorization")).toBe(
    "Bearer token-123",
  );
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `apps/web`:

```bash
rtk npx vitest run src/lib/dashboard-api.test.ts
```

Expected: FAIL because prepared-view API functions and fixture do not exist.

- [ ] **Step 3: Add prepared-view types and parser**

Modify `apps/web/src/lib/dashboard-contracts.ts`.

Add types mirroring the existing component view-model shape in camelCase:

```ts
export type DashboardViewRange = {
  mode: "relative" | "custom";
  windowDays: number | null;
  startDate: string;
  endDate: string;
};

export type DashboardProjectionRange = {
  startDate: string;
  endDate: string;
};

export type DollarPoint = { date: string; spend: number; spendLabel: string };
export type ServicePoint = { date: string; values: Record<string, number> };
export type RankedSpendRow = {
  name: string;
  spend: number;
  spendLabel: string;
  credits: number | null;
};
export type RankedBarRow = RankedSpendRow & { barWidthPercent: number };
```

Continue adding the same prepared-view types from `dashboard_view_models.py`, using camelCase names:

```ts
export type DashboardView = {
  schema_version: 1;
  run: DashboardRun;
  range: DashboardViewRange;
  projectionRange: DashboardProjectionRange;
  header: HeaderViewModel;
  unsupported: UnsupportedViewModel | null;
  totalSpend: TotalSpendViewModel;
  computeSpend: ComputeSpendViewModel;
  storageSpend: StorageSpendViewModel;
  serviceSpend: ServiceSpendViewModel;
  detailTables: DetailTablesViewModel;
};
```

Add `parseDashboardView(payload: unknown): DashboardView`. The parser must:

- require `schema_version === 1`
- parse `run` with existing `parseDashboardRun`
- map snake_case API fields to camelCase frontend fields
- map `bar_width_percent` to `barWidthPercent`
- map `spend_label` to `spendLabel`
- map `daily_series` to `dailySeries`
- map `projection_range` to `projectionRange`
- throw `Error("Dashboard view response is invalid")` for malformed required fields

- [ ] **Step 4: Add demo prepared-view fixture**

Create `apps/web/src/lib/demo-dashboard-view.ts`:

```ts
import demoDashboardData from "./demo-dashboard-data";
import {
  buildDashboardViewModel,
  DEFAULT_WINDOW_DAYS,
} from "./dashboard-transforms";
import type { DashboardView } from "./dashboard-contracts";

const viewModel = buildDashboardViewModel(
  demoDashboardData,
  DEFAULT_WINDOW_DAYS,
);

const demoDashboardView: DashboardView = {
  schema_version: 1,
  run: demoDashboardData.run,
  range: {
    mode: "relative",
    windowDays: DEFAULT_WINDOW_DAYS,
    startDate: "2026-05-10",
    endDate: "2026-06-08",
  },
  projectionRange: {
    startDate: "2026-05-10",
    endDate: "2026-06-08",
  },
  header: viewModel.header,
  unsupported: viewModel.unsupported,
  totalSpend: viewModel.totalSpend,
  computeSpend: viewModel.computeSpend,
  storageSpend: viewModel.storageSpend,
  serviceSpend: viewModel.serviceSpend,
  detailTables: viewModel.detailTables,
};

export default demoDashboardView;
```

This fixture is temporary. Task 7 removes dependency on `dashboard-transforms.ts` from runtime code; tests may keep static fixtures.

- [ ] **Step 5: Add API functions**

Modify `apps/web/src/lib/dashboard-api.ts`:

```ts
import parseDashboardDatasets, {
  parseDashboardRun,
  parseDashboardView,
  type DashboardData,
  type DashboardRun,
  type DashboardView,
} from "./dashboard-contracts";

export type DashboardViewRangeRequest =
  | { windowDays?: number; startDate?: never; endDate?: never }
  | { windowDays?: never; startDate: string; endDate: string };

export async function fetchDemoDashboardView(
  range: DashboardViewRangeRequest = { windowDays: 30 },
): Promise<DashboardView> {
  return fetchDashboardViewPath("/api/dashboard-runs/demo/view", range);
}

export async function fetchDashboardView(
  runId: string,
  range: DashboardViewRangeRequest = { windowDays: 30 },
  options: DashboardApiOptions = {},
): Promise<DashboardView> {
  return fetchDashboardViewPath(`/api/dashboard-runs/${runId}/view`, range, options);
}

async function fetchDashboardViewPath(
  path: string,
  range: DashboardViewRangeRequest,
  options: DashboardApiOptions = {},
): Promise<DashboardView> {
  const params = new URLSearchParams();
  if (range.windowDays) params.set("window_days", String(range.windowDays));
  if (range.startDate && range.endDate) {
    params.set("start_date", range.startDate);
    params.set("end_date", range.endDate);
  }
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  const payload = await fetchJson(`${path}${suffix}`, {}, options);
  return parseDashboardView(payload);
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run from `apps/web`:

```bash
rtk npx vitest run src/lib/dashboard-api.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
rtk git add apps/web/src/lib/dashboard-contracts.ts apps/web/src/lib/dashboard-api.ts apps/web/src/lib/dashboard-api.test.ts apps/web/src/lib/demo-dashboard-view.ts
rtk git commit -m "feat: add dashboard prepared view contract"
```

---

## Task 6: Frontend Prepared-View Rendering, Cache, Prefetch, and Date Range Controls

**Files:**
- Modify: `apps/web/src/components/dashboard/cost-dashboard.tsx`
- Modify: `apps/web/src/components/dashboard/filter-bar.tsx`
- Modify: `apps/web/src/components/dashboard/spend-sections.tsx`
- Modify: `apps/web/src/components/dashboard/detail-tables.tsx`
- Modify: `apps/web/src/components/dashboard/dashboard-header.tsx`
- Modify: `apps/web/src/components/dashboard/cost-dashboard.test.tsx`
- Modify: component tests that import `dashboard-transforms` types

- [ ] **Step 1: Update imports in render-only components**

Change these imports:

```ts
import type {
  ComputeSpendViewModel,
  RankedBarRow,
  ServiceSpendViewModel,
  StorageSpendViewModel,
  TotalSpendViewModel,
} from "../../lib/dashboard-transforms";
```

to:

```ts
import type {
  ComputeSpendViewModel,
  RankedBarRow,
  ServiceSpendViewModel,
  StorageSpendViewModel,
  TotalSpendViewModel,
} from "../../lib/dashboard-contracts";
```

Apply this in:

- `spend-sections.tsx`
- `detail-tables.tsx`
- any dashboard component test that imports prepared-view types

- [ ] **Step 2: Add failing CostDashboard tests for prepared views**

Modify `apps/web/src/components/dashboard/cost-dashboard.test.tsx`.

Change the API mock:

```ts
import {
  fetchDashboardView,
  fetchDemoDashboardView,
  pollDashboardRun,
  startDashboardRun,
} from "../../lib/dashboard-api";
import demoDashboardView from "../../lib/demo-dashboard-view";

vi.mock("../../lib/dashboard-api", () => ({
  fetchDashboardView: vi.fn(),
  fetchDemoDashboardView: vi.fn(),
  pollDashboardRun: vi.fn(),
  startDashboardRun: vi.fn(),
}));
```

Add tests:

```tsx
it("loads demo prepared view and prefetches relative windows", async () => {
  vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);

  render(<CostDashboard demoMode />);

  await screen.findByText("Total spend");
  expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 30 });
  expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 7 });
  expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 90 });
});

it("switches to cached relative prepared view without another request", async () => {
  vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);

  render(<CostDashboard demoMode />);

  await screen.findByText("Total spend");
  await waitFor(() => expect(fetchDemoDashboardView).toHaveBeenCalledTimes(3));

  fireEvent.click(screen.getByRole("button", { name: "7 days" }));

  expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(fetchDemoDashboardView).toHaveBeenCalledTimes(3);
});

it("fetches and caches an uncached custom date range", async () => {
  vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);

  render(<CostDashboard demoMode />);

  await screen.findByText("Total spend");
  fireEvent.change(screen.getByLabelText("Start date"), {
    target: { value: "2026-06-01" },
  });
  fireEvent.change(screen.getByLabelText("End date"), {
    target: { value: "2026-06-08" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));

  await waitFor(() =>
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({
      startDate: "2026-06-01",
      endDate: "2026-06-08",
    }),
  );
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run from `apps/web`:

```bash
rtk npx vitest run src/components/dashboard/cost-dashboard.test.tsx
```

Expected: FAIL because `CostDashboard` still fetches dataset payloads and `FilterBar` lacks date inputs.

- [ ] **Step 4: Update `FilterBar`**

Modify `apps/web/src/components/dashboard/filter-bar.tsx`:

```tsx
"use client";

import type { DashboardViewRange } from "../../lib/dashboard-contracts";

export const WINDOW_DAYS = [7, 30, 90] as const;
export type WindowDays = (typeof WINDOW_DAYS)[number];

type FilterBarProps = {
  range: DashboardViewRange;
  currency: string;
  startDate: string;
  endDate: string;
  onWindowChange: (windowDays: WindowDays) => void;
  onStartDateChange: (value: string) => void;
  onEndDateChange: (value: string) => void;
  onApplyDateRange: () => void;
};

export default function FilterBar({
  range,
  currency,
  startDate,
  endDate,
  onWindowChange,
  onStartDateChange,
  onEndDateChange,
  onApplyDateRange,
}: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div
        aria-label="Spend window"
        className="inline-flex rounded-md border border-slate-200 bg-white p-0.5"
        role="group"
      >
        {WINDOW_DAYS.map((option) => (
          <button
            key={option}
            aria-pressed={range.mode === "relative" && option === range.windowDays}
            className={
              range.mode === "relative" && option === range.windowDays
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
      <div className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1 text-xs font-medium text-slate-600">
          Start date
          <input
            aria-label="Start date"
            className="rounded border border-slate-200 bg-white px-2 py-1 text-xs"
            type="date"
            value={startDate}
            onChange={(event) => onStartDateChange(event.target.value)}
          />
        </label>
        <label className="grid gap-1 text-xs font-medium text-slate-600">
          End date
          <input
            aria-label="End date"
            className="rounded border border-slate-200 bg-white px-2 py-1 text-xs"
            type="date"
            value={endDate}
            onChange={(event) => onEndDateChange(event.target.value)}
          />
        </label>
        <button
          className="rounded bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white"
          type="button"
          onClick={onApplyDateRange}
        >
          Apply date range
        </button>
      </div>
      <span className="text-xs font-medium text-slate-500">{currency}</span>
    </div>
  );
}
```

- [ ] **Step 5: Rewrite `CostDashboard` to use prepared views**

Modify `apps/web/src/components/dashboard/cost-dashboard.tsx`.

Replace dataset state with view state:

```ts
import {
  fetchDashboardView,
  fetchDemoDashboardView,
  pollDashboardRun,
  startDashboardRun,
  type DashboardViewRangeRequest,
} from "../../lib/dashboard-api";
import type { DashboardRunStatus, DashboardView } from "../../lib/dashboard-contracts";
import { FETCH_WINDOW_DAYS } from "../../lib/dashboard-contracts";
import FilterBar, { type WindowDays } from "./filter-bar";
```

Use cache keys that include run ID:

```ts
function rangeKey(runId: string, range: DashboardViewRangeRequest): string {
  if ("startDate" in range) {
    return `${runId}:custom:${range.startDate}:${range.endDate}`;
  }
  return `${runId}:relative:${range.windowDays ?? 30}`;
}
```

State shape:

```ts
type LoadState = {
  status: DashboardRunStatus | "loading";
  message?: string | null;
  view?: DashboardView;
};
```

Behavior requirements:

- Demo initial load calls `fetchDemoDashboardView({ windowDays: 30 })`.
- Snowflake run calls `startDashboardRun`, `pollDashboardRun`, then `fetchDashboardView(run.id, { windowDays: 30 })`.
- After the default view loads, prefetch `{ windowDays: 7 }` and `{ windowDays: 90 }` for the same run.
- Relative click first checks cache; if present, switch without request.
- Custom apply checks cache; if absent, fetches the custom range and caches it.
- `runDisabled` remains true during run creation/polling, not during cached range switching.

- [ ] **Step 6: Run dashboard tests**

Run from `apps/web`:

```bash
rtk npx vitest run src/components/dashboard/cost-dashboard.test.tsx src/components/dashboard/filter-bar.test.tsx
```

Expected: PASS after updating any assertions to prepared-view APIs.

- [ ] **Step 7: Run all dashboard component tests**

Run from `apps/web`:

```bash
rtk npx vitest run src/components/dashboard/
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
rtk git add apps/web/src/components/dashboard apps/web/src/lib/demo-dashboard-view.ts
rtk git commit -m "feat: render cached prepared dashboard views"
```

---

## Task 7: Remove Frontend Analytics Ownership and Verify Full Slice

**Files:**
- Modify or delete: `apps/web/src/lib/dashboard-transforms.ts`
- Modify or delete: `apps/web/src/lib/dashboard-transforms.test.ts`
- Modify: any remaining frontend imports from `dashboard-transforms.ts`
- Modify: `apps/web/src/components/dashboard/*.test.tsx`

- [ ] **Step 1: Find remaining analytics imports**

Run from repo root:

```bash
rtk grep "dashboard-transforms" apps/web/src -n
```

Expected before cleanup: imports only in test fixtures or legacy transform tests.

- [ ] **Step 2: Add a guard test against analytics imports in dashboard components**

Create or modify `apps/web/src/components/dashboard/dashboard-imports.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

describe("dashboard component imports", () => {
  it("does not import dashboard analytics transforms from render components", () => {
    const directory = join(process.cwd(), "src/components/dashboard");
    const offenders = readdirSync(directory)
      .filter((file) => file.endsWith(".tsx"))
      .filter((file) =>
        readFileSync(join(directory, file), "utf8").includes("dashboard-transforms"),
      );

    expect(offenders).toEqual([]);
  });
});
```

- [ ] **Step 3: Reduce or delete `dashboard-transforms.ts`**

If runtime code no longer imports `dashboard-transforms.ts`, delete it and its test file:

```bash
rtk git rm apps/web/src/lib/dashboard-transforms.ts apps/web/src/lib/dashboard-transforms.test.ts
```

If `demo-dashboard-view.ts` still needs it for a temporary fixture, replace that fixture with a static prepared-view object copied from the backend `/api/dashboard-runs/demo/view` response shape and then delete the transform files.

- [ ] **Step 4: Run frontend tests**

Run from `apps/web`:

```bash
rtk npm run test
rtk npm run typecheck
rtk npm run lint
```

Expected: PASS.

- [ ] **Step 5: Run root verification**

Run from repo root:

```bash
rtk npm run test
rtk npm run lint
rtk npm run typecheck
```

Expected: PASS.

- [ ] **Step 6: Manual live verification checklist**

With the dev stack running against Snowflake:

- Click `Run analysis`.
- Confirm the header shows `Local Snowflake`, `Billed`, locator `TU24199`, and billing freshness.
- Confirm `7`, `30`, and `90` switch after prefetch without new `/view` requests.
- Pick a custom date range inside the source bounds; confirm exactly one `/view?start_date=2026-06-01&end_date=2026-06-08` style request.
- Pick an end date after freshness through-date; confirm the UI renders the clamped returned range.
- Pick a start date older than source bounds; confirm the unsupported range message includes available bounds.
- Confirm demo mode uses `/api/dashboard-runs/demo/view` and renders the same sections.
- Confirm estimated fallback still renders if Organization Usage is unavailable.

- [ ] **Step 7: Commit**

```bash
rtk git add apps/web/src
rtk git commit -m "refactor: remove frontend dashboard analytics transforms"
```

---

## Final Self-Review Checklist

- [ ] `npm run test`, `npm run lint`, and `npm run typecheck` pass from repo root.
- [ ] No Snowflake SQL files were changed in this slice.
- [ ] `dashboard-transforms.ts` is removed or contains only presentation helpers with no billing/rate/window/ranking logic.
- [ ] `apps/web/src/components/dashboard/` imports prepared-view types from `dashboard-contracts.ts`.
- [ ] `/api/dashboard-runs/{run_id}/datasets` still works for debug compatibility.
- [ ] `/api/dashboard-runs/{run_id}/view` records `dashboard_run.view_retrieved`.
- [ ] Range cache keys include `run_id`.
- [ ] Custom end dates after through-date clamp to through-date.
- [ ] Out-of-bounds old start dates return `409 range_out_of_bounds` with stored bounds.
- [ ] Demo, billed, and estimated modes all have backend view-builder test coverage.
