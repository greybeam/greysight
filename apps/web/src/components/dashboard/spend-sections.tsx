"use client";

import type {
  CapacityBalanceViewModel,
  ComputeSpendViewModel,
  ServicePoint,
  ServiceSpendViewModel,
  StorageSpendViewModel,
  TotalSpendViewModel,
} from "../../lib/dashboard-contracts";
import {
  CapacityBalanceCard,
  DashboardGrid,
  DashboardPanel,
  DashboardSection,
  RankedSpendBars,
  SpendBarChart,
  SpendLineChart,
  TotalSpendCard,
} from "./dashboard-design-system";
import SectionEmptyState from "./section-empty-state";

type ServiceChartPoint = {
  date: string;
} & Record<string, string | number>;

export function flattenServiceDailySeries(
  dailySeries: ServicePoint[],
): ServiceChartPoint[] {
  return dailySeries.map((point) => ({
    ...point.values,
    date: point.date,
  }));
}

export function OverviewSection({
  capacityBalance,
  currency,
  totalSpend,
}: {
  capacityBalance?: CapacityBalanceViewModel | null;
  currency: string;
  totalSpend: TotalSpendViewModel;
}) {
  return (
    <DashboardSection
      ariaLabel="Overview"
      testId="dashboard-section-overview"
      title="Overview"
    >
      <DashboardGrid columns={2} testId="dashboard-grid-overview">
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
            label="Ending Balance"
            value={capacityBalance.currentBalanceLabel}
            data={capacityBalance.dailySeries}
            testId="capacity-balance-card"
            chartTestId="capacity-balance-tremor-line-chart"
          />
        )}
        {totalSpend.isEmpty ? (
          <DashboardPanel ariaLabel="Total spend summary" title="Total Spend">
            <SectionEmptyState message="No total spend data" />
          </DashboardPanel>
        ) : (
          <TotalSpendCard
            ariaLabel="Total spend summary"
            currency={currency}
            label="Total Spend in Period"
            value={totalSpend.totalLabel}
            data={totalSpend.dailySeries}
            testId="total-spend-card"
            chartTestId="total-spend-tremor-line-chart"
          />
        )}
      </DashboardGrid>
    </DashboardSection>
  );
}

export function TotalSpendSection({
  currency,
  viewModel,
}: {
  currency: string;
  viewModel: TotalSpendViewModel;
}) {
  return (
    <DashboardSection
      ariaLabel="Total spend"
      testId="dashboard-section-total-spend"
      title="Total spend"
    >
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No total spend data" />
      ) : (
        <TotalSpendCard
          ariaLabel="Total spend summary"
          currency={currency}
          label="Total Spend in Period"
          value={viewModel.totalLabel}
          data={viewModel.dailySeries}
          testId="total-spend-card"
          chartTestId="total-spend-tremor-line-chart"
        />
      )}
    </DashboardSection>
  );
}

export function ComputeSpendSection({
  currency,
  viewModel,
}: {
  currency: string;
  viewModel: ComputeSpendViewModel;
}) {
  return (
    <DashboardSection
      ariaLabel="Compute spend"
      testId="dashboard-section-compute-spend"
      title="Compute spend"
    >
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No compute spend data" />
      ) : (
        <DashboardGrid columns={3} testId="dashboard-grid-compute-spend">
          <DashboardPanel ariaLabel="Daily compute" title="Daily compute">
            <SpendLineChart
              currency={currency}
              data={viewModel.dailySeries}
              heightClass="h-64"
              testId="compute-spend-tremor-line-chart"
            />
          </DashboardPanel>
          <DashboardPanel ariaLabel="Warehouse compute spend" title="Warehouses">
            <RankedSpendBars rows={viewModel.warehouseBars} />
          </DashboardPanel>
          <DashboardPanel ariaLabel="User compute spend" title="Users">
            <RankedSpendBars rows={viewModel.userBars} />
          </DashboardPanel>
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
          <DashboardPanel ariaLabel="Daily storage" title="Daily storage">
            <SpendLineChart
              currency={currency}
              data={viewModel.dailySeries}
              heightClass="h-64"
              testId="storage-spend-tremor-line-chart"
            />
          </DashboardPanel>
          <DashboardPanel
            ariaLabel="Latest storage by database"
            title="Latest storage by database"
          >
            <RankedSpendBars rows={viewModel.databaseBars} />
          </DashboardPanel>
        </DashboardGrid>
      )}
    </DashboardSection>
  );
}

export function ServiceSpendSection({
  currency,
  viewModel,
}: {
  currency: string;
  viewModel: ServiceSpendViewModel;
}) {
  const chartData = flattenServiceDailySeries(viewModel.dailySeries);

  return (
    <DashboardSection
      ariaLabel="Service spend"
      testId="dashboard-section-service-spend"
      title="Service spend"
    >
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No service spend data" />
      ) : (
        <DashboardGrid columns={2} testId="dashboard-grid-service-spend">
          <DashboardPanel
            ariaLabel="Daily service spend"
            title="Daily by service"
          >
            <SpendBarChart
              categories={viewModel.serviceNames}
              currency={currency}
              data={chartData}
              heightClass="h-64"
              showLegend={false}
              stack
              testId="service-spend-tremor-bar-chart"
            />
          </DashboardPanel>
          <DashboardPanel ariaLabel="Ranked services" title="Ranked services">
            <RankedSpendBars rows={viewModel.serviceBars} />
          </DashboardPanel>
        </DashboardGrid>
      )}
    </DashboardSection>
  );
}
