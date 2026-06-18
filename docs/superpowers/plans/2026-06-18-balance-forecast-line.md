# Forecasted-Drawdown Line Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dotted lime "Forecasted balance" line to the Overview balance card that projects the current capacity balance forward at the trailing-7-day average daily spend until it reaches $0.

**Architecture:** The forecast is computed server-side in the Python view builder (`dashboard_view_builder.py`) and shipped as a `forecast_series: list[BalancePoint]` on `CapacityBalanceViewModel`. The rate is the mean of the last 7 `projection_daily` spend points, gated to billed/demo mode. The frontend parses the new field and, when it is non-empty, renders a dedicated two-series Tremor `AreaChart` (purple filled balance + lime dotted forecast) in place of the existing single-series chart; a scoped CSS rule makes the lime series dotted and fill-less.

**Tech Stack:** Python 3 + Pydantic v2 + pytest (`apps/api`); Next.js + React + TypeScript + Tremor 3.18.7 (Recharts) + Vitest + Testing Library (`apps/web`).

## Global Constraints

Every task's requirements implicitly include these (verbatim from the spec):

- Lime color is the registered token `chart-lime` (`#C9E930`); balance stays `chart-purple`.
- Forecast line is **dotted** (`stroke-dasharray`), with **no area fill**.
- **No legend.** Tooltip-only; the lime series is labeled exactly **"Forecasted balance"**; the forecast tooltip has **no "Total" row**.
- **One chart in place.** The balance card always renders exactly one chart; the forecast path *replaces* the existing chart when forecast data exists, and the card is byte-for-byte unchanged when it does not.
- Forecast is **backend-computed**; the frontend never derives forecast points.
- Forecast is gated on `is_billed` (`metadata.data_mode in {"billed", "demo"}`); estimated mode yields `forecast_series == []`.
- `MAX_FORECAST_DAYS = 1825` (~5 years); if the runway exceeds it, return `[]` (no fake-zero point).
- Reuse the existing `BalancePoint` shape (`{date, balance, balance_label}`) — no new point type.
- **No `demo_data.py` changes.**
- Frontend forecast chart passes `connectNulls={false}`.
- Target the forecast series in CSS by its **lime color class**, never by child order.
- The chart row type must permit `undefined` series values; do not reuse `ChartPoint` (`Record<string, string | number>`).

## File Structure

**Backend (`apps/api`)**
- `app/services/dashboard_view_models.py` — *modify*: add `forecast_series` field (default empty) to `CapacityBalanceViewModel`.
- `app/services/dashboard_view_builder.py` — *modify*: add `MAX_FORECAST_DAYS`, `FORECAST_AVERAGE_WINDOW_DAYS`, `_build_forecast_series(...)`, `_trailing_average_spend(...)`; thread `forecast_daily_spend` into `_build_capacity_balance(...)` and its call site under the `is_billed` gate.
- `tests/test_dashboard_view_builder.py` — *modify*: add forecast unit + integration tests.

**Frontend (`apps/web`)**
- `src/lib/dashboard-contracts.ts` — *modify*: add `forecastSeries` to `CapacityBalanceViewModel`, parse it with a legacy `[]` fallback, default it in `emptyCapacityBalanceViewModel`.
- `src/lib/dashboard-contracts.test.ts` — *modify*: add forecast parse test; update the legacy-default assertion.
- `src/components/dashboard/dashboard-design-system.tsx` — *modify*: add the dedicated `CapacityForecastChart`, its tooltip, the local row type, and a `forecastData` prop on `CapacityBalanceCard`.
- `src/components/dashboard/dashboard-design-system.test.tsx` — *modify*: add a test that forecast data routes to the dedicated chart and absence keeps the existing chart.
- `src/styles/globals.css` — *modify*: add the scoped `.capacity-forecast-chart` dotted/no-fill rule.
- `src/components/dashboard/spend-sections.tsx` — *modify*: pass `forecastData={capacityBalance.forecastSeries}` to `CapacityBalanceCard`.

**Test commands** (verified against `apps/api/pyproject.toml` `[tool.pytest.ini_options]` `pythonpath=["."] testpaths=["tests"]` and `apps/web/package.json` `"test": "vitest run"`):
- Backend: from `apps/api`, `pytest tests/test_dashboard_view_builder.py -v` (append `::test_name` to scope).
- Frontend: from `apps/web`, `npx vitest run <file>` (append `-t "<name>"` to scope).

---

### Task 1: Backend — `forecast_series` field on the view model

**Files:**
- Modify: `apps/api/app/services/dashboard_view_models.py:6` (import) and `:80-85` (`CapacityBalanceViewModel`)
- Test: `apps/api/tests/test_dashboard_view_builder.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CapacityBalanceViewModel.forecast_series: list[BalancePoint]`, defaulting to `[]` (Pydantic `default_factory=list`). Serializes to JSON as `forecast_series`. No change needed to `_empty_capacity_balance` (the default supplies `[]`).

- [ ] **Step 1: Write the failing test**

Add to `apps/api/tests/test_dashboard_view_builder.py`. First extend the existing `dashboard_view_models` import (line 26) to include `CapacityBalanceViewModel`:

