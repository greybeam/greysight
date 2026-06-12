import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import demoDashboardView from "../../lib/demo-dashboard-view";
import DetailTables from "./detail-tables";

describe("DetailTables", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders service, warehouse, user, and storage detail tables", () => {
    render(<DetailTables viewModel={demoDashboardView.detailTables} />);

    expect(screen.getByText("Service spend")).toBeInTheDocument();
    expect(screen.getByText("Warehouse spend")).toBeInTheDocument();
    expect(screen.getByText("User compute spend")).toBeInTheDocument();
    expect(screen.getByText("Storage by database")).toBeInTheDocument();
    expect(
      screen.getAllByText(demoDashboardView.detailTables.warehouses[0].name)
        .length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(demoDashboardView.detailTables.users[0].name),
    ).toBeInTheDocument();
  });

  it("provides accessible names for each detail table", () => {
    render(<DetailTables viewModel={demoDashboardView.detailTables} />);

    expect(
      screen.getByRole("table", { name: "Service spend" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: "Warehouse spend" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: "User compute spend" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: "Storage by database" }),
    ).toBeInTheDocument();
  });

  it("renders all rows provided by the detail table view model", () => {
    const services = Array.from({ length: 51 }, (_, index) => ({
      name: `SERVICE_${index}`,
      spend: index,
      spendLabel: `$${index}.00`,
      credits: index,
    }));

    render(
      <DetailTables
        viewModel={{
          ...demoDashboardView.detailTables,
          services,
        }}
      />,
    );

    const serviceTable = screen.getByRole("table", { name: "Service spend" });
    expect(within(serviceTable).getAllByRole("row")).toHaveLength(
      services.length + 1,
    );
  });
});
