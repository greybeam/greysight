"use client";

import type React from "react";
import type { ReactNode } from "react";
import { AreaChart, BarChart, Card, LineChart, Text } from "@tremor/react";
import type { CustomTooltipProps, IntervalType } from "@tremor/react";

import type {
  BalancePoint,
  DashboardViewRange,
  DollarPoint,
  RankedBarRow,
} from "../../lib/dashboard-contracts";
import {
  getSeriesColors,
  orderCategoriesByTotal,
  PRIMARY_CHART_COLOR,
  resolveChartColor,
} from "../../lib/chart-colors";

type DashboardGridColumns = 2 | 3 | 4;

type DashboardPanelSpan = 1 | 2 | 3;

type ChartPoint = {
  date: string;
} & Record<string, string | number>;

function cx(...classes: Array<string | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

// Tailwind needs literal class strings, so map spans to fixed col-span classes
// rather than interpolating the number into the class name.
const PANEL_SPAN_CLASS: Record<DashboardPanelSpan, string> = {
  1: "",
  2: "lg:col-span-2",
  3: "lg:col-span-3",
};

function resolvePanelSpanClass(span?: DashboardPanelSpan): string {
  return span ? PANEL_SPAN_CLASS[span] : "";
}

export function DashboardSection({
  ariaLabel,
  children,
  testId,
  title,
}: {
  ariaLabel: string;
  children: ReactNode;
  testId: string;
  title: string;
}) {
  return (
    <section
      aria-label={ariaLabel}
      className="grid gap-4"
      data-testid={testId}
    >
      <h2 className="text-xl font-semibold tracking-tight text-slate-100">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function DashboardGrid({
  children,
  columns,
  testId,
}: {
  children: ReactNode;
  columns: DashboardGridColumns;
  testId: string;
}) {
  const columnClass = {
    2: "lg:grid-cols-2",
    3: "lg:grid-cols-3",
    4: "lg:grid-cols-4",
  }[columns];

  return (
    <div className={cx("grid gap-4", columnClass)} data-testid={testId}>
      {children}
    </div>
  );
}

export function DashboardPanel({
  ariaLabel,
  badge,
  children,
  className,
  fill,
  span,
  title,
}: {
  ariaLabel: string;
  badge?: ReactNode;
  children: ReactNode;
  className?: string;
  // When true the panel stretches to fill its grid row (matching the row's
  // chart card) and lays out as a flex column so a `flex-1` child can claim the
  // leftover height. A scrollable child (e.g. RankedSpendBars) uses the
  // absolute-fill pattern internally so it scrolls instead of growing the row.
  fill?: boolean;
  span?: DashboardPanelSpan;
  title: string;
}) {
  return (
    <section
      aria-label={ariaLabel}
      className={cx(resolvePanelSpanClass(span) || undefined, fill ? "h-full" : undefined)}
      data-dashboard-panel="true"
    >
      <Card className={cx("p-6", fill ? "flex h-full flex-col" : undefined, className)}>
        <div className="flex items-center gap-2">
          <Text>{title}</Text>
          {badge}
        </div>
        {fill ? (
          <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        ) : (
          children
        )}
      </Card>
    </section>
  );
}

// Drops the cents from a server-formatted currency label once the magnitude
// reaches $10 so the ranked list reads compactly and the value column fits in
// narrow cards; sub-$10 rows keep their decimals where the precision matters.
// We round to whole units (not truncate) from the canonical numeric spend so a
// $10.99 row reads as "$11" instead of understating it as "$10", then swap the
// rounded, grouped integer back into the label's numeric run — preserving
// whatever currency symbol/suffix the server attached. The server formats with
// Python `,.2f` (comma groups, period decimal), so en-US grouping matches the
// source and the first run of digits/commas/decimals is the amount to replace.
function compactSpendLabel(row: RankedBarRow): string {
  if (Math.abs(row.spend) < 10) {
    return row.spendLabel;
  }
  const rounded = Math.round(Math.abs(row.spend)).toLocaleString("en-US");
  return row.spendLabel.replace(/[\d,]+(?:\.\d+)?/, rounded);
}

export function RankedSpendBars({ rows }: { rows: RankedBarRow[] }) {
  const visibleRows = rows.filter((row) => Math.round(row.spend * 100) !== 0);

  if (visibleRows.length === 0) {
    return <p className="mt-4 text-xs text-slate-400">No ranked spend data</p>;
  }

  // Absolute-fill pattern so a long ranked list never forces its grid row to
  // grow. The relative wrapper takes the leftover flex height inside a `fill`
  // panel; the absolutely-positioned <ul> contributes zero intrinsic height, so
  // the row height is driven by the sibling chart card (fixed h-64/h-80) and the
  // list scrolls within whatever space remains. Below lg the dashboard grids are
  // single-column with no chart sibling to set the height, so the wrapper keeps
  // a min-h-[16rem] floor there and only releases it (lg:min-h-0) once the row
  // has a chart neighbour to cap against.
  //
  // The grid owns the column tracks so name / bar / value line up across rows.
  // The name and bar tracks have a 0 (name) / small (bar) minimum so they give
  // up width on narrow cards instead of overflowing — the value track stays
  // `auto` so the dollar amount is never the column that gets clipped. The name
  // cell carries truncate/min-w-0 (it ellipsizes), with title= exposing the
  // full name on hover for objects with long names.
  // role="list"/role="listitem" restore semantics that a `contents` li can drop
  // in some screen readers; the inner cell spans (not the `contents` li, which
  // cannot truncate) carry truncate/min-w-0.
  return (
    <div className="relative mt-4 min-h-[16rem] flex-1 lg:min-h-0">
      <ul
        className="dashboard-scroll absolute inset-0 grid grid-cols-[minmax(0,9rem)_minmax(1.5rem,1fr)_auto] content-start items-center gap-x-3 gap-y-2 overflow-y-auto"
        role="list"
      >
        {visibleRows.map((row) => (
          <li className="contents" key={row.name} role="listitem">
            <span
              className="min-w-0 truncate text-xs text-slate-400"
              title={row.name}
            >
              {row.name}
            </span>
            <span className="h-2 rounded bg-hairline">
              <span
                className="block h-2 rounded bg-chart-purple"
                style={{ width: `${row.barWidthPercent}%` }}
              />
            </span>
            <span className="text-xs font-semibold tabular-nums text-slate-200">
              {compactSpendLabel(row)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function TotalSpendBarCard({
  ariaLabel,
  categories,
  chart,
  currency,
  emptyValueMessage,
  label,
  value,
  data,
  span,
  testId,
  chartTestId,
}: {
  ariaLabel: string;
  categories: string[];
  // Optional chart slot. When provided it replaces the default stacked bar
  // chart, letting callers render an empty state in its place while keeping
  // the KPI visible.
  chart?: ReactNode;
  currency: string;
  // Rendered in place of the KPI value when total spend has no data but the
  // service breakdown chart is still worth showing.
  emptyValueMessage?: string;
  label: string;
  value?: string;
  data: ChartPoint[];
  span?: DashboardPanelSpan;
  testId?: string;
  chartTestId: string;
}) {
  return (
    <section
      aria-label={ariaLabel}
      className={cx(resolvePanelSpanClass(span) || undefined, "h-full")}
      data-dashboard-panel="true"
    >
      <Card className="flex h-full flex-col p-6" data-testid={testId}>
        <Text>{label}</Text>
        {value === undefined ? (
          <p className="mt-2 text-sm text-slate-400">{emptyValueMessage}</p>
        ) : (
          <p className="mt-2 text-4xl font-semibold tracking-normal text-slate-50">
            {value}
          </p>
        )}
        {chart ?? (
          <SpendBarChart
            categories={categories}
            currency={currency}
            data={data}
            heightClass="h-80"
            segmentGap
            showLegend={false}
            stack
            testId={chartTestId}
          />
        )}
      </Card>
    </section>
  );
}

export function CapacityBalanceCard({
  ariaLabel,
  currency,
  label,
  value,
  data,
  testId,
  chartTestId,
}: {
  ariaLabel: string;
  currency: string;
  label: string;
  value: string;
  data: BalancePoint[];
  testId?: string;
  chartTestId: string;
}) {
  return (
    <section aria-label={ariaLabel} data-dashboard-panel="true">
      <Card className="p-6" data-testid={testId}>
        <Text>{label}</Text>
        <p className="mt-2 text-4xl font-semibold tracking-normal text-slate-50">
          {value}
        </p>
        <CurrencyLineChart
          autoMinValue
          categories={["balance"]}
          currency={currency}
          data={data}
          heightClass="h-80"
          testId={chartTestId}
        />
      </Card>
    </section>
  );
}

// Few points (e.g. a 7-day window) should label every day; longer ranges let
// Tremor auto-thin labels so they don't crowd.
const DENSE_TICK_MAX_POINTS = 10;

// Recharts' XAxis accepts a numeric `interval` (0 = render every tick), but
// Tremor's IntervalType doesn't surface the numeric form, so we cast the 0.
const SHOW_EVERY_TICK = 0 as unknown as IntervalType;

function resolveTickInterval(pointCount: number): IntervalType {
  return pointCount <= DENSE_TICK_MAX_POINTS
    ? SHOW_EVERY_TICK
    : "equidistantPreserveStart";
}

export function SpendLineChart({
  color = PRIMARY_CHART_COLOR,
  currency,
  data,
  heightClass = "h-64",
  testId,
}: {
  color?: string;
  currency: string;
  data: DollarPoint[];
  heightClass?: string;
  testId: string;
}) {
  return (
    <CurrencyLineChart
      autoMinValue={false}
      categories={["spend"]}
      colors={[color]}
      currency={currency}
      data={data}
      heightClass={heightClass}
      minValue={0}
      testId={testId}
    />
  );
}

export function CurrencyLineChart({
  autoMinValue = false,
  categories,
  colors,
  currency,
  data,
  heightClass = "h-64",
  minValue,
  testId,
}: {
  autoMinValue?: boolean;
  categories: string[];
  colors?: string[];
  currency: string;
  data: ChartPoint[];
  heightClass?: string;
  minValue?: number;
  testId: string;
}) {
  const valueFormatter = createCurrencyTickFormatter(currency);
  const chartData = data.map((point) => ({
    ...point,
    date: formatChartDateLabel(String(point.date)),
  }));

  // Props shared by the LineChart / AreaChart branches. Both Tremor charts
  // extend the same BaseChartProps, so a single object keeps them identical
  // apart from the component swap below.
  const sharedChartProps = {
    autoMinValue,
    categories,
    className: cx("mt-4 w-full", heightClass),
    colors: colors ?? getSeriesColors(categories),
    customTooltip: createChartTooltip(valueFormatter),
    data: chartData,
    "data-chart-library": "tremor",
    "data-testid": testId,
    index: "date",
    intervalType: resolveTickInterval(chartData.length),
    minValue,
    showLegend: false,
    showTooltip: true,
    tickGap: 32,
    valueFormatter,
    yAxisWidth: 56,
  } as const;

  // A single-series chart reads cleaner as a filled area (gradient on by
  // default); multi-series stays a LineChart so overlapping fills don't muddy
  // the comparison.
  if (categories.length === 1) {
    return <AreaChart {...sharedChartProps} showGradient />;
  }

  return <LineChart {...sharedChartProps} />;
}

export function SpendBarChart({
  categories,
  currency,
  data,
  heightClass = "h-64",
  segmentGap = false,
  showLegend = true,
  stack = false,
  testId,
}: {
  categories: string[];
  currency: string;
  data: ChartPoint[];
  heightClass?: string;
  // Opt-in: draw a 1px surface-colored stroke between stacked segments so small
  // segments stay legible. Backed by the `.bar-segment-gap` rule in globals.css
  // since Tremor doesn't expose a per-bar stroke. Only meaningful with `stack`.
  segmentGap?: boolean;
  showLegend?: boolean;
  stack?: boolean;
  testId: string;
}) {
  const valueFormatter = createCurrencyTickFormatter(currency);
  const chartData = data.map((point) => ({
    ...point,
    date: formatChartDateLabel(String(point.date)),
  }));
  // Stacked charts order series by descending total so the largest sits at the
  // bottom of the stack and takes the first palette color. Tremor's BarChart
  // emits categories[0] as the first (bottom/base) stacked Recharts <Bar>.
  const orderedCategories = stack
    ? orderCategoriesByTotal(categories, chartData)
    : categories;

  return (
    <BarChart
      categories={orderedCategories}
      className={cx(
        "mt-4 w-full",
        heightClass,
        segmentGap ? "bar-segment-gap" : undefined,
      )}
      colors={getSeriesColors(orderedCategories)}
      customTooltip={createChartTooltip(valueFormatter)}
      data={chartData}
      data-chart-library="tremor"
      data-testid={testId}
      index="date"
      intervalType={resolveTickInterval(chartData.length)}
      showLegend={showLegend}
      showTooltip
      stack={stack}
      tickGap={32}
      valueFormatter={valueFormatter}
      yAxisWidth={56}
    />
  );
}

export function createChartTooltip(
  valueFormatter: (value: number) => string,
): React.ComponentType<CustomTooltipProps> {
  // One shared coercion for both per-row display and the Total so they always
  // agree: a non-numeric entry value reads as 0 everywhere rather than rendering
  // NaN in the row while the total silently skips it.
  function toNumericValue(value: unknown): number {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : 0;
  }

  function ChartTooltip({ active, label, payload }: CustomTooltipProps) {
    if (!active || !payload || payload.length === 0) {
      return null;
    }

    // Sort an immutable copy by this point's value, descending, so the largest
    // value at the hovered point is the top row. This is the point's own value
    // order and can differ from the stacked-bar order, which is fixed by each
    // series' total across the whole range. Single-series line charts are
    // unaffected (a one-row sort is a no-op).
    const rows = [...payload].sort(
      (a, b) => toNumericValue(b.value) - toNumericValue(a.value),
    );

    // Multi-series points get a summary "Total" row so the hovered stack's
    // combined value is legible at a glance. Single-series tooltips omit it
    // (the lone row already is the total).
    const showTotal = rows.length > 1;
    const total = rows.reduce(
      (sum, entry) => sum + toNumericValue(entry.value),
      0,
    );

    return (
      <div className="rounded-md border border-hairline bg-surface px-3 py-2 shadow-lg">
        <p className="text-xs font-medium text-slate-100">{label}</p>
        <div className="mt-1 grid gap-1">
          {rows.map((entry, index) => {
            const name = entry.dataKey ?? entry.name;
            const key = String(name ?? index);

            return (
              <div
                className="flex items-center justify-between gap-3 text-xs text-slate-400"
                key={key}
              >
                <span className="flex items-center gap-1.5">
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: resolveChartColor(entry.color) }}
                  />
                  {String(name ?? "")}
                </span>
                <span className="tabular-nums text-slate-200">
                  {valueFormatter(toNumericValue(entry.value))}
                </span>
              </div>
            );
          })}
          {showTotal ? (
            <div className="mt-1 flex items-center justify-between gap-3 border-t border-hairline pt-1 text-xs font-medium text-slate-100">
              <span>Total</span>
              <span className="tabular-nums text-slate-100">
                {valueFormatter(total)}
              </span>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return ChartTooltip;
}

// Maps relative window sizes to their human label. Falls back to a day count
// for any window not in the canonical set so the label stays meaningful.
const RELATIVE_WINDOW_LABELS: Record<number, string> = {
  7: "Last 7 Days",
  30: "Last 30 Days",
  90: "Last 90 Days",
};

/**
 * Builds a spend KPI label scoped to the active range, prefixed with `prefix`:
 * relative windows read as "<prefix> in Last 30 Days"; custom ranges read as
 * "<prefix> between May 12 and Jun 11" using the shared chart date formatter.
 * Falls back to the bare prefix when range data is unavailable.
 */
export function buildSpendPeriodLabel(
  prefix: string,
  range: DashboardViewRange | null | undefined,
): string {
  if (!range) {
    return prefix;
  }

  if (range.mode === "custom") {
    const start = formatChartDateLabel(range.startDate);
    const end = formatChartDateLabel(range.endDate);
    return `${prefix} between ${start} and ${end}`;
  }

  const windowDays = range.windowDays;
  if (windowDays === null) {
    return prefix;
  }

  const windowLabel =
    RELATIVE_WINDOW_LABELS[windowDays] ?? `Last ${windowDays} Days`;
  return `${prefix} in ${windowLabel}`;
}

/**
 * Overview "Total Spend" KPI label, e.g. "Total Spend in Last 30 Days".
 */
export function buildTotalSpendLabel(
  range: DashboardViewRange | null | undefined,
): string {
  return buildSpendPeriodLabel("Total Spend", range);
}

/**
 * Warehouse-section "Total Warehouse Spend" KPI label, e.g.
 * "Total Warehouse Spend in Last 30 Days".
 */
export function buildTotalWarehouseSpendLabel(
  range: DashboardViewRange | null | undefined,
): string {
  return buildSpendPeriodLabel("Total Warehouse Spend", range);
}

/**
 * Storage-section KPI label, e.g. "Storage Spend in Last 30 Days".
 */
export function buildStorageSpendLabel(
  range: DashboardViewRange | null | undefined,
): string {
  return buildSpendPeriodLabel("Storage Spend", range);
}

/**
 * Builds the Overview capacity-balance KPI title. When the view model carries a
 * current balance date (the last point in the series) it reads as "Ending
 * Balance as of Jun 11" using the shared chart date formatter; otherwise (empty
 * state, no date) it stays the plain "Ending Balance".
 */
export function buildEndingBalanceLabel(
  currentBalanceDate: string | null | undefined,
): string {
  const base = "Ending Balance";
  if (!currentBalanceDate) {
    return base;
  }

  return `${base} as of ${formatChartDateLabel(currentBalanceDate)}`;
}

const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

export function formatChartDateLabel(value: string): string {
  const match = ISO_DATE_PATTERN.exec(value);
  if (!match) {
    return value;
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);

  const utcMillis = Date.UTC(year, month - 1, day);
  const parsed = new Date(utcMillis);
  if (
    Number.isNaN(utcMillis) ||
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    return value;
  }

  return new Intl.DateTimeFormat("en-US", {
    day: "2-digit",
    month: "short",
    timeZone: "UTC",
  }).format(parsed);
}

export function createCurrencyTickFormatter(
  currency: string,
): (value: number) => string {
  const resolvedCurrency = resolveCurrencyCode(currency);

  return (value: number): string => {
    const magnitude = Math.abs(value);

    let fractionDigits = 0;
    let useCompact = false;
    if (value === 0) {
      fractionDigits = 0;
    } else if (magnitude < 1) {
      fractionDigits = 2;
    } else if (magnitude < 1000) {
      fractionDigits = 0;
    } else {
      fractionDigits = 1;
      useCompact = true;
    }

    return new Intl.NumberFormat("en-US", {
      currency: resolvedCurrency,
      maximumFractionDigits: fractionDigits,
      minimumFractionDigits: fractionDigits,
      notation: useCompact ? "compact" : "standard",
      style: "currency",
    }).format(value);
  };
}

const FALLBACK_CURRENCY_CODE = "USD";

function resolveCurrencyCode(currency: string): string {
  try {
    // Intl.NumberFormat throws RangeError on an invalid currency code.
    new Intl.NumberFormat("en-US", {
      currency,
      style: "currency",
    }).format(0);
    return currency;
  } catch {
    return FALLBACK_CURRENCY_CODE;
  }
}
