import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import demoDashboardView from "../../lib/demo-dashboard-view";
import {
  AiSpendSection,
  OverviewSection,
  StorageSpendSection,
  WarehouseSpendSection,
  flattenServiceDailySeries,
} from "./spend-sections";

describe("spend sections", () => {
  afterEach(() => {
    cleanup();
  });

  it("scopes the total spend KPI label to the active range", () => {
    render(
      <OverviewSection
        status="ready"
        currency={demoDashboardView.header.currency}
        capacityBalance={demoDashboardView.capacityBalance}
        range={{
          mode: "custom",
          windowDays: null,
          startDate: "2026-05-12",
          endDate: "2026-06-11",
        }}
        serviceSpend={demoDashboardView.serviceSpend}
        totalSpend={demoDashboardView.totalSpend}
      />,
    );

    const totalCard = screen.getByTestId("total-spend-card");
    expect(
      within(totalCard).getByText("Total Spend between May 12 and Jun 11"),
    ).toBeInTheDocument();
  });

  it("renders an empty capacity card for older views without capacity balance", () => {
    render(
      <OverviewSection
        status="ready"
        currency={demoDashboardView.header.currency}
        serviceSpend={demoDashboardView.serviceSpend}
        totalSpend={demoDashboardView.totalSpend}
      />,
    );

    expect(screen.getByText("No capacity balance data")).toBeInTheDocument();
    // The empty-state panel keeps the plain title (no date available).
    expect(screen.getByText("Ending Balance")).toBeInTheDocument();
    expect(screen.queryByText(/Ending Balance as of/)).not.toBeInTheDocument();
    expect(screen.getByText("Total Spend")).toBeInTheDocument();
  });

  it("renders a combined empty panel when both total and service spend are empty", () => {
    render(
      <OverviewSection
        status="ready"
        currency={demoDashboardView.header.currency}
        capacityBalance={demoDashboardView.capacityBalance}
        serviceSpend={{
          ...demoDashboardView.serviceSpend,
          isEmpty: true,
        }}
        totalSpend={{
          ...demoDashboardView.totalSpend,
          isEmpty: true,
        }}
      />,
    );

    // Both empty: the full empty-state panel replaces the card entirely, and
    // ranked services shows its own empty state.
    expect(screen.getByText("No total spend data")).toBeInTheDocument();
    expect(screen.getByText("No service spend data")).toBeInTheDocument();
    expect(screen.queryByTestId("total-spend-card")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("service-spend-tremor-bar-chart"),
    ).not.toBeInTheDocument();
  });

  it("keeps the total spend KPI when only the service breakdown is empty", () => {
    render(
      <OverviewSection
        status="ready"
        currency={demoDashboardView.header.currency}
        capacityBalance={demoDashboardView.capacityBalance}
        serviceSpend={{
          ...demoDashboardView.serviceSpend,
          isEmpty: true,
        }}
        totalSpend={demoDashboardView.totalSpend}
      />,
    );

    // Total spend has data, so the KPI card still renders with its value.
    const totalCard = screen.getByTestId("total-spend-card");
    expect(within(totalCard).getByText("Total Spend")).toBeInTheDocument();
    expect(
      within(totalCard).getByText(demoDashboardView.totalSpend.totalLabel),
    ).toBeInTheDocument();

    // The stacked bar chart is replaced by an empty state in the chart slot.
    expect(
      within(totalCard).queryByTestId("service-spend-tremor-bar-chart"),
    ).not.toBeInTheDocument();
    expect(
      within(totalCard).getByText("No service spend data"),
    ).toBeInTheDocument();

    // Total spend by service panel still renders its own empty state.
    expect(screen.getByText("Total spend by service")).toBeInTheDocument();
    const rankedPanel = screen.getByRole("region", {
      name: "Total spend by service",
    });
    expect(
      within(rankedPanel).getByText("No service spend data"),
    ).toBeInTheDocument();
  });

  it("keeps the service breakdown chart when only total spend is empty", () => {
    render(
      <OverviewSection
        status="ready"
        currency={demoDashboardView.header.currency}
        capacityBalance={demoDashboardView.capacityBalance}
        serviceSpend={demoDashboardView.serviceSpend}
        totalSpend={{
          ...demoDashboardView.totalSpend,
          isEmpty: true,
        }}
      />,
    );

    // Service spend has data, so the bar chart still renders inside the card.
    const totalCard = screen.getByTestId("total-spend-card");
    expect(
      within(totalCard).getByTestId("service-spend-tremor-bar-chart"),
    ).toHaveAttribute("data-chart-library", "recharts");

    // The KPI value is omitted gracefully and replaced by an empty message.
    expect(within(totalCard).getByText("Total Spend")).toBeInTheDocument();
    expect(
      within(totalCard).getByText("No total spend data"),
    ).toBeInTheDocument();
    expect(
      within(totalCard).queryByText(demoDashboardView.totalSpend.totalLabel),
    ).not.toBeInTheDocument();

    // Total spend by service still renders its populated rows.
    expect(
      screen.getByText(demoDashboardView.serviceSpend.rankedServices[0].name),
    ).toBeInTheDocument();
  });

  it("renders the ranked bar rows provided by the view model", () => {
    const warehouseBars = Array.from({ length: 9 }, (_, index) => ({
      name: `WH_${index + 1}`,
      spend: 9 - index,
      spendLabel: `$${9 - index}.00`,
      credits: 9 - index,
      barWidthPercent: 100 - index,
    }));

    render(
      <WarehouseSpendSection
        status="ready"
        currency={demoDashboardView.header.currency}
        viewModel={{
          ...demoDashboardView.warehouseSpend,
          rankedWarehouses: [],
          rankedUsers: [],
          warehouseBars,
          userBars: [],
        }}
      />,
    );

    expect(screen.getByText("WH_9")).toBeInTheDocument();
  });

  it("renders a warehouse empty state when warehouse data is missing", () => {
    render(
      <WarehouseSpendSection
        status="ready"
        currency={demoDashboardView.header.currency}
        viewModel={{
          ...demoDashboardView.warehouseSpend,
          isEmpty: true,
        }}
      />,
    );

    expect(screen.getByText("No warehouse spend data")).toBeInTheDocument();
  });

  it("renders a right-side database table with name, spend, and size", () => {
    render(
      <StorageSpendSection
        status="ready"
        currency={demoDashboardView.header.currency}
        viewModel={demoDashboardView.storageSpend}
      />,
    );

    const tablePanel = screen.getByRole("region", {
      name: "Total spend by database",
    });
    // Table headers match the pinned contract: period-scoped "Spend", not the
    // monthly estimate the bottom 2x2 detail table still shows.
    expect(within(tablePanel).getByText("Database")).toBeInTheDocument();
    expect(within(tablePanel).getByText("Spend")).toBeInTheDocument();
    expect(within(tablePanel).getByText("Size")).toBeInTheDocument();
    expect(
      within(tablePanel).queryByText("Est. monthly spend"),
    ).not.toBeInTheDocument();

    // Each database row renders name / period spend label / humanized size.
    const firstRow = demoDashboardView.storageSpend.databases[0];
    expect(within(tablePanel).getByText(firstRow.name)).toBeInTheDocument();
    expect(
      within(tablePanel).getByText(firstRow.periodSpendLabel),
    ).toBeInTheDocument();
    expect(
      within(tablePanel).getByText(firstRow.bytesLabel),
    ).toBeInTheDocument();
  });

  it("renders a storage empty state when storage data is missing", () => {
    // A storage view model flagged empty must render the empty state without
    // ever reaching into its stacked-series data (the regression: chartData was
    // computed before the isEmpty guard, throwing on a missing/empty series).
    render(
      <StorageSpendSection
        status="ready"
        currency={demoDashboardView.header.currency}
        viewModel={{
          basis: "billed",
          databaseBasis: "estimated",
          total: 0,
          totalLabel: "$0.00",
          dailySeries: [],
          databases: [],
          databaseBars: [],
          databaseNames: [],
          databaseDailySeries: [],
          isEmpty: true,
        }}
      />,
    );

    expect(screen.getByText("No storage spend data")).toBeInTheDocument();
    // The chart and table from the populated layout never render.
    expect(
      screen.queryByTestId("storage-spend-tremor-bar-chart"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Total spend by database"),
    ).not.toBeInTheDocument();
  });

  it("renders the empty state for a legacy view that predates the storage series fields", () => {
    // Real legacy payloads are parsed by parseStorageSpendViewModel, which
    // defaults the new series fields to empty arrays. Simulate the parsed result
    // of such a payload (empty series, flagged empty) and confirm it renders.
    render(
      <StorageSpendSection
        status="ready"
        currency={demoDashboardView.header.currency}
        viewModel={{
          basis: "billed",
          databaseBasis: "estimated",
          total: 0,
          totalLabel: "$0.00",
          dailySeries: [],
          databases: [],
          databaseBars: [],
          databaseNames: [],
          databaseDailySeries: [],
          isEmpty: true,
        }}
      />,
    );

    expect(screen.getByText("No storage spend data")).toBeInTheDocument();
  });

  it("flattens prepared service points for the service bar chart", () => {
    expect(
      flattenServiceDailySeries([
        {
          date: "2026-06-01",
          values: {
            Compute: 12,
            date: 99,
            Storage: 3,
          },
        },
      ]),
    ).toEqual([
      {
        date: "2026-06-01",
        Compute: 12,
        Storage: 3,
      },
    ]);
  });
});

describe("capacity forecast wiring", () => {
  afterEach(() => {
    cleanup();
  });

  const EMPTY_SERVICE_SPEND: import("../../lib/dashboard-contracts").ServiceSpendViewModel =
    {
      basis: "billed",
      dailySeries: [],
      serviceNames: [],
      rankedServices: [],
      serviceBars: [],
      isEmpty: true,
    };

  const EMPTY_TOTAL_SPEND: import("../../lib/dashboard-contracts").TotalSpendViewModel =
    {
      basis: "billed",
      total: 0,
      totalLabel: "$0.00",
      averageDaily: 0,
      averageDailyLabel: "$0.00",
      projectedMonthly: 0,
      projectedMonthlyLabel: "$0.00",
      projectionBasisLabel: "projected",
      dailySeries: [],
      topDriver: null,
      isEmpty: true,
    };

  it("renders the capacity forecast chart in the overview when forecast data exists", () => {
    const { container } = render(
      <OverviewSection
        status="ready"
        currency="USD"
        capacityBalance={{
          currentBalance: 12345,
          currentBalanceLabel: "$12,345.00",
          currentBalanceDate: "2026-06-11",
          dailySeries: [
            { date: "2026-06-11", balance: 12345, balanceLabel: "$12,345.00" },
          ],
          forecastSeries: [
            { date: "2026-06-11", balance: 12345, balanceLabel: "$12,345.00" },
            { date: "2026-06-12", balance: 0, balanceLabel: "$0.00" },
          ],
          isEmpty: false,
        }}
        serviceSpend={EMPTY_SERVICE_SPEND}
        totalSpend={EMPTY_TOTAL_SPEND}
      />,
    );

    expect(container.querySelector(".capacity-forecast-chart")).not.toBeNull();
  });
});

describe("AiSpendSection", () => {
  afterEach(() => {
    cleanup();
  });

  const summary = { total: 1234.5, totalLabel: "$1,234.50", isEmpty: false };

  it("shows KPI immediately while detail is loading", () => {
    render(
      <AiSpendSection
        currency="USD"
        summary={summary}
        detail={{ status: "loading" }}
      />,
    );
    expect(screen.getByText("$1,234.50")).toBeInTheDocument();
  });

  it("renders chart + ranked card when detail is ready", () => {
    render(
      <AiSpendSection
        currency="USD"
        summary={summary}
        detail={{
          status: "ready",
          viewModel: {
            dailySeries: [{ date: "2026-06-01", values: { CORTEX_ANALYST: 4 } }],
            consumptionTypeNames: ["CORTEX_ANALYST"],
            rankedConsumptionTypes: [],
            consumptionBars: [
              { name: "CORTEX_ANALYST", spend: 4, spendLabel: "$4.00", credits: 2, barWidthPercent: 100 },
            ],
            isEmpty: false,
            partial: true,
            skippedBranches: ["cortex_code_cli"],
          },
        }}
      />,
    );
    expect(screen.getByTestId("dashboard-section-ai-spend")).toBeInTheDocument();
  });
});