```python
from app.services.dashboard_view_models import (
    CapacityBalanceViewModel,
    DashboardViewRange,
    DashboardViewResponse,
)
```

Then append this test:

```python
def test_capacity_balance_view_model_defaults_forecast_series_to_empty() -> None:
    model = CapacityBalanceViewModel(
        current_balance=100.0,
        current_balance_label="$100.00",
        current_balance_date="2026-06-08",
        daily_series=[],
        is_empty=False,
    )

    assert model.forecast_series == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_view_builder.py::test_capacity_balance_view_model_defaults_forecast_series_to_empty -v`
Expected: FAIL with `AttributeError: 'CapacityBalanceViewModel' object has no attribute 'forecast_series'`.

- [ ] **Step 3: Write minimal implementation**

In `apps/api/app/services/dashboard_view_models.py`, change the import on line 6:

```python
from pydantic import BaseModel, Field
```

Add the field to `CapacityBalanceViewModel` (between `daily_series` and `is_empty`):

```python
class CapacityBalanceViewModel(BaseModel):
    current_balance: float
    current_balance_label: str
    current_balance_date: str | None
    daily_series: list[BalancePoint]
    forecast_series: list[BalancePoint] = Field(default_factory=list)
    is_empty: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard_view_builder.py::test_capacity_balance_view_model_defaults_forecast_series_to_empty -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/dashboard_view_models.py apps/api/tests/test_dashboard_view_builder.py
git commit -m "feat(api): add forecast_series field to CapacityBalanceViewModel"
```

---

### Task 2: Backend — `_build_forecast_series` helper

**Files:**
- Modify: `apps/api/app/services/dashboard_view_builder.py` (add constants + function; place directly above `_build_capacity_balance` at ~L974)
- Test: `apps/api/tests/test_dashboard_view_builder.py`

**Interfaces:**
- Consumes: `BalancePoint`, `_format_currency`, `math`, `date`, `timedelta` (all already imported in the module).
- Produces:
  - `MAX_FORECAST_DAYS: int = 1825`
  - `def _build_forecast_series(*, current_balance: float, current_date: date, forecast_daily_spend: float, currency: str) -> list[BalancePoint]` — returns `[]` for non-positive spend/balance or runway over the cap; otherwise `days_to_zero + 1` points starting at `(current_date, current_balance)` and ending exactly at `0`.

- [ ] **Step 1: Write the failing test**

Extend the `dashboard_view_builder` import block (line 8-25) to add `_build_forecast_series`:

```python
    _build_forecast_series,
    _build_rate_index,
```

Append these tests:

```python
def test_build_forecast_series_projects_to_zero() -> None:
    series = _build_forecast_series(
        current_balance=100.0,
        current_date=date(2026, 6, 8),
        forecast_daily_spend=25.0,
        currency="USD",
    )

    assert [point.date for point in series] == [
        "2026-06-08",
        "2026-06-09",
        "2026-06-10",
        "2026-06-11",
        "2026-06-12",
    ]
    assert [point.balance for point in series] == [100.0, 75.0, 50.0, 25.0, 0.0]
    assert series[0].balance == 100.0  # join point == current balance
    assert series[-1].balance == 0.0
    assert series[-1].balance_label == "$0.00"


def test_build_forecast_series_clamps_final_point_to_zero() -> None:
    # ceil(100 / 30) = 4 -> 5 points; the final point clamps below zero to 0.0
    series = _build_forecast_series(
        current_balance=100.0,
        current_date=date(2026, 6, 8),
        forecast_daily_spend=30.0,
        currency="USD",
    )

    assert len(series) == 5
    assert series[-2].balance == pytest.approx(10.0, abs=0.01)  # 100 - 90
    assert series[-1].balance == 0.0


def test_build_forecast_series_empty_for_non_positive_inputs() -> None:
    base = dict(current_date=date(2026, 6, 8), currency="USD")
    assert _build_forecast_series(current_balance=100.0, forecast_daily_spend=0.0, **base) == []
    assert _build_forecast_series(current_balance=100.0, forecast_daily_spend=-5.0, **base) == []
    assert _build_forecast_series(current_balance=0.0, forecast_daily_spend=25.0, **base) == []
    assert _build_forecast_series(current_balance=-5.0, forecast_daily_spend=25.0, **base) == []


def test_build_forecast_series_empty_when_runway_exceeds_cap() -> None:
    # 1_000_000 / 0.01 = 100_000_000 days, far beyond MAX_FORECAST_DAYS
    series = _build_forecast_series(
        current_balance=1_000_000.0,
        current_date=date(2026, 6, 8),
        forecast_daily_spend=0.01,
        currency="USD",
    )

    assert series == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_view_builder.py -v -k build_forecast_series`
Expected: FAIL at import / collection with `ImportError: cannot import name '_build_forecast_series'`.

- [ ] **Step 3: Write minimal implementation**

In `apps/api/app/services/dashboard_view_builder.py`, directly above `def _build_capacity_balance(` (~L974), add:

