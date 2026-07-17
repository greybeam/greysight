# Chart design system

Conventions shared across the dashboard's Tremor/Recharts charts. Code is the
source of truth — this doc is a map, not a spec; follow the referenced files
for exact behavior.

## Colors

`src/lib/chart-colors.ts`

- `getSeriesColors(categories, options)` resolves category names to color
  tokens:
  - A single real series → `PRIMARY_CHART_COLOR` (`chart-purple`), unless the
    caller passes `singleSeriesPrimary: false` (stacked charts always pass
    this, since even a lone stacked series should take a pastel, not brand
    purple).
  - Multiple series → consecutive colors from `SERIES_PALETTE` (`chart-1`
    through `chart-14`), assigned positionally.
- `orderCategoriesByTotal(categories, rows)` sorts stacked series by total
  value descending, so the largest series anchors the base of the stack and
  takes the first palette color.
- `SERIES_PALETTE` is capped at `STACKED_SERIES_LIMIT` (14, defined in
  `src/lib/stacked-series-bucketing.ts`). Series beyond the cap are folded
  into a synthetic "Other" bucket (`bucketStackedSeries`), which always takes
  the last palette color (`OTHER_SERIES_COLOR` = `chart-14`) and is pinned to
  the top of the stack regardless of its total.

## Stacked-segment gap

**Every stacked bar chart must enable the segment gap.** Opt in one of two
ways, both landing the `bar-segment-gap` class on an ancestor of the chart's
segments (it targets both `path` and `rect` segment nodes):

- Tremor-backed stacked charts: pass `segmentGap` to `SpendBarChart` (which
  forwards it to `StackedSpendBarChart`).
- Raw Tremor `<BarChart stack>`: add the `bar-segment-gap` class directly to
  the chart's `className`, as `suspensions-chart.tsx` does.

See `src/styles/globals.css` for the mechanism (stroke color, `crispEdges`)
and rationale — one source of truth, not duplicated here.

## Date axis

`formatChartDateLabel` (`dashboard-design-system.tsx`) formats an ISO calendar
date (`"2026-07-09"`) in UTC — `day: "2-digit", month: "short", timeZone:
"UTC"`. This avoids the browser-local shift that would otherwise mislabel
calendar-day buckets near a UTC offset boundary. `formatChartDateLabelWithYear`
is the year-inclusive variant used where dates could span years (e.g. the
capacity-forecast tooltip header).

## Tooltip

`createChartTooltip` (`dashboard/chart-tooltip.tsx`) builds a shared custom
tooltip component for both Tremor and Recharts charts (their payload shapes
are structurally compatible). It sorts the hovered point's series by value,
adds a "Total" row for multi-series points, and can split out a rolling-average
overlay into its own labeled row via `options.averageKey` /
`options.averageLabel`.

## Stacked-chart entry points

Two ways to render a stacked bar chart, depending on need:

- **`StackedSpendBarChart`** (`dashboard/stacked-bar-chart.tsx`) — a Recharts
  `ComposedChart` with stacked bars plus a 7-day rolling-average trendline
  overlay. Used when a chart needs the trendline; Tremor's sealed `BarChart`
  can't host a line series. Reached via `SpendBarChart({ stack: true })`.
- **Raw Tremor `<BarChart stack>`** — for simple stacked charts with no
  trendline (e.g. `automated-savings/suspensions-chart.tsx`). Lighter-weight,
  but remember to add `bar-segment-gap` manually since it isn't wrapped by
  `SpendBarChart`.
