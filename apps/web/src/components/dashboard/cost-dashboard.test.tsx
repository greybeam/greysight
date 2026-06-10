import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchDashboardDatasets,
  fetchDemoDashboardDatasets,
  pollDashboardRun,
  startDashboardRun,
} from "../../lib/dashboard-api";
import demoDashboardDatasets from "../../lib/demo-dashboard-data";
import type { DashboardRun } from "../../lib/dashboard-contracts";
import CostDashboard from "./cost-dashboard";

vi.mock("../../lib/dashboard-api", () => ({
  fetchDashboardDatasets: vi.fn(),
  fetchDemoDashboardDatasets: vi.fn(),
  pollDashboardRun: vi.fn(),
  startDashboardRun: vi.fn(),
}));

describe("CostDashboard", () => {
  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it("renders required dashboard sections", () => {
    render(<CostDashboard data={demoDashboardDatasets} />);

    expect(screen.getByText("Total credits")).toBeInTheDocument();
    expect(screen.getByText("Warehouse spend")).toBeInTheDocument();
    expect(screen.getByText("Service spend")).toBeInTheDocument();
    expect(screen.getByText("Compute by user")).toBeInTheDocument();
    expect(screen.getByText("Storage by database")).toBeInTheDocument();
    expect(screen.getByText("Top warehouses")).toBeInTheDocument();
    expect(screen.getAllByText("Analysis complete").length).toBeGreaterThan(0);
  });

  it("loads demo dashboard datasets in demo mode", async () => {
    vi.mocked(fetchDemoDashboardDatasets).mockResolvedValue(demoDashboardDatasets);

    render(<CostDashboard />);

    await waitFor(() => {
      expect(fetchDemoDashboardDatasets).toHaveBeenCalledTimes(1);
    });
    expect(screen.getAllByText("Analysis complete").length).toBeGreaterThan(0);
    expect(fetchDashboardDatasets).not.toHaveBeenCalled();
  });

  it("starts a Snowflake run with selected organization and bearer token", async () => {
    const runningRun: DashboardRun = {
      ...demoDashboardDatasets.run,
      id: "run-123",
      source: "snowflake",
      status: "running",
    };
    const completedRun: DashboardRun = {
      ...runningRun,
      status: "completed",
    };
    vi.mocked(startDashboardRun).mockResolvedValue(runningRun);
    vi.mocked(pollDashboardRun).mockResolvedValue(completedRun);
    vi.mocked(fetchDashboardDatasets).mockResolvedValue({
      ...demoDashboardDatasets,
      run: completedRun,
    });

    render(
      <CostDashboard
        demoMode={false}
        runtime={{
          accessToken: "test-access-token",
          organizationId: "org-123",
          organizationName: "Acme Analytics",
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    await waitFor(() => {
      expect(startDashboardRun).toHaveBeenCalledWith(
        { organizationId: "org-123", windowDays: 30 },
        { accessToken: "test-access-token" },
      );
    });
    expect(pollDashboardRun).toHaveBeenCalledWith(
      "run-123",
      expect.objectContaining({ accessToken: "test-access-token" }),
    );
    expect(fetchDashboardDatasets).toHaveBeenCalledWith("run-123", {
      accessToken: "test-access-token",
    });
    expect(fetchDemoDashboardDatasets).not.toHaveBeenCalled();
  });
});