```python
MAX_FORECAST_DAYS = 1825  # ~5 years; bounds the payload if the runway is implausibly long


def _build_forecast_series(
    *,
    current_balance: float,
    current_date: date,
    forecast_daily_spend: float,
    currency: str,
) -> list[BalancePoint]:
    """Project the balance forward at a flat daily spend until it reaches zero.

    Returns an empty list when there is nothing to forecast (non-positive spend
    or balance) or when the runway exceeds MAX_FORECAST_DAYS. The first point is
    the current (date, balance) so the forecast line joins the historical line;
    the final point lands exactly on zero (clamped).
    """
    if forecast_daily_spend <= 0 or current_balance <= 0:
        return []

    days_to_zero = math.ceil(current_balance / forecast_daily_spend)
    if days_to_zero > MAX_FORECAST_DAYS:
        return []

    points: list[BalancePoint] = []
    for offset in range(days_to_zero + 1):
        point_date = current_date + timedelta(days=offset)
        balance = max(current_balance - forecast_daily_spend * offset, 0.0)
        points.append(
            BalancePoint(
                date=point_date.isoformat(),
                balance=balance,
                balance_label=_format_currency(balance, currency),
            )
        )
    return points
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard_view_builder.py -v -k build_forecast_series`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/dashboard_view_builder.py apps/api/tests/test_dashboard_view_builder.py
git commit -m "feat(api): add _build_forecast_series drawdown projection"
```

---

### Task 3: Backend — wire forecast into capacity balance under the `is_billed` gate

**Files:**
- Modify: `apps/api/app/services/dashboard_view_builder.py` — `_build_capacity_balance` (~L974-1007) and its call site (~L429), plus a new `_trailing_average_spend` helper.
- Test: `apps/api/tests/test_dashboard_view_builder.py`

**Interfaces:**
- Consumes: `_build_forecast_series` (Task 2); `projection_daily: list[DollarPoint]` and `is_billed: bool` (both already in scope at the call site); `DollarPoint`.
- Produces:
  - `FORECAST_AVERAGE_WINDOW_DAYS: int = 7`
  - `def _trailing_average_spend(daily: list[DollarPoint]) -> float` — mean `spend` over the trailing 7 points (0.0 when empty).
  - `_build_capacity_balance(*, rows, currency, forecast_daily_spend: float = 0.0)` now populates `forecast_series`. The default keeps existing direct callers and the empty-balance path unchanged.

- [ ] **Step 1: Write the failing tests**

Extend the `dashboard_view_builder` import block to add `_build_capacity_balance` and
`_trailing_average_spend`:

```python
    _build_capacity_balance,
    _build_forecast_series,
    _trailing_average_spend,
```

Also ensure `DollarPoint` is imported from the view models (add to the existing
`dashboard_view_models` import block if not already present):

```python
from app.services.dashboard_view_models import (
    CapacityBalanceViewModel,
    DashboardViewRange,
    DashboardViewResponse,
    DollarPoint,
)
```

Append these tests. First, a direct unit test that the rate is the mean of the **last 7**
`projection_daily` spend points (not inferred via demo output):

```python
def test_trailing_average_spend_uses_last_seven_points() -> None:
    daily = [
        DollarPoint(
            date=f"2026-06-{day:02d}", spend=float(day), spend_label=f"${day}.00"
        )
        for day in range(1, 11)  # spends 1..10
    ]

    # mean of the last 7 (spends 4..10) = 49 / 7 = 7.0
    assert _trailing_average_spend(daily) == 7.0
    assert _trailing_average_spend([]) == 0.0


def test_build_capacity_balance_includes_forecast_when_spend_positive() -> None:
    rows = [
        {"usage_date": "2026-06-07", "currency": "USD", "balance": 150.0},
        {"usage_date": "2026-06-08", "currency": "USD", "balance": 100.0},
    ]

    vm = _build_capacity_balance(rows=rows, currency="USD", forecast_daily_spend=25.0)

    assert vm.forecast_series[0].date == "2026-06-08"  # joins the latest balance date
    assert vm.forecast_series[0].balance == 100.0
    assert vm.forecast_series[-1].balance == 0.0


def test_build_capacity_balance_omits_forecast_by_default() -> None:
    rows = [{"usage_date": "2026-06-08", "currency": "USD", "balance": 100.0}]

    vm = _build_capacity_balance(rows=rows, currency="USD")

    assert vm.forecast_series == []


def test_demo_view_includes_capacity_forecast() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["capacity_balance_daily"] = [
        {"usage_date": "2026-06-07", "currency": "USD", "balance": 12_000.0},
        {"usage_date": "2026-06-08", "currency": "USD", "balance": 11_875.25},
    ]

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 6),
        end_date=date(2026, 6, 8),
    )

    forecast = view.capacity_balance.forecast_series
    assert forecast, "demo (billed) view should include a forecast line"
    assert forecast[0].date == "2026-06-08"
    assert forecast[0].balance == pytest.approx(11_875.25, abs=0.01)
    assert forecast[-1].balance == 0.0
    assert all(
        forecast[i].balance >= forecast[i + 1].balance
        for i in range(len(forecast) - 1)
    )


