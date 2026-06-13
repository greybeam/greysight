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

    // Ranked services occupies the third column.
    expect(screen.getByText("Ranked services")).toBeInTheDocument();
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

  it("stretches the ranked services panel to fill the overview row height", () => {
    render(
      <OverviewSection
        currency={demoDashboardView.header.currency}
        capacityBalance={demoDashboardView.capacityBalance}
        serviceSpend={demoDashboardView.serviceSpend}
        totalSpend={demoDashboardView.totalSpend}
      />,
    );

    // The ranked panel must stretch to match the (taller) chart card and lay
    // out as a flex column so its scrollable list claims the leftover height.
    const rankedPanel = screen.getByRole("region", { name: "Ranked services" });
    expect(rankedPanel).toHaveClass("h-full");
    expect(rankedPanel.querySelector("[class*='flex-col']")).not.toBeNull();
  });

  it("renders an empty capacity card for older views without capacity balance", () => {
    render(
      <OverviewSection
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

    // Ranked services panel still renders its own empty state.
    expect(screen.getByText("Ranked services")).toBeInTheDocument();
    const rankedPanel = screen.getByRole("region", { name: "Ranked services" });
    expect(
      within(rankedPanel).getByText("No service spend data"),
    ).toBeInTheDocument();
  });

  it("keeps the service breakdown chart when only total spend is empty", () => {
    render(
      <OverviewSection
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

    // Ranked services still renders its populated rows.
    expect(
      screen.getByText(demoDashboardView.serviceSpend.rankedServices[0].name),
    ).toBeInTheDocument();
  });

  it("renders a total-warehouse-spend KPI card with a stacked chart spanning two columns", () => {
    render(
      <WarehouseSpendSection
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
        currency={demoDashboardView.header.currency}
        viewModel={demoDashboardView.warehouseSpend}
      />,
    );

    // Warehouses on top, Users below, each as its own labeled panel.
    const warehousePanel = screen.getByRole("region", {
      name: "Warehouse ranking",
    });
    const userPanel = screen.getByRole("region", { name: "User ranking" });
    expect(within(warehousePanel).getByText("Warehouses")).toBeInTheDocument();
    expect(within(userPanel).getByText("Users")).toBeInTheDocument();

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
        currency={demoDashboardView.header.currency}
        viewModel={{
          ...demoDashboardView.warehouseSpend,
          isEmpty: true,
        }}
      />,
    );

    expect(screen.getByText("No warehouse spend data")).toBeInTheDocument();
  });

  it("renders a storage empty state when storage data is missing", () => {
    render(
      <StorageSpendSection
        currency={demoDashboardView.header.currency}
        viewModel={{
          basis: "billed",
          databaseBasis: "estimated",
          dailySeries: [],
          databases: [],
          databaseBars: [],
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
