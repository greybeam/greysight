import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type DashboardViewRangeRequest,
  fetchDashboardView,
  fetchDemoDashboardView,
  pollDashboardRun,
  startDashboardRun,
} from "../../lib/dashboard-api";
import demoDashboardView from "../../lib/demo-dashboard-view";
import { FETCH_WINDOW_DAYS, type DashboardRun } from "../../lib/dashboard-contracts";
import CostDashboard from "./cost-dashboard";

vi.mock("../../lib/dashboard-api", () => ({
  fetchDashboardView: vi.fn(),
  fetchDemoDashboardView: vi.fn(),
  pollDashboardRun: vi.fn(),
  startDashboardRun: vi.fn(),
}));

type Deferred<T> = {
  promise: Promise<T>;
  reject: (error: Error) => void;
  resolve: (value: T) => void;
};

function createDeferred<T>(): Deferred<T> {
  let resolve: Deferred<T>["resolve"] | undefined;
  let reject: Deferred<T>["reject"] | undefined;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });

  if (!resolve || !reject) {
    throw new Error("Deferred promise callbacks were not initialized");
  }

  return { promise, reject, resolve };
}

function demoViewForRange(
  range: DashboardViewRangeRequest = { windowDays: 30 },
) {
  if (range.startDate !== undefined && range.endDate !== undefined) {
    return {
      ...demoDashboardView,
      range: {
        mode: "custom" as const,
        windowDays: null,
        startDate: range.startDate,
        endDate: range.endDate,
      },
    };
  }

  return {
    ...demoDashboardView,
    range: {
      ...demoDashboardView.range,
      mode: "relative" as const,
      windowDays: range.windowDays ?? 30,
    },
  };
}