def test_estimated_mode_has_no_capacity_forecast() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["capacity_balance_daily"] = [
        {"usage_date": "2026-06-07", "currency": "USD", "balance": 150.0},
        {"usage_date": "2026-06-08", "currency": "USD", "balance": 100.0},
    ]
    # model_copy(update=...) does not re-validate; pass a real SourceAvailability.
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 6),
        end_date=date(2026, 6, 8),
    )

    assert view.capacity_balance.daily_series  # balance line still present
    assert view.capacity_balance.forecast_series == []  # gated off in estimated mode
```

> Assumption to confirm while running: `test_demo_view_includes_capacity_forecast` relies on the demo `org_spend_daily` having positive consumption in the 7 days ending at the billing through-date (so the trailing average is > 0). The demo generator emits daily consumption, so this holds; if a future demo change zeroes that tail, the test would need an explicit `org_spend_daily` fixture.

> Note: `test_estimated_mode_has_no_capacity_forecast` is a **regression guard**, not a
> red test — once Task 1 defaults `forecast_series` to `[]`, the estimated path already
> yields `[]`. It is included here to lock that behavior against the `is_billed` gate so a
> future change can't silently turn the forecast on in estimated mode.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_view_builder.py -v -k "trailing_average or capacity_forecast or capacity_balance_includes or capacity_balance_omits"`
Expected: FAIL at collection — `ImportError: cannot import name '_trailing_average_spend'` (and `_build_capacity_balance` with the new `forecast_daily_spend` keyword does not exist yet). After the import resolves, the demo-forecast test stays red because `forecast_series` is empty until the call site is wired in Step 3.

- [ ] **Step 3: Write minimal implementation**

In `apps/api/app/services/dashboard_view_builder.py`, add the average helper directly above `_build_forecast_series`:

```python
FORECAST_AVERAGE_WINDOW_DAYS = 7


def _trailing_average_spend(daily: list[DollarPoint]) -> float:
    """Mean spend over the trailing FORECAST_AVERAGE_WINDOW_DAYS of a daily series."""
    window = daily[-FORECAST_AVERAGE_WINDOW_DAYS:]
    if not window:
        return 0.0
    return sum(point.spend for point in window) / len(window)
```

Replace `_build_capacity_balance` (~L974-1007) with:

```python
def _build_capacity_balance(
    *,
    rows: list[DatasetRow],
    currency: str,
    forecast_daily_spend: float = 0.0,
) -> CapacityBalanceViewModel:
    balance_by_date: dict[date, float] = {}
    for row in rows:
        if _optional_string(row.get("currency")) != currency:
            continue
        usage_date = _as_date(row["usage_date"])
        balance_by_date[usage_date] = balance_by_date.get(
            usage_date, 0.0
        ) + _required_float_field(row, "capacity_balance_daily", "balance")

    if not balance_by_date:
        return _empty_capacity_balance(currency)

    sorted_dates = sorted(balance_by_date)
    daily_series = [
        BalancePoint(
            date=usage_date.isoformat(),
            balance=balance_by_date[usage_date],
            balance_label=_format_currency(balance_by_date[usage_date], currency),
        )
        for usage_date in sorted_dates
    ]
    current_date = sorted_dates[-1]
    current_point = daily_series[-1]
    forecast_series = _build_forecast_series(
        current_balance=current_point.balance,
        current_date=current_date,
        forecast_daily_spend=forecast_daily_spend,
        currency=currency,
    )

    return CapacityBalanceViewModel(
        current_balance=current_point.balance,
        current_balance_label=current_point.balance_label,
        current_balance_date=current_point.date,
        daily_series=daily_series,
        forecast_series=forecast_series,
        is_empty=False,
    )
```

At the call site (~L429), replace the `_build_capacity_balance(...)` call with:

```python
    # Only forecast when the view ends at the latest known balance. For a custom
    # range ending before through_date, the balance endpoint (bounded by
    # view_range) and the projection window (ending at through_date) diverge, so
    # the runway would be drawn over a period we already have actual data for.
    forecast_anchored_to_latest = view_range.end_date == projection_range.end_date
    forecast_daily_spend = (
        _trailing_average_spend(projection_daily)
        if is_billed and forecast_anchored_to_latest
        else 0.0
    )
    capacity_balance = _build_capacity_balance(
        rows=capacity_balance_rows,
        currency=currency,
        forecast_daily_spend=forecast_daily_spend,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_view_builder.py -v`
