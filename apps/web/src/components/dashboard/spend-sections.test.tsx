import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import demoDashboardView from "../../lib/demo-dashboard-view";
import {
  ComputeSpendSection,
  OverviewSection,
  ServiceSpendSection,
  StorageSpendSection,
  TotalSpendSection,
  flattenServiceDailySeries,
} from "./spend-sections";

describe("spend sections", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders capacity balance left of total spend in the overview", () => {
    render(
      <OverviewSection
        currency={demoDashboardView.header.currency}
        capacityBalance={demoDashboardView.capacityBalance}
        totalSpend={demoDashboardView.totalSpend}
      />,
    );

    const grid = screen.getByTestId("dashboard-grid-overview");
    const capacityCard = screen.getByTestId("capacity-balance-card");
    const totalCard = screen.getByTestId("total-spend-card");

    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(within(capacityCard).getByText("Ending Balance")).toBeInTheDocument();
    expect(
      within(capacityCard).getByText(
        demoDashboardView.capacityBalance.currentBalanceLabel,
      ),
    ).toBeInTheDocument();
    expect(
      within(capacityCard).getByTestId("capacity-balance-tremor-line-chart"),
    ).toHaveAttribute("data-chart-library", "tremor");
    expect(within(totalCard).getByText("Total Spend in Period")).toBeInTheDocument();
    expect(Array.from(grid.children)).toEqual([
      capacityCard.closest("section"),
      totalCard.closest("section"),
    ]);
  });

  it("renders an empty capacity card for older views without capacity balance", () => {
    render(
      <OverviewSection
        currency={demoDashboardView.header.currency}
        totalSpend={demoDashboardView.totalSpend}
      />,
    );

    expect(screen.getByText("No capacity balance data")).toBeInTheDocument();
    expect(screen.getByText("Total Spend in Period")).toBeInTheDocument();
  });

  it("renders total spend dollars as the only first-section KPI", () => {
    render(
      <TotalSpendSection
        currency={demoDashboardView.header.currency}
        viewModel={demoDashboardView.totalSpend}
      />,
    );

    expect(screen.getByText("Total spend")).toBeInTheDocument();
    expect(
      screen.getAllByText(demoDashboardView.totalSpend.totalLabel).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("Average daily")).not.toBeInTheDocument();
    expect(screen.queryByText("Projected monthly")).not.toBeInTheDocument();
    expect(screen.queryByText("Basis")).not.toBeInTheDocument();
  });

  it("renders total spend value and Tremor chart within a single card", () => {
    render(
      <TotalSpendSection
        currency={demoDashboardView.header.currency}
        viewModel={demoDashboardView.totalSpend}
      />,
    );

    const section = screen.getByTestId("dashboard-section-total-spend");
    expect(section).toHaveClass("gap-4");

    const cardRegion = screen.getByTestId("total-spend-card");

    expect(within(cardRegion).getByText("Total Spend in Period")).toBeInTheDocument();
    expect(
      within(cardRegion).getByText(demoDashboardView.totalSpend.totalLabel),
    ).toBeInTheDocument();
    expect(
      within(cardRegion).getByTestId("total-spend-tremor-line-chart"),
    ).toHaveAttribute("data-chart-library", "tremor");

    expect(
      screen.queryByRole("region", { name: "Total spend KPI row" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Daily total spend" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("total-spend-line")).not.toBeInTheDocument();
    expect(screen.queryByTestId("total-spend-point")).not.toBeInTheDocument();
  });

  it("renders compute spend on the shared card grid", () => {
    render(
      <ComputeSpendSection
        currency={demoDashboardView.header.currency}
        viewModel={demoDashboardView.computeSpend}
      />,
    );

    const section = screen.getByTestId("dashboard-section-compute-spend");
    const grid = screen.getByTestId("dashboard-grid-compute-spend");

    expect(section).toHaveClass("gap-4");
    expect(grid).toHaveClass("grid", "gap-4", "lg:grid-cols-3");
    expect(screen.getByRole("region", { name: "Daily compute" })).toHaveAttribute(
      "data-dashboard-panel",
      "true",
    );
    expect(screen.getByTestId("compute-spend-tremor-line-chart")).toHaveAttribute(
      "data-chart-library",
      "tremor",
    );
  });

  it("renders warehouse and user rankings", () => {
    render(
      <ComputeSpendSection
        currency={demoDashboardView.header.currency}
        viewModel={demoDashboardView.computeSpend}
      />,
    );

    expect(screen.getByText("Compute spend")).toBeInTheDocument();
    expect(
      screen.getByText(demoDashboardView.computeSpend.rankedWarehouses[0].name),
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
      <ComputeSpendSection
        currency={demoDashboardView.header.currency}
        viewModel={{
          ...demoDashboardView.computeSpend,
          rankedWarehouses: [],
          rankedUsers: [],
          warehouseBars,
          userBars: [],
        }}
      />,
    );

    expect(screen.getByText("WH_9")).toBeInTheDocument();
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

  it("renders ranked services", () => {
    render(
      <ServiceSpendSection
        currency={demoDashboardView.header.currency}
        viewModel={demoDashboardView.serviceSpend}
      />,
    );

    expect(screen.getByText("Service spend")).toBeInTheDocument();
    expect(
      screen.getByText(demoDashboardView.serviceSpend.rankedServices[0].name),
    ).toBeInTheDocument();
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
