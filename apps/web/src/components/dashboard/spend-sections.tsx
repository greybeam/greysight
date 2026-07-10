"use client";

import { Card, Text } from "@tremor/react";

import type {
  AIDetailViewModel,
  AISpendSummaryViewModel,
  CapacityBalanceViewModel,
  DashboardViewRange,
  ServiceSpendViewModel,
  StorageSpendViewModel,
  TotalSpendViewModel,
  WarehouseSpendViewModel,
} from "../../lib/dashboard-contracts";
import {
  buildEndingBalanceLabel,
  buildSpendPeriodLabel,
  buildStorageSpendLabel,
  buildTotalSpendLabel,
  buildTotalWarehouseSpendLabel,
  CapacityBalanceCard,
  ChartSkeleton,
  DashboardGrid,
  DashboardPanel,
  DashboardSection,
  DetailTableSkeleton,
  RankedSpendBars,
  RankedSpendBarsSkeleton,
  WarehouseIdleBars,
  SpendBarChart,
  StatValueSkeleton,
  TotalSpendBarCard,
} from "./dashboard-design-system";
import { DetailTable } from "./detail-tables";
import SectionEmptyState from "./section-empty-state";
import SectionIdleState from "./section-idle-state";
import { SectionFilter } from "./section-filter";
import { useSectionFilter } from "./use-section-filter";
import {
  filterAiDetail,
  filterServiceSpend,
  filterStorageSpend,
  filterWarehouseSpend,
  isFullSelection,
  type FilteredStorageSpend,
} from "../../lib/section-filters";

type StackedChartPoint = {
  date: string;
} & Record<string, string | number>;

// Flattens any {date, values} stacked series (service- or warehouse-keyed) into
// the flat {date, ...values} rows Tremor's stacked BarChart expects.
export function flattenServiceDailySeries(
  dailySeries: { date: string; values: Record<string, number> }[],
): StackedChartPoint[] {
  return dailySeries.map((point) => ({
    ...point.values,
    date: point.date,
  }));
}

type OverviewSectionProps =
  | { status: "idle" }
  | { status: "loading"; loadingMessage?: string }
  | {
      status: "ready";
      capacityBalance?: CapacityBalanceViewModel | null;
      currency: string;
      range?: DashboardViewRange | null;
      serviceSpend: ServiceSpendViewModel;
      totalSpend: TotalSpendViewModel;
    };

export function OverviewSection(props: OverviewSectionProps) {
  // Hook must run unconditionally on every render (rules-of-hooks); derive the
  // options defensively since ready-only fields are absent in idle/loading.
  const options =
    props.status === "ready" ? props.serviceSpend.serviceNames : null;
  const { selected, setSelected } = useSectionFilter(options);
  if (props.status === "idle") {
    return (
      <DashboardSection
        ariaLabel="Overview"
        testId="dashboard-section-overview"
        title="Overview"
      >
        <SectionIdleState />
      </DashboardSection>
    );
  }
  if (props.status === "loading") {
    return <OverviewSectionSkeleton loadingMessage={props.loadingMessage} />;
  }
  const { capacityBalance, currency, range, serviceSpend, totalSpend } = props;
  const filtered = filterServiceSpend(serviceSpend, selected, currency);
  const serviceChartData = flattenServiceDailySeries(filtered.dailySeries);
  const totalSpendLabel = buildTotalSpendLabel(range);
  // Filtered → recomputed detail sum; unfiltered → verbatim billed prop
  // (filtered.totalLabel is null in the zero-drift default).
  const kpiValue = totalSpend.isEmpty
    ? undefined
    : (filtered.totalLabel ?? totalSpend.totalLabel);

  return (
    <DashboardSection
      ariaLabel="Overview"
      testId="dashboard-section-overview"
      title="Overview"
    >
      <SectionFilter
        options={serviceSpend.serviceNames}
        selected={selected}
        onChange={setSelected}
      />
      {!capacityBalance || capacityBalance.isEmpty ? (
        <DashboardPanel
          ariaLabel="Capacity balance summary"
          title="Ending Balance"
        >
          <SectionEmptyState message="No capacity balance data" />
        </DashboardPanel>
      ) : (
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
      )}
      <DashboardGrid columns={3} testId="dashboard-grid-overview">
        {totalSpend.isEmpty && serviceSpend.isEmpty ? (
          <DashboardPanel
            ariaLabel="Total spend summary"
            span={2}
            title={totalSpendLabel}
          >
            <SectionEmptyState message="No total spend data" />
          </DashboardPanel>
        ) : (
          <TotalSpendBarCard
            ariaLabel="Total spend summary"
            categories={filtered.serviceNames}
            chart={
              serviceSpend.isEmpty ? (
                <SectionEmptyState message="No service spend data" />
              ) : undefined
            }
            currency={currency}
            emptyValueMessage="No total spend data"
            label={totalSpendLabel}
            value={kpiValue}
            data={serviceChartData}
            span={2}
            testId="total-spend-card"
            chartTestId="service-spend-tremor-bar-chart"
          />
        )}
        {serviceSpend.isEmpty ? (
          <DashboardPanel
            ariaLabel="Total spend by service"
            fill
            title="Total spend by service"
          >
            <SectionEmptyState message="No service spend data" />
          </DashboardPanel>
        ) : (
          <DashboardPanel
            ariaLabel="Total spend by service"
            fill
            title="Total spend by service"
          >
            <RankedSpendBars rows={filtered.serviceBars} />
          </DashboardPanel>
        )}
      </DashboardGrid>
    </DashboardSection>
  );
}

