"use client";

import { Card, Text } from "@tremor/react";

import type {
  CapacityBalanceViewModel,
  DashboardViewRange,
  ServiceSpendViewModel,
  StorageSpendViewModel,
  TotalSpendViewModel,
  WarehouseSpendViewModel,
} from "../../lib/dashboard-contracts";
import {
  buildEndingBalanceLabel,
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
  SpendBarChart,
  StatValueSkeleton,
  TotalSpendBarCard,
} from "./dashboard-design-system";
import { DetailTable } from "./detail-tables";
import SectionEmptyState from "./section-empty-state";

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
  | { status: "loading" }
  | {
      status: "ready";
      capacityBalance?: CapacityBalanceViewModel | null;
      currency: string;
      range?: DashboardViewRange | null;
      serviceSpend: ServiceSpendViewModel;
      totalSpend: TotalSpendViewModel;
    };

export function OverviewSection(props: OverviewSectionProps) {
  if (props.status === "loading") {
    return <OverviewSectionSkeleton />;
  }
  const { capacityBalance, currency, range, serviceSpend, totalSpend } = props;
  const serviceChartData = flattenServiceDailySeries(serviceSpend.dailySeries);
  const totalSpendLabel = buildTotalSpendLabel(range);

  return (
    <DashboardSection
      ariaLabel="Overview"
      testId="dashboard-section-overview"
      title="Overview"
    >
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
            categories={serviceSpend.serviceNames}
            chart={
              serviceSpend.isEmpty ? (
                <SectionEmptyState message="No service spend data" />
              ) : undefined
            }
            currency={currency}
            emptyValueMessage="No total spend data"
            label={totalSpendLabel}
            value={totalSpend.isEmpty ? undefined : totalSpend.totalLabel}
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
            <RankedSpendBars rows={serviceSpend.serviceBars} />
          </DashboardPanel>
        )}
      </DashboardGrid>
    </DashboardSection>
  );
}

// Skeleton frame for the Overview section. Mirrors the ready body's structure —
// a full-width capacity card above a 3-col grid (2-col total spend card + ranked
// services panel) — so heights and layout match and revealing data never shifts.
function OverviewSectionSkeleton() {
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
  | { status: "loading" }
  | {
      status: "ready";
      currency: string;
      range?: DashboardViewRange | null;
      viewModel: WarehouseSpendViewModel;
    };

export function WarehouseSpendSection(props: WarehouseSpendSectionProps) {
  if (props.status === "loading") {
    return <WarehouseSpendSectionSkeleton />;
  }
  const { currency, range, viewModel } = props;
  const chartData = flattenServiceDailySeries(viewModel.dailySeries);
  const totalLabel = buildTotalWarehouseSpendLabel(range);

  return (
    <DashboardSection
      ariaLabel="Warehouse spend"
      testId="dashboard-section-warehouse-spend"
      title="Warehouse spend"
    >
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No warehouse spend data" />
      ) : (
        <DashboardGrid columns={3} testId="dashboard-grid-warehouse-spend">
          <TotalSpendBarCard
            ariaLabel="Total warehouse spend"
            categories={viewModel.warehouseNames}
            chart={
              <SpendBarChart
                categories={viewModel.warehouseNames}
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
            value={viewModel.totalLabel}
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
                  <RankedSpendBars rows={viewModel.warehouseBars} />
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
                  <RankedSpendBars rows={viewModel.userBars} />
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
function WarehouseSpendSectionSkeleton() {
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
  | { status: "loading" }
  | {
      status: "ready";
      currency: string;
      range?: DashboardViewRange | null;
      viewModel: StorageSpendViewModel;
    };

export function StorageSpendSection(props: StorageSpendSectionProps) {
  if (props.status === "loading") {
    return <StorageSpendSectionSkeleton />;
  }
  const { currency, range, viewModel } = props;
  const totalLabel = buildStorageSpendLabel(range);

  return (
    <DashboardSection
      ariaLabel="Storage spend"
      testId="dashboard-section-storage-spend"
      title="Storage spend"
    >
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No storage spend data" />
      ) : (
        <StorageSpendBody
          currency={currency}
          totalLabel={totalLabel}
          viewModel={viewModel}
        />
      )}
    </DashboardSection>
  );
}

// Skeleton frame for the Storage section: a 2-col total spend card with the
// h-80 chart, plus the right column's fill-height database table placeholder.
function StorageSpendSectionSkeleton() {
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
  viewModel,
}: {
  currency: string;
  totalLabel: string;
  viewModel: StorageSpendViewModel;
}) {
  const chartData = flattenServiceDailySeries(viewModel.databaseDailySeries);

  return (
    <DashboardGrid columns={3} testId="dashboard-grid-storage-spend">
      <TotalSpendBarCard
        ariaLabel="Storage spend"
        categories={viewModel.databaseNames}
        chart={
          <SpendBarChart
            categories={viewModel.databaseNames}
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
        value={viewModel.totalLabel}
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
          rows={viewModel.databases.map((row) => ({
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
