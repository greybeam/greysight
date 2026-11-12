"use client";

import { useCallback, useMemo } from "react";
import { BarChart } from "@tremor/react";

import {
  fetchSavingsStats,
  type SavingsStatsResponse,
} from "../../lib/automated-savings-api";
import { getSeriesColors, orderCategoriesByTotal } from "../../lib/chart-colors";
import { formatCurrency } from "../../lib/currency-format";
import { queryKeys } from "../../lib/query-keys";
import { useQueryIdentity } from "../../lib/query-identity";
import {
  bucketStackedSeries,
  type StackedPoint,
} from "../../lib/stacked-series-bucketing";
import { LoadStatePanel, useOrgScopedFetch } from "../../lib/use-org-scoped-fetch";
import { createChartTooltip } from "../dashboard/chart-tooltip";
import {
  createCurrencyTickFormatter,
  formatChartDateLabel,
} from "../dashboard/dashboard-design-system";

const SAVINGS_DAYS = 7;

interface SavingsChartProps {
  orgId: string;
  accessToken: string | null;
}

function formatUsd(value: number): string {
  return formatCurrency(value, "USD");
}

const SAVINGS_TOOLTIP = createChartTooltip(formatUsd);
const SAVINGS_TICK = createCurrencyTickFormatter("USD");

function formatHours(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  return `${hours.toLocaleString("en-US", { maximumFractionDigits: 1 })} h`;
}

function deriveIndexKey(categoryNames: readonly string[]): string {
  const categoryKeys = new Set(categoryNames);
  let indexKey = "day";
  while (categoryKeys.has(indexKey)) indexKey += "_";
  return indexKey;
}

function toStackedPoints(response: SavingsStatsResponse): StackedPoint[] {
  return response.buckets.map((bucket) => ({
    date: bucket.day,
    values: bucket.dollars,
  }));
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-hairline bg-canvas px-4 py-3">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-1 font-display text-xl font-semibold text-slate-50">
        {value}
      </p>
    </div>
  );
}

export function SavingsChart({ orgId, accessToken }: SavingsChartProps) {
  const { snapshot } = useQueryIdentity();
  const fetchStats = useCallback(
    (org: string, token: string | null) =>
      fetchSavingsStats(org, SAVINGS_DAYS, { accessToken: token }),
    [],
  );
  const { data, loadState, retry } = useOrgScopedFetch<SavingsStatsResponse>(
    queryKeys.autoSavings.savings(snapshot.userId, orgId, {
      days: SAVINGS_DAYS,
    }),
    orgId,
    accessToken,
    fetchStats,
  );

  const { rows, orderedCategories, colors, indexKey } = useMemo(() => {
    const response: SavingsStatsResponse | null = data ?? null;
    const { names, dailySeries } = bucketStackedSeries(
      response?.warehouses ?? [],
      response ? toStackedPoints(response) : [],
    );
    const indexKey = deriveIndexKey(names);
    const rows = dailySeries.map((point) => ({
      [indexKey]: formatChartDateLabel(point.date),
      ...point.values,
    }));
    const orderedCategories = orderCategoriesByTotal(names, rows);
    const colors = getSeriesColors(orderedCategories, {
      singleSeriesPrimary: false,
    });
    return { rows, orderedCategories, colors, indexKey };
  }, [data]);

  const isEmpty = !data || data.totalSuspensions === 0;

  return (
    <section className="rounded-lg border border-hairline bg-surface p-6">
      <h2 className="text-sm font-semibold text-slate-200">
        Estimated savings — last 7 days
      </h2>
      <LoadStatePanel
        loadState={loadState}
        loadingMessage="Loading savings estimate…"
        errorMessage="We couldn’t load the savings estimate. Please try again."
        onRetry={retry}
      >
        {isEmpty || !data ? (
          <p className="mt-4 text-sm text-slate-400">
            No recorded suspensions in the last 7 days.
          </p>
        ) : (
          <>
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Kpi
                label="Estimated savings"
                value={formatUsd(data.totalDollars)}
              />
              <Kpi
                label="Idle warehouse time avoided"
                value={formatHours(data.totalAvoidedHours)}
              />
              <Kpi
                label="Suspensions"
                value={data.totalSuspensions.toLocaleString("en-US")}
              />
            </div>
            <BarChart
              className="mt-4 h-56 w-full bar-segment-gap"
              data={rows}
              data-chart-library="tremor"
              data-testid="savings-chart"
              index={indexKey}
              categories={orderedCategories}
              colors={colors}
              customTooltip={SAVINGS_TOOLTIP}
              showLegend={false}
              showTooltip
              stack
              valueFormatter={SAVINGS_TICK}
              yAxisWidth={56}
            />
            <p className="mt-3 text-xs text-slate-500">
              Estimate at ${data.pricePerCredit.toFixed(2)} per credit. Each
              suspension is credited with the idle compute Snowflake would
              otherwise have billed: multi-cluster warehouses scale in one
              cluster at a time (about every 3 min on Standard, 6 min on
              Economy) down to their minimum, then run until AUTO_SUSPEND —
              cut short if the warehouse was next resumed sooner. Each
              suspension deducts ~30s per scaled-out cluster for Snowflake’s
              60-second minimum billing.
              {data.assumedSettingsCount > 0
                ? ` ${data.assumedSettingsCount.toLocaleString("en-US")} older suspension${
                    data.assumedSettingsCount === 1 ? "" : "s"
                  } predate size/AUTO_SUSPEND capture and assume X-Small and 600s.`
                : ""}
            </p>
          </>
        )}
      </LoadStatePanel>
    </section>
  );
}
