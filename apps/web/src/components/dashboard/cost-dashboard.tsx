"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { usePrefersReducedMotion } from "../../lib/use-prefers-reduced-motion";
import {
  fetchDashboardView,
  fetchDemoDashboardView,
  fetchDemoDashboardSource,
  pollDashboardSource,
  pollUntilTerminal,
  startDashboardRun,
  triggerDashboardSource,
  type DashboardViewRangeRequest,
} from "../../lib/dashboard-api";
import {
  FETCH_WINDOW_DAYS,
  type DashboardRunStatus,
  type DashboardView,
  type DashboardViewRange,
  type DashboardViewSectionStatuses,
} from "../../lib/dashboard-contracts";
import DashboardHeader, {
  type DashboardModeLabel,
} from "./dashboard-header";
import FilterBar, {
  WINDOW_DAYS,
  canApplyDateRange,
  type WindowDays,
} from "./filter-bar";
import SectionEmptyState from "./section-empty-state";
import {
  AiSpendSection,
  OverviewSection,
  StorageSpendSection,
  WarehouseSpendSection,
  type AiSpendDetailState,
} from "./spend-sections";
import { useSectionStatuses } from "./use-section-statuses";

export type CostDashboardRuntime = {
  accessToken: string | null;
  organizationId: string;
  organizationName: string;
  // Account locator from the org's persisted Snowflake connection, shown in the
  // header before an analysis run has produced a view model.
  accountLocator?: string | null;
};

export type { DashboardModeLabel };

type CostDashboardProps = {
  data?: DashboardView;
  demoMode?: boolean;
  modeLabel?: DashboardModeLabel;
  runtime?: CostDashboardRuntime | null;
};

type LoadState = {
  status: DashboardRunStatus | "loading";
  message?: string | null;
  view?: DashboardView;
};

type ViewFetcher = (
  range: DashboardViewRangeRequest,
) => Promise<DashboardView>;

const DEFAULT_VIEW_RANGE = {
  windowDays: 30,
} as const satisfies DashboardViewRangeRequest;
const DEFAULT_WINDOW_DAYS = DEFAULT_VIEW_RANGE.windowDays;

// Run statuses that end the progressive `/view` poll. Mirrors the lifecycle
// terminal set used by `pollDashboardRun` in dashboard-api.
const TERMINAL_RUN_STATUSES: ReadonlySet<DashboardRunStatus> = new Set([
  "completed",
  "failed",
  "expired",
  "deleted",
]);

function rangeKey(runId: string, range: DashboardViewRangeRequest): string {
  if (isCustomRangeRequest(range)) {
    return `${runId}:custom:${range.startDate}:${range.endDate}`;
  }
  return `${runId}:relative:${range.windowDays ?? DEFAULT_WINDOW_DAYS}`;
}

function isCustomRangeRequest(
  range: DashboardViewRangeRequest,
): range is { windowDays?: never; startDate: string; endDate: string } {
  return range.startDate !== undefined && range.endDate !== undefined;
}

function requestFromViewRange(
  range: DashboardViewRange,
): DashboardViewRangeRequest {
  if (range.mode === "custom") {
    return { startDate: range.startDate, endDate: range.endDate };
  }
  return { windowDays: range.windowDays ?? DEFAULT_WINDOW_DAYS };
}

export default function CostDashboard({
  data,
  demoMode,
  runtime,
}: CostDashboardProps) {
  const shouldUseDemo = demoMode ?? !runtime;
  const contextKey = shouldUseDemo
    ? "demo"
    : `snowflake:${runtime?.organizationId ?? "none"}`;

  return (
    <CostDashboardContent
      key={contextKey}
      data={data}
      demoMode={demoMode}
      runtime={runtime}
      shouldUseDemo={shouldUseDemo}
    />
  );
}

