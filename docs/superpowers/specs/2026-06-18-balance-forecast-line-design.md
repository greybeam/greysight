# Forecasted-drawdown line on the Overview balance chart

**Date:** 2026-06-18
**Branch:** `feat/balance-forecast-line`
**Status:** Approved design — pending implementation plan

## Goal

On the dashboard **Overview**, the "Ending Balance" card shows a filled purple area
chart of capacity balance over the historical window. Add a **forecasted-drawdown
line**: project the current balance forward using recent average daily spend and draw
the decline until it reaches `$0`, as a **dotted line in brand lime** (`chart-lime`,
`#C9E930`).

- **No legend.** Tooltip-only on hover, labeled **"Forecasted balance"**.
- **Forecast model:** average daily spend over the **last 7 complete days**, applied
  daily from the current balance forward until balance hits `$0` (full runway —
  approved even when that is many months out).
- **One chart.** The forecast replaces the existing chart *in place*; the balance card
  always renders exactly one chart. When no forecast is available, the card renders
  exactly as it does today.

## Architecture decision: compute server-side

The forecast is computed in the Python view builder and shipped as finished points,
consistent with the rest of the dashboard (the prepared view model owns all derived
analytics; the frontend only renders). This avoids duplicating date math, clamp logic,
and currency formatting in TypeScript.

This was reviewed cross-model (Codex) for simplicity. Two simplifications were adopted
(see "Simplifications adopted" below).

## Backend (`apps/api`)

### View model
- Add `forecast_series: list[BalancePoint]` to `CapacityBalanceViewModel`, defaulting to
  `[]`. Reuses the existing `BalancePoint` shape `{date, balance, balance_label}` — no
  new point type or parser.
- `_empty_capacity_balance(...)` returns `forecast_series=[]`.

### Average daily spend (simplification #1 — reuse `projection_daily`, billed/demo only)
- `_build_dashboard_view_for_ranges` already computes `projection_daily`
  (`list[DollarPoint]`) before capacity balance is built
  (`dashboard_view_builder.py` ~L388). Derive the forecast rate by **averaging the last
  7 `spend` values of `projection_daily`** and pass a single `forecast_daily_spend: float`
  into `_build_capacity_balance(...)`.
- **No standalone averaging helper over raw `org_spend_daily` rows.**
- **Gate on `is_billed`** (`data_mode in {"billed", "demo"}`, `dashboard_view_builder.py`
  L251). The forecast is computed **only** in billed/demo mode; in estimated mode
  `forecast_daily_spend` is `0` so `forecast_series` is `[]` (a balance chart, if present,
  renders without a forecast line).
- **Why the gate (resolves the prior open question, per Codex review):** in billed/demo
  mode `projection_range` is the inclusive 30-day window ending at `billing_through_date`
  and `_daily_billed_totals` zero-fills every date, so `projection_daily[-7:]` is exactly
  the 7 complete days `through_date - 6 … through_date`, including true zero-spend days.
- **Also gate on the view ending at `through_date`** (`view_range.end_date ==
  projection_range.end_date`): the capacity balance endpoint is bounded by `view_range`,
  but the projection window ends at `through_date`. For a **custom range ending before
  `through_date`** the two diverge, so projecting the older balance forward at the trailing
  rate would draw a runway over a period we already have actual data for. In that case
  `forecast_daily_spend` is `0` → `forecast_series` is `[]` (history-only). Relative ranges
  always end at `through_date`, so they are unaffected.
  In **estimated** mode `projection_daily` is built only from `service_spend_daily` while
  `account_usage_through_date` is the max across several sources, so a newer non-service
  source could make `[-7:]` include artificial zero days. Gating on `is_billed` avoids
  that entirely with no extra code. (Estimated mode also rarely carries org capacity
  balance, since that comes from `organization_usage`.)

### Forecast generation
- `_build_forecast_series(current_balance, current_date, forecast_daily_spend, currency)`:
  - Returns `[]` when `forecast_daily_spend <= 0` or `current_balance <= 0`
    (flat/growing balance, no spend, or already depleted → no line).
  - Precompute `days_to_zero = ceil(current_balance / forecast_daily_spend)`.
  - **Safety cap:** if `days_to_zero > MAX_FORECAST_DAYS` (~5 years), return `[]` — do
    **not** emit a fake `0` at the cap (the cap is not the real depletion date). This
    removes the "terminate at 0" vs "cap" conflict Codex flagged. In real data the runway
    is well within the cap and terminates naturally.
  - Otherwise emit one `BalancePoint` per day:
    - First point is `(current_date, current_balance)` so the dotted line **joins** the
      end of the solid balance line.
    - Each subsequent day subtracts `forecast_daily_spend`, clamped at `0`; the final
      point lands exactly on `0` at `current_date + days_to_zero`.

### Tests (Python, pytest, TDD)
- Average is the mean of the last 7 `projection_daily` spend values (billed/demo).
- Forecast decrements correctly, clamps to `0`, and terminates with a `0` point at
  `current_date + days_to_zero`.
