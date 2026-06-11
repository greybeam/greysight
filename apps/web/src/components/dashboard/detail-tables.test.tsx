import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import demoDashboardData from "../../lib/demo-dashboard-data";
import { buildDashboardViewModel } from "../../lib/dashboard-transforms";
import DetailTables from "./detail-tables";

const viewModel = buildDashboardViewModel(demoDashboardData, 30);

describe("DetailTables", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders service, warehouse, user, and storage detail tables", () => {
    render(<DetailTables viewModel={viewModel.detailTables} />);

    expect(screen.getByText("Service spend")).toBeInTheDocument();
    expect(screen.getByText("Warehouse spend")).toBeInTheDocument();
    expect(screen.getByText("User compute spend")).toBeInTheDocument();
    expect(screen.getByText("Storage by database")).toBeInTheDocument();
    expect(
      screen.getAllByText(viewModel.detailTables.warehouses[0].name).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(viewModel.detailTables.users[0].name)).toBeInTheDocument();
  });
});
