import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
  type RenderOptions,
} from "@testing-library/react";
import { QueryClient, notifyManager } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

// React Query batches observer notifications through a setTimeout(0) scheduler by
// default, which fake timers freeze. Flush notifications synchronously so the
// fake-timer reveal tests observe query results without advancing timers. Scope
// the override to this suite and restore the default scheduler afterwards so the
// altered batching does not leak into other suites sharing the worker.
const DEFAULT_NOTIFY_SCHEDULER = (callback: () => void) => setTimeout(callback, 0);
beforeAll(() => {
  notifyManager.setScheduler((callback) => callback());
});
afterAll(() => {
  notifyManager.setScheduler(DEFAULT_NOTIFY_SCHEDULER);
});

import {
  type DashboardViewRangeRequest,
  fetchCachedDashboardRun,
  fetchDashboardView,
  fetchDemoDashboardView,
  pollDashboardSource,
  pollUntilTerminal,
  startDashboardRun,
  triggerDashboardSource,
} from "../../lib/dashboard-api";
import demoDashboardView from "../../lib/demo-dashboard-view";
import {
  FETCH_WINDOW_DAYS,
  type AIDetailViewModel,
  type DashboardRun,
  type DashboardView,
} from "../../lib/dashboard-contracts";
import { queryKeys } from "../../lib/query-keys";
import {
  DEMO_ORG_ID,
  DEMO_USER_ID,
} from "../../lib/query-identity";
import {
  QueryTestProvider,
  createTestQueryClient,
} from "../../lib/query-test-utils";
import CostDashboard from "./cost-dashboard";
import { REVEAL_STEP_MS } from "./use-section-statuses";

vi.mock("../../lib/dashboard-api", () => ({
  fetchCachedDashboardRun: vi.fn(),
  fetchDashboardView: vi.fn(),
  fetchDemoDashboardView: vi.fn(),
  fetchDemoDashboardSource: vi.fn(),
  pollDashboardSource: vi.fn(),
  triggerDashboardSource: vi.fn(),
  pollUntilTerminal: vi.fn(),
  startDashboardRun: vi.fn(),
}));

// Wrap every dashboard render in a QueryClientProvider so the component's
// useQuery/useQueryClient hooks resolve. A fresh client per render keeps tests
// isolated unless a persistent client is passed for remount/cross-consumer cases.
type TestIdentity = {
  userId?: string;
  identityEpoch?: number;
  activeOrganizationId?: string;
};

function makeWrapper(client: QueryClient, identity?: TestIdentity) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryTestProvider client={client} identity={identity}>
        {children}
      </QueryTestProvider>
    );
  };
}

// Mirrors the OrgShell production client: results stay fresh for 60s so a
// remount within staleTime paints from the cache without refetching.
function createPersistentQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, staleTime: 60_000 },
    },
  });
}

