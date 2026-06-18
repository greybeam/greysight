"use client";

import type React from "react";
import type { CustomTooltipProps } from "@tremor/react";

import { resolveChartColor } from "../../lib/chart-colors";

// Shared custom tooltip for the dashboard charts (Tremor line/area charts and
// the Recharts stacked bar chart, whose payloads are structurally compatible).
// Sorts the hovered point's series by value, sums a Total for multi-series
// points, and optionally splits out a rolling-average overlay into its own row.
export function createChartTooltip(
  valueFormatter: (value: number) => string,
  // When the chart carries a rolling-average overlay series, pass its data key
  // and label here. That entry is then split out of the per-series rows and the
  // Total sum and rendered as its own labeled row under the Total — it is a
  // derived trendline, not a stacked component of the day's spend.
  options?: { averageKey?: string; averageLabel?: string },
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

    // Pull the rolling-average overlay (if any) out of the stacked series so it
    // never inflates the Total or appears as a stacked row.
    const averageKey = options?.averageKey;
    const averageEntry = averageKey
      ? payload.find((entry) => entry.dataKey === averageKey)
      : undefined;
    const seriesPayload = averageKey
      ? payload.filter((entry) => entry.dataKey !== averageKey)
      : payload;

    // Sort an immutable copy by this point's value, descending, so the largest
    // value at the hovered point is the top row. This is the point's own value
    // order and can differ from the stacked-bar order, which is fixed by each
    // series' total across the whole range. Single-series line charts are
    // unaffected (a one-row sort is a no-op).
    const rows = [...seriesPayload].sort(
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
          {averageEntry ? (
            <div className="flex items-center justify-between gap-3 text-xs text-slate-400">
              <span className="flex items-center gap-1.5">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: resolveChartColor(averageEntry.color) }}
                />
                {options?.averageLabel ?? "Avg"}
              </span>
              <span className="tabular-nums text-slate-200">
                {valueFormatter(toNumericValue(averageEntry.value))}
              </span>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return ChartTooltip;
}
