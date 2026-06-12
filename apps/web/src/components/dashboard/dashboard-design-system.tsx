"use client";

import type React from "react";
import type { ReactNode } from "react";
import { BarChart, Card, LineChart, Text } from "@tremor/react";
import type { CustomTooltipProps, IntervalType } from "@tremor/react";

import type { DollarPoint, RankedBarRow } from "../../lib/dashboard-contracts";

type DashboardGridColumns = 2 | 3 | 4;

type ChartPoint = {
  date: string;
} & Record<string, string | number>;

export const CHART_PALETTE = [
  "blue",
  "cyan",
  "indigo",
  "violet",
  "amber",
  "emerald",
  "rose",
  "teal",
] as const;
export const PRIMARY_CHART_COLOR: string = CHART_PALETTE[0];

function cx(...classes: Array<string | undefined>): string {
  return classes.filter(Boolean).join(" ");
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
      <h2 className="text-xl font-semibold tracking-tight text-slate-900">
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
  title,
}: {
  ariaLabel: string;
  badge?: ReactNode;
  children: ReactNode;
  className?: string;
  title: string;
}) {
  return (
    <section aria-label={ariaLabel} data-dashboard-panel="true">
      <Card className={cx("p-6", className)}>
        <div className="flex items-center gap-2">
          <Text>{title}</Text>
          {badge}
        </div>
        {children}
      </Card>
    </section>
  );
}

export function RankedSpendBars({ rows }: { rows: RankedBarRow[] }) {
  const visibleRows = rows.filter((row) => Math.round(row.spend * 100) !== 0);

  if (visibleRows.length === 0) {
    return <p className="mt-4 text-xs text-slate-500">No ranked spend data</p>;
  }

  return (
    <ul className="mt-4 grid gap-2">
      {visibleRows.map((row) => (
        <li
          className="grid grid-cols-[minmax(8rem,10rem)_minmax(6rem,1fr)_auto] items-center gap-4"
          key={row.name}
        >
          <span className="truncate text-xs text-slate-600">{row.name}</span>
          <span className="h-2 rounded bg-slate-200">
            <span
              className="block h-2 rounded bg-blue-600"
              style={{ width: `${row.barWidthPercent}%` }}
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

export function TotalSpendCard({
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
  data: DollarPoint[];
  testId?: string;
  chartTestId: string;
}) {
  return (
    <section aria-label={ariaLabel} data-dashboard-panel="true">
      <Card className="p-6" data-testid={testId}>
        <Text>{label}</Text>
        <p className="mt-2 text-4xl font-semibold tracking-normal text-slate-950">
          {value}
        </p>
        <SpendLineChart
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
  const valueFormatter = createCurrencyTickFormatter(currency);
  const chartData = data.map((point) => ({
    ...point,
    date: formatChartDateLabel(String(point.date)),
  }));

  return (
    <LineChart
      autoMinValue={false}
      categories={["spend"]}
      className={cx("mt-4 w-full", heightClass)}
      colors={[color]}
      customTooltip={createChartTooltip(valueFormatter)}
      data={chartData}
      data-chart-library="tremor"
      data-testid={testId}
      index="date"
      intervalType={resolveTickInterval(chartData.length)}
      minValue={0}
      showLegend={false}
      showTooltip
      tickGap={32}
      valueFormatter={valueFormatter}
      yAxisWidth={56}
    />
  );
}

export function SpendBarChart({
  categories,
  currency,
  data,
  heightClass = "h-64",
  showLegend = true,
  stack = false,
  testId,
}: {
  categories: string[];
  currency: string;
  data: ChartPoint[];
  heightClass?: string;
  showLegend?: boolean;
  stack?: boolean;
  testId: string;
}) {
  const valueFormatter = createCurrencyTickFormatter(currency);
  const chartData = data.map((point) => ({
    ...point,
    date: formatChartDateLabel(String(point.date)),
  }));

  return (
    <BarChart
      categories={categories}
      className={cx("mt-4 w-full", heightClass)}
      colors={categories.map((_, i) => CHART_PALETTE[i % CHART_PALETTE.length])}
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
  function ChartTooltip({ active, label, payload }: CustomTooltipProps) {
    if (!active || !payload || payload.length === 0) {
      return null;
    }

    return (
      <div className="rounded-md border border-slate-200 bg-white px-3 py-2 shadow-lg">
        <p className="text-xs font-medium text-slate-900">{label}</p>
        <div className="mt-1 grid gap-1">
          {payload.map((entry, index) => {
            const name = entry.dataKey ?? entry.name;
            const key = String(name ?? index);

            return (
              <div
                className="flex items-center justify-between gap-3 text-xs text-slate-600"
                key={key}
              >
                <span className="flex items-center gap-1.5">
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: entry.color }}
                  />
                  {String(name ?? "")}
                </span>
                <span className="tabular-nums text-slate-900">
                  {valueFormatter(Number(entry.value))}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return ChartTooltip;
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