- First forecast point equals the current balance / current date (the join point).
- Empty forecast when rate `<= 0`, when there is no spend, and when balance `<= 0`.
- Empty forecast when `days_to_zero` exceeds `MAX_FORECAST_DAYS` (no fake-zero point).
- **Estimated mode → `forecast_series == []`** (the `is_billed` gate), even when a balance
  series is present.

## Frontend (`apps/web`)

### Contract parsing (`dashboard-contracts.ts`)
- Add `forecastSeries: BalancePoint[]` to `CapacityBalanceViewModel`.
- Parse it in `parseCapacityBalanceViewModel` reusing `parseBalancePoint`, with a
  **legacy `[]` fallback** (via `hasViewValue`) so older stored views don't throw.
- `emptyCapacityBalanceViewModel` returns `forecastSeries: []`.

### Chart (`dashboard-design-system.tsx`)
- `CapacityBalanceCard` gains an optional `forecastData?: BalancePoint[]`.
  - **When non-empty:** render a **dedicated capacity-balance chart path** — a Tremor
    `AreaChart` with two series: **Balance** (`chart-purple`, filled area, visually
    unchanged) and **Forecasted balance** (`chart-lime`, dotted line, no fill). Explicit
    `colors={["chart-purple", "chart-lime"]}` (the default `getSeriesColors` would assign
    pastels for 2 series).
  - **When empty/absent:** render exactly the current single-series chart. No visual or
    structural change in the no-forecast case.
- The forecast path is **not** routed through `CurrencyLineChart`, deliberately: that
  helper switches multi-series charts to a (fill-less) `LineChart`, and its shared tooltip
  injects a bogus multi-series **"Total"** row. The dedicated path uses a focused tooltip
  that labels series plainly and shows **"Forecasted balance"** for the lime series, with
  **no "Total" row** and **no legend**.
- Data shape: a single rows array keyed by date. History rows carry the Balance value;
  forecast rows carry the Forecasted-balance value; the join date carries both so the two
  lines meet. Absent values are left **`undefined`** (recharts renders a gap), so each line
  only draws over its own date span. Pass **`connectNulls={false}`** explicitly so the
  forecast line does not bridge back across the historical gap. Use a **local row type
  that permits `undefined` values** — do not reuse `ChartPoint` (`Record<string, string |
  number>`), which won't type-check the gaps.

### Dotted + no-fill styling (`globals.css`)
- One scoped rule, `.capacity-forecast-chart`, mirroring the existing `.bar-segment-gap`
  pattern. Tremor 3.18.7 exposes only chart-level `showGradient`/`connectNulls`, not
  per-series dash/fill props, so a scoped CSS hook is the minimal approach.
- **Target the forecast series by its lime color class, not by child order** — Tremor
  emits a color-derived class (e.g. `.stroke-chart-lime`) on each series. Apply
  `stroke-dasharray` to `.capacity-forecast-chart .stroke-chart-lime .recharts-area-curve`
  and suppress the fill on `.capacity-forecast-chart .stroke-chart-lime .recharts-area-area`,
  so the purple balance area is never touched. **Verify the exact emitted class name at
  implementation** (confirm against the rendered DOM / Tremor's `getColorClassNames`).

### Wiring (`spend-sections.tsx`)
- `OverviewSection` passes `forecastData={capacityBalance.forecastSeries}` to
  `CapacityBalanceCard`. The empty-state branch is unchanged.

### Serialization boundary
- `forecast_series` serializes to JSON as `forecast_series` (snake_case) and is read by
  the parser via the existing snake/camel-aware `readViewArray(..., "forecast_series",
  "forecastSeries")` convention used for `daily_series` — no new serialization machinery.

### Tests (TypeScript)
- `parseCapacityBalanceViewModel` parses `forecastSeries`, including the legacy `[]`
  fallback for views that omit it.
- `CapacityBalanceCard` / `OverviewSection`: forecast data routes to the dedicated
  two-series chart path; empty/absent forecast keeps the existing single-series chart
  unchanged.

## Simplifications adopted (from Codex cross-model review)

1. **Reuse `projection_daily[-7:]`** for the average instead of a new raw-row averaging
   helper, **gated to billed/demo mode** (`is_billed`) so the slice is always the
   zero-filled billed series ending at `through_date` (see "Average daily spend" above).
2. **Dedicated capacity chart + tooltip path**, not `CurrencyLineChart` and not a new
   generalized chart abstraction — and it **replaces the existing chart in place**, so the
   Overview still shows a single balance chart.

Also confirmed by review and retained:
- Ship forecast points from the backend (frontend derivation rejected).
- Reuse `BalancePoint` (incl. `balance_label`) rather than a second point type.
- **No `demo_data.py` changes** — the demo already emits `CONSUMPTION` org-spend rows and
  a drawn-down `capacity_balance_daily`, so the forecast falls out of existing demo data
  and renders in the demo dashboard for visual verification.

## Edge cases
- Balance flat or growing (avg `<= 0`) → no forecast line.
- Balance already `0` → no forecast line.
- The dotted line stretching the x-axis many months into the future is **expected and
  intended** — it is the runway story.

## Out of scope
- Changing the historical balance line's appearance.
- Any new legend, axis annotation, or controls.
- Alternative forecast models (e.g. trend/regression) — fixed 7-day average only.
