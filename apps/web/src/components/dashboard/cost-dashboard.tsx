"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { usePrefersReducedMotion } from "../../lib/use-prefers-reduced-motion";
import { dashboardFailure } from "../../lib/dashboard-errors";
import {
  fetchCachedDashboardRun,
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
  type DashboardRun,
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
import DashboardFailureMessage from "./dashboard-failure-message";
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
  reportable?: boolean;
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

function runFailure(run: DashboardRun) {
  const message = run.user_safe_message ?? run.error;
  return {
    message,
    reportable: Boolean(run.error && !run.user_safe_message),
  };
}

function sectionFailureMessage(
  view: DashboardView | null,
  section: "overview" | "warehouse" | "storage",
) {
  // When a run completes with one source group unavailable, `run.user_safe_message`
  // is null and the classified safe message lives on the relevant metadata group.
  // Overview draws from organization usage in billed/demo modes, account usage
  // otherwise; warehouse/storage always draw from account usage.
  const group =
    section === "overview" &&
    (view?.metadata?.data_mode === "billed" ||
      view?.metadata?.data_mode === "demo")
      ? view?.metadata?.organization_usage
      : view?.metadata?.account_usage;
  const groupSafeMessage = group?.user_safe_message ?? null;
  const runSafeMessage = view?.run.user_safe_message ?? null;
  const userSafeMessage = groupSafeMessage ?? runSafeMessage;
  const message =
    userSafeMessage ?? group?.detail ?? `Could not load ${section} data.`;
  return (
    <DashboardFailureMessage
      message={message}
      reportable={!userSafeMessage}
    />
  );
}

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

// The `sectionReadiness` value to apply for a Snowflake view: `undefined` when
// every section is ready (drop to the timed all-ready reveal), otherwise the
// server's per-section statuses so unavailable/pending sections are never
// falsely revealed as ready. Shared by the completed-run and range-change
// apply paths so both protect the same way.
function readinessForView(
  dashboardView: DashboardView,
): DashboardViewSectionStatuses | undefined {
  const allSectionsReady = Object.values(dashboardView.sectionStatuses).every(
    (status) => status === "ready",
  );
  return allSectionsReady ? undefined : dashboardView.sectionStatuses;
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
  // Why the dashboard is currently loading, so the loading indicator can show
  // the right copy: "cache" for the mount cache-load/hydration, "fresh" for a
  // user-started Run analysis. Null when idle/loaded. Set alongside runInFlight
  // and cleared when the load settles.
  const [loadingReason, setLoadingReason] = useState<"cache" | "fresh" | null>(
    null,
  );
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
  // ISO8601 timestamp when the on-screen view was served from the cache, or null
  // for fresh/demo runs. Set only by the cached initial-load path; cleared when
  // the user starts a fresh run. Window switches keep it, since they re-fetch the
  // same cached run's `/view` (the backend re-derives windows from cached data).
  const [cachedAsOf, setCachedAsOf] = useState<string | null>(null);

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
    const failure = runFailure(dashboardView.run);
    setLoadState({
      status: dashboardView.run.status,
      message: failure.message,
      reportable: failure.reportable,
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
    setLoadingReason("fresh");
    setSectionReadiness(undefined);
    setLoadState((current) => ({ ...current, status: "loading" }));
    try {
      const dashboardView = await fetchDemoDashboardView(DEFAULT_VIEW_RANGE);
      runGenerationRef.current += 1;
      cacheView(dashboardView.run.id, DEFAULT_VIEW_RANGE, dashboardView);
      applyDashboardView(dashboardView);
      prefetchRelativeWindows(dashboardView.run.id, fetchDemoDashboardView);
    } catch (error) {
      if (data) {
        setLoadState({ status: data.run.status, view: data });
        setActiveRange(data.range);
        setStartDate(data.range.startDate);
        setEndDate(data.range.endDate);
        return;
      }
      setLoadState({ status: "failed", ...dashboardFailure(error) });
    } finally {
      setRunInFlight(false);
      setLoadingReason(null);
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
    setLoadingReason("fresh");
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
      const failure = runFailure(run);
      setLoadState((current) => ({
        ...current,
        status: run.status,
        message: failure.message,
        reportable: failure.reportable,
      }));

      // Stop holding every section in the skeleton via `runInFlight` once the
      // first provisional view lands; per-section readiness takes over from
      // there. The `finally` clears it as a backstop for early returns/errors.
      let firstViewApplied = false;

      const isTerminal = (view: DashboardView) =>
        TERMINAL_RUN_STATUSES.has(view.run.status);

      const finalView = await pollUntilTerminal(
        () => fetchDashboardView(run.id, DEFAULT_VIEW_RANGE, options),
        isTerminal,
        {
          // Window must outlast the backend 120s query-execution timeout plus
          // executor queueing. 120 × 1.5s = 180s leaves a ~60s queueing margin;
          // 60 × 1.5s (90s) would time the client out before the backend did.
          intervalMs: 1_500,
          maxAttempts: 120,
          onResult: (view) => {
            // Ignore partial views from a superseded run before any state write.
            if (runGeneration !== runGenerationRef.current) {
              return;
            }
            // Skip terminal views here — the post-await block applies the final
            // view and clears sectionReadiness, so painting it twice would cause
            // a brief readiness churn with no benefit.
            if (isTerminal(view)) {
              return;
            }
            setSectionReadiness(view.sectionStatuses);
            // Paints ready sections; pending/unavailable stay skeletoned.
            applyDashboardView(view);
            if (!firstViewApplied) {
              firstViewApplied = true;
              setRunInFlight(false);
              setLoadingReason(null);
            }
          },
        },
      );

      if (runGeneration !== runGenerationRef.current) {
        return;
      }
      if (finalView.run.status !== "completed") {
        // Terminal failed/expired/deleted: apply the final view (so the last
        // PROVISIONAL view isn't left on screen) but keep the server's section
        // statuses as the source of truth. Crucially, do NOT clear
        // sectionReadiness — that would drop to the timed-stagger reveal and
        // misleadingly mark every section ready for a run that did not finish.
        setSectionReadiness(finalView.sectionStatuses);
        applyDashboardView(finalView);
        return;
      }

      // Completed: keep the server's section statuses as the source of truth
      // when any section is still unavailable/pending, otherwise drop back to
      // the standard timed reveal. readinessForView() returns undefined only
      // when every section is ready, so unavailable sections are never falsely
      // revealed as ready.
      setSectionReadiness(readinessForView(finalView));
      cacheView(finalView.run.id, DEFAULT_VIEW_RANGE, finalView);
      applyDashboardView(finalView);
      prefetchRelativeWindows(finalView.run.id, (range) =>
        fetchDashboardView(finalView.run.id, range, options),
      );
    } catch (error) {
      if (runGeneration !== runGenerationRef.current) {
        return;
      }
      setLoadState({ status: "failed", ...dashboardFailure(error) });
    } finally {
      if (runGeneration === runGenerationRef.current) {
        setRunInFlight(false);
        setLoadingReason(null);
      }
    }
  }, [applyDashboardView, cacheView, prefetchRelativeWindows, runtime]);

  const startRun = useCallback(async () => {
    // A fresh run replaces any cached view, so clear the cached indicator before
    // the run starts and mark the loading reason as a fresh analysis so the
    // indicator shows "Running fresh analysis…" rather than the cache copy.
    setCachedAsOf(null);
    setLoadingReason("fresh");
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
      setLoadingReason("fresh");
      setSectionReadiness(undefined);
      try {
        const dashboardView = await fetchDemoDashboardView(DEFAULT_VIEW_RANGE);
        if (isActive) {
          cacheView(dashboardView.run.id, DEFAULT_VIEW_RANGE, dashboardView);
          applyDashboardView(dashboardView);
          prefetchRelativeWindows(dashboardView.run.id, fetchDemoDashboardView);
        }
      } catch (error) {
        if (isActive) {
          setLoadState({ status: "failed", ...dashboardFailure(error) });
        }
      } finally {
        if (isActive) {
          setRunInFlight(false);
          setLoadingReason(null);
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

  // On the initial Snowflake mount, look for a cached run before doing anything
  // else. On a hit, render its `/view` (no Snowflake query) and show the cached
  // indicator; on a 204 miss, fall back to the existing behavior (no auto-run —
  // the user starts a run via "Run analysis").
  useEffect(() => {
    if (data || shouldUseDemo || !runtime) {
      return;
    }
    let isActive = true;
    const options = { accessToken: runtime.accessToken };

    async function loadCachedRun() {
      // Capture the run-generation token before the cache lookup goes in flight.
      // `startRun` bumps this token when a user-initiated fresh run begins, so if
      // the user clicks "Run analysis" while `fetchCachedDashboardRun` (or the
      // subsequent `/view` fetch) is pending, the captured value no longer
      // matches and this cached load aborts, touching nothing — the fresh run's
      // state (and cleared cached indicator) wins. This complements the
      // `isActive` unmount guard.
      const runGeneration = runGenerationRef.current;
      let cached: Awaited<ReturnType<typeof fetchCachedDashboardRun>> = null;
      try {
        cached = await fetchCachedDashboardRun(runtime!.organizationId, options);
      } catch {
        // A cache-lookup failure must not break the dashboard: leave the idle
        // state untouched so the user can still start a fresh run.
        return;
      }
      // On a 204 miss (or after unmount) do nothing — preserve the existing
      // Snowflake idle behavior where the user starts a run via "Run analysis".
      // Crucially, never touch runInFlight here, or a concurrent user-initiated
      // run's in-flight state would be cleared. If a fresh run started while the
      // lookup was pending, abort before any state write.
      if (!isActive || !cached || runGeneration !== runGenerationRef.current) {
        return;
      }
      const cachedRunId = cached.run.id;

      setRunInFlight(true);
      setLoadingReason("cache");
      setSectionReadiness(undefined);
      setLoadState((current) => ({ ...current, status: "loading" }));
      try {
        const dashboardView = await fetchDashboardView(
          cachedRunId,
          DEFAULT_VIEW_RANGE,
          options,
        );
        if (!isActive || runGeneration !== runGenerationRef.current) {
          return;
        }
        cacheView(dashboardView.run.id, DEFAULT_VIEW_RANGE, dashboardView);
        setSectionReadiness(readinessForView(dashboardView));
        applyDashboardView(dashboardView);
        setCachedAsOf(cached.cachedAsOf);
        prefetchRelativeWindows(dashboardView.run.id, (range) =>
          fetchDashboardView(cachedRunId, range, options),
        );
      } catch (error) {
        if (isActive && runGeneration === runGenerationRef.current) {
          setLoadState({ status: "failed", ...dashboardFailure(error) });
        }
      } finally {
        if (isActive && runGeneration === runGenerationRef.current) {
          setRunInFlight(false);
          setLoadingReason(null);
        }
      }
    }

    void loadCachedRun();

    return () => {
      isActive = false;
    };
  }, [
    applyDashboardView,
    cacheView,
    data,
    prefetchRelativeWindows,
    runtime,
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
        // Reuse the cached view's per-section statuses so a completed Snowflake
        // run with unavailable sections stays protected when switching ranges,
        // rather than falling back to the timed all-ready reveal. Demo views
        // always use the standard reveal.
        setSectionReadiness(shouldUseDemo ? undefined : readinessForView(cachedView));
        applyDashboardView(cachedView);
        return;
      }

      setRangeFetchesInFlight((count) => count + 1);
      setLoadState((current) => ({
        ...current,
        status: "loading",
        message: null,
        reportable: false,
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
          // Preserve non-all-ready section statuses for Snowflake range views so
          // unavailable sections are not revealed as ready (see readinessForView).
          setSectionReadiness(
            shouldUseDemo ? undefined : readinessForView(dashboardView),
          );
          applyDashboardView(dashboardView);
        }
      } catch (error) {
        if (
          runGeneration === runGenerationRef.current &&
          requestSeq === rangeRequestSeqRef.current
        ) {
          const failure = dashboardFailure(error);
          setLoadState((current) => ({
            ...current,
            status: "failed",
            message: failure.reportable
              ? "Could not load selected date range."
              : failure.message,
            reportable: failure.reportable,
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
  // Pre-run Snowflake idle: real (non-demo) context, no run started yet in this
  // session (revealGeneration still 0), no view present, nothing in flight, and
  // the load state is still the initial pre-run "queued". In this state we show
  // a static empty CTA instead of the animated skeletons so an idle dashboard is
  // not mistaken for a loading one (issue #40).
  const isIdle =
    !shouldUseDemo &&
    viewModel == null &&
    !runInFlight &&
    revealGeneration === 0 &&
    loadState.status === "queued";

  // Derive a stable source spec from primitive field values so that
  // poll-driven reference churn (viewModel / activeRange get new object
  // identities every 1.5 s) does not re-trigger the AI fetch.
  const aiSource = useMemo(
    () =>
      dataReady && viewModel
        ? {
            runId: viewModel.run.id,
            request: requestFromViewRange(activeRange ?? viewModel.range),
          }
        : null,
    // Intentionally using primitive fields rather than the objects themselves —
    // object identity changes on every poll even when the values are identical.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      dataReady,
      viewModel?.run.id,
      activeRange?.mode,
      activeRange?.windowDays,
      activeRange?.startDate,
      activeRange?.endDate,
      viewModel?.range.mode,
      viewModel?.range.windowDays,
      viewModel?.range.startDate,
      viewModel?.range.endDate,
    ],
  );

  useEffect(() => {
    if (!aiSource) return;
    const { runId, request } = aiSource;
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
            setAiDetail({
              status: "error",
              message: "Could not load dashboard data.",
              reportable: true,
            });
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
          setAiDetail({
            status: "error",
            message:
              result.userSafeMessage ?? "Could not load dashboard data.",
            reportable: result.userSafeMessage === null,
          });
        }
      } catch (error) {
        if (seq === aiSeqRef.current) {
          setAiDetail({ status: "error", ...dashboardFailure(error) });
        }
      }
    })();
  }, [aiSource, shouldUseDemo, accessToken]);

  const isFailedWithoutView =
    !viewModel &&
    (loadState.status === "failed" ||
      loadState.status === "expired" ||
      loadState.status === "deleted");
  const sectionStatuses = useSectionStatuses({
    dataReady,
    idle: isIdle,
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
  // Copy for the in-flight loading indicator, keyed off why we are loading:
  // a mount cache-load/hydration vs a user-started fresh analysis. Null when the
  // reason is unset (no indicator).
  const loadingMessage =
    loadingReason === "cache"
      ? "Loading cached view…"
      : loadingReason === "fresh"
        ? "Running fresh analysis…"
        : null;

  return (
    <main className="dark min-h-screen bg-canvas [color-scheme:dark]">
      <DashboardHeader
        header={viewModel?.header ?? null}
        accountLocator={runtime?.accountLocator ?? null}
        runDisabled={runDisabled}
        running={runInFlight || loadState.status === "running"}
        cachedAsOf={cachedAsOf}
        onRun={() => {
          void startRun();
        }}
      />
      <h1 className="sr-only">Snowflake cost dashboard</h1>
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
            message={
              <DashboardFailureMessage
                message={loadState.message ?? "Could not load dashboard data."}
                reportable={loadState.reportable ?? true}
              />
            }
          />
        ) : (
          <>
            {transientLoadError ? (
              <p
                className="rounded-md border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm font-medium text-rose-300"
                role="alert"
              >
                <DashboardFailureMessage
                  message={transientLoadError}
                  reportable={loadState.reportable ?? true}
                />
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
                : sectionStatuses.overview === "error"
                  ? {
                      status: "error",
                      message: sectionFailureMessage(viewModel, "overview"),
                    }
                  : {
                      status:
                        sectionStatuses.overview === "idle"
                          ? "idle"
                          : "loading",
                      loadingMessage: loadingMessage ?? undefined,
                    })}
            />
            <WarehouseSpendSection
              {...(sectionStatuses.warehouse === "ready" && dataReady && viewModel
                ? {
                    status: "ready",
                    currency: viewModel.header.currency,
                    range: activeRange ?? viewModel.range,
                    viewModel: viewModel.warehouseSpend,
                  }
                : sectionStatuses.warehouse === "error"
                  ? {
                      status: "error",
                      message: sectionFailureMessage(viewModel, "warehouse"),
                    }
                  : {
                      status:
                        sectionStatuses.warehouse === "idle"
                          ? "idle"
                          : "loading",
                      loadingMessage: loadingMessage ?? undefined,
                    })}
            />
            <AiSpendSection
              {...(sectionStatuses.overview === "idle"
                ? { status: "idle" as const }
                : {
                    currency: viewModel?.header.currency ?? "USD",
                    loadingMessage: loadingMessage ?? undefined,
                    range: activeRange ?? viewModel?.range ?? null,
                    summary: viewModel?.aiSpendSummary ?? {
                      total: 0,
                      totalLabel: "$0.00",
                      isEmpty: true,
                    },
                    detail: dataReady ? aiDetail : { status: "loading" },
                  })}
            />
            <StorageSpendSection
              {...(sectionStatuses.storage === "ready" && dataReady && viewModel
                ? {
                    status: "ready",
                    currency: viewModel.header.currency,
                    range: activeRange ?? viewModel.range,
                    viewModel: viewModel.storageSpend,
                  }
                : sectionStatuses.storage === "error"
                  ? {
                      status: "error",
                      message: sectionFailureMessage(viewModel, "storage"),
                    }
                  : {
                      status:
                        sectionStatuses.storage === "idle" ? "idle" : "loading",
                      loadingMessage: loadingMessage ?? undefined,
                    })}
            />
          </>
        )}
      </div>
    </main>
  );
}