Expected: PASS (all tests, including the pre-existing `test_builds_capacity_balance_from_latest_filtered_date`, which asserts only `daily_series`/`current_*` and is unaffected by the added `forecast_series`).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/dashboard_view_builder.py apps/api/tests/test_dashboard_view_builder.py
git commit -m "feat(api): compute capacity forecast from trailing-7-day spend (billed/demo)"
```

---

### Task 4: Frontend — parse `forecastSeries` with a legacy fallback

**Files:**
- Modify: `apps/web/src/lib/dashboard-contracts.ts` — `CapacityBalanceViewModel` (~L203-209), `parseCapacityBalanceViewModel` (~L613-637), `emptyCapacityBalanceViewModel` (~L696-706)
- Test: `apps/web/src/lib/dashboard-contracts.test.ts`

**Interfaces:**
- Consumes: `parseBalancePoint`, `readViewArray`, `hasViewValue` (all already in the module); JSON key `forecast_series` (Task 1/3).
- Produces: `CapacityBalanceViewModel.forecastSeries: BalancePoint[]` on the parsed view; `[]` when the key is absent (legacy views) or in the empty model.

- [ ] **Step 1: Write the failing test / update the legacy assertion**

In `apps/web/src/lib/dashboard-contracts.test.ts`:

(a) In the shared `preparedViewPayload`, add a `forecast_series` to its `capacity_balance` block (just after `daily_series`, ~L234-241):

```javascript
      forecast_series: [
        { date: "2026-06-08", balance: 11875.25, balance_label: "$11,875.25" },
        { date: "2026-06-09", balance: 0, balance_label: "$0.00" },
      ],
```

(b) In the existing `it("maps a prepared dashboard view response to camelCase fields", ...)` test, after the `capacityBalance.dailySeries[0]` assertion (~L340-344), add:

```javascript
    expect(parsed.capacityBalance.forecastSeries).toEqual([
      { date: "2026-06-08", balance: 11875.25, balanceLabel: "$11,875.25" },
      { date: "2026-06-09", balance: 0, balanceLabel: "$0.00" },
    ]);
```

(c) In `it("defaults missing capacity balance on older prepared dashboard views", ...)` (~L362-376), add `forecastSeries: []` to the expected object:

```javascript
    expect(parsed.capacityBalance).toEqual({
      currentBalance: 0,
      currentBalanceLabel: "$0.00",
      currentBalanceDate: null,
      dailySeries: [],
      forecastSeries: [],
      isEmpty: true,
    });
