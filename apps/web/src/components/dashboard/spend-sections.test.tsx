import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import demoDashboardView from "../../lib/demo-dashboard-view";
import {
  OverviewSection,
  StorageSpendSection,
  WarehouseSpendSection,
  flattenServiceDailySeries,
} from "./spend-sections";

describe("spend sections", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders capacity balance as a full-width row above the total spend grid", () => {
    render(
      <OverviewSection
        status="ready"
        currency={demoDashboardView.header.currency}
        capacityBalance={demoDashboardView.capacityBalance}
        serviceSpend={demoDashboardView.serviceSpend}
        totalSpend={demoDashboardView.totalSpend}
      />,
    );

    const section = screen.getByTestId("dashboard-section-overview");
    const grid = screen.getByTestId("dashboard-grid-overview");
    const capacityCard = screen.getByTestId("capacity-balance-card");

    expect(screen.getByText("Overview")).toBeInTheDocument();
    // The populated card dates the title from the view model's current balance
    // date (last point in the series), formatted with the shared date helper.
    expect(
      within(capacityCard).getByText("Ending Balance as of Jun 08"),
    ).toBeInTheDocument();
    expect(
      within(capacityCard).getByText(
        demoDashboardView.capacityBalance.currentBalanceLabel,
      ),
    ).toBeInTheDocument();
    expect(
      within(capacityCard).getByTestId("capacity-balance-tremor-line-chart"),
    ).toHaveAttribute("data-chart-library", "tremor");

    // The capacity card sits in its own full-width row, outside the grid.
    expect(grid).not.toContainElement(capacityCard);
    const capacitySection = capacityCard.closest("section");
    expect(Array.from(section.children)).toContain(capacitySection);
  });

  it("renders a 3-col overview row with a 2-col total spend card and ranked services", () => {
    render(
      <OverviewSection
        status="ready"
        currency={demoDashboardView.header.currency}
        capacityBalance={demoDashboardView.capacityBalance}
        serviceSpend={demoDashboardView.serviceSpend}
        totalSpend={demoDashboardView.totalSpend}
      />,
    );

    const grid = screen.getByTestId("dashboard-grid-overview");
    expect(grid).toHaveClass("grid", "gap-4", "lg:grid-cols-3");

    const totalCard = screen.getByTestId("total-spend-card");
    const totalSection = totalCard.closest("section");

    // KPI labeled exactly "Total Spend" (not "Total Spend in Period").
    expect(within(totalCard).getByText("Total Spend")).toBeInTheDocument();
    expect(
      within(totalCard).getByText(demoDashboardView.totalSpend.totalLabel),
    ).toBeInTheDocument();
    expect(screen.queryByText("Total Spend in Period")).not.toBeInTheDocument();

    // Stacked daily-by-service bar chart lives inside the total spend card.
    expect(
      within(totalCard).getByTestId("service-spend-tremor-bar-chart"),
    ).toHaveAttribute("data-chart-library", "tremor");

    // The total spend card spans two of the three columns.
    expect(totalSection).toHaveClass("lg:col-span-2");

    // Total spend by service occupies the third column.
    expect(screen.getByText("Total spend by service")).toBeInTheDocument();
    expect(
      screen.getByText(demoDashboardView.serviceSpend.rankedServices[0].name),
    ).toBeInTheDocument();

    // Both panels are grid children, in card-then-ranked order.
    const gridSections = Array.from(grid.children);
    expect(gridSections).toHaveLength(2);
    expect(gridSections[0]).toBe(totalSection);
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

  it("stretches the total spend by service panel to fill the overview row height", () => {
    render(
      <OverviewSection
        status="ready"
        currency={demoDashboardView.header.currency}
        capacityBalance={demoDashboardView.capacityBalance}
        serviceSpend={demoDashboardView.serviceSpend}
        totalSpend={demoDashboardView.totalSpend}
      />,
    );

    // The ranked panel must stretch to match the (taller) chart card and lay
    // out as a flex column so its scrollable list claims the leftover height.
    const rankedPanel = screen.getByRole("region", {
      name: "Total spend by service",
    });
    expect(rankedPanel).toHaveClass("h-full");
    expect(rankedPanel.querySelector("[class*='flex-col']")).not.toBeNull();
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
    ).toHaveAttribute("data-chart-library", "tremor");

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

  it("renders a total-warehouse-spend KPI card with a stacked chart spanning two columns", () => {
    render(
      <WarehouseSpendSection
        status="ready"
        currency={demoDashboardView.header.currency}
        range={demoDashboardView.range}
        viewModel={demoDashboardView.warehouseSpend}
      />,
    );

    const section = screen.getByTestId("dashboard-section-warehouse-spend");
    const grid = screen.getByTestId("dashboard-grid-warehouse-spend");

    expect(screen.getByText("Warehouse spend")).toBeInTheDocument();
    expect(section).toHaveClass("gap-4");
    expect(grid).toHaveClass("grid", "gap-4", "lg:grid-cols-3");

    // The KPI card spans two of the three columns, carries the period-scoped
    // label plus the big total value, and renders the stacked chart taller than
    // the default so the third column fits two half-height panels.
    const totalCard = screen.getByTestId("total-warehouse-spend-card");
    const chartPanel = screen.getByRole("region", {
      name: "Total warehouse spend",
    });
    expect(chartPanel).toHaveClass("lg:col-span-2");
    expect(
      within(totalCard).getByText("Total Warehouse Spend in Last 30 Days"),
    ).toBeInTheDocument();
    expect(
      within(totalCard).getByText(
        demoDashboardView.warehouseSpend.totalLabel,
      ),
    ).toBeInTheDocument();
    const chart = screen.getByTestId("warehouse-spend-tremor-bar-chart");
    expect(chart).toHaveAttribute("data-chart-library", "tremor");
    expect(chart).toHaveClass("h-96");
  });

  it("renders two half-height ranked panels in the third column", () => {
    render(
      <WarehouseSpendSection
        status="ready"
        currency={demoDashboardView.header.currency}
        viewModel={demoDashboardView.warehouseSpend}
      />,
    );

    // Warehouses on top, Users below, each as its own labeled panel.
    const warehousePanel = screen.getByRole("region", {
      name: "Warehouse ranking",
    });
    const userPanel = screen.getByRole("region", { name: "User ranking" });
    expect(
      within(warehousePanel).getByText("Total spend by warehouse"),
    ).toBeInTheDocument();
    expect(
      within(userPanel).getByText("Total spend by user"),
    ).toBeInTheDocument();

    // Both panels share the row height (flex-1) and clip overflow (min-h-0) so
    // their internal ranked lists scroll instead of growing the row.
    expect(warehousePanel).toHaveClass("flex-1", "min-h-0");
    expect(userPanel).toHaveClass("flex-1", "min-h-0");

    expect(
      within(warehousePanel).getByText(
        demoDashboardView.warehouseSpend.warehouseBars[0].name,
      ),
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

  it("renders a storage-spend KPI card with a stacked chart spanning two columns", () => {
    render(
      <StorageSpendSection
        status="ready"
        currency={demoDashboardView.header.currency}
        range={demoDashboardView.range}
        viewModel={demoDashboardView.storageSpend}
      />,
    );

    const grid = screen.getByTestId("dashboard-grid-storage-spend");
    expect(screen.getByText("Storage spend")).toBeInTheDocument();
    expect(grid).toHaveClass("grid", "gap-4", "lg:grid-cols-3");

    // KPI card spans two columns, carries the period-scoped label plus the total.
    const totalCard = screen.getByTestId("storage-spend-card");
    const chartPanel = totalCard.closest("section");
    expect(chartPanel).toHaveClass("lg:col-span-2");
    expect(
      within(totalCard).getByText("Storage Spend in Last 30 Days"),
    ).toBeInTheDocument();
    expect(
      within(totalCard).getByText(demoDashboardView.storageSpend.totalLabel),
    ).toBeInTheDocument();

    // The stacked daily-by-database bar chart lives inside the card, sized to
    // the storage section's shorter height.
    const chart = screen.getByTestId("storage-spend-tremor-bar-chart");
    expect(chart).toHaveAttribute("data-chart-library", "tremor");
    expect(chart).toHaveClass("h-80");

    // The view model exposes the bucketed database names the chart stacks by.
    expect(demoDashboardView.storageSpend.databaseNames).toEqual([
      "RAW",
      "ANALYTICS",
      "APP",
    ]);
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

describe("section skeletons", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders the Overview skeleton with title and chart skeleton when loading", () => {
    render(<OverviewSection status="loading" />);
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-section-overview")).toBeInTheDocument();
    expect(screen.getByTestId("overview-skeleton")).toBeInTheDocument();
    // The capacity line skeleton matches the real h-80 height.
    const lineChart = screen.getByTestId("overview-capacity-skeleton");
    expect(lineChart).toHaveAttribute("data-chart-skeleton", "line");
    expect(lineChart).toHaveClass("h-80");
  });

  it("renders the Warehouse skeleton chart at h-96 when loading", () => {
    render(<WarehouseSpendSection status="loading" />);
    expect(
      screen.getByTestId("dashboard-section-warehouse-spend"),
    ).toBeInTheDocument();
    const chart = screen.getByTestId("warehouse-spend-skeleton-chart");
    expect(chart).toHaveAttribute("data-chart-skeleton", "bar");
    expect(chart).toHaveClass("h-96");
  });

  it("renders the Storage skeleton with chart h-80 and table skeleton when loading", () => {
    render(<StorageSpendSection status="loading" />);
    expect(
      screen.getByTestId("dashboard-section-storage-spend"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("storage-spend-skeleton-chart")).toHaveClass(
      "h-80",
    );
    expect(screen.getByTestId("detail-table-skeleton")).toBeInTheDocument();
  });
});

describe("skeleton/ready height parity", () => {
  afterEach(cleanup);

  // demoDashboardView.warehouseSpend is a non-empty fixture (isEmpty === false),
  // so the ready branch renders the real SpendBarChart rather than the empty
  // state — letting us assert h-96 in both the loading and ready states.
  const demoWarehouseSpend = demoDashboardView.warehouseSpend;

  it("uses h-96 for the warehouse chart in both states", () => {
    const { unmount } = render(<WarehouseSpendSection status="loading" />);
    expect(screen.getByTestId("warehouse-spend-skeleton-chart")).toHaveClass(
      "h-96",
    );
    unmount();
    // The ready warehouse chart is rendered via SpendBarChart heightClass="h-96"
    // (see WarehouseSpendSection ready branch). Assert the class on its testid.
    render(
      <WarehouseSpendSection
        status="ready"
        currency="USD"
        range={null}
        viewModel={demoWarehouseSpend}
      />,
    );
    expect(
      screen.getByTestId("warehouse-spend-tremor-bar-chart"),
    ).toHaveClass("h-96");
  });
});