describe("CostDashboard", () => {
  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it("renders required dollar dashboard sections", () => {
    render(<CostDashboard data={demoDashboardView} />);

    expect(screen.getByText("Overview")).toBeInTheDocument();
    // The demo view carries a current balance date, so the title is dated.
    expect(screen.getByText("Ending Balance as of Jun 08")).toBeInTheDocument();
    // The demo view uses a relative 30-day window, so the KPI label is scoped.
    expect(screen.getByText("Total Spend in Last 30 Days")).toBeInTheDocument();
    expect(screen.getByText("Ranked services")).toBeInTheDocument();
    expect(
      screen.getByTestId("dashboard-section-warehouse-spend"),
    ).toBeInTheDocument();
    expect(screen.getByText("Storage spend")).toBeInTheDocument();
    expect(screen.queryByText("Total Spend in Period")).not.toBeInTheDocument();
    // "Warehouse spend" appears twice: the section heading and the detail table.
    expect(screen.getAllByText("Warehouse spend").length).toBeGreaterThan(0);
    expect(screen.getByText("User compute spend")).toBeInTheDocument();
    // "Storage by database" appears twice: the storage section's right-side
    // table and the detail-tables section at the bottom of the dashboard.
    expect(
      screen.getAllByText("Storage by database").length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("Analysis complete").length).toBeGreaterThan(0);
  });

  it("uses the shared dashboard content container scale", () => {
    render(<CostDashboard data={demoDashboardView} />);

    expect(screen.getByLabelText("Dashboard content")).toHaveClass(
      "max-w-[1200px]",
      "gap-6",
      "px-6",
      "py-6",
    );
  });

  it("loads demo prepared view and prefetches relative windows", async () => {
    vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);

    render(<CostDashboard demoMode />);

    await screen.findByText("Overview");
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 30 });
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 7 });
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 90 });
  });

  it("switches to cached relative prepared view without another request", async () => {
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) =>
      demoViewForRange(range),
    );

    render(<CostDashboard demoMode />);

    await screen.findByText("Overview");
    await waitFor(() => expect(fetchDemoDashboardView).toHaveBeenCalledTimes(3));

    fireEvent.click(screen.getByRole("button", { name: "7 days" }));

    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(fetchDemoDashboardView).toHaveBeenCalledTimes(3);
  });

  it("fetches and caches an uncached custom date range", async () => {
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) =>
      demoViewForRange(range),
    );

    render(<CostDashboard demoMode />);

    await screen.findByText("Overview");
    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-06-01" },
    });
    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-08" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));

    await waitFor(() =>
      expect(fetchDemoDashboardView).toHaveBeenCalledWith({
        startDate: "2026-06-01",
        endDate: "2026-06-08",
      }),
    );
    await waitFor(() => expect(fetchDemoDashboardView).toHaveBeenCalledTimes(4));

    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));

    expect(fetchDemoDashboardView).toHaveBeenCalledTimes(4);
  });

  it("keeps the run action disabled while an uncached custom range request is pending", async () => {
    const pendingRange = createDeferred<ReturnType<typeof demoViewForRange>>();
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) => {
      if (range?.startDate === "2026-06-01") {
        return pendingRange.promise;
      }
      return demoViewForRange(range);
    });

    render(<CostDashboard demoMode data={demoDashboardView} />);

    const runButton = screen.getByRole("button", { name: "Run analysis" });
    expect(runButton).not.toBeDisabled();

    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-06-01" },
    });
    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-08" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));

    await waitFor(() =>
      expect(fetchDemoDashboardView).toHaveBeenCalledWith({
        startDate: "2026-06-01",
        endDate: "2026-06-08",
      }),
    );
    expect(runButton).toBeDisabled();

    await act(async () => {
      pendingRange.resolve(
        demoViewForRange({ startDate: "2026-06-01", endDate: "2026-06-08" }),
      );
    });

    await waitFor(() => expect(runButton).not.toBeDisabled());
  });

  it("resets stale prepared view state when the organization changes", async () => {
    const orgAView = {
      ...demoDashboardView,
      run: {
        ...demoDashboardView.run,
        id: "run-org-a",
        source: "snowflake" as const,
      },
      header: {
        ...demoDashboardView.header,
        accountLocator: "ORG_A",
      },
    };
    const { rerender } = render(
      <CostDashboard
        demoMode={false}
        data={orgAView}
        runtime={{
          accessToken: "token-a",
          organizationId: "org-a",
          organizationName: "Org A",
        }}
      />,
    );

    expect(screen.getByText("ORG_A")).toBeInTheDocument();

    rerender(
      <CostDashboard
        demoMode={false}
        runtime={{
          accessToken: "token-b",
          organizationId: "org-b",
          organizationName: "Org B",
        }}
      />,
    );

    await waitFor(() =>
      expect(screen.queryByText("ORG_A")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Loading dashboard")).toBeInTheDocument();
  });

  it("keeps the latest range response active when custom range requests resolve out of order", async () => {
    const firstRange = createDeferred<ReturnType<typeof demoViewForRange>>();
    const secondRange = createDeferred<ReturnType<typeof demoViewForRange>>();
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) => {
      if (range?.startDate === "2026-06-01") {
        return firstRange.promise;
      }
      if (range?.startDate === "2026-06-02") {
        return secondRange.promise;
      }
      return demoViewForRange(range);
    });

    render(<CostDashboard demoMode />);

    await screen.findByText("Overview");
    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-06-01" },
    });
    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-08" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));
    await waitFor(() =>
      expect(fetchDemoDashboardView).toHaveBeenCalledWith({
        startDate: "2026-06-01",
        endDate: "2026-06-08",
      }),
    );

    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-06-02" },
    });
    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-09" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));
    await waitFor(() =>
      expect(fetchDemoDashboardView).toHaveBeenCalledWith({
        startDate: "2026-06-02",
        endDate: "2026-06-09",
      }),
    );

    await act(async () => {
      secondRange.resolve(
        demoViewForRange({ startDate: "2026-06-02", endDate: "2026-06-09" }),
      );
    });
    await waitFor(() =>
      expect(screen.getByLabelText("Start date")).toHaveValue("2026-06-02"),
    );

    await act(async () => {
      firstRange.resolve(
        demoViewForRange({ startDate: "2026-06-01", endDate: "2026-06-08" }),
      );
    });

    expect(screen.getByLabelText("Start date")).toHaveValue("2026-06-02");
    expect(screen.getByLabelText("End date")).toHaveValue("2026-06-09");
  });

  it("allows a new run after a pending range response settles", async () => {
    const pendingRange = createDeferred<ReturnType<typeof demoViewForRange>>();
    let defaultLoadCount = 0;
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) => {
      if (range?.startDate === "2026-06-01") {
        return pendingRange.promise;
      }
      if (range?.windowDays === 30) {
        defaultLoadCount += 1;
        return {
          ...demoViewForRange(range),
          run: {
            ...demoDashboardView.run,
            id: defaultLoadCount === 1 ? "initial-run" : "new-run",
          },
          header: {
            ...demoDashboardView.header,
            accountLocator: defaultLoadCount === 1 ? "INITIAL_RUN" : "NEW_RUN",
          },
        };
      }
      return demoViewForRange(range);
    });

    render(<CostDashboard demoMode />);

    expect(await screen.findByText("INITIAL_RUN")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-06-01" },
    });
    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-08" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));
    await waitFor(() =>
      expect(fetchDemoDashboardView).toHaveBeenCalledWith({
        startDate: "2026-06-01",
        endDate: "2026-06-08",
      }),
    );

    const runButton = screen.getByRole("button", { name: "Run analysis" });
    expect(runButton).toBeDisabled();

    await act(async () => {
      pendingRange.resolve({
        ...demoViewForRange({ startDate: "2026-06-01", endDate: "2026-06-08" }),
        run: {
          ...demoDashboardView.run,
          id: "custom-range-run",
        },
        header: {
          ...demoDashboardView.header,
          accountLocator: "CUSTOM_RANGE",
        },
      });
    });

    expect(await screen.findByText("CUSTOM_RANGE")).toBeInTheDocument();
    await waitFor(() => expect(runButton).not.toBeDisabled());

    fireEvent.click(runButton);
    expect(await screen.findByText("NEW_RUN")).toBeInTheDocument();
    expect(screen.getByText("NEW_RUN")).toBeInTheDocument();
  });

  it("allows a new run after a pending range rejection settles", async () => {
    const pendingRange = createDeferred<ReturnType<typeof demoViewForRange>>();
    let defaultLoadCount = 0;
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) => {
      if (range?.startDate === "2026-06-01") {
        return pendingRange.promise;
      }
      if (range?.windowDays === 30) {
        defaultLoadCount += 1;
        return {
          ...demoViewForRange(range),
          run: {
            ...demoDashboardView.run,
            id: defaultLoadCount === 1 ? "initial-run" : "new-run",
          },
          header: {
            ...demoDashboardView.header,
            accountLocator: defaultLoadCount === 1 ? "INITIAL_RUN" : "NEW_RUN",
          },
        };
      }
      return demoViewForRange(range);
    });

    render(<CostDashboard demoMode />);

    expect(await screen.findByText("INITIAL_RUN")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-06-01" },
    });
    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-08" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));
    await waitFor(() =>
      expect(fetchDemoDashboardView).toHaveBeenCalledWith({
        startDate: "2026-06-01",
        endDate: "2026-06-08",
      }),
    );

    const runButton = screen.getByRole("button", { name: "Run analysis" });
    expect(runButton).toBeDisabled();

    await act(async () => {
      pendingRange.reject(new Error("range failure"));
    });

    expect(
      await screen.findByText("Could not load selected date range."),
    ).toBeInTheDocument();
    await waitFor(() => expect(runButton).not.toBeDisabled());

    fireEvent.click(runButton);
    expect(await screen.findByText("NEW_RUN")).toBeInTheDocument();
    expect(screen.getByText("NEW_RUN")).toBeInTheDocument();
    expect(
      screen.queryByText("Could not load selected date range."),
    ).not.toBeInTheDocument();
  });

  it("reflects the backend range returned for a fetched custom date range", async () => {
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) => {
      if (range?.startDate === "2026-06-01") {
        return {
          ...demoDashboardView,
          range: {
            mode: "custom",
            windowDays: null,
            startDate: "2026-06-01",
            endDate: "2026-06-07",
          },
        };
      }

      return demoDashboardView;
    });

    render(<CostDashboard demoMode />);

    await screen.findByText("Overview");
    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-06-01" },
    });
    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-08" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));

    await waitFor(() =>
      expect(screen.getByLabelText("End date")).toHaveValue("2026-06-07"),
    );
    await waitFor(() => expect(fetchDemoDashboardView).toHaveBeenCalledTimes(4));

    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));

    expect(fetchDemoDashboardView).toHaveBeenCalledTimes(4);

    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-08" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));

    expect(fetchDemoDashboardView).toHaveBeenCalledTimes(4);
  });

  it("keeps the current view and shows a range-specific message when range fetch fails", async () => {
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) => {
      if (range?.startDate === "2026-06-01") {
        throw new Error("Dashboard API request failed with 400");
      }
      return demoViewForRange(range);
    });

    render(<CostDashboard demoMode />);

    await screen.findByText("Overview");
    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-06-01" },
    });
    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-08" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));

    expect(
      await screen.findByText("Could not load selected date range."),
    ).toBeInTheDocument();
    expect(screen.getByText("Overview")).toBeInTheDocument();
  });

  it("shows demo freshness and account locator in the header", async () => {
    vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);

    render(<CostDashboard demoMode />);

    expect(
      await screen.findByText("Demo data through Jun 8, 2026"),
    ).toBeInTheDocument();
    expect(screen.getByText("DEMO123")).toBeInTheDocument();
  });

  it("renders the mixed-currency unsupported state from metadata", async () => {
    vi.mocked(fetchDemoDashboardView).mockResolvedValue({
      ...demoDashboardView,
      unsupported: {
        title: "Mixed currencies are not supported",
        detail: "Select a single billing currency before running the dashboard.",
      },
    });

    render(<CostDashboard demoMode />);

    expect(
      await screen.findByText(/Mixed currencies are not supported/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Overview")).not.toBeInTheDocument();
  });

  it("disables the run action and shows placeholders while loading", () => {
    vi.mocked(fetchDemoDashboardView).mockReturnValue(
      new Promise(() => undefined),
    );

    render(<CostDashboard demoMode />);

    expect(screen.getByRole("button", { name: "Run analysis" })).toBeDisabled();
    expect(screen.getByLabelText("Loading dashboard")).toBeInTheDocument();
  });

  it("starts a Snowflake run with selected organization and bearer token", async () => {
    const runningRun: DashboardRun = {
      ...demoDashboardView.run,
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
    vi.mocked(fetchDashboardView).mockResolvedValue({
      ...demoDashboardView,
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
    expect(fetchDashboardView).toHaveBeenCalledWith(
      "run-123",
      { windowDays: 30 },
      { accessToken: "test-access-token" },
    );
    await waitFor(() => expect(fetchDashboardView).toHaveBeenCalledTimes(3));
    expect(fetchDashboardView).toHaveBeenCalledWith(
      "run-123",
      { windowDays: 7 },
      { accessToken: "test-access-token" },
    );
    expect(fetchDashboardView).toHaveBeenCalledWith(
      "run-123",
      { windowDays: 90 },
      { accessToken: "test-access-token" },
    );
    expect(fetchDemoDashboardView).not.toHaveBeenCalled();
  });

  it("keeps the run action disabled while a queued Snowflake run is polling", async () => {
    const queuedRun: DashboardRun = {
      ...demoDashboardView.run,
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