// Skeleton frame for the Overview section. Mirrors the ready body's structure —
// a full-width capacity card above a 3-col grid (2-col total spend card + ranked
// services panel) — so heights and layout match and revealing data never shifts.
function OverviewSectionSkeleton({
  loadingMessage,
}: {
  loadingMessage?: string;
}) {
  return (
    <DashboardSection
      ariaLabel="Overview"
      testId="dashboard-section-overview"
      title="Overview"
    >
      <div data-testid="overview-skeleton" className="grid gap-4">
        <section aria-label="Capacity balance summary" data-dashboard-panel="true">
          <Card className="p-6">
            <Text>Ending Balance</Text>
            <StatValueSkeleton />
            <ChartSkeleton
              variant="line"
              heightClass="h-80"
              loadingMessage={loadingMessage}
              testId="overview-capacity-skeleton"
            />
          </Card>
        </section>
        <DashboardGrid columns={3} testId="dashboard-grid-overview">
          <section
            aria-label="Total spend summary"
            className="lg:col-span-2 h-full"
            data-dashboard-panel="true"
          >
            <Card className="flex h-full flex-col p-6">
              <Text>Total Spend</Text>
              <StatValueSkeleton />
              <ChartSkeleton
                variant="bar"
                heightClass="h-80"
                loadingMessage={loadingMessage}
                testId="overview-total-skeleton-chart"
              />
            </Card>
          </section>
          <DashboardPanel
            ariaLabel="Total spend by service"
            fill
            title="Total spend by service"
          >
            <RankedSpendBarsSkeleton />
          </DashboardPanel>
        </DashboardGrid>
      </div>
    </DashboardSection>
  );
}

type WarehouseSpendSectionProps =
  | { status: "idle" }
  | { status: "loading"; loadingMessage?: string }
  | {
      status: "ready";
      currency: string;
      range?: DashboardViewRange | null;
      viewModel: WarehouseSpendViewModel;
    };

