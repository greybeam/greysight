import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type DashboardViewRangeRequest,
  fetchCachedDashboardRun,
  fetchDashboardView,
  fetchDemoDashboardView,
  pollUntilTerminal,
  startDashboardRun,
} from "../../lib/dashboard-api";
import demoDashboardView from "../../lib/demo-dashboard-view";
import {
  FETCH_WINDOW_DAYS,
  type DashboardRun,
  type DashboardView,
} from "../../lib/dashboard-contracts";
import CostDashboard from "./cost-dashboard";
import { REVEAL_STEP_MS } from "./use-section-statuses";

vi.mock("../../lib/dashboard-api", () => ({
  fetchCachedDashboardRun: vi.fn(),
  fetchDashboardView: vi.fn(),
  fetchDemoDashboardView: vi.fn(),
  pollUntilTerminal: vi.fn(),
  startDashboardRun: vi.fn(),
}));

// Drives the progressive `/view` poll the component runs for Snowflake runs:
// fetch once, surface the result via `onResult`, then resolve with it as the
// terminal view. Mirrors the real `pollUntilTerminal` for a single-shot result.
function mockPollResolvesWith(view: DashboardView) {
  vi.mocked(pollUntilTerminal<DashboardView>).mockImplementation(
    async (fetcher, _isTerminal, options) => {
      const result = await fetcher();
      options?.onResult?.(result);
      return view;
    },
  );
}

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

  it("loads demo prepared view and prefetches relative windows", async () => {
    vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);

    render(<CostDashboard demoMode />);

    await screen.findByText("Total Spend in Last 30 Days");
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 30 });
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 7 });
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 90 });
  });

  it("switches to cached relative prepared view without another request", async () => {
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) =>
      demoViewForRange(range),
    );

    render(<CostDashboard demoMode />);

    await screen.findByLabelText("Start date");
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

    await screen.findByLabelText("Start date");
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

    // Data is pre-loaded; the overview section should be ready (no skeleton).
    expect(screen.queryByTestId("overview-skeleton")).not.toBeInTheDocument();

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

    expect(
      screen.getByTestId("dashboard-section-overview"),
    ).toBeInTheDocument();
    // Switching to a pre-run org drops to the static idle state, not the
    // animated skeleton (no run has been initiated for org-b yet).
    expect(screen.queryByTestId("overview-skeleton")).not.toBeInTheDocument();
    expect(
      screen.getAllByText(/No cached run available/i).length,
    ).toBeGreaterThan(0);
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

    await screen.findByLabelText("Start date");
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
    const NEW_RUN_SENTINEL = "$9,999.99";
    let defaultLoadCount = 0;
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) => {
      if (range?.startDate === "2026-06-01") {
        return pendingRange.promise;
      }
      if (range?.windowDays === 30) {
        defaultLoadCount += 1;
        return {
          ...demoViewForRange(range),
          totalSpend: {
            ...demoDashboardView.totalSpend,
            totalLabel:
              defaultLoadCount === 1 ? demoDashboardView.totalSpend.totalLabel : NEW_RUN_SENTINEL,
          },
        };
      }
      return demoViewForRange(range);
    });

    render(<CostDashboard demoMode />);

    await screen.findByLabelText("Start date");
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
      pendingRange.resolve(
        demoViewForRange({ startDate: "2026-06-01", endDate: "2026-06-08" }),
      );
    });

    await waitFor(() => expect(runButton).not.toBeDisabled());

    // Record call count before clicking Run analysis so we can prove a NEW call
    // fires (not just that the initial-load call happened to match windowDays=30).
    const callCountBeforeRun = vi.mocked(fetchDemoDashboardView).mock.calls.length;

    fireEvent.click(runButton);

    // The click must trigger exactly one additional fetchDemoDashboardView({windowDays:30}).
    await waitFor(() =>
      expect(vi.mocked(fetchDemoDashboardView).mock.calls.length).toBe(callCountBeforeRun + 1),
    );
    expect(
      vi.mocked(fetchDemoDashboardView).mock.calls[callCountBeforeRun][0],
    ).toEqual({ windowDays: 30 });

    // The sentinel value from the new run's response must become visible,
    // proving the result was applied (not just that the call was made).
    expect(await screen.findByText(NEW_RUN_SENTINEL)).toBeInTheDocument();
    expect(runButton).not.toBeDisabled();
  });

  it("allows a new run after a pending range rejection settles", async () => {
    const pendingRange = createDeferred<ReturnType<typeof demoViewForRange>>();
    const NEW_RUN_SENTINEL = "$8,888.88";
    let defaultLoadCount = 0;
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) => {
      if (range?.startDate === "2026-06-01") {
        return pendingRange.promise;
      }
      if (range?.windowDays === 30) {
        defaultLoadCount += 1;
        return {
          ...demoViewForRange(range),
          totalSpend: {
            ...demoDashboardView.totalSpend,
            totalLabel:
              defaultLoadCount === 1 ? demoDashboardView.totalSpend.totalLabel : NEW_RUN_SENTINEL,
          },
        };
      }
      return demoViewForRange(range);
    });

    render(<CostDashboard demoMode />);

    await screen.findByLabelText("Start date");
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

    // Record call count before clicking Run analysis to prove a NEW call fires.
    const callCountBeforeRun = vi.mocked(fetchDemoDashboardView).mock.calls.length;

    fireEvent.click(runButton);

    // The click must trigger exactly one additional fetchDemoDashboardView({windowDays:30}).
    await waitFor(() =>
      expect(vi.mocked(fetchDemoDashboardView).mock.calls.length).toBe(callCountBeforeRun + 1),
    );
    expect(
      vi.mocked(fetchDemoDashboardView).mock.calls[callCountBeforeRun][0],
    ).toEqual({ windowDays: 30 });

    // The sentinel value from the new run's response must become visible,
    // proving the result was applied. The range error is cleared by the new run.
    expect(await screen.findByText(NEW_RUN_SENTINEL)).toBeInTheDocument();
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

    await screen.findByLabelText("Start date");
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

    await screen.findByLabelText("Start date");
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

  it("disables the run action and shows skeleton sections while loading", () => {
    vi.mocked(fetchDemoDashboardView).mockReturnValue(
      new Promise(() => undefined),
    );

    render(<CostDashboard demoMode />);

    // While loading the button swaps its label for the running spinner state.
    expect(screen.getByRole("button", { name: /Running/ })).toBeDisabled();
    expect(screen.getByTestId("overview-skeleton")).toBeInTheDocument();
    expect(
      screen.getByTestId("dashboard-section-warehouse-spend"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("dashboard-section-storage-spend"),
    ).toBeInTheDocument();
    // The filter bar is not rendered until a view exists.
    expect(
      screen.queryByRole("button", { name: "Apply date range" }),
    ).not.toBeInTheDocument();
  });

  it("shows a static idle empty state (no skeletons) before the first Snowflake run", async () => {
    const queuedRun: DashboardRun = {
      ...demoDashboardView.run,
      id: "run-idle",
      source: "snowflake",
      status: "queued",
    };
    vi.mocked(startDashboardRun).mockResolvedValue(queuedRun);
    vi.mocked(pollUntilTerminal).mockReturnValue(new Promise(() => undefined));

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

    // Idle: no animated skeleton test-ids anywhere on screen.
    for (const skeletonId of [
      "overview-skeleton",
      "warehouse-spend-skeleton-chart",
      "storage-spend-skeleton-chart",
      "ai-spend-skeleton-chart",
    ]) {
      expect(screen.queryByTestId(skeletonId)).not.toBeInTheDocument();
    }
    // A clear call-to-action prompts the user to run an analysis (one per
    // idle section).
    expect(
      screen.getAllByText(/No cached run available/i).length,
    ).toBeGreaterThan(0);
    // No run has been started yet.
    expect(startDashboardRun).not.toHaveBeenCalled();

    // Clicking "Run analysis" transitions out of idle into the loading skeletons.
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    await waitFor(() =>
      expect(screen.getByTestId("overview-skeleton")).toBeInTheDocument(),
    );
    expect(
      screen.getByTestId("warehouse-spend-skeleton-chart"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("storage-spend-skeleton-chart"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/No cached run available/i),
    ).not.toBeInTheDocument();
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
    const completedView: DashboardView = {
      ...demoDashboardView,
      run: completedRun,
    };
    vi.mocked(startDashboardRun).mockResolvedValue(runningRun);
    vi.mocked(fetchDashboardView).mockResolvedValue(completedView);
    mockPollResolvesWith(completedView);

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

  it("reveals only server-ready sections while a Snowflake run streams provisional views", async () => {
    const runningRun: DashboardRun = {
      ...demoDashboardView.run,
      id: "run-progressive",
      source: "snowflake",
      status: "running",
      user_safe_message: "Storage failed safely.",
    };
    const provisionalView: DashboardView = {
      ...demoDashboardView,
      run: runningRun,
      sectionStatuses: {
        overview: "ready",
        warehouse: "pending",
        storage: "unavailable",
      },
    };
    vi.mocked(startDashboardRun).mockResolvedValue(runningRun);
    vi.mocked(fetchDashboardView).mockResolvedValue(provisionalView);
    // Surface the provisional view via onResult, then stay pending so the
    // per-section readiness (not a terminal completion) drives the reveal.
    vi.mocked(pollUntilTerminal).mockImplementation(
      async (fetcher, _isTerminal, options) => {
        const result = (await fetcher()) as DashboardView;
        options?.onResult?.(result);
        return new Promise<never>(() => undefined);
      },
    );

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

    // Overview is server-ready and warehouse is still pending. Storage reached
    // a terminal unavailable state, so it must show its error instead of a
    // permanent loading skeleton.
    expect(
      await screen.findByText("Total Spend in Last 30 Days"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("overview-skeleton")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("warehouse-spend-skeleton-chart"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Storage failed safely."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("storage-spend-skeleton-chart")).toBeNull();
  });

  it("keeps unavailable sections failed after changing range on a completed Snowflake run", async () => {
    const runningRun: DashboardRun = {
      ...demoDashboardView.run,
      id: "run-range-unavailable",
      source: "snowflake",
      status: "running",
    };
    const completedRun: DashboardRun = {
      ...runningRun,
      status: "completed",
      user_safe_message: "Storage failed safely.",
    };
    // Completed run where storage never landed: its terminal error must persist
    // across range changes rather than falling back to the all-ready reveal.
    const completedView: DashboardView = {
      ...demoDashboardView,
      run: completedRun,
      sectionStatuses: {
        overview: "ready",
        warehouse: "ready",
        storage: "unavailable",
      },
    };
    vi.mocked(startDashboardRun).mockResolvedValue(runningRun);
    // Every range returns the same completed-but-storage-unavailable view, with
    // its range reflecting the requested window so the active-range chip tracks.
    vi.mocked(fetchDashboardView).mockImplementation(async (_runId, request) => ({
      ...completedView,
      range: {
        ...completedView.range,
        mode: "relative" as const,
        windowDays: request?.windowDays ?? 30,
      },
    }));
    mockPollResolvesWith(completedView);

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

    // The completed view paints overview and the storage error. Wait for the
    // relative-window prefetch so the 7-day view is cached before switching.
    expect(
      await screen.findByText("Total Spend in Last 30 Days"),
    ).toBeInTheDocument();
    await waitFor(() => expect(fetchDashboardView).toHaveBeenCalledTimes(3));
    expect(screen.getByText("Storage failed safely.")).toBeInTheDocument();
    expect(screen.queryByTestId("storage-spend-skeleton-chart")).toBeNull();

    // Switch to the cached 7-day window: the cached view's section statuses
    // must be reused so the unavailable storage section remains terminal. No
    // additional fetch is issued for the cached range.
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));

    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(fetchDashboardView).toHaveBeenCalledTimes(3);
    await waitFor(() =>
      expect(screen.getByText("Storage failed safely.")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("storage-spend-skeleton-chart")).toBeNull();
    // The 7-day overview content still paints (it is ready); only the
    // unavailable storage section stays failed.
    expect(screen.getByText("Total Spend in Last 7 Days")).toBeInTheDocument();
  });

  it.each([["failed"], ["expired"]] as const)(
    "applies a %s terminal view without revealing all sections as ready",
    async (terminalStatus) => {
      const runningRun: DashboardRun = {
        ...demoDashboardView.run,
        id: "run-terminal",
        source: "snowflake",
        status: "running",
      };
      const provisionalView: DashboardView = {
        ...demoDashboardView,
        run: runningRun,
        sectionStatuses: {
          overview: "ready",
          warehouse: "pending",
          storage: "pending",
        },
      };
      // The terminal view carries the failure status and section statuses that
      // are NOT all ready: only overview is ready, warehouse/storage are pending.
      const terminalRun: DashboardRun = {
        ...runningRun,
        status: terminalStatus,
        user_safe_message: "The run did not finish.",
      };
      const terminalView: DashboardView = {
        ...demoDashboardView,
        run: terminalRun,
        sectionStatuses: {
          overview: "ready",
          warehouse: "pending",
          storage: "pending",
        },
      };
      vi.mocked(startDashboardRun).mockResolvedValue(runningRun);
      vi.mocked(fetchDashboardView).mockResolvedValue(provisionalView);
      // First call surfaces the provisional (non-terminal) view via onResult,
      // then the poll resolves with the terminal view as its final result.
      vi.mocked(pollUntilTerminal).mockImplementation(
        async (fetcher, _isTerminal, options) => {
          const result = (await fetcher()) as DashboardView;
          options?.onResult?.(result);
          return terminalView;
        },
      );

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

      // The terminal view's error message surfaces as the inline alert.
      expect(
        await screen.findByText("The run did not finish."),
      ).toBeInTheDocument();

      // The terminal view is applied (overview content paints), but the pending
      // warehouse/storage sections must NOT be revealed as ready — their
      // skeletons remain because the terminal section statuses are the source
      // of truth, not the all-ready timed-stagger reveal.
      expect(
        screen.getByText("Total Spend in Last 30 Days"),
      ).toBeInTheDocument();
      await waitFor(() =>
        expect(
          screen.getByTestId("warehouse-spend-skeleton-chart"),
        ).toBeInTheDocument(),
      );
      expect(
        screen.getByTestId("storage-spend-skeleton-chart"),
      ).toBeInTheDocument();
    },
  );

  it("keeps the run action disabled while a queued Snowflake run is polling", async () => {
    const queuedRun: DashboardRun = {
      ...demoDashboardView.run,
      id: "run-queued",
      source: "snowflake",
      status: "queued",
    };
    vi.mocked(startDashboardRun).mockResolvedValue(queuedRun);
    vi.mocked(pollUntilTerminal).mockReturnValue(new Promise(() => undefined));

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
    // The queued/polling run keeps the button disabled in its running state.
    expect(screen.getByRole("button", { name: /Running/ })).toBeDisabled();
  });

  it("shows skeletons instead of stale data while a Snowflake re-run is in flight", async () => {
    vi.useFakeTimers();
    try {
      const priorView = {
        ...demoDashboardView,
        run: {
          ...demoDashboardView.run,
          id: "run-prior",
          source: "snowflake" as const,
          status: "completed" as const,
        },
      };
      const queuedRun: DashboardRun = {
        ...demoDashboardView.run,
        id: "run-rerun",
        source: "snowflake",
        status: "queued",
      };
      vi.mocked(startDashboardRun).mockResolvedValue(queuedRun);
      // Keep the poll pending so the re-run stays in flight.
      vi.mocked(pollUntilTerminal).mockReturnValue(new Promise(() => undefined));

      render(
        <CostDashboard
          demoMode={false}
          data={priorView}
          runtime={{
            accessToken: "test-access-token",
            organizationId: "org-123",
            organizationName: "Acme Analytics",
          }}
        />,
      );

      // The prior successful view is rendered before the re-run starts.
      expect(
        screen.getByText("Total Spend in Last 30 Days"),
      ).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

      // Flush the microtasks so `startDashboardRun` resolves and the load state
      // flips from "loading" to "queued" while the poll stays pending. Under the
      // pre-fix `dataReady` predicate (which ignored `runInFlight`), this queued
      // window is treated as "ready", so the stagger effect schedules the reveal
      // timers below against the STALE prior view.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(startDashboardRun).toHaveBeenCalledTimes(1);

      // Advance past the full stagger window (overview 1x, warehouse 2x, storage
      // 3x REVEAL_STEP_MS) plus a margin. Pre-fix, this is exactly when the stale
      // sections would reveal; the fix keeps `dataReady` false for the whole
      // in-flight window so the skeletons must remain.
      act(() => {
        vi.advanceTimersByTime(REVEAL_STEP_MS * 3 + REVEAL_STEP_MS);
      });

      expect(screen.getByTestId("overview-skeleton")).toBeInTheDocument();
      expect(
        screen.getByTestId("warehouse-spend-skeleton-chart"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("storage-spend-skeleton-chart"),
      ).toBeInTheDocument();
      expect(
        screen.queryByText("Total Spend in Last 30 Days"),
      ).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows skeletons instead of the stale unsupported state while a re-run is in flight", async () => {
    const unsupportedView = {
      ...demoDashboardView,
      run: {
        ...demoDashboardView.run,
        id: "run-unsupported",
        source: "snowflake" as const,
        status: "completed" as const,
      },
      unsupported: {
        title: "Mixed currencies are not supported",
        detail:
          "Select a single billing currency before running the dashboard.",
      },
    };
    const queuedRun: DashboardRun = {
      ...demoDashboardView.run,
      id: "run-rerun",
      source: "snowflake",
      status: "queued",
    };
    vi.mocked(startDashboardRun).mockResolvedValue(queuedRun);
    vi.mocked(pollUntilTerminal).mockReturnValue(new Promise(() => undefined));

    render(
      <CostDashboard
        demoMode={false}
        data={unsupportedView}
        runtime={{
          accessToken: "test-access-token",
          organizationId: "org-123",
          organizationName: "Acme Analytics",
        }}
      />,
    );

    // The stale unsupported message is shown before the re-run starts.
    expect(
      screen.getByText(/Mixed currencies are not supported/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    // While the re-run is in flight, the unsupported branch is skipped and the
    // skeleton layout shows instead of the stale unsupported message.
    await waitFor(() => expect(startDashboardRun).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId("overview-skeleton")).toBeInTheDocument(),
    );
    expect(
      screen.queryByText(/Mixed currencies are not supported/),
    ).not.toBeInTheDocument();
  });

  it("staggers section reveal on initial demo load", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);

      render(<CostDashboard demoMode />);

      // Flush the initial fetch microtasks.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      // Data resolved but sections still revealing: overview reveals first,
      // so its skeleton is gone while the warehouse skeleton is still present.
      act(() => {
        vi.advanceTimersByTime(140);
      });
      expect(screen.queryByTestId("overview-skeleton")).not.toBeInTheDocument();
      expect(
        screen.getByTestId("warehouse-spend-skeleton-chart"),
      ).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(140 * 3);
      });
      // After the full stagger, ready content is present and no skeletons remain.
      expect(
        screen.getByText("Total Spend in Last 30 Days"),
      ).toBeInTheDocument();
      expect(
        screen.queryByTestId("storage-spend-skeleton-chart"),
      ).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("reveals all sections instantly under reduced motion", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    try {
      vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);
      render(<CostDashboard demoMode />);

      // Flush only the fetch microtasks; do NOT advance any timers.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      // `usePrefersReducedMotion` reads matchMedia().matches synchronously via
      // useSyncExternalStore, so the hook reveals all sections at once with no
      // timers. Ready content must therefore be present with zero timer
      // advancement — under a normal stagger this would still be skeletons.
      expect(
        screen.getByText("Total Spend in Last 30 Days"),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("overview-skeleton")).toBeNull();
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it("shows an error state instead of skeletons when the initial run fails", async () => {
    vi.mocked(fetchDemoDashboardView).mockRejectedValue(new Error("boom"));

    render(<CostDashboard demoMode />);

    // On an initial-run failure with no view to fall back on, the message is
    // surfaced by SectionEmptyState inside the "Dashboard content" region.
    const content = screen.getByLabelText("Dashboard content");
    expect(
      await within(content).findByText("Could not load dashboard data."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("overview-skeleton")).not.toBeInTheDocument();
  });

  describe("cached views", () => {
    const cachedRun: DashboardRun = {
      ...demoDashboardView.run,
      id: "cached-run-1",
      source: "snowflake",
      status: "completed",
    };
    const cachedView: DashboardView = {
      ...demoDashboardView,
      run: cachedRun,
    };

    it("renders a cached run with the indicator and no fresh POST on mount", async () => {
      vi.mocked(fetchCachedDashboardRun).mockResolvedValue({
        run: cachedRun,
        cachedAsOf: "2026-07-06T14:30:00Z",
      });
      vi.mocked(fetchDashboardView).mockResolvedValue(cachedView);

      render(
        <CostDashboard
          demoMode={false}
          runtime={{
            accessToken: "tok",
            organizationId: "org-cache",
            organizationName: "Acme",
          }}
        />,
      );

      expect(
        await screen.findByText(/Using cached view as of/),
      ).toBeInTheDocument();
      expect(fetchCachedDashboardRun).toHaveBeenCalledWith("org-cache", {
        accessToken: "tok",
      });
      // A cache hit must render without starting a Snowflake query.
      expect(startDashboardRun).not.toHaveBeenCalled();
      expect(fetchDashboardView).toHaveBeenCalledWith(
        "cached-run-1",
        { windowDays: 30 },
        { accessToken: "tok" },
      );
    });

    it("keeps the cached run when switching the time window (no fresh POST)", async () => {
      vi.mocked(fetchCachedDashboardRun).mockResolvedValue({
        run: cachedRun,
        cachedAsOf: "2026-07-06T14:30:00Z",
      });
      vi.mocked(fetchDashboardView).mockImplementation(async (_runId, request) => ({
        ...cachedView,
        range: {
          ...cachedView.range,
          mode: "relative" as const,
          windowDays: request?.windowDays ?? 30,
        },
      }));

      render(
        <CostDashboard
          demoMode={false}
          runtime={{
            accessToken: "tok",
            organizationId: "org-cache",
            organizationName: "Acme",
          }}
        />,
      );

      await screen.findByText(/Using cached view as of/);
      // Prefetch already covers 7/90; switching to a prefetched window serves the
      // cached view without a fresh run and keeps the indicator on screen.
      fireEvent.click(screen.getByRole("button", { name: "7 days" }));

      await waitFor(() =>
        expect(screen.getByText(/Using cached view as of/)).toBeInTheDocument(),
      );
      expect(startDashboardRun).not.toHaveBeenCalled();
    });

    it("clears the cached indicator when Run analysis is clicked", async () => {
      vi.mocked(fetchCachedDashboardRun).mockResolvedValue({
        run: cachedRun,
        cachedAsOf: "2026-07-06T14:30:00Z",
      });
      vi.mocked(fetchDashboardView).mockResolvedValue(cachedView);

      const freshRun: DashboardRun = {
        ...cachedRun,
        id: "fresh-run-1",
        status: "running",
      };
      const freshCompleted: DashboardView = {
        ...cachedView,
        run: { ...freshRun, status: "completed" },
      };
      vi.mocked(startDashboardRun).mockResolvedValue(freshRun);
      mockPollResolvesWith(freshCompleted);

      render(
        <CostDashboard
          demoMode={false}
          runtime={{
            accessToken: "tok",
            organizationId: "org-cache",
            organizationName: "Acme",
          }}
        />,
      );

      await screen.findByText(/Using cached view as of/);
      fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

      await waitFor(() =>
        expect(screen.queryByText(/Using cached view as of/)).not.toBeInTheDocument(),
      );
      expect(startDashboardRun).toHaveBeenCalled();
    });

    it("does not let an in-flight cache lookup clobber a fresh run started mid-flight", async () => {
      // The cache lookup is deferred so it is still in flight when the user
      // clicks "Run analysis". Without the run-generation guard, the late cached
      // response would re-apply the cached view and re-set the cached indicator,
      // clobbering the fresh run the user just started.
      const pendingCache = createDeferred<{
        run: DashboardRun;
        cachedAsOf: string;
      }>();
      vi.mocked(fetchCachedDashboardRun).mockReturnValue(pendingCache.promise);

      const freshRun: DashboardRun = {
        ...cachedRun,
        id: "fresh-run-1",
        status: "running",
      };
      const freshCompleted: DashboardView = {
        ...cachedView,
        run: { ...freshRun, status: "completed" },
      };
      vi.mocked(startDashboardRun).mockResolvedValue(freshRun);
      // The fresh run's `/view` fetches resolve with the fresh view; the cached
      // `/view` is never reached because the guard aborts first.
      vi.mocked(fetchDashboardView).mockResolvedValue(freshCompleted);
      mockPollResolvesWith(freshCompleted);

      render(
        <CostDashboard
          demoMode={false}
          runtime={{
            accessToken: "tok",
            organizationId: "org-cache",
            organizationName: "Acme",
          }}
        />,
      );

      // Cache lookup is in flight; the user starts a fresh run.
      await waitFor(() => expect(fetchCachedDashboardRun).toHaveBeenCalled());
      fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
      await waitFor(() => expect(startDashboardRun).toHaveBeenCalled());

      // The cache lookup resolves late — after the fresh run took over.
      await act(async () => {
        pendingCache.resolve({
          run: cachedRun,
          cachedAsOf: "2026-07-06T14:30:00Z",
        });
        await pendingCache.promise;
      });

      // The stale cached response must not resurrect the cached indicator, and
      // it must not fetch the cached run's `/view`.
      await waitFor(() =>
        expect(screen.getByText("Total Spend in Last 30 Days")).toBeInTheDocument(),
      );
      expect(screen.queryByText(/Using cached view as of/)).not.toBeInTheDocument();
      expect(fetchDashboardView).not.toHaveBeenCalledWith(
        "cached-run-1",
        expect.anything(),
        expect.anything(),
      );
    });

    it("falls back to the idle state on a 204 cache miss (no auto-run)", async () => {
      vi.mocked(fetchCachedDashboardRun).mockResolvedValue(null);

      render(
        <CostDashboard
          demoMode={false}
          runtime={{
            accessToken: "tok",
            organizationId: "org-cache",
            organizationName: "Acme",
          }}
        />,
      );

      await waitFor(() => expect(fetchCachedDashboardRun).toHaveBeenCalled());
      expect(screen.queryByText(/Using cached view as of/)).not.toBeInTheDocument();
      expect(startDashboardRun).not.toHaveBeenCalled();
      expect(fetchDashboardView).not.toHaveBeenCalled();
    });
  });
});
