"use client";

import { useCallback, useMemo } from "react";
import { BarChart } from "@tremor/react";

import {
  fetchSuspensionStats,
  type SuspensionStatsResponse,
} from "../../lib/automated-savings-api";
import { LoadStatePanel, useOrgScopedFetch } from "../../lib/use-org-scoped-fetch";
import { createChartTooltip } from "../dashboard/chart-tooltip";
import { formatChartDateLabel } from "../dashboard/dashboard-design-system";
import { getSeriesColors, orderCategoriesByTotal } from "../../lib/chart-colors";
import { bucketStackedSeries, type StackedPoint } from "../../lib/stacked-series-bucketing";

const STATS_DAYS = 7;

interface SuspensionsChartProps {
  orgId: string;
  accessToken: string | null;
}

type SuspensionsChartRow = Record<string, string | number>;

function formatSuspensionCount(value: number): string {
  return String(value);
}

const SUSPENSIONS_TOOLTIP = createChartTooltip(formatSuspensionCount);

// The API returns a dense, zero-filled per-warehouse series of UTC calendar
// days; the chart therefore always renders all 7 X-axis slots, empty days
// simply have no bar. Bucket days are UTC calendar dates ("2026-07-09");
// formatChartDateLabel parses them as a UTC date and formats in UTC, so it
// labels them as-is rather than shifting into browser-local time, which would
// mislabel them.
function toStackedPoints(response: SuspensionStatsResponse): StackedPoint[] {
  return response.buckets.map((bucket) => ({
    date: bucket.day,
    values: bucket.counts,
  }));
}

// Tremor's `index` prop only names which row key holds the x-axis value — it
// never renders the key itself, only the value — so any key not already used
// by a category is safe. A warehouse could in principle be named "day", so we
// derive a key guaranteed not to collide with the bucketed category names
// rather than hardcoding one.
function deriveIndexKey(categoryNames: readonly string[]): string {
  const categoryKeys = new Set(categoryNames);
  let indexKey = "day";
  while (categoryKeys.has(indexKey)) indexKey += "_";
  return indexKey;
}

function toChartRows(
  dailySeries: StackedPoint[],
  indexKey: string,
): SuspensionsChartRow[] {
  return dailySeries.map((point) => ({
    [indexKey]: formatChartDateLabel(point.date),
    ...point.values,
  }));
}

function isAllZero(response: SuspensionStatsResponse): boolean {
  if (response.warehouses.length === 0) return true;
  return response.buckets.every((bucket) =>
    Object.values(bucket.counts).every((count) => count === 0),
  );
}

export function SuspensionsChart({ orgId, accessToken }: SuspensionsChartProps) {
  const fetchStats = useCallback(
    (org: string, token: string | null) =>
      fetchSuspensionStats(org, STATS_DAYS, { accessToken: token }),
    [],
  );
  const { data, loadState, retry } = useOrgScopedFetch<SuspensionStatsResponse>(
    orgId,
    accessToken,
    fetchStats,
  );

  const { isEmpty, rows, orderedCategories, colors, indexKey } = useMemo(() => {
    const response: SuspensionStatsResponse = data ?? {
      days: STATS_DAYS,
      warehouses: [],
      buckets: [],
    };

    // Cap displayed series to the shared stacked-series limit (grouping
    // overflow into "Other"), then order the resulting categories by total
    // suspensions descending so the largest series anchors the bottom of the
    // stack — matching the dashboard's own stacked-chart convention in
    // dashboard-design-system.tsx's `SpendBarChart` (stack path).
    const { names: bucketedNames, dailySeries: bucketedSeries } =
      bucketStackedSeries(response.warehouses, toStackedPoints(response));
    const indexKey = deriveIndexKey(bucketedNames);
    const rows = toChartRows(bucketedSeries, indexKey);
    const orderedCategories = orderCategoriesByTotal(bucketedNames, rows);
    const colors = getSeriesColors(orderedCategories, { singleSeriesPrimary: false });

    return { isEmpty: isAllZero(response), rows, orderedCategories, colors, indexKey };
  }, [data]);

  return (
    <section className="rounded-lg border border-hairline bg-surface p-6">
      <h2 className="text-sm font-semibold text-slate-200">
        Suspensions — last 7 days
      </h2>
      <LoadStatePanel
        loadState={loadState}
        loadingMessage="Loading suspension stats…"
        errorMessage="We couldn’t load suspension stats. Please try again."
        onRetry={retry}
      >
        {isEmpty ? (
          <p className="mt-4 text-sm text-slate-400">
            No recorded suspensions in the last 7 days.
          </p>
        ) : (
          <BarChart
            className="mt-4 h-56 w-full bar-segment-gap"
            data={rows}
            data-chart-library="tremor"
            data-testid="suspensions-chart"
            index={indexKey}
            categories={orderedCategories}
            colors={colors}
            customTooltip={SUSPENSIONS_TOOLTIP}
            showLegend={false}
            showTooltip
            stack
            allowDecimals={false}
            valueFormatter={formatSuspensionCount}
            yAxisWidth={40}
          />
        )}
      </LoadStatePanel>
    </section>
  );
}