function CostDashboardContent({
  data,
  runtime,
  shouldUseDemo,
}: CostDashboardProps & { shouldUseDemo: boolean }) {
  const cacheRef = useRef<Map<string, DashboardView>>(
    data
      ? new Map([[rangeKey(data.run.id, requestFromViewRange(data.range)), data]])
      : new Map(),
  );
  const rangeRequestSeqRef = useRef(0);
  const aiSeqRef = useRef(0);
  const runGenerationRef = useRef(0);
  const [activeRange, setActiveRange] = useState<DashboardViewRange | null>(
    data?.range ?? null,
  );
  const [startDate, setStartDate] = useState(data?.range.startDate ?? "");
  const [endDate, setEndDate] = useState(data?.range.endDate ?? "");
  const [runInFlight, setRunInFlight] = useState(false);
  const [rangeFetchesInFlight, setRangeFetchesInFlight] = useState(0);
  const [revealGeneration, setRevealGeneration] = useState(0);
  // Per-section readiness from the server during a live Snowflake run. Undefined
  // for demo/cached/range loads, which keep the timed-stagger reveal.
  const [sectionReadiness, setSectionReadiness] = useState<
    DashboardViewSectionStatuses | undefined
  >(undefined);
  const [loadState, setLoadState] = useState<LoadState>({
    status: data?.run.status ?? (shouldUseDemo ? "loading" : "queued"),
    view: data,
  });
  const [aiDetail, setAiDetail] = useState<AiSpendDetailState>({
    status: "loading",
  });

  const cacheView = useCallback(
    (
      runId: string,
      request: DashboardViewRangeRequest,
      dashboardView: DashboardView,
    ) => {
      cacheRef.current.set(rangeKey(runId, request), dashboardView);
      cacheRef.current.set(
        rangeKey(runId, requestFromViewRange(dashboardView.range)),
        dashboardView,
      );
    },
    [],
  );

  const applyDashboardView = useCallback((dashboardView: DashboardView) => {
    setLoadState({
      status: dashboardView.run.status,
      message: dashboardView.run.error ?? dashboardView.run.user_safe_message,
      view: dashboardView,
    });
    setActiveRange(dashboardView.range);
    setStartDate(dashboardView.range.startDate);
    setEndDate(dashboardView.range.endDate);
  }, []);

  const prefetchRelativeWindows = useCallback(
    (runId: string, fetchView: ViewFetcher) => {
      for (const windowDays of WINDOW_DAYS) {
        if (windowDays === DEFAULT_VIEW_RANGE.windowDays) {
          continue;
        }

        const request: DashboardViewRangeRequest = { windowDays };
        if (cacheRef.current.has(rangeKey(runId, request))) {
          continue;
        }

        void fetchView(request)
          .then((dashboardView) => {
            cacheView(runId, request, dashboardView);
          })
          .catch(() => undefined);
      }
    },
    [cacheView],
  );

  const loadDemoRun = useCallback(async () => {
    setRevealGeneration((value) => value + 1);
    runGenerationRef.current += 1;
    setRunInFlight(true);
    setSectionReadiness(undefined);
    setLoadState((current) => ({ ...current, status: "loading" }));
    try {
      const dashboardView = await fetchDemoDashboardView(DEFAULT_VIEW_RANGE);
      runGenerationRef.current += 1;
      cacheView(dashboardView.run.id, DEFAULT_VIEW_RANGE, dashboardView);
      applyDashboardView(dashboardView);
      prefetchRelativeWindows(dashboardView.run.id, fetchDemoDashboardView);
    } catch {
      if (data) {
        setLoadState({ status: data.run.status, view: data });
        setActiveRange(data.range);
        setStartDate(data.range.startDate);
        setEndDate(data.range.endDate);
        return;
      }
      setLoadState({
        status: "failed",
        message: "Could not load dashboard data.",
      });
    } finally {
      setRunInFlight(false);
    }
  }, [applyDashboardView, cacheView, data, prefetchRelativeWindows]);

  const loadSnowflakeRun = useCallback(async () => {
    if (!runtime) {
      setLoadState({
        status: "failed",
        message: "Select an organization before starting a run.",
      });
      return;
    }

    const options = { accessToken: runtime.accessToken };
    setRevealGeneration((value) => value + 1);
    runGenerationRef.current += 1;
    const runGeneration = runGenerationRef.current;
    setRunInFlight(true);
    setSectionReadiness(undefined);
    setLoadState((current) => ({ ...current, status: "loading" }));

    try {
      const run = await startDashboardRun(
        {
          organizationId: runtime.organizationId,
          windowDays: FETCH_WINDOW_DAYS,
        },
        options,
      );
      if (runGeneration !== runGenerationRef.current) {
        return;
      }
      setLoadState((current) => ({
        ...current,
        status: run.status,
        message: run.error ?? run.user_safe_message,
      }));

      // Stop holding every section in the skeleton via `runInFlight` once the
      // first provisional view lands; per-section readiness takes over from
      // there. The `finally` clears it as a backstop for early returns/errors.
      let firstViewApplied = false;

      const finalView = await pollUntilTerminal(
        () => fetchDashboardView(run.id, DEFAULT_VIEW_RANGE, options),
        (view) => TERMINAL_RUN_STATUSES.has(view.run.status),
        {
          intervalMs: 1_500,
          maxAttempts: 60,
          onResult: (view) => {
            // Ignore partial views from a superseded run before any state write.
            if (runGeneration !== runGenerationRef.current) {
              return;
            }
            setSectionReadiness(view.sectionStatuses);
            // Paints ready sections; pending/unavailable stay skeletoned.
            applyDashboardView(view);
            if (!firstViewApplied) {
              firstViewApplied = true;
              setRunInFlight(false);
            }
          },
        },
      );

      if (runGeneration !== runGenerationRef.current) {
        return;
      }
      if (finalView.run.status !== "completed") {
        setLoadState((current) => ({
          ...current,
          status: finalView.run.status,
          message: finalView.run.error ?? finalView.run.user_safe_message,
        }));
        return;
      }

      // Completed → all sections ready; drop back to the standard reveal path.
      setSectionReadiness(undefined);
      cacheView(finalView.run.id, DEFAULT_VIEW_RANGE, finalView);
      applyDashboardView(finalView);
      prefetchRelativeWindows(finalView.run.id, (range) =>
        fetchDashboardView(finalView.run.id, range, options),
      );
    } catch {
      if (runGeneration !== runGenerationRef.current) {
        return;
      }
      setLoadState({
        status: "failed",
        message: "Could not load dashboard data.",
      });
    } finally {
      if (runGeneration === runGenerationRef.current) {
        setRunInFlight(false);
      }
    }
  }, [applyDashboardView, cacheView, prefetchRelativeWindows, runtime]);

  const startRun = useCallback(async () => {
    if (shouldUseDemo) {
      await loadDemoRun();
      return;
    }

    await loadSnowflakeRun();
  }, [loadDemoRun, loadSnowflakeRun, shouldUseDemo]);

  useEffect(() => {
    if (data || !shouldUseDemo) {
      return;
    }
    let isActive = true;

    async function fetchInitialDemoView() {
      setRevealGeneration((value) => value + 1);
      setRunInFlight(true);
      setSectionReadiness(undefined);
      try {
        const dashboardView = await fetchDemoDashboardView(DEFAULT_VIEW_RANGE);
        if (isActive) {
          cacheView(dashboardView.run.id, DEFAULT_VIEW_RANGE, dashboardView);
          applyDashboardView(dashboardView);
          prefetchRelativeWindows(dashboardView.run.id, fetchDemoDashboardView);
        }
      } catch {
        if (isActive) {
          setLoadState({
            status: "failed",
            message: "Could not load dashboard data.",
          });
        }
      } finally {
        if (isActive) {
          setRunInFlight(false);
        }
      }
    }

    void fetchInitialDemoView();

    return () => {
      isActive = false;
    };
  }, [
    applyDashboardView,
    cacheView,
    data,
    prefetchRelativeWindows,
    shouldUseDemo,
  ]);

  const accessToken = runtime?.accessToken;

  const loadRange = useCallback(
    async (request: DashboardViewRangeRequest) => {
      const currentView = loadState.view;
      if (!currentView) {
        return;
      }

      setRevealGeneration((value) => value + 1);
      setSectionReadiness(undefined);
      rangeRequestSeqRef.current += 1;
      const requestSeq = rangeRequestSeqRef.current;
      const runGeneration = runGenerationRef.current;
      const cachedView = cacheRef.current.get(rangeKey(currentView.run.id, request));
      if (cachedView) {
        applyDashboardView(cachedView);
        return;
      }

      setRangeFetchesInFlight((count) => count + 1);
      setLoadState((current) => ({
        ...current,
        status: "loading",
        message: null,
      }));

      try {
        const dashboardView = shouldUseDemo
          ? await fetchDemoDashboardView(request)
          : await fetchDashboardView(currentView.run.id, request, {
              accessToken,
            });
        if (runGeneration !== runGenerationRef.current) {
          return;
        }
        cacheView(currentView.run.id, request, dashboardView);
        if (requestSeq === rangeRequestSeqRef.current) {
          applyDashboardView(dashboardView);
        }
      } catch {
        if (
          runGeneration === runGenerationRef.current &&
          requestSeq === rangeRequestSeqRef.current
        ) {
          setLoadState((current) => ({
            ...current,
            status: "failed",
            message: "Could not load selected date range.",
          }));
        }
      } finally {
        setRangeFetchesInFlight((count) => Math.max(0, count - 1));
      }
    },
    [
      applyDashboardView,
      cacheView,
      accessToken,
      loadState.view,
      shouldUseDemo,
    ],
  );

  const handleWindowChange = useCallback(
    (windowDays: WindowDays) => {
      void loadRange({ windowDays });
    },
    [loadRange],
  );

  const handleCustomRangeApply = useCallback(() => {
    if (!canApplyDateRange(startDate, endDate)) {
      return;
    }
    void loadRange({ startDate, endDate });
  }, [endDate, loadRange, startDate]);

  const viewModel = loadState.view ?? data ?? null;
  const reduceMotion = usePrefersReducedMotion();
  const dataReady =
    viewModel != null && loadState.status !== "loading" && !runInFlight;

  useEffect(() => {
    if (!dataReady || !viewModel) return;
    const runId = viewModel.run.id;
    const request = requestFromViewRange(activeRange ?? viewModel.range);
    aiSeqRef.current += 1;
    const seq = aiSeqRef.current;
    // Intentional reset: when the active view/range changes, the AI detail must
    // immediately fall back to its loading skeleton before the async refetch
    // resolves. This is derived-state synchronization, not a cascading loop.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAiDetail({ status: "loading" });

    void (async () => {
      try {
        if (shouldUseDemo) {
          const result = await fetchDemoDashboardSource(
            "ai_consumption_daily",
            request,
          );
          if (seq !== aiSeqRef.current) return;
          if (result.view) {
            setAiDetail({ status: "ready", viewModel: result.view });
          } else {
            setAiDetail({ status: "error" });
          }
          return;
        }
        await triggerDashboardSource(runId, "ai_consumption_daily", {
          accessToken,
        });
        const result = await pollDashboardSource(
          runId,
          "ai_consumption_daily",
          request,
          { accessToken },
        );
        if (seq !== aiSeqRef.current) return;
        if (result.status === "completed" && result.view) {
          setAiDetail({ status: "ready", viewModel: result.view });
        } else {
          setAiDetail({ status: "error" });
        }
      } catch {
        if (seq === aiSeqRef.current) setAiDetail({ status: "error" });
      }
    })();
  }, [dataReady, viewModel, activeRange, shouldUseDemo, accessToken]);

  const isFailedWithoutView =
    !viewModel &&
    (loadState.status === "failed" ||
      loadState.status === "expired" ||
      loadState.status === "deleted");
  const sectionStatuses = useSectionStatuses({
    dataReady,
    instant: reduceMotion,
    revealGeneration,
    sectionReadiness,
  });
  const runDisabled =
    runInFlight ||
    rangeFetchesInFlight > 0 ||
    (!viewModel && loadState.status === "loading") ||
    loadState.status === "running" ||
    (!shouldUseDemo && !runtime);
  // A failure that still has a view to fall back on (e.g. a date-range refetch
  // that errored) used to surface only in the now-removed status band. Surface
  // it as a slim inline alert above the content so the error isn't swallowed.
  const transientLoadError =
    viewModel &&
    (loadState.status === "failed" ||
      loadState.status === "expired" ||
      loadState.status === "deleted")
      ? loadState.message ?? "Could not load dashboard data."
      : null;

  return (
    <main className="dark min-h-screen bg-canvas [color-scheme:dark]">
      <DashboardHeader
        header={viewModel?.header ?? null}
        accountLocator={runtime?.accountLocator ?? null}
        runDisabled={runDisabled}
        running={runInFlight || loadState.status === "running"}
        onRun={() => {
          void startRun();
        }}
      />
      <div
        aria-label="Dashboard content"
        className="mx-auto grid w-full max-w-[1200px] gap-6 px-6 py-6"
      >
        {!runInFlight && viewModel?.unsupported ? (
          <SectionEmptyState
            message={`${viewModel.unsupported.title}. ${viewModel.unsupported.detail}`}
          />
        ) : isFailedWithoutView ? (
          <SectionEmptyState
            message={loadState.message ?? "Could not load dashboard data."}
          />
        ) : (
          <>
            {transientLoadError ? (
              <p
                className="rounded-md border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm font-medium text-rose-300"
                role="alert"
              >
                {transientLoadError}
              </p>
            ) : null}
            {viewModel ? (
              <FilterBar
                range={activeRange ?? viewModel.range}
                currency={viewModel.header.currency}
                startDate={startDate}
                endDate={endDate}
                onWindowChange={handleWindowChange}
                onStartDateChange={setStartDate}
                onEndDateChange={setEndDate}
                onApplyDateRange={handleCustomRangeApply}
              />
            ) : null}
            <OverviewSection
              {...(sectionStatuses.overview === "ready" && dataReady && viewModel
                ? {
                    status: "ready",
                    capacityBalance: viewModel.capacityBalance,
                    currency: viewModel.header.currency,
                    range: activeRange ?? viewModel.range,
                    serviceSpend: viewModel.serviceSpend,
                    totalSpend: viewModel.totalSpend,
                  }
                : { status: "loading" })}
            />
            <WarehouseSpendSection
              {...(sectionStatuses.warehouse === "ready" && dataReady && viewModel
                ? {
                    status: "ready",
                    currency: viewModel.header.currency,
                    range: activeRange ?? viewModel.range,
                    viewModel: viewModel.warehouseSpend,
                  }
                : { status: "loading" })}
            />
            <AiSpendSection
              currency={viewModel?.header.currency ?? "USD"}
              range={activeRange ?? viewModel?.range ?? null}
              summary={
                viewModel?.aiSpendSummary ?? {
                  total: 0,
                  totalLabel: "$0.00",
                  isEmpty: true,
                }
              }
              detail={dataReady ? aiDetail : { status: "loading" }}
            />
            <StorageSpendSection
              {...(sectionStatuses.storage === "ready" && dataReady && viewModel
                ? {
                    status: "ready",
                    currency: viewModel.header.currency,
                    range: activeRange ?? viewModel.range,
                    viewModel: viewModel.storageSpend,
                  }
                : { status: "loading" })}
            />
          </>
        )}
      </div>
    </main>
  );
}
