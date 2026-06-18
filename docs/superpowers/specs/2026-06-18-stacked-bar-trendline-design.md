# Stacked bar chart 7-day rolling-average trendline (issue #19)

## Goal

Overlay a 7-day rolling-average trendline on the three main **stacked** spend bar
charts (Overview "Total Spend", Warehouse, Storage), drawn in the brand purple
(`chart-purple` / `#9F57E7`). The same rolling-average value also appears in the
hover tooltip, directly under the existing **Total** row.

## Why this is more than a prop change

The charts use Tremor `3.18.7`, whose `BarChart` is a sealed component with no
way to add a line series, and this Tremor version ships no `ComboChart`. We
therefore render the **stacked** path with Recharts' `ComposedChart` (the same
library Tremor renders under the hood) — stacked `<Bar>`s plus a `<Line>` on a
single shared y-axis. The line is an average of the same dollar values, so it
sits at its true height against the bars with no second scale.

Non-stacked `SpendBarChart`, `SpendLineChart`, and `CurrencyLineChart` stay on
Tremor, untouched.

## Rolling average

- **Subject:** the daily **total** (sum of all stacked segments for that day).
- **Window:** trailing 7 days. Days 1–6 average over whatever days exist so far
  (no blank lead-in) rather than waiting for a full window.
- **Where:** computed client-side from the existing view-model data. No backend
  change.

## Components

- **`src/lib/rolling-average.ts`** (new, pure, unit-tested)
  - `ROLLING_AVERAGE_KEY`, `ROLLING_AVERAGE_LABEL` (`"7-day avg"`), `ROLLING_AVERAGE_WINDOW` (`7`).
  - `stackedDailyTotals(rows, categories): number[]` — per-row sum of numeric category values.
  - `rollingAverage(values, window): number[]` — trailing average over available data.
  - `withRollingAverage(rows, categories, ...)` — returns new rows each carrying
    a `ROLLING_AVERAGE_KEY` numeric field (immutable; original rows untouched).

- **`src/components/dashboard/stacked-bar-chart.tsx`** (new)
  - Recharts `ResponsiveContainer` > `ComposedChart` with stacked `<Bar>`s
    (first = bottom, matching the current Tremor ordering) + a purple `<Line>`
    (`dataKey = ROLLING_AVERAGE_KEY`, 2px, no dots).
  - Dark-theme styling mirrors the current charts: hairline `CartesianGrid`,
    slate axis ticks, `yAxisWidth` 56, shared currency tick formatter, dense-tick
    interval rule reused from the design system.
  - Wrapper `<div>` carries `data-testid` and `data-chart-library="recharts"`.
  - Keeps the `bar-segment-gap` class so the existing segment-stroke CSS applies.

- **`createChartTooltip` (in `dashboard-design-system.tsx`)** — gains an optional
  config `{ averageKey, averageLabel }`. When set, the matching payload entry is
  split out: excluded from the per-series rows and the Total sum, and rendered as
  a dedicated labeled row under Total. Default (no config) is byte-for-byte the
  current behavior, so existing tooltip tests stay green; Tremor line/area charts
  pass no config.

- **`SpendBarChart`** — when `stack`, augments the chart data with the rolling
  average and delegates to `StackedSpendBarChart`; otherwise keeps the Tremor
  `BarChart` branch.

## Dependency

Declare `recharts` (`^2.15.4`, already resolved transitively via Tremor) as an
explicit dependency in `apps/web/package.json`.

## Tests

- `rolling-average.test.ts` — totals, trailing window incl. partial lead-in,
  immutability.
- `dashboard-design-system.test.tsx` — extend the `createChartTooltip` block with
  the average-row case (excluded from Total, rendered with its label). Existing
  cases unchanged.
- `spend-sections.test.tsx` — update the 5 `data-chart-library` assertions from
  `"tremor"` to `"recharts"` for the stacked charts. Opaque test ids are kept.

Explicitly **not** added: brittle Recharts DOM-shape assertions (Recharts renders
nothing measurable in jsdom).
