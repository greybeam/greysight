import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import demoDashboardView from "../../lib/demo-dashboard-view";
import {
  ComputeSpendSection,
  ServiceSpendSection,
  StorageSpendSection,
  TotalSpendSection,
  flattenServiceDailySeries,
} from "./spend-sections";

describe("spend sections", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders total spend dollars with projection basis", () => {
    render(<TotalSpendSection viewModel={demoDashboardView.totalSpend} />);

    expect(screen.getByText("Total spend")).toBeInTheDocument();
    expect(
      screen.getAllByText(demoDashboardView.totalSpend.totalLabel).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(
        new RegExp(demoDashboardView.totalSpend.projectionBasisLabel),
      ),
    ).toBeInTheDocument();
  });

  it("labels estimated warehouse and user rankings", () => {
    render(<ComputeSpendSection viewModel={demoDashboardView.computeSpend} />);

    expect(screen.getByText("Compute spend")).toBeInTheDocument();
    expect(screen.getAllByText(/Estimated/).length).toBeGreaterThan(0);
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
    render(<ServiceSpendSection viewModel={demoDashboardView.serviceSpend} />);

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
