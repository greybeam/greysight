import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import demoDashboardData from "../../lib/demo-dashboard-data";
import { buildDashboardViewModel } from "../../lib/dashboard-transforms";
import {
  ComputeSpendSection,
  ServiceSpendSection,
  StorageSpendSection,
  TotalSpendSection,
} from "./spend-sections";

const viewModel = buildDashboardViewModel(demoDashboardData, 30);

describe("spend sections", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders total spend dollars with projection basis", () => {
    render(<TotalSpendSection viewModel={viewModel.totalSpend} />);

    expect(screen.getByText("Total spend")).toBeInTheDocument();
    expect(screen.getAllByText(viewModel.totalSpend.totalLabel).length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getByText(new RegExp(viewModel.totalSpend.projectionBasisLabel)),
    ).toBeInTheDocument();
  });

  it("labels estimated warehouse and user rankings", () => {
    render(<ComputeSpendSection viewModel={viewModel.computeSpend} />);

    expect(screen.getByText("Compute spend")).toBeInTheDocument();
    expect(screen.getAllByText(/Estimated/).length).toBeGreaterThan(0);
    expect(
      screen.getByText(viewModel.computeSpend.rankedWarehouses[0].name),
    ).toBeInTheDocument();
  });

  it("renders a storage empty state when storage data is missing", () => {
    render(
      <StorageSpendSection
        viewModel={{
          basis: "billed",
          databaseBasis: "estimated",
          dailySeries: [],
          databases: [],
          isEmpty: true,
        }}
      />,
    );

    expect(screen.getByText("No storage spend data")).toBeInTheDocument();
  });

  it("renders ranked services", () => {
    render(<ServiceSpendSection viewModel={viewModel.serviceSpend} />);

    expect(screen.getByText("Service spend")).toBeInTheDocument();
    expect(
      screen.getByText(viewModel.serviceSpend.rankedServices[0].name),
    ).toBeInTheDocument();
  });
});