```

(d) Append a new test that a present-balance view lacking `forecast_series` still parses to `[]`:

```javascript
  it("defaults forecastSeries to empty when a prepared view omits it", () => {
    const payload: Record<string, unknown> = { ...preparedViewPayload };
    const capacity = {
      ...(payload.capacity_balance as Record<string, unknown>),
    };
    delete capacity.forecast_series;
    payload.capacity_balance = capacity;

    const parsed = parseDashboardView(payload);

    expect(parsed.capacityBalance.forecastSeries).toEqual([]);
    expect(parsed.capacityBalance.dailySeries).not.toHaveLength(0);
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `apps/web`): `npx vitest run src/lib/dashboard-contracts.test.ts`
Expected: FAIL — `forecastSeries` is `undefined` on the parsed model.

- [ ] **Step 3: Write minimal implementation**

In `apps/web/src/lib/dashboard-contracts.ts`:

Add the field to the type (~L203):

```typescript
export type CapacityBalanceViewModel = {
  currentBalance: number;
  currentBalanceLabel: string;
  currentBalanceDate: string | null;
  dailySeries: BalancePoint[];
  forecastSeries: BalancePoint[];
  isEmpty: boolean;
};
```

In `parseCapacityBalanceViewModel` (~L613), add the `forecastSeries` field after `dailySeries`:

```typescript
    dailySeries: readViewArray(payload, "daily_series", "dailySeries").map(
      parseBalancePoint,
    ),
    forecastSeries: hasViewValue(payload, "forecast_series", "forecastSeries")
      ? readViewArray(payload, "forecast_series", "forecastSeries").map(
          parseBalancePoint,
        )
      : [],
    isEmpty: readViewBoolean(payload, "is_empty", "isEmpty"),
```

In `emptyCapacityBalanceViewModel` (~L696), add `forecastSeries: []`:

```typescript
  return {
    currentBalance: 0,
    currentBalanceLabel: formatZeroCurrencyLabel(currency),
    currentBalanceDate: null,
    dailySeries: [],
    forecastSeries: [],
    isEmpty: true,
  };
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `apps/web`): `npx vitest run src/lib/dashboard-contracts.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/dashboard-contracts.ts apps/web/src/lib/dashboard-contracts.test.ts
git commit -m "feat(web): parse capacity forecastSeries with legacy fallback"
```

---

### Task 5: Frontend — dedicated forecast chart + tooltip on `CapacityBalanceCard`

**Files:**
- Modify: `apps/web/src/components/dashboard/dashboard-design-system.tsx` — `CapacityBalanceCard` (~L377-412); add `CapacityForecastChart`, `createCapacityTooltip`, the row type, and the data builder.
- Test: `apps/web/src/components/dashboard/dashboard-design-system.test.tsx`

**Interfaces:**
- Consumes: `AreaChart` (already imported from `@tremor/react`), `CustomTooltipProps` (already imported), `BalancePoint`, `resolveChartColor` (already imported), and in-file helpers `cx`, `createCurrencyTickFormatter`, `formatChartDateLabel`, `resolveTickInterval`.
- Produces: `CapacityBalanceCard` accepts a new optional prop `forecastData?: BalancePoint[]`. When `forecastData` is non-empty the card renders `CapacityForecastChart` (a two-series Tremor `AreaChart` on a wrapper carrying the `capacity-forecast-chart` class with `data-testid={chartTestId}`); otherwise it renders the existing `CurrencyLineChart` unchanged.

- [ ] **Step 1: Write the failing test**

Append to `apps/web/src/components/dashboard/dashboard-design-system.test.tsx`, inside the existing `describe("CapacityBalanceCard", ...)` block:

```tsx
  it("renders the dedicated forecast chart when forecast data is present", () => {
    const { container } = render(
      <CapacityBalanceCard
        ariaLabel="Capacity balance summary"
        chartTestId="capacity-balance-chart"
        currency="USD"
        data={[{ date: "2026-06-11", balance: 12345, balanceLabel: "$12,345.00" }]}
        forecastData={[
          { date: "2026-06-11", balance: 12345, balanceLabel: "$12,345.00" },
          { date: "2026-06-12", balance: 0, balanceLabel: "$0.00" },
        ]}
        label="Ending Balance"
        value="$12,345.00"
        testId="capacity-balance-card"
      />,
    );

    expect(
      container.querySelector(".capacity-forecast-chart"),
    ).not.toBeNull();
  });

  it("renders the single-series chart when no forecast data is present", () => {
    const { container } = render(
      <CapacityBalanceCard
        ariaLabel="Capacity balance summary"
        chartTestId="capacity-balance-chart"
        currency="USD"
        data={[{ date: "2026-06-11", balance: 12345, balanceLabel: "$12,345.00" }]}
        label="Ending Balance"
        value="$12,345.00"
        testId="capacity-balance-card"
      />,
    );

    expect(container.querySelector(".capacity-forecast-chart")).toBeNull();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `apps/web`): `npx vitest run src/components/dashboard/dashboard-design-system.test.tsx -t "forecast"`
Expected: FAIL — `forecastData` is not a prop and no `.capacity-forecast-chart` element exists.

- [ ] **Step 3: Write minimal implementation**

In `apps/web/src/components/dashboard/dashboard-design-system.tsx`, add the row type, data builder, tooltip, and chart component directly above `CapacityBalanceCard` (~L377):

```tsx
const CAPACITY_BALANCE_CATEGORY = "Balance";
const CAPACITY_FORECAST_CATEGORY = "Forecasted balance";

// Local row type: unlike ChartPoint (Record<string, string | number>), each
// series value is optional so history rows omit the forecast and forecast rows
// omit the balance, leaving recharts a gap (no bridge across the join).
type CapacityForecastRow = {
  date: string;
  Balance?: number;
  "Forecasted balance"?: number;
};

// Merge the historical balance and forecast series into one date-indexed rows
// array without mutating either input. The shared join date carries both values
// so the dotted forecast line starts exactly where the solid balance line ends.
function buildCapacityForecastData(
  balanceData: BalancePoint[],
  forecastData: BalancePoint[],
): CapacityForecastRow[] {
  const dates = Array.from(
    new Set([
      ...balanceData.map((point) => point.date),
      ...forecastData.map((point) => point.date),
    ]),
  ).sort();
  const balanceByDate = new Map(balanceData.map((p) => [p.date, p.balance]));
  const forecastByDate = new Map(forecastData.map((p) => [p.date, p.balance]));

  return dates.map((date) => {
    const balance = balanceByDate.get(date);
    const forecast = forecastByDate.get(date);
    return {
      date: formatChartDateLabel(date),
      ...(balance !== undefined ? { Balance: balance } : {}),
      ...(forecast !== undefined ? { "Forecasted balance": forecast } : {}),
    };
  });
}

// Tooltip for the forecast chart: shows only the series with a finite value at
// the hovered date (so the gap series is hidden) and never renders a "Total"
// row. The lime series reads as "Forecasted balance".
function createCapacityTooltip(
  valueFormatter: (value: number) => string,
): React.ComponentType<CustomTooltipProps> {
  function CapacityTooltip({ active, label, payload }: CustomTooltipProps) {
    if (!active || !payload || payload.length === 0) {
      return null;
    }
    const rows = payload.filter((entry) => Number.isFinite(Number(entry.value)));
    if (rows.length === 0) {
      return null;
    }
    return (
      <div className="rounded-md border border-hairline bg-surface px-3 py-2 shadow-lg">
        <p className="text-xs font-medium text-slate-100">{label}</p>
        <div className="mt-1 grid gap-1">
          {rows.map((entry, index) => {
            const name = entry.dataKey ?? entry.name;
            return (
              <div
                className="flex items-center justify-between gap-3 text-xs text-slate-400"
                key={String(name ?? index)}
              >
                <span className="flex items-center gap-1.5">
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: resolveChartColor(entry.color) }}
                  />
                  {String(name ?? "")}
                </span>
                <span className="tabular-nums text-slate-200">
                  {valueFormatter(Number(entry.value))}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return CapacityTooltip;
}

function CapacityForecastChart({
  balanceData,
  forecastData,
  currency,
  heightClass = "h-80",
  testId,
}: {
  balanceData: BalancePoint[];
  forecastData: BalancePoint[];
  currency: string;
  heightClass?: string;
  testId: string;
}) {
  const valueFormatter = createCurrencyTickFormatter(currency);
  const chartData = buildCapacityForecastData(balanceData, forecastData);

  return (
    <AreaChart
      autoMinValue
      categories={[CAPACITY_BALANCE_CATEGORY, CAPACITY_FORECAST_CATEGORY]}
      className={cx("capacity-forecast-chart mt-4 w-full", heightClass)}
      colors={["chart-purple", "chart-lime"]}
      connectNulls={false}
      customTooltip={createCapacityTooltip(valueFormatter)}
      data={chartData}
      data-chart-library="tremor"
      data-testid={testId}
      index="date"
      intervalType={resolveTickInterval(chartData.length)}
      showGradient
      showLegend={false}
      showTooltip
      tickGap={32}
      valueFormatter={valueFormatter}
      yAxisWidth={56}
    />
  );
}
```

Then update `CapacityBalanceCard` (~L377-412) to add the `forecastData` prop and branch:

```tsx
export function CapacityBalanceCard({
  ariaLabel,
  currency,
  label,
  value,
  data,
  forecastData,
  testId,
  chartTestId,
}: {
  ariaLabel: string;
  currency: string;
  label: string;
  value: string;
  data: BalancePoint[];
  forecastData?: BalancePoint[];
  testId?: string;
  chartTestId: string;
}) {
  const hasForecast = (forecastData?.length ?? 0) > 0;

  return (
    <section aria-label={ariaLabel} data-dashboard-panel="true">
      <Card className="p-6" data-testid={testId}>
        <Text>{label}</Text>
        <p className="mt-2 text-4xl font-semibold tracking-normal text-slate-50">
          {value}
        </p>
        {hasForecast ? (
          <CapacityForecastChart
            balanceData={data}
            forecastData={forecastData ?? []}
            currency={currency}
            heightClass="h-80"
            testId={chartTestId}
          />
        ) : (
          <CurrencyLineChart
            autoMinValue
            categories={["balance"]}
            currency={currency}
            data={data}
            heightClass="h-80"
            testId={chartTestId}
          />
        )}
      </Card>
    </section>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `apps/web`): `npx vitest run src/components/dashboard/dashboard-design-system.test.tsx`
Expected: PASS (existing card test + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/dashboard/dashboard-design-system.tsx apps/web/src/components/dashboard/dashboard-design-system.test.tsx
git commit -m "feat(web): render dedicated capacity forecast chart with focused tooltip"
```

---

### Task 6: Frontend — scoped dotted, fill-less CSS for the forecast series

**Files:**
- Modify: `apps/web/src/styles/globals.css` (append after the `.bar-segment-gap` rule, ~L65)

**Interfaces:**
- Consumes: the `.capacity-forecast-chart` wrapper class (Task 5) and the lime series color class Tremor emits for `chart-lime`.
- Produces: a dotted, fill-less lime forecast series; the purple balance area is untouched.

- [ ] **Step 1: Verify the emitted lime class name**

Tremor derives series classes from the color token. Confirm the class on the forecast
series before writing the selector (do not target by child order). Per Tremor 3.18.7,
`chart-lime` emits `stroke-chart-lime dark:stroke-chart-lime` and Recharts places it on
the **ancestor** `.recharts-area` `<g>` (the descendant paths are `.recharts-area-curve`
and `.recharts-area-area`) — so the descendant selectors in Step 2 are correct, and there
is **no** `fill-chart-lime` class. Confirm this against the rendered DOM: run the dev app
(`cd apps/web && npm run dev`), open the Overview, inspect the lime forecast series in
DevTools, and verify the `.recharts-area` group carries `stroke-chart-lime`. If a future
Tremor version emits a hashed/opacity variant instead, substitute the actual class in
Step 2.

> This is a manual visual/DOM verification step required by the spec. The dotted styling is CSS-only and cannot be asserted in jsdom (Vitest), so it is verified in the browser, not by a unit test.

- [ ] **Step 2: Add the scoped rule**

Append to `apps/web/src/styles/globals.css`:

```css
/*
 * Forecasted-drawdown overlay: the lime forecast series renders as a dotted line
 * with no area fill, leaving the purple balance area untouched. Tremor exposes no
 * per-series dash/fill props, so we target the forecast series by its lime color
 * class — never by child order — mirroring the .bar-segment-gap hook above.
 * Scoped to charts that opt in via .capacity-forecast-chart.
 */
.capacity-forecast-chart .stroke-chart-lime .recharts-area-curve {
  stroke-dasharray: 4 4;
}

.capacity-forecast-chart .stroke-chart-lime .recharts-area-area {
  display: none;
}
```

- [ ] **Step 3: Verify in the browser**

With `npm run dev` running and the demo Overview open, confirm: the balance line keeps its solid purple filled area; the forecast line is dotted lime with no fill and extends to the $0 baseline; hovering a forecast point shows a single "Forecasted balance" row (no "Total"); there is no legend.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/styles/globals.css
git commit -m "feat(web): dotted fill-less styling for the capacity forecast line"
```

---

### Task 7: Frontend — wire the forecast through `OverviewSection`

**Files:**
- Modify: `apps/web/src/components/dashboard/spend-sections.tsx` — the `CapacityBalanceCard` usage (~L81-89)
- Test: `apps/web/src/components/dashboard/spend-sections.test.tsx`

**Interfaces:**
- Consumes: `capacityBalance.forecastSeries` (Task 4) and the `forecastData` prop (Task 5).
- Produces: a wired Overview where a non-empty `forecastSeries` renders the forecast chart end-to-end.

- [ ] **Step 1: Write the failing test**

Append to `apps/web/src/components/dashboard/spend-sections.test.tsx` a test that renders `OverviewSection` with a forecast and asserts the dedicated chart appears. Match the file's existing import/render style; if it lacks a ready capacity view-model fixture, build one inline:

```tsx
  it("renders the capacity forecast chart in the overview when forecast data exists", () => {
    const { container } = render(
      <OverviewSection
        status="ready"
        currency="USD"
        capacityBalance={{
          currentBalance: 12345,
          currentBalanceLabel: "$12,345.00",
          currentBalanceDate: "2026-06-11",
          dailySeries: [
            { date: "2026-06-11", balance: 12345, balanceLabel: "$12,345.00" },
          ],
          forecastSeries: [
            { date: "2026-06-11", balance: 12345, balanceLabel: "$12,345.00" },
            { date: "2026-06-12", balance: 0, balanceLabel: "$0.00" },
          ],
          isEmpty: false,
        }}
        serviceSpend={EMPTY_SERVICE_SPEND}
        totalSpend={EMPTY_TOTAL_SPEND}
      />,
    );

    expect(container.querySelector(".capacity-forecast-chart")).not.toBeNull();
  });
```

> `EMPTY_SERVICE_SPEND` / `EMPTY_TOTAL_SPEND`: reuse the existing empty fixtures in `spend-sections.test.tsx` if present; otherwise construct minimal `isEmpty: true` view models matching `ServiceSpendViewModel` / `TotalSpendViewModel` (both render an empty-state branch, so only `isEmpty: true` plus required fields are needed). Confirm the exact shapes against `dashboard-contracts.ts` when writing the fixture.

- [ ] **Step 2: Run test to verify it fails**

Run (from `apps/web`): `npx vitest run src/components/dashboard/spend-sections.test.tsx -t "forecast"`
Expected: FAIL — no `.capacity-forecast-chart` element (the card is not yet receiving `forecastData`).

- [ ] **Step 3: Write minimal implementation**

In `apps/web/src/components/dashboard/spend-sections.tsx`, add the `forecastData` prop to the `CapacityBalanceCard` usage (~L81):

```tsx
        <CapacityBalanceCard
          ariaLabel="Capacity balance summary"
          currency={currency}
          label={buildEndingBalanceLabel(capacityBalance.currentBalanceDate)}
          value={capacityBalance.currentBalanceLabel}
          data={capacityBalance.dailySeries}
          forecastData={capacityBalance.forecastSeries}
          testId="capacity-balance-card"
          chartTestId="capacity-balance-tremor-line-chart"
        />
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `apps/web`): `npx vitest run src/components/dashboard/spend-sections.test.tsx`
Expected: PASS.

- [ ] **Step 5: Full regression + commit**

Run the whole suite for both apps to confirm nothing regressed:

```bash
( cd apps/api && pytest -q )
( cd apps/web && npx vitest run )
```

Expected: PASS.

```bash
git add apps/web/src/components/dashboard/spend-sections.tsx apps/web/src/components/dashboard/spend-sections.test.tsx
git commit -m "feat(web): wire capacity forecast through the Overview section"
```

---

## Self-Review

**Spec coverage:**
- View-model field + empty path → Task 1 (default supplies the empty path).
- Trailing-7-day average from `projection_daily`, `is_billed` gate → Task 3.
- `_build_forecast_series` join/clamp/terminate/cap → Task 2.
- Contract parse + legacy fallback → Task 4.
- Dedicated two-series chart, explicit colors, no-Total tooltip, "Forecasted balance" label, local undefined-permitting row type, `connectNulls={false}` → Task 5.
- Dotted + no-fill CSS by lime color class, verify emitted class → Task 6.
- One-chart-in-place (branch in `CapacityBalanceCard`) → Task 5; Overview wiring → Task 7.
- No `demo_data.py` changes → not touched. Reuse `BalancePoint` → Tasks 1-5.

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". The two browser-verification steps (Task 6) and the fixture-shape confirmations (Tasks 3, 7) are explicit, bounded verification actions required by the spec, with the exact thing to confirm named — not deferred implementation.

**Type consistency:** `forecast_series` (Python/JSON) ↔ `forecastSeries` (TS) consistent across Tasks 1/3/4. `forecastData` prop name consistent across Tasks 5/7. `CapacityForecastRow` keys `"Balance"`/`"Forecasted balance"` match the `categories` and `colors` order in `CapacityForecastChart`. `_build_forecast_series` and `_trailing_average_spend` signatures match their call site in Task 3.