export function WarehouseSpendSection(props: WarehouseSpendSectionProps) {
  // Hook must run unconditionally on every render (rules-of-hooks); derive the
  // options defensively since ready-only fields are absent in idle/loading.
  const options =
    props.status === "ready" ? props.viewModel.warehouseNames : null;
  const { selected, setSelected } = useSectionFilter(options);
  if (props.status === "idle") {
    return (
      <DashboardSection
        ariaLabel="Warehouse spend"
        testId="dashboard-section-warehouse-spend"
        title="Warehouse spend"
      >
        <SectionIdleState />
      </DashboardSection>
    );
  }
  if (props.status === "loading") {
    return (
      <WarehouseSpendSectionSkeleton loadingMessage={props.loadingMessage} />
    );
  }
  const { currency, range, viewModel } = props;
  const filtered = filterWarehouseSpend(viewModel, selected, currency);
  const isFiltered =
    !isFullSelection(selected, viewModel.warehouseNames) &&
    selected.length > 0;
  const chartData = flattenServiceDailySeries(filtered.dailySeries);
  const totalLabel = buildTotalWarehouseSpendLabel(range);

  return (
    <DashboardSection
      ariaLabel="Warehouse spend"
      testId="dashboard-section-warehouse-spend"
      title="Warehouse spend"
    >
      <SectionFilter
        options={viewModel.warehouseNames}
        selected={selected}
        onChange={setSelected}
      />
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No warehouse spend data" />
      ) : (
        <DashboardGrid columns={3} testId="dashboard-grid-warehouse-spend">
          <TotalSpendBarCard
            ariaLabel="Total warehouse spend"
            categories={filtered.warehouseNames}
            chart={
              <SpendBarChart
                categories={filtered.warehouseNames}
                currency={currency}
                data={chartData}
                heightClass="h-96"
                segmentGap
                showLegend={false}
                stack
                testId="warehouse-spend-tremor-bar-chart"
              />
            }
            currency={currency}
            label={totalLabel}
            value={filtered.totalLabel ?? viewModel.totalLabel}
            data={chartData}
            span={2}
            testId="total-warehouse-spend-card"
            chartTestId="warehouse-spend-tremor-bar-chart"
          />
          {/* Third column splits into two half-height panels that each scroll
              their ranked list internally instead of growing the row. */}
          <div className="flex h-full min-h-0 flex-col gap-4">
            <section
              aria-label="Warehouse ranking"
              className="flex min-h-0 flex-1 flex-col"
              data-dashboard-panel="true"
            >
              <Card className="flex h-full flex-col p-6">
                <Text>Total spend by warehouse</Text>
                <div className="flex min-h-0 flex-1 flex-col">
                  <WarehouseIdleBars rows={filtered.warehouseBars} />
                </div>
              </Card>
            </section>
            <section
              aria-label="User ranking"
              className="flex min-h-0 flex-1 flex-col"
              data-dashboard-panel="true"
            >
              <Card className="flex h-full flex-col p-6">
                <Text>Total spend by user</Text>
                {isFiltered ? (
                  <Text className="text-xs opacity-60">
                    Warehouse filter not applied
                  </Text>
                ) : null}
                <div className="flex min-h-0 flex-1 flex-col">
                  <RankedSpendBars rows={filtered.userBars} />
                </div>
              </Card>
            </section>
          </div>
        </DashboardGrid>
      )}
    </DashboardSection>
  );
}

// Skeleton frame for the Warehouse section: a 2-col total spend card with the
// taller h-96 chart, plus the third column's two half-height ranked panels.
function WarehouseSpendSectionSkeleton({
  loadingMessage,
}: {
  loadingMessage?: string;
}) {
  return (
    <DashboardSection
      ariaLabel="Warehouse spend"
      testId="dashboard-section-warehouse-spend"
      title="Warehouse spend"
    >
      <DashboardGrid columns={3} testId="dashboard-grid-warehouse-spend">
        <section
          aria-label="Total warehouse spend"
          className="lg:col-span-2 h-full"
          data-dashboard-panel="true"
        >
          <Card className="flex h-full flex-col p-6">
            <Text>Total Warehouse Spend</Text>
            <StatValueSkeleton />
            <ChartSkeleton
              variant="bar"
              heightClass="h-96"
              loadingMessage={loadingMessage}
              testId="warehouse-spend-skeleton-chart"
            />
          </Card>
        </section>
        <div className="flex h-full min-h-0 flex-col gap-4">
          <section
            aria-label="Warehouse ranking"
            className="flex min-h-0 flex-1 flex-col"
            data-dashboard-panel="true"
          >
            <Card className="flex h-full flex-col p-6">
              <Text>Total spend by warehouse</Text>
              <div className="flex min-h-0 flex-1 flex-col">
                <RankedSpendBarsSkeleton rows={4} />
              </div>
            </Card>
          </section>
          <section
            aria-label="User ranking"
            className="flex min-h-0 flex-1 flex-col"
            data-dashboard-panel="true"
          >
            <Card className="flex h-full flex-col p-6">
              <Text>Total spend by user</Text>
              <div className="flex min-h-0 flex-1 flex-col">
                <RankedSpendBarsSkeleton rows={4} />
              </div>
            </Card>
          </section>
        </div>
      </DashboardGrid>
    </DashboardSection>
  );
}

type StorageSpendSectionProps =
  | { status: "idle" }
  | { status: "loading"; loadingMessage?: string }
  | {
      status: "ready";
      currency: string;
      range?: DashboardViewRange | null;
      viewModel: StorageSpendViewModel;
    };