function renderDashboard(
  ui: ReactElement,
  options: {
    client?: QueryClient;
    identity?: TestIdentity;
  } & Omit<RenderOptions, "wrapper"> = {},
) {
  const { client, identity, ...rest } = options;
  const queryClient = client ?? createTestQueryClient();
  return {
    client: queryClient,
    ...render(ui, { wrapper: makeWrapper(queryClient, identity), ...rest }),
  };
}

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
  beforeEach(() => {
    // Default the Snowflake discovery lookup to a 204 miss so authenticated
    // renders land in the idle state unless a test opts into a cache hit.
    vi.mocked(fetchCachedDashboardRun).mockResolvedValue(null);
  });

  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it("loads demo prepared view and prefetches relative windows", async () => {
    vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);

    renderDashboard(<CostDashboard demoMode />);

    await screen.findByText("Total Spend in Last 30 Days");
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 30 });
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 7 });
    expect(fetchDemoDashboardView).toHaveBeenCalledWith({ windowDays: 90 });
  });

  it("switches to cached relative prepared view without another request", async () => {
    vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) =>
      demoViewForRange(range),
    );

    renderDashboard(<CostDashboard demoMode />);

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

    renderDashboard(<CostDashboard demoMode />);

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

    renderDashboard(<CostDashboard demoMode data={demoDashboardView} />);

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
    const { rerender } = renderDashboard(
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

    renderDashboard(<CostDashboard demoMode />);

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

    renderDashboard(<CostDashboard demoMode />);

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

    renderDashboard(<CostDashboard demoMode />);

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

    renderDashboard(<CostDashboard demoMode />);

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

    renderDashboard(<CostDashboard demoMode />);

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

    renderDashboard(<CostDashboard demoMode />);

    expect(
      await screen.findByText(/Mixed currencies are not supported/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Overview")).not.toBeInTheDocument();
  });

  it("disables the run action and shows skeleton sections while loading", () => {
    vi.mocked(fetchDemoDashboardView).mockReturnValue(
      new Promise(() => undefined),
    );

    renderDashboard(<CostDashboard demoMode />);

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

    renderDashboard(
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

    renderDashboard(
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

    renderDashboard(
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

  it("surfaces the classified group message on a completed run with an unavailable source group", async () => {
    // The run completed (no run-level user_safe_message); the classified safe
    // message lives on the collapsed metadata group (account_usage), which the
    // storage section must surface without a "Report this issue" link.
    const completedRun: DashboardRun = {
      ...demoDashboardView.run,
      id: "run-group-message",
      source: "snowflake",
      status: "completed",
      user_safe_message: null,
    };
    const completedView: DashboardView = {
      ...demoDashboardView,
      run: completedRun,
      metadata: {
        data_mode: "estimated",
        account_locator: null,
        currency: "USD",
        billing_through_date: null,
        account_usage_through_date: null,
        estimated_credit_price_usd: 3,
        storage_price_usd_per_tb_month: 20,
        unsupported_reason: null,
        organization_usage: {
          available: true,
          detail: null,
          user_safe_message: null,
        },
        account_usage: {
          available: false,
          detail: "Snowflake Account Usage is unavailable for this role.",
          user_safe_message:
            "Snowflake Account Usage is unavailable for this role.",
        },
      },
      sectionStatuses: {
        overview: "ready",
        warehouse: "ready",
        storage: "unavailable",
      },
    };
    vi.mocked(startDashboardRun).mockResolvedValue(completedRun);
    vi.mocked(fetchDashboardView).mockResolvedValue(completedView);
    mockPollResolvesWith(completedView);

    renderDashboard(
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

    expect(
      await screen.findByText(
        "Snowflake Account Usage is unavailable for this role.",
      ),
    ).toBeInTheDocument();
    // The classified group message is user-safe, so the storage section shows
    // no "Report this issue" link.
    const storageSection = screen.getByTestId(
      "dashboard-section-storage-spend",
    );
    expect(
      within(storageSection).queryByRole("link", {
        name: /report this issue/i,
      }),
    ).toBeNull();
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

    renderDashboard(
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

      renderDashboard(
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

    renderDashboard(
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

      renderDashboard(
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

    renderDashboard(
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

      renderDashboard(<CostDashboard demoMode />);

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
      renderDashboard(<CostDashboard demoMode />);

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

    renderDashboard(<CostDashboard demoMode />);

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

      renderDashboard(
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

      renderDashboard(
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

      renderDashboard(
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

      renderDashboard(
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

      renderDashboard(
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

  describe("query cache integration", () => {
    const USER_ID = "test-user";
    const ORG_ID = "org-cache";
    const runtime = {
      accessToken: "tok",
      organizationId: ORG_ID,
      organizationName: "Acme",
    };
    const identity = { activeOrganizationId: ORG_ID };
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
    const aiDetailFixture: AIDetailViewModel = {
      dailySeries: [],
      consumptionTypeNames: [],
      rankedConsumptionTypes: [],
      consumptionBars: [],
      isEmpty: true,
      partial: false,
      skippedBranches: [],
    };

    it("renders the previous view on remount within staleTime without repeating cached-run/view requests", async () => {
      vi.mocked(fetchCachedDashboardRun).mockResolvedValue({
        run: cachedRun,
        cachedAsOf: "2026-07-06T14:30:00Z",
      });
      vi.mocked(fetchDashboardView).mockResolvedValue(cachedView);

      const client = createPersistentQueryClient();
      const first = renderDashboard(
        <CostDashboard demoMode={false} runtime={runtime} />,
        { client, identity },
      );

      await screen.findByText(/Using cached view as of/);
      // Wait for the relative-window prefetches so all requests have settled.
      await waitFor(() => expect(fetchDashboardView).toHaveBeenCalledTimes(3));
      const cachedRunCalls = vi.mocked(fetchCachedDashboardRun).mock.calls.length;
      const viewCalls = vi.mocked(fetchDashboardView).mock.calls.length;

      first.unmount();

      renderDashboard(<CostDashboard demoMode={false} runtime={runtime} />, {
        client,
        identity,
      });

      // The remount paints from the cache: the cached view and indicator return
      // without any additional discovery or view requests.
      await screen.findByText(/Using cached view as of/);
      expect(
        await screen.findByText("Total Spend in Last 30 Days"),
      ).toBeInTheDocument();
      expect(fetchCachedDashboardRun).toHaveBeenCalledTimes(cachedRunCalls);
      expect(fetchDashboardView).toHaveBeenCalledTimes(viewCalls);
    });

    it("issues one request when simultaneous consumers read the same default view", async () => {
      vi.mocked(fetchCachedDashboardRun).mockResolvedValue({
        run: cachedRun,
        cachedAsOf: "2026-07-06T14:30:00Z",
      });
      vi.mocked(fetchDashboardView).mockResolvedValue(cachedView);

      const client = createPersistentQueryClient();
      renderDashboard(
        <>
          <CostDashboard demoMode={false} runtime={runtime} />
          <CostDashboard demoMode={false} runtime={runtime} />
        </>,
        { client, identity },
      );

      const indicators = await screen.findAllByText(/Using cached view as of/);
      expect(indicators).toHaveLength(2);

      // Both consumers share one discovery request and one default-view request.
      expect(fetchCachedDashboardRun).toHaveBeenCalledTimes(1);
      const defaultViewCalls = vi
        .mocked(fetchDashboardView)
        .mock.calls.filter(([, request]) => request?.windowDays === 30);
      expect(defaultViewCalls).toHaveLength(1);
    });

    it("retrieves the same DashboardView from the cache for requested and server-resolved ranges", async () => {
      vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) => {
        if (range?.startDate === "2026-06-01") {
          // The server resolves the requested end date one day earlier.
          return {
            ...demoDashboardView,
            range: {
              mode: "custom" as const,
              windowDays: null,
              startDate: "2026-06-01",
              endDate: "2026-06-07",
            },
          };
        }
        return demoViewForRange(range);
      });

      const { client } = renderDashboard(<CostDashboard demoMode />);

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

      const requestedKey = queryKeys.dashboard.view(
        DEMO_USER_ID,
        DEMO_ORG_ID,
        "demo-run",
        { startDate: "2026-06-01", endDate: "2026-06-08" },
      );
      const resolvedKey = queryKeys.dashboard.view(
        DEMO_USER_ID,
        DEMO_ORG_ID,
        "demo-run",
        { startDate: "2026-06-01", endDate: "2026-06-07" },
      );
      const requested = client.getQueryData<DashboardView>(requestedKey);
      const resolved = client.getQueryData<DashboardView>(resolvedKey);
      expect(requested).toBeDefined();
      expect(resolved).toBe(requested);
    });

    it("makes a terminal pollUntilTerminal result view-addressable without a follow-up fetch", async () => {
      const completedRun: DashboardRun = {
        ...demoDashboardView.run,
        id: "run-terminal-cache",
        source: "snowflake",
        status: "completed",
      };
      const completedView: DashboardView = {
        ...demoDashboardView,
        run: completedRun,
      };
      vi.mocked(startDashboardRun).mockResolvedValue({
        ...completedRun,
        status: "running",
      });
      vi.mocked(fetchDashboardView).mockResolvedValue(completedView);
      mockPollResolvesWith(completedView);

      const { client } = renderDashboard(
        <CostDashboard demoMode={false} runtime={runtime} />,
        { client: createPersistentQueryClient(), identity },
      );

      fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

      await screen.findByText("Total Spend in Last 30 Days");
      await waitFor(() => expect(fetchDashboardView).toHaveBeenCalledTimes(3));

      // The terminal view is readable straight from the view key — no follow-up
      // fetch of the default range beyond the single poll fetch.
      const viewKey = queryKeys.dashboard.view(
        USER_ID,
        ORG_ID,
        "run-terminal-cache",
        { windowDays: 30 },
      );
      expect(client.getQueryData<DashboardView>(viewKey)).toBe(completedView);
      const defaultViewCalls = vi
        .mocked(fetchDashboardView)
        .mock.calls.filter(([, request]) => request?.windowDays === 30);
      expect(defaultViewCalls).toHaveLength(1);
    });

    it("stores a terminal pollDashboardSource result only under source(...), never under view(...)", async () => {
      const dataView: DashboardView = {
        ...demoDashboardView,
        run: {
          ...demoDashboardView.run,
          id: "run-source",
          source: "snowflake",
          status: "completed",
        },
      };
      vi.mocked(triggerDashboardSource).mockResolvedValue(undefined);
      vi.mocked(pollDashboardSource).mockResolvedValue({
        status: "completed",
        userSafeMessage: null,
        view: aiDetailFixture,
      });

      const { client } = renderDashboard(
        <CostDashboard demoMode={false} data={dataView} runtime={runtime} />,
        { identity },
      );

      await waitFor(() => expect(pollDashboardSource).toHaveBeenCalled());

      const request = {
        windowDays: dataView.range.windowDays ?? 30,
      };
      const sourceKey = queryKeys.dashboard.source(
        USER_ID,
        ORG_ID,
        "run-source",
        "ai_consumption_daily",
        request,
      );
      await waitFor(() =>
        expect(client.getQueryData<AIDetailViewModel>(sourceKey)).toBe(
          aiDetailFixture,
        ),
      );

      // The AI detail view model must never leak into a dashboard view key.
      const entriesHoldingDetail = client
        .getQueryCache()
        .getAll()
        .filter((query) => query.state.data === aiDetailFixture);
      expect(entriesHoldingDetail).toHaveLength(1);
      expect(entriesHoldingDetail[0].queryKey).toEqual(sourceKey);
    });

    it("drops a poll that resolves after an identity switch so it cannot repopulate old keys", async () => {
      vi.mocked(startDashboardRun).mockResolvedValue({
        ...cachedRun,
        id: "run-stale-identity",
        status: "running",
      });
      const completedView: DashboardView = {
        ...demoDashboardView,
        run: {
          ...demoDashboardView.run,
          id: "run-stale-identity",
          source: "snowflake",
          status: "completed",
        },
      };
      const pendingPoll = createDeferred<DashboardView>();
      vi.mocked(pollUntilTerminal<DashboardView>).mockReturnValue(
        pendingPoll.promise,
      );

      const client = createPersistentQueryClient();
      const view = render(
        <QueryTestProvider
          client={client}
          identity={{ activeOrganizationId: ORG_ID, identityEpoch: 0 }}
        >
          <CostDashboard demoMode={false} runtime={runtime} />
        </QueryTestProvider>,
      );

      fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
      await waitFor(() => expect(startDashboardRun).toHaveBeenCalledTimes(1));

      // The identity epoch bumps (sign-out / account switch / re-auth) while the
      // poll is still in flight.
      view.rerender(
        <QueryTestProvider
          client={client}
          identity={{ activeOrganizationId: ORG_ID, identityEpoch: 1 }}
        >
          <CostDashboard demoMode={false} runtime={runtime} />
        </QueryTestProvider>,
      );

      await act(async () => {
        pendingPoll.resolve(completedView);
        await pendingPoll.promise;
      });

      // The stale poll result must not populate view or discovery keys.
      const viewKey = queryKeys.dashboard.view(
        USER_ID,
        ORG_ID,
        "run-stale-identity",
        { windowDays: 30 },
      );
      expect(client.getQueryData(viewKey)).toBeUndefined();
      const discoveryEntry = client.getQueryData<{ run: DashboardRun }>(
        queryKeys.dashboard.cachedRun(USER_ID, ORG_ID),
      );
      expect(discoveryEntry?.run.id).not.toBe("run-stale-identity");
      // No prefetches were issued for the dropped run either.
      expect(fetchDashboardView).not.toHaveBeenCalledWith(
        "run-stale-identity",
        { windowDays: 7 },
        expect.anything(),
      );
    });

    it("drops an in-flight range fetch after an identity switch + cache clear so TanStack cannot repopulate the old key", async () => {
      const dataView: DashboardView = {
        ...demoDashboardView,
        run: {
          ...demoDashboardView.run,
          id: "run-range-guard",
          source: "snowflake",
          status: "completed",
        },
      };
      const pending = createDeferred<DashboardView>();
      vi.mocked(fetchDashboardView).mockImplementation(async (_runId, request) => {
        if (request?.windowDays === 7) return pending.promise;
        return dataView;
      });

      const client = createPersistentQueryClient();
      const view = render(
        <QueryTestProvider
          client={client}
          identity={{ activeOrganizationId: ORG_ID, identityEpoch: 0 }}
        >
          <CostDashboard demoMode={false} data={dataView} runtime={runtime} />
        </QueryTestProvider>,
      );

      // Switch to the 7-day window: loadRange runs a fetchQuery whose queryFn is
      // held pending, so it is still in flight during the identity switch.
      fireEvent.click(screen.getByRole("button", { name: "7 days" }));
      await waitFor(() =>
        expect(fetchDashboardView).toHaveBeenCalledWith(
          "run-range-guard",
          { windowDays: 7 },
          expect.anything(),
        ),
      );

      // Identity epoch bumps and the cache is cleared, mirroring OrgShell's
      // transitionUser (cancelQueries + clear).
      view.rerender(
        <QueryTestProvider
          client={client}
          identity={{ activeOrganizationId: ORG_ID, identityEpoch: 1 }}
        >
          <CostDashboard demoMode={false} data={dataView} runtime={runtime} />
        </QueryTestProvider>,
      );
      client.clear();

      await act(async () => {
        pending.resolve(dataView);
        await pending.promise;
      });

      // The queryFn throws on the stale identity, so TanStack must not store the
      // result under the now-cleared 7-day key.
      const key = queryKeys.dashboard.view(USER_ID, ORG_ID, "run-range-guard", {
        windowDays: 7,
      });
      expect(client.getQueryData(key)).toBeUndefined();
    });

    it("does not paint a range fetch rejection that settles after an identity switch", async () => {
      const dataView: DashboardView = {
        ...demoDashboardView,
        run: {
          ...demoDashboardView.run,
          id: "run-range-reject",
          source: "snowflake",
          status: "completed",
        },
      };
      const pending = createDeferred<DashboardView>();
      vi.mocked(fetchDashboardView).mockImplementation(async (_runId, request) => {
        if (request?.windowDays === 7) return pending.promise;
        return dataView;
      });

      const client = createPersistentQueryClient();
      const view = render(
        <QueryTestProvider
          client={client}
          identity={{ activeOrganizationId: ORG_ID, identityEpoch: 0 }}
        >
          <CostDashboard demoMode={false} data={dataView} runtime={runtime} />
        </QueryTestProvider>,
      );

      fireEvent.click(screen.getByRole("button", { name: "7 days" }));
      await waitFor(() =>
        expect(fetchDashboardView).toHaveBeenCalledWith(
          "run-range-reject",
          { windowDays: 7 },
          expect.anything(),
        ),
      );

      view.rerender(
        <QueryTestProvider
          client={client}
          identity={{ activeOrganizationId: ORG_ID, identityEpoch: 1 }}
        >
          <CostDashboard demoMode={false} data={dataView} runtime={runtime} />
        </QueryTestProvider>,
      );

      await act(async () => {
        pending.reject(new Error("range failure"));
        await pending.promise.catch(() => undefined);
      });

      // The identity-guarded catch must not surface the range error for a
      // request that belonged to the previous identity.
      expect(
        screen.queryByText("Could not load selected date range."),
      ).not.toBeInTheDocument();
    });

    it("caches demo views under the demo sentinel scope even inside authenticated chrome", async () => {
      vi.mocked(fetchDemoDashboardView).mockImplementation(async (range) =>
        demoViewForRange(range),
      );

      const client = createPersistentQueryClient();
      render(
        <QueryTestProvider
          client={client}
          identity={{ userId: "auth-user", activeOrganizationId: "auth-org" }}
        >
          <CostDashboard demoMode />
        </QueryTestProvider>,
      );

      await screen.findByText("Total Spend in Last 30 Days");

      // The demo view lands under the fixed demo sentinels…
      const demoKey = queryKeys.dashboard.view(
        DEMO_USER_ID,
        DEMO_ORG_ID,
        "demo-run",
        { windowDays: 30 },
      );
      expect(client.getQueryData<DashboardView>(demoKey)).toBeDefined();
      // …and never under the surrounding authenticated chrome identity, so demo
      // reads (which use the demo sentinels) can find every write.
      const authKey = queryKeys.dashboard.view(
        "auth-user",
        "auth-org",
        "demo-run",
        { windowDays: 30 },
      );
      expect(client.getQueryData(authKey)).toBeUndefined();
    });

    it("clears cachedAsOf on a fresh run and updates discovery on completion without another /cached request", async () => {
      vi.mocked(fetchCachedDashboardRun).mockResolvedValue({
        run: cachedRun,
        cachedAsOf: "2026-07-06T14:30:00Z",
      });
      vi.mocked(fetchDashboardView).mockResolvedValue(cachedView);

      const freshCompletedRun: DashboardRun = {
        ...demoDashboardView.run,
        id: "fresh-run-2",
        source: "snowflake",
        status: "completed",
      };
      const freshView: DashboardView = {
        ...demoDashboardView,
        run: freshCompletedRun,
      };
      vi.mocked(startDashboardRun).mockResolvedValue({
        ...freshCompletedRun,
        status: "running",
      });
      mockPollResolvesWith(freshView);

      const { client } = renderDashboard(
        <CostDashboard demoMode={false} runtime={runtime} />,
        { client: createPersistentQueryClient(), identity },
      );

      await screen.findByText(/Using cached view as of/);
      vi.mocked(fetchDashboardView).mockResolvedValue(freshView);

      fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

      // The cached indicator clears the moment the fresh run starts.
      await waitFor(() =>
        expect(
          screen.queryByText(/Using cached view as of/),
        ).not.toBeInTheDocument(),
      );

      // Terminal completion populates discovery directly from the run…
      await waitFor(() => {
        const discovery = client.getQueryData<{
          run: DashboardRun;
          cachedAsOf: string;
        }>(queryKeys.dashboard.cachedRun(USER_ID, ORG_ID));
        expect(discovery?.run.id).toBe("fresh-run-2");
        expect(discovery?.cachedAsOf).toBe(freshCompletedRun.completed_at);
      });
      // …without a second /dashboard-runs/cached round-trip.
      expect(fetchCachedDashboardRun).toHaveBeenCalledTimes(1);
    });
  });
});
