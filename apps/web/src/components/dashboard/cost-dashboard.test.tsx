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

  it("renders required dollar dashboard sections", () => {
    render(<CostDashboard data={demoDashboardView} />);

    expect(screen.getByText("Overview")).toBeInTheDocument();
    // The demo view carries a current balance date, so the title is dated.
    expect(screen.getByText("Ending Balance as of Jun 08")).toBeInTheDocument();
    // The demo view uses a relative 30-day window, so the KPI label is scoped.
    expect(screen.getByText("Total Spend in Last 30 Days")).toBeInTheDocument();
    expect(screen.getByText("Total spend by service")).toBeInTheDocument();
    expect(
      screen.getByTestId("dashboard-section-warehouse-spend"),
    ).toBeInTheDocument();
    expect(screen.getByText("Storage spend")).toBeInTheDocument();
    expect(screen.queryByText("Total Spend in Period")).not.toBeInTheDocument();
    // "Warehouse spend" now only appears as the section heading; the bottom 2x2
    // detail tables were removed.
    expect(screen.getAllByText("Warehouse spend").length).toBeGreaterThan(0);
    // The storage section's right-side card is titled "Total spend by database".
    // The removed bottom 2x2 detail tables ("User compute spend",
    // "Storage by database") should no longer render.
    expect(screen.getByText("Total spend by database")).toBeInTheDocument();
    expect(screen.queryByText("User compute spend")).not.toBeInTheDocument();
    expect(screen.queryByText("Storage by database")).not.toBeInTheDocument();
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
    expect(screen.getByTestId("overview-skeleton")).toBeInTheDocument();
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

    // Overview is server-ready: its content paints while warehouse (pending) and
    // storage (unavailable) remain in their loading skeletons.
    expect(
      await screen.findByText("Total Spend in Last 30 Days"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("overview-skeleton")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("warehouse-spend-skeleton-chart"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("storage-spend-skeleton-chart"),
    ).toBeInTheDocument();
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
});