export function StorageSpendSection(props: StorageSpendSectionProps) {
  // Hook must run unconditionally on every render (rules-of-hooks); derive the
  // options defensively since ready-only fields are absent in idle/loading.
  const options =
    props.status === "ready" ? props.viewModel.databaseNames : null;
  const { selected, setSelected } = useSectionFilter(options);
  if (props.status === "idle") {
    return (
      <DashboardSection
        ariaLabel="Storage spend"
        testId="dashboard-section-storage-spend"
        title="Storage spend"
      >
        <SectionIdleState />
      </DashboardSection>
    );
  }
  if (props.status === "loading") {
    return <StorageSpendSectionSkeleton loadingMessage={props.loadingMessage} />;
  }
  const { currency, range, viewModel } = props;
  const filtered = filterStorageSpend(viewModel, selected, currency);
  const totalLabel = buildStorageSpendLabel(range);

  return (
    <DashboardSection
      ariaLabel="Storage spend"
      testId="dashboard-section-storage-spend"
      title="Storage spend"
    >
      <SectionFilter
        options={viewModel.databaseNames}
        selected={selected}
        onChange={setSelected}
      />
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No storage spend data" />
      ) : (
        <StorageSpendBody
          currency={currency}
          totalLabel={totalLabel}
          filtered={filtered}
          viewModel={viewModel}
        />
      )}
    </DashboardSection>
  );
}

// Skeleton frame for the Storage section: a 2-col total spend card with the
// h-80 chart, plus the right column's fill-height database table placeholder.
function StorageSpendSectionSkeleton({
  loadingMessage,
}: {
  loadingMessage?: string;
}) {
  return (
    <DashboardSection
      ariaLabel="Storage spend"
      testId="dashboard-section-storage-spend"
      title="Storage spend"
    >
      <DashboardGrid columns={3} testId="dashboard-grid-storage-spend">
        <section
          aria-label="Storage spend"
          className="lg:col-span-2 h-full"
          data-dashboard-panel="true"
        >
          <Card className="flex h-full flex-col p-6">
            <Text>Storage Spend</Text>
            <StatValueSkeleton />
            <ChartSkeleton
              variant="bar"
              heightClass="h-80"
              loadingMessage={loadingMessage}
              testId="storage-spend-skeleton-chart"
            />
          </Card>
        </section>
        <section
          aria-label="Total spend by database"
          className="flex h-full min-h-0 flex-col"
          data-dashboard-panel="true"
        >
          <DetailTableSkeleton title="Total spend by database" />
        </section>
      </DashboardGrid>
    </DashboardSection>
  );
}

function StorageSpendBody({
  currency,
  totalLabel,
  filtered,
  viewModel,
}: {
  currency: string;
  totalLabel: string;
  filtered: FilteredStorageSpend;
  viewModel: StorageSpendViewModel;
}) {
  const chartData = flattenServiceDailySeries(filtered.databaseDailySeries);

  return (
    <DashboardGrid columns={3} testId="dashboard-grid-storage-spend">
      <TotalSpendBarCard
        ariaLabel="Storage spend"
        categories={filtered.databaseNames}
        chart={
          <SpendBarChart
            categories={filtered.databaseNames}
            currency={currency}
            data={chartData}
            heightClass="h-80"
            segmentGap
            showLegend={false}
            stack
            testId="storage-spend-tremor-bar-chart"
          />
        }
        currency={currency}
        label={totalLabel}
        value={filtered.totalLabel ?? viewModel.totalLabel}
        data={chartData}
        span={2}
        testId="storage-spend-card"
        chartTestId="storage-spend-tremor-bar-chart"
      />
      {/* Right column mirrors the warehouse section's third column: a single
          card that scrolls its own list (here a compact table) internally
          instead of growing the row. */}
      <section
        aria-label="Total spend by database"
        className="flex h-full min-h-0 flex-col"
        data-dashboard-panel="true"
      >
        <DetailTable
          title="Total spend by database"
          headers={["Database", "Spend", "Size"]}
          fillHeight
          truncateFirstColumn
          rows={filtered.databases.map((row) => ({
            key: row.name,
            cells: [
              { key: "name", value: row.name },
              { key: "periodSpend", value: row.periodSpendLabel },
              { key: "bytes", value: row.bytesLabel },
            ],
          }))}
        />
      </section>
    </DashboardGrid>
  );
}

