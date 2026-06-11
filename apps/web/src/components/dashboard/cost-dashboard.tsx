"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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
import DetailTables from "./detail-tables";
import FilterBar, { WINDOW_DAYS, type WindowDays } from "./filter-bar";
import RunStatus from "./run-status";
import SectionEmptyState from "./section-empty-state";
import {
  ComputeSpendSection,
  ServiceSpendSection,
  StorageSpendSection,
  TotalSpendSection,
} from "./spend-sections";

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

const DEFAULT_VIEW_RANGE: DashboardViewRangeRequest = { windowDays: 30 };

function rangeKey(runId: string, range: DashboardViewRangeRequest): string {
  if (isCustomRangeRequest(range)) {
    return `${runId}:custom:${range.startDate}:${range.endDate}`;
  }
  return `${runId}:relative:${range.windowDays ?? 30}`;
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
  return { windowDays: range.windowDays ?? 30 };
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

function isValidDateRange(startDate: string, endDate: string): boolean {
  return startDate.length > 0 && endDate.length > 0 && startDate <= endDate;
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

      rangeRequestSeqRef.current += 1;
      const requestSeq = rangeRequestSeqRef.current;
      const runGeneration = runGenerationRef.current;
      const cachedView = cacheRef.current.get(rangeKey(currentView.run.id, request));
      if (cachedView) {
        applyDashboardView(cachedView);
        return;
      }

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
    if (!isValidDateRange(startDate, endDate)) {
      return;
    }
    void loadRange({ startDate, endDate });
  }, [endDate, loadRange, startDate]);

  const viewModel = loadState.view ?? data ?? null;
  const runDisabled =
    runInFlight ||
    (!viewModel && loadState.status === "loading") ||
    loadState.status === "running" ||
    (!shouldUseDemo && !runtime);
  const resolvedModeLabel =
    modeLabel ?? (shouldUseDemo ? "Demo" : "Local Snowflake");

  return (
    <main className="min-h-screen bg-slate-50">
      <DashboardHeader
        header={viewModel?.header ?? null}
        modeLabel={resolvedModeLabel}
        runDisabled={runDisabled}
        onRun={() => {
          void startRun();
        }}
      />
      <RunStatus status={loadState.status} message={loadState.message} />
      <div className="mx-auto grid w-full max-w-7xl gap-5 px-4 py-4">
        {viewModel ? (
          viewModel.unsupported ? (
            <SectionEmptyState
              message={`${viewModel.unsupported.title}. ${viewModel.unsupported.detail}`}
            />
          ) : (
            <>
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
              <TotalSpendSection viewModel={viewModel.totalSpend} />
              <ComputeSpendSection viewModel={viewModel.computeSpend} />
              <StorageSpendSection viewModel={viewModel.storageSpend} />
              <ServiceSpendSection viewModel={viewModel.serviceSpend} />
              <DetailTables viewModel={viewModel.detailTables} />
            </>
          )
        ) : (
          <section
            aria-label="Loading dashboard"
            className="grid min-h-96 gap-4"
          >
            {[0, 1, 2].map((placeholder) => (
              <div
                key={placeholder}
                className="h-40 animate-pulse rounded-lg border border-slate-200 bg-white"
              />
            ))}
          </section>
        )}
      </div>
    </main>
  );
}
