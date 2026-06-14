"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { usePrefersReducedMotion } from "../../lib/use-prefers-reduced-motion";
import {
  fetchDashboardView,
  fetchDemoDashboardView,
  pollDashboardRun,
  startDashboardRun,
  type DashboardViewRangeRequest,
} from "../../lib/dashboard-api";
import {
  FETCH_WINDOW_DAYS,
  type DashboardRunStatus,
  type DashboardView,
  type DashboardViewRange,
} from "../../lib/dashboard-contracts";
import DashboardHeader, {
  type DashboardModeLabel,
} from "./dashboard-header";
import FilterBar, {
  WINDOW_DAYS,
  canApplyDateRange,
  type WindowDays,
} from "./filter-bar";
import RunStatus from "./run-status";
import SectionEmptyState from "./section-empty-state";
import {
  OverviewSection,
  StorageSpendSection,
  WarehouseSpendSection,
} from "./spend-sections";
import { useSectionStatuses } from "./use-section-statuses";

export type CostDashboardRuntime = {
  accessToken: string | null;
  organizationId: string;
  organizationName: string;
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
  modeLabel,
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
      modeLabel={modeLabel}
      runtime={runtime}
      shouldUseDemo={shouldUseDemo}
    />
  );
}

function CostDashboardContent({
  data,
  modeLabel,
  runtime,
  shouldUseDemo,
}: CostDashboardProps & { shouldUseDemo: boolean }) {
  const cacheRef = useRef<Map<string, DashboardView>>(
    data
      ? new Map([[rangeKey(data.run.id, requestFromViewRange(data.range)), data]])
      : new Map(),
  );
  const rangeRequestSeqRef = useRef(0);
  const runGenerationRef = useRef(0);
  const [activeRange, setActiveRange] = useState<DashboardViewRange | null>(
    data?.range ?? null,
  );
  const [startDate, setStartDate] = useState(data?.range.startDate ?? "");
  const [endDate, setEndDate] = useState(data?.range.endDate ?? "");
  const [runInFlight, setRunInFlight] = useState(false);
  const [rangeFetchesInFlight, setRangeFetchesInFlight] = useState(0);
  const [revealGeneration, setRevealGeneration] = useState(0);
  const [loadState, setLoadState] = useState<LoadState>({
    status: data?.run.status ?? (shouldUseDemo ? "loading" : "queued"),
    view: data,
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
    setRunInFlight(true);
    setLoadState((current) => ({ ...current, status: "loading" }));

    try {
      const run = await startDashboardRun(
        {
          organizationId: runtime.organizationId,
          windowDays: FETCH_WINDOW_DAYS,
        },
        options,
      );
      setLoadState((current) => ({
        ...current,
        status: run.status,
        message: run.error ?? run.user_safe_message,
      }));

      const completedRun = await pollDashboardRun(run.id, options);
      if (completedRun.status !== "completed") {
        setLoadState((current) => ({
          ...current,
          status: completedRun.status,
          message: completedRun.error ?? completedRun.user_safe_message,
        }));
        return;
      }

      const dashboardView = await fetchDashboardView(
        completedRun.id,
        DEFAULT_VIEW_RANGE,
        options,
      );
      runGenerationRef.current += 1;
      cacheView(completedRun.id, DEFAULT_VIEW_RANGE, dashboardView);
      applyDashboardView(dashboardView);
      prefetchRelativeWindows(completedRun.id, (range) =>
        fetchDashboardView(completedRun.id, range, options),
      );
    } catch {
      setLoadState({
        status: "failed",
        message: "Could not load dashboard data.",
      });
    } finally {
      setRunInFlight(false);
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
  const dataReady = viewModel != null && loadState.status !== "loading";
  const isFailedWithoutView =
    !viewModel &&
    (loadState.status === "failed" ||
      loadState.status === "expired" ||
      loadState.status === "deleted");
  const sectionStatuses = useSectionStatuses({
    dataReady,
    instant: reduceMotion,
    revealGeneration,
  });
  const runDisabled =
    runInFlight ||
    rangeFetchesInFlight > 0 ||
    (!viewModel && loadState.status === "loading") ||
    loadState.status === "running" ||
    (!shouldUseDemo && !runtime);
  const resolvedModeLabel =
    modeLabel ?? (shouldUseDemo ? "Demo" : "Local Snowflake");

  return (
    <main className="dark min-h-screen bg-canvas [color-scheme:dark]">
      <DashboardHeader
        header={viewModel?.header ?? null}
        modeLabel={resolvedModeLabel}
        runDisabled={runDisabled}
        onRun={() => {
          void startRun();
        }}
      />
      <RunStatus status={loadState.status} message={loadState.message} />
      <div
        aria-label="Dashboard content"
        className="mx-auto grid w-full max-w-[1200px] gap-6 px-6 py-6"
      >
        {viewModel?.unsupported ? (
          <SectionEmptyState
            message={`${viewModel.unsupported.title}. ${viewModel.unsupported.detail}`}
          />
        ) : isFailedWithoutView ? (
          <SectionEmptyState
            message={loadState.message ?? "Could not load dashboard data."}
          />
        ) : (
          <>
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
