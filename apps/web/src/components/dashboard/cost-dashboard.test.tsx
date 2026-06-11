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
import { FETCH_WINDOW_DAYS, type DashboardRun } from "../../lib/dashboard-contracts";
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

  it("renders required dollar dashboard sections", () => {
    render(<CostDashboard data={demoDashboardDatasets} />);

    expect(screen.getByText("Total spend")).toBeInTheDocument();
    expect(screen.getByText("Compute spend")).toBeInTheDocument();
    expect(screen.getByText("Storage spend")).toBeInTheDocument();
    expect(screen.getAllByText("Service spend").length).toBeGreaterThan(0);
    expect(screen.getByText("Warehouse spend")).toBeInTheDocument();
    expect(screen.getByText("User compute spend")).toBeInTheDocument();
    expect(screen.getByText("Storage by database")).toBeInTheDocument();
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

  it("changes the local window without another Snowflake round trip", async () => {
    vi.mocked(fetchDemoDashboardDatasets).mockResolvedValue(demoDashboardDatasets);

    render(<CostDashboard demoMode />);

    await screen.findByText("Total spend");
    const callsAfterLoad = vi.mocked(fetchDemoDashboardDatasets).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "7 days" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    expect(fetchDemoDashboardDatasets).toHaveBeenCalledTimes(callsAfterLoad);
    expect(startDashboardRun).not.toHaveBeenCalled();
  });

  it("shows billed freshness and account locator in the header", async () => {
    vi.mocked(fetchDemoDashboardDatasets).mockResolvedValue(demoDashboardDatasets);

    render(<CostDashboard demoMode />);

    expect(
      await screen.findByText("Billing data through Jun 8, 2026"),
    ).toBeInTheDocument();
    expect(screen.getByText("DEMO123")).toBeInTheDocument();
  });

  it("renders the mixed-currency unsupported state from metadata", async () => {
    vi.mocked(fetchDemoDashboardDatasets).mockResolvedValue({
      ...demoDashboardDatasets,
      metadata: {
        ...demoDashboardDatasets.metadata,
        unsupported_reason: "mixed_currency",
        currency: null,
      },
    });

    render(<CostDashboard demoMode />);

    expect(
      await screen.findByText(/Mixed currencies are not supported/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Total spend")).not.toBeInTheDocument();
  });

  it("disables the run action and shows placeholders while loading", () => {
    vi.mocked(fetchDemoDashboardDatasets).mockReturnValue(
      new Promise(() => undefined),
    );

    render(<CostDashboard demoMode />);

    expect(screen.getByRole("button", { name: "Run analysis" })).toBeDisabled();
    expect(screen.getByLabelText("Loading dashboard")).toBeInTheDocument();
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

    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    await waitFor(() => {
      expect(startDashboardRun).toHaveBeenCalledWith(
        { organizationId: "org-123", windowDays: FETCH_WINDOW_DAYS },
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

  it("keeps the run action disabled while a queued Snowflake run is polling", async () => {
    const queuedRun: DashboardRun = {
      ...demoDashboardDatasets.run,
      id: "run-queued",
      source: "snowflake",
      status: "queued",
    };
    vi.mocked(startDashboardRun).mockResolvedValue(queuedRun);
    vi.mocked(pollDashboardRun).mockReturnValue(new Promise(() => undefined));

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

    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    await waitFor(() => expect(startDashboardRun).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Run analysis" })).toBeDisabled();
  });
});
