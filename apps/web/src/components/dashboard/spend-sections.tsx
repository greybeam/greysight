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
  buildTotalSpendLabel,
  buildTotalWarehouseSpendLabel,
  CapacityBalanceCard,
  DashboardGrid,
  DashboardPanel,
  DashboardSection,
  RankedSpendBars,
  SpendBarChart,
  SpendLineChart,
  TotalSpendBarCard,
} from "./dashboard-design-system";
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

export function OverviewSection({
  capacityBalance,
  currency,
  range,
  serviceSpend,
  totalSpend,
}: {
  capacityBalance?: CapacityBalanceViewModel | null;
  currency: string;
  range?: DashboardViewRange | null;
  serviceSpend: ServiceSpendViewModel;
  totalSpend: TotalSpendViewModel;
}) {
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
            ariaLabel="Ranked services"
            fill
            title="Ranked services"
          >
            <SectionEmptyState message="No service spend data" />
          </DashboardPanel>
        ) : (
          <DashboardPanel
            ariaLabel="Ranked services"
            fill
            title="Ranked services"
          >
            <RankedSpendBars rows={serviceSpend.serviceBars} />
          </DashboardPanel>
        )}
      </DashboardGrid>
    </DashboardSection>
  );
}

export function WarehouseSpendSection({
  currency,
  range,
  viewModel,
}: {
  currency: string;
  range?: DashboardViewRange | null;
  viewModel: WarehouseSpendViewModel;
}) {
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
                <Text>Warehouses</Text>
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
                <Text>Users</Text>
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

export function StorageSpendSection({
  currency,
  viewModel,
}: {
  currency: string;
  viewModel: StorageSpendViewModel;
}) {
  return (
    <DashboardSection
      ariaLabel="Storage spend"
      testId="dashboard-section-storage-spend"
      title="Storage spend"
    >
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No storage spend data" />
      ) : (
        <DashboardGrid columns={2} testId="dashboard-grid-storage-spend">
          <DashboardPanel ariaLabel="Daily storage" fill title="Daily storage">
            <SpendLineChart
              currency={currency}
              data={viewModel.dailySeries}
              heightClass="h-64"
              testId="storage-spend-tremor-line-chart"
            />
          </DashboardPanel>
          <DashboardPanel
            ariaLabel="Latest storage by database"
            fill
            title="Latest storage by database"
          >
            <RankedSpendBars rows={viewModel.databaseBars} />
          </DashboardPanel>
        </DashboardGrid>
      )}
    </DashboardSection>
  );
}
