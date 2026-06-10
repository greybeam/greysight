import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import demoDashboardDatasets from "../../lib/demo-dashboard-data";
import CostDashboard from "./cost-dashboard";

describe("CostDashboard", () => {
  it("renders the required dashboard sections", () => {
    render(<CostDashboard data={demoDashboardDatasets} />);

    expect(screen.getByText("Total credits")).toBeInTheDocument();
    expect(screen.getByText("Warehouse spend")).toBeInTheDocument();
    expect(screen.getByText("Service spend")).toBeInTheDocument();
    expect(screen.getByText("Compute by user")).toBeInTheDocument();
    expect(screen.getByText("Storage by database")).toBeInTheDocument();
    expect(screen.getByText("Top warehouses")).toBeInTheDocument();
    expect(screen.getByText("Analysis complete")).toBeInTheDocument();
  });
});
