"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CustomTooltipProps } from "@tremor/react";

import {
  PRIMARY_CHART_COLOR,
  resolveChartColor,
  seriesDisplayLabel,
} from "../../lib/chart-colors";
import { ROLLING_AVERAGE_LABEL } from "../../lib/rolling-average";
import { createChartTooltip } from "./dashboard-design-system";

// Tremor's sealed BarChart can't host a line series and this Tremor version has
// no ComboChart, so the stacked spend charts render directly on Recharts (the
// library Tremor uses under the hood) as a ComposedChart: stacked bars plus a
// 7-day rolling-average trendline on the same shared y-axis. Styling mirrors the
// dashboard's Tremor charts (hairline grid, slate ticks, 56px y-axis).

const HAIRLINE = "#2A2A2A";
const AXIS_TICK_COLOR = "#94A3B8"; // slate-400
const AXIS_TICK = { fill: AXIS_TICK_COLOR, fontSize: 12 } as const;

// Few points (a ~7-day window) label every day; longer ranges preserve the
// endpoints and let Recharts thin the rest so labels don't crowd.
const DENSE_TICK_MAX_POINTS = 10;

function resolveBarInterval(pointCount: number): 0 | "preserveStartEnd" {
  return pointCount <= DENSE_TICK_MAX_POINTS ? 0 : "preserveStartEnd";
}

export function StackedSpendBarChart({
  averageKey,
  categories,
  colors,
  data,
  heightClass = "h-64",
  segmentGap = false,
  testId,
  valueFormatter,
}: {
  // Data key the rolling-average overlay is stored under in `data`. Derived by
  // `withRollingAverage` to never collide with a category name.
  averageKey: string;
  categories: string[];
  // Color tokens aligned positionally with `categories` (e.g. "chart-1").
  colors: string[];
  data: Array<Record<string, string | number>>;
  heightClass?: string;
  // Opt-in 1px surface stroke between stacked segments, applied by the shared
  // `.bar-segment-gap` rule in globals.css (it targets `.recharts-bar-rectangle`,
  // which this chart renders natively).
  segmentGap?: boolean;
  testId: string;
  valueFormatter: (value: number) => string;
}) {
  const TooltipContent = createChartTooltip(valueFormatter, {
    averageKey,
    averageLabel: ROLLING_AVERAGE_LABEL,
  });
  const trendColor = resolveChartColor(PRIMARY_CHART_COLOR);

  return (
    <div
      className={[
        "mt-4 w-full",
        heightClass,
        segmentGap ? "bar-segment-gap" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      data-chart-library="recharts"
      data-testid={testId}
    >
      <ResponsiveContainer height="100%" width="100%">
        <ComposedChart
          data={data}
          margin={{ bottom: 0, left: 0, right: 0, top: 4 }}
        >
          <CartesianGrid horizontal stroke={HAIRLINE} vertical={false} />
          <XAxis
            axisLine={false}
            dataKey="date"
            interval={resolveBarInterval(data.length)}
            minTickGap={32}
            tick={AXIS_TICK}
            tickLine={false}
          />
          <YAxis
            axisLine={false}
            tick={AXIS_TICK}
            tickFormatter={valueFormatter}
            tickLine={false}
            width={56}
          />
          <Tooltip
            content={(props) => (
              <TooltipContent {...(props as CustomTooltipProps)} />
            )}
            cursor={{ fill: HAIRLINE, fillOpacity: 0.4 }}
          />
          {categories.map((category, index) => (
            <Bar
              dataKey={category}
              fill={resolveChartColor(colors[index])}
              key={category}
              name={seriesDisplayLabel(category, categories)}
              stackId="spend"
            />
          ))}
          <Line
            activeDot={{ r: 3 }}
            dataKey={averageKey}
            dot={false}
            isAnimationActive={false}
            name={ROLLING_AVERAGE_LABEL}
            stroke={trendColor}
            strokeWidth={2}
            type="monotone"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