/**
 * AI-section "Total AI Spend" KPI label, e.g. "Total AI Spend in Last 30 Days".
 */
function buildTotalAiSpendLabel(
  range: DashboardViewRange | null | undefined,
): string {
  return buildSpendPeriodLabel("Total AI Spend", range);
}

export type AiSpendDetailState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; viewModel: AIDetailViewModel };

type AiSpendSectionProps =
  | { status: "idle" }
  | {
      status?: never;
      currency: string;
      loadingMessage?: string;
      range?: DashboardViewRange | null;
      summary: AISpendSummaryViewModel;
      detail: AiSpendDetailState;
    };

export function AiSpendSection(props: AiSpendSectionProps) {
  // Hook must run unconditionally on every render (rules-of-hooks); derive the
  // options defensively since the ready detail is absent in the idle state.
  const ready =
    props.status !== "idle" && props.detail.status === "ready"
      ? props.detail.viewModel
      : null;
  const options = ready ? ready.consumptionTypeNames : null;
  const { selected, setSelected } = useSectionFilter(options);
  if (props.status === "idle") {
    return (
      <DashboardSection
        ariaLabel="AI spend"
        testId="dashboard-section-ai-spend"
        title="AI spend"
      >
        <SectionIdleState />
      </DashboardSection>
    );
  }
  const { currency, loadingMessage, range, summary } = props;
  const filtered = ready ? filterAiDetail(ready, selected, currency) : null;
  const chartData = filtered
    ? flattenServiceDailySeries(filtered.dailySeries)
    : [];
  const categories = filtered ? filtered.consumptionTypeNames : [];
  const totalLabel = buildTotalAiSpendLabel(range);
  // Filtered → detail-derived KPI + microcopy; unfiltered → verbatim billed KPI.
  const kpiValue = filtered?.detailTotalLabel ?? summary.totalLabel;
  const showDetailNote = filtered?.detailTotalLabel != null;

  return (
    <DashboardSection
      ariaLabel="AI spend"
      testId="dashboard-section-ai-spend"
      title="AI spend"
    >
      <SectionFilter
        options={ready ? ready.consumptionTypeNames : []}
        selected={selected}
        onChange={setSelected}
        disabled={ready == null}
      />
      <DashboardGrid columns={3} testId="dashboard-grid-ai-spend">
        <TotalSpendBarCard
          ariaLabel="Total AI spend"
          categories={categories}
          chart={
            ready ? (
              <SpendBarChart
                categories={categories}
                currency={currency}
                data={chartData}
                heightClass="h-96"
                segmentGap
                showLegend={false}
                stack
                testId="ai-spend-tremor-bar-chart"
              />
            ) : (
              <ChartSkeleton
                variant="bar"
                heightClass="h-96"
                loadingMessage={loadingMessage}
                testId="ai-spend-skeleton-chart"
              />
            )
          }
          currency={currency}
          label={totalLabel}
          value={kpiValue}
          data={chartData}
          span={2}
          testId="total-ai-spend-card"
          chartTestId="ai-spend-tremor-bar-chart"
        />
        {/* Third column: single ranked card for consumption types. Matches the
            warehouse section's right-column structure (flex column, min-h-0). */}
        <div className="flex h-full min-h-0 flex-col gap-4">
          <section
            aria-label="AI consumption ranking"
            className="flex min-h-0 flex-1 flex-col"
            data-dashboard-panel="true"
          >
            <Card className="flex h-full flex-col p-6">
              <Text>Total AI spend by consumption type</Text>
              {/* Data-consistency note: KPI = billed metering; chart = per-feature
                  breakdown. Small differences between the two are expected. */}
              <div className="flex min-h-0 flex-1 flex-col">
                {filtered ? (
                  <RankedSpendBars rows={filtered.consumptionBars} />
                ) : (
                  <RankedSpendBarsSkeleton rows={4} />
                )}
              </div>
              {showDetailNote ? (
                <Text className="mt-2 text-xs opacity-60">
                  Estimated from detail — differs from billed metering.
                </Text>
              ) : null}
              {ready?.partial ? (
                <Text className="mt-2 text-xs opacity-60">
                  Some sources unavailable: {ready.skippedBranches.join(", ")}
                </Text>
              ) : null}
            </Card>
          </section>
        </div>
      </DashboardGrid>
    </DashboardSection>
  );
}
