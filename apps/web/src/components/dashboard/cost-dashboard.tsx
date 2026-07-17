"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

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
  type AIDetailViewModel,
  type DashboardRun,
  type DashboardRunStatus,
  type DashboardView,
  type DashboardViewRange,
  type DashboardViewSectionStatuses,
} from "../../lib/dashboard-contracts";
import { queryKeys } from "../../lib/query-keys";
import {
  DEMO_ORG_ID,
  DEMO_USER_ID,
  useQueryIdentity,
  type QueryIdentitySnapshot,
} from "../../lib/query-identity";
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

const DEFAULT_VIEW_RANGE = {
  windowDays: 30,
} as const satisfies DashboardViewRangeRequest;
const DEFAULT_WINDOW_DAYS = DEFAULT_VIEW_RANGE.windowDays;

// Fixed run id emitted by the demo dataset generator
// (`apps/api/app/services/demo_data.py`). Used to scope demo view/source cache
// keys before the demo request resolves, and asserted as a cross-layer invariant
// once the demo view lands.
const DEMO_RUN_ID = "demo-run";

const AI_SOURCE_ID = "ai_consumption_daily";

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

// Thrown by `throwIfIdentityChanged` when the query identity changed mid-fetch.
// This is a benign cancellation, NOT a load failure: an identity transition
// (sign-out / account / org switch) already clears and repaints the surface, so
// any user-visible error handler must treat it as a no-op rather than painting a
// failed dashboard state via `dashboardFailure`. Discovery/cache-guard paths
// already drop it silently; this marker lets the default-view and demo error
// paths distinguish it from a genuine transport/API failure.
class IdentityChangedError extends Error {
  constructor() {
    super("Query identity changed before the result could be stored");
    this.name = "IdentityChangedError";
  }
}

function isIdentityChangedError(error: unknown): boolean {
  return error instanceof IdentityChangedError;
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
  const queryClient = useQueryClient();
  const identity = useQueryIdentity();
  // useQueryIdentity() returns a fresh object each render; keep a ref so async
  // callbacks/effects read the live identity without re-subscribing on identity
  // reference churn.
  const identityRef = useRef(identity);
  identityRef.current = identity;

  // Cache scope. Demo data always lives under the fixed demo sentinels so it can
  // never collide with a real user's org data; authenticated Snowflake data is
  // scoped to the signed-in user and the active organization.
  const userId = shouldUseDemo ? DEMO_USER_ID : identity.snapshot.userId;
  const orgId = shouldUseDemo
    ? DEMO_ORG_ID
    : runtime?.organizationId ?? identity.snapshot.orgId;

  // Read the access token at call time rather than keying queries on it: Supabase
  // rotates it roughly hourly, and a rotation must not invalidate cache entries.
  const accessTokenRef = useRef(runtime?.accessToken ?? null);
  accessTokenRef.current = runtime?.accessToken ?? null;

  const rangeRequestSeqRef = useRef(0);
  const aiSeqRef = useRef(0);
  const runGenerationRef = useRef(0);
  const demoSeededRef = useRef(false);
  const [activeRange, setActiveRange] = useState<DashboardViewRange | null>(
    data?.range ?? null,
  );
  const [startDate, setStartDate] = useState(data?.range.startDate ?? "");
  const [endDate, setEndDate] = useState(data?.range.endDate ?? "");
  const [runInFlight, setRunInFlight] = useState(false);
  // A user-started fresh run (or its dismissal of cache discovery) has taken
  // over, so the initial discovery/default-view queries must never repopulate
  // the on-screen state again.
  const [discoveryDismissed, setDiscoveryDismissed] = useState(false);
  // Why the dashboard is currently loading, so the loading indicator can show
  // the right copy: "cache" for the mount cache-load/hydration, "fresh" for a
  // user-started Run analysis. Null when idle/loaded.
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
  // for fresh/demo runs.
  const [cachedAsOf, setCachedAsOf] = useState<string | null>(null);

  // Throw from a query function when identity has changed since it started, so
  // TanStack (which only stores successful results) never writes an in-flight
  // result under a now-stale scoped key after an identity transition cleared the
  // cache. Callers capture identity when the queryFn begins and call this right
  // before returning the result.
  const throwIfIdentityChanged = useCallback(
    (captured: QueryIdentitySnapshot) => {
      if (!identityRef.current.isCurrent(captured)) {
        throw new IdentityChangedError();
      }
    },
    [],
  );

  // Write a resolved view under both the requested-range key and the
  // server-resolved-range key, so a later read by either range retrieves it. The
  // identity guard drops writes that resolved after a sign-out / account / org
  // switch, so a stale poll can never repopulate old keys. Keys always use the
  // component-scoped userId/orgId (the DEMO sentinels in demo mode, the signed-in
  // scope otherwise) so writes land in the SAME scope the reads use — never the
  // surrounding chrome identity when demo mode renders inside authenticated
  // chrome.
  const cacheView = useCallback(
    (
      captured: QueryIdentitySnapshot,
      runId: string,
      requested: DashboardViewRangeRequest,
      view: DashboardView,
    ) => {
      if (!identityRef.current.isCurrent(captured)) return false;
      queryClient.setQueryData(
        queryKeys.dashboard.view(userId, orgId, runId, requested),
        view,
      );
      queryClient.setQueryData(
        queryKeys.dashboard.view(
          userId,
          orgId,
          runId,
          requestFromViewRange(view.range),
        ),
        view,
      );
      return true;
    },
    [queryClient, userId, orgId],
  );

  const readCachedView = useCallback(
    (runId: string, request: DashboardViewRangeRequest) =>
      queryClient.getQueryData<DashboardView>(
        queryKeys.dashboard.view(userId, orgId, runId, request),
      ),
    [queryClient, userId, orgId],
  );

  const fetchViewForRequest = useCallback(
    (runId: string, request: DashboardViewRangeRequest) =>
      queryClient.fetchQuery({
        queryKey: queryKeys.dashboard.view(userId, orgId, runId, request),
        queryFn: async () => {
          const captured = identityRef.current.capture();
          const view = shouldUseDemo
            ? await fetchDemoDashboardView(request)
            : await fetchDashboardView(runId, request, {
                accessToken: accessTokenRef.current,
              });
          throwIfIdentityChanged(captured);
          return view;
        },
      }),
    [queryClient, userId, orgId, shouldUseDemo, throwIfIdentityChanged],
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

  // Prefetch every non-default relative window into the query cache. Already
  // present keys are skipped so a re-run cannot re-issue window requests that are
  // already cached (the query client would otherwise refetch them as stale).
  const prefetchRelativeWindows = useCallback(
    (runId: string) => {
      for (const windowDays of WINDOW_DAYS) {
        if (windowDays === DEFAULT_WINDOW_DAYS) {
          continue;
        }
        const request: DashboardViewRangeRequest = { windowDays };
        const key = queryKeys.dashboard.view(userId, orgId, runId, request);
        if (queryClient.getQueryData(key) !== undefined) {
          continue;
        }
        void queryClient.prefetchQuery({
          queryKey: key,
          queryFn: async () => {
            const captured = identityRef.current.capture();
            const view = shouldUseDemo
              ? await fetchDemoDashboardView(request)
              : await fetchDashboardView(runId, request, {
                  accessToken: accessTokenRef.current,
                });
            throwIfIdentityChanged(captured);
            return view;
          },
        });
      }
    },
    [queryClient, userId, orgId, shouldUseDemo, throwIfIdentityChanged],
  );

  const loadDemoRun = useCallback(async () => {
    const captured = identityRef.current.capture();
    setRevealGeneration((value) => value + 1);
    runGenerationRef.current += 1;
    const runGeneration = runGenerationRef.current;
    setRunInFlight(true);
    setLoadingReason("fresh");
    setSectionReadiness(undefined);
    setLoadState((current) => ({ ...current, status: "loading" }));
    try {
      const dashboardView = await queryClient.fetchQuery({
        queryKey: queryKeys.dashboard.view(
          userId,
          orgId,
          DEMO_RUN_ID,
          DEFAULT_VIEW_RANGE,
        ),
        queryFn: async () => {
          const queryCaptured = identityRef.current.capture();
          const view = await fetchDemoDashboardView(DEFAULT_VIEW_RANGE);
          if (view.run.id !== DEMO_RUN_ID) {
            throw new Error("Demo dashboard run id does not match DEMO_RUN_ID");
          }
          throwIfIdentityChanged(queryCaptured);
          return view;
        },
      });
      if (
        runGeneration !== runGenerationRef.current ||
        !identityRef.current.isCurrent(captured)
      ) {
        return;
      }
      cacheView(captured, DEMO_RUN_ID, DEFAULT_VIEW_RANGE, dashboardView);
      applyDashboardView(dashboardView);
      prefetchRelativeWindows(DEMO_RUN_ID);
    } catch (error) {
      if (
        runGeneration !== runGenerationRef.current ||
        !identityRef.current.isCurrent(captured)
      ) {
        return;
      }
      if (data) {
        setLoadState({ status: data.run.status, view: data });
        setActiveRange(data.range);
        setStartDate(data.range.startDate);
        setEndDate(data.range.endDate);
        return;
      }
      setLoadState({ status: "failed", ...dashboardFailure(error) });
    } finally {
      if (runGeneration === runGenerationRef.current) {
        setRunInFlight(false);
        setLoadingReason(null);
      }
    }
  }, [
    applyDashboardView,
    cacheView,
    data,
    orgId,
    prefetchRelativeWindows,
    queryClient,
    throwIfIdentityChanged,
    userId,
  ]);

  const loadSnowflakeRun = useCallback(async () => {
    if (!runtime) {
      setLoadState({
        status: "failed",
        message: "Select an organization before starting a run.",
      });
      return;
    }

    const captured = identityRef.current.capture();
    const options = { accessToken: accessTokenRef.current };
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
      if (
        runGeneration !== runGenerationRef.current ||
        !identityRef.current.isCurrent(captured)
      ) {
        return;
      }
      // A fresh run supersedes the previously discovered cached run: discard its
      // discovery entry so nothing points at a stale run id.
      void queryClient.invalidateQueries({
        queryKey: queryKeys.dashboard.cachedRun(
          captured.userId,
          captured.orgId,
        ),
      });
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
            // Ignore partial views from a superseded run or a stale identity
            // (sign-out / account / org switch) before any state write.
            if (
              runGeneration !== runGenerationRef.current ||
              !identityRef.current.isCurrent(captured)
            ) {
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

      if (
        runGeneration !== runGenerationRef.current ||
        !identityRef.current.isCurrent(captured)
      ) {
        return;
      }
      if (finalView.run.status !== "completed") {
        // Terminal failed/expired/deleted: apply the final view (so the last
        // PROVISIONAL view isn't left on screen) but keep the server's section
        // statuses as the source of truth. Crucially, do NOT clear
        // sectionReadiness — that would drop to the timed-stagger reveal and
        // misleadingly mark every section ready for a run that did not finish.
        // These terminal states never populate discovery.
        setSectionReadiness(finalView.sectionStatuses);
        applyDashboardView(finalView);
        return;
      }

      if (!finalView.run.completed_at) {
        throw new Error("Completed dashboard run is missing completed_at");
      }

      // Completed: keep the server's section statuses as the source of truth
      // when any section is still unavailable/pending, otherwise drop back to
      // the standard timed reveal.
      setSectionReadiness(readinessForView(finalView));
      if (cacheView(captured, finalView.run.id, DEFAULT_VIEW_RANGE, finalView)) {
        // The completed run is still live and view-addressable, so it is the
        // correct session discovery target — no snapshot round-trip needed for
        // navigation within this QueryClient session.
        queryClient.setQueryData(
          queryKeys.dashboard.cachedRun(captured.userId, captured.orgId),
          { run: finalView.run, cachedAsOf: finalView.run.completed_at },
        );
      }
      applyDashboardView(finalView);
      prefetchRelativeWindows(finalView.run.id);
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
  }, [
    applyDashboardView,
    cacheView,
    prefetchRelativeWindows,
    queryClient,
    runtime,
  ]);

  const startRun = useCallback(async () => {
    // A fresh run replaces any cached view, so clear the cached indicator and
    // dismiss the initial discovery queries before the run starts.
    setCachedAsOf(null);
    setDiscoveryDismissed(true);
    setLoadingReason("fresh");
    if (shouldUseDemo) {
      await loadDemoRun();
      return;
    }

    await loadSnowflakeRun();
  }, [loadDemoRun, loadSnowflakeRun, shouldUseDemo]);

  // Seed the query cache with a caller-supplied `data` view once, under its
  // actual run id, so subsequent reads/remounts hit the cache.
  useEffect(() => {
    if (!data || demoSeededRef.current) {
      return;
    }
    demoSeededRef.current = true;
    queryClient.setQueryData(
      queryKeys.dashboard.view(
        userId,
        orgId,
        data.run.id,
        requestFromViewRange(data.range),
      ),
      data,
    );
  }, [data, orgId, queryClient, userId]);

  // Initial demo view: keyed under the demo sentinels + fixed DEMO_RUN_ID so a
  // remount within staleTime reuses it without another request. The queryFn
  // asserts the backend-emitted run id matches DEMO_RUN_ID as a cross-layer
  // invariant.
  const demoInitial = useQuery({
    queryKey: queryKeys.dashboard.view(
      DEMO_USER_ID,
      DEMO_ORG_ID,
      DEMO_RUN_ID,
      DEFAULT_VIEW_RANGE,
    ),
    queryFn: async () => {
      const captured = identityRef.current.capture();
      const view = await fetchDemoDashboardView(DEFAULT_VIEW_RANGE);
      if (view.run.id !== DEMO_RUN_ID) {
        throw new Error("Demo dashboard run id does not match DEMO_RUN_ID");
      }
      throwIfIdentityChanged(captured);
      return view;
    },
    enabled: shouldUseDemo && !data && !discoveryDismissed,
  });

  // Initial Snowflake discovery: look for a cached run for the active org. A hit
  // drives the dependent default-view read below; a 204 miss or transport error
  // leaves the idle CTA in place (non-blocking).
  const discovery = useQuery({
    queryKey: queryKeys.dashboard.cachedRun(userId, orgId),
    queryFn: async () => {
      const captured = identityRef.current.capture();
      const result = await fetchCachedDashboardRun(orgId, {
        accessToken: accessTokenRef.current,
      });
      throwIfIdentityChanged(captured);
      return result;
    },
    enabled: Boolean(runtime && !shouldUseDemo && !data && !discoveryDismissed),
  });
  const discoveredRunId = discovery.data?.run.id ?? null;

  const defaultView = useQuery({
    queryKey: queryKeys.dashboard.view(
      userId,
      orgId,
      discoveredRunId ?? "__no-run__",
      DEFAULT_VIEW_RANGE,
    ),
    queryFn: async () => {
      const captured = identityRef.current.capture();
      const view = await fetchDashboardView(discoveredRunId!, DEFAULT_VIEW_RANGE, {
        accessToken: accessTokenRef.current,
      });
      throwIfIdentityChanged(captured);
      return view;
    },
    enabled: Boolean(
      runtime &&
        !shouldUseDemo &&
        !data &&
        !discoveryDismissed &&
        discoveredRunId,
    ),
  });

  // Apply the initial demo view once it resolves. Prefetch + dual-key aliasing
  // happen here; the network call itself is owned by the query above.
  const demoData = demoInitial.data;
  const demoIsError = demoInitial.isError;
  const demoError = demoInitial.error;
  useEffect(() => {
    if (!shouldUseDemo || data || discoveryDismissed) {
      return;
    }
    // Guard every state write against a mid-flight identity transition. During a
    // transition the captured snapshot is uncapturable (transitioning flag), so
    // isCurrent() is false and we skip the writes — the transition already
    // clears/repaints the surface. Genuine demo rendering (no transition) keeps
    // its captured snapshot current, so writes proceed as before. cacheView keys
    // still use the component-scoped demo sentinels regardless of chrome.
    const captured = identityRef.current.capture();
    if (demoData) {
      if (!identityRef.current.isCurrent(captured)) {
        return;
      }
      cacheView(captured, DEMO_RUN_ID, DEFAULT_VIEW_RANGE, demoData);
      applyDashboardView(demoData);
      prefetchRelativeWindows(DEMO_RUN_ID);
      setRunInFlight(false);
      setLoadingReason(null);
      return;
    }
    if (demoIsError) {
      // An identity-change cancellation is benign: the transition owns the
      // repaint, so never paint a failed dashboard state for it.
      if (isIdentityChangedError(demoError)) {
        return;
      }
      if (!identityRef.current.isCurrent(captured)) {
        return;
      }
      setLoadState({ status: "failed", ...dashboardFailure(demoError) });
      setRunInFlight(false);
      setLoadingReason(null);
      return;
    }
    // Still fetching the initial demo view: show the fresh-analysis skeletons.
    setRunInFlight(true);
    setLoadingReason("fresh");
    setSectionReadiness(undefined);
  }, [
    applyDashboardView,
    cacheView,
    data,
    demoData,
    demoError,
    demoIsError,
    discoveryDismissed,
    prefetchRelativeWindows,
    shouldUseDemo,
  ]);

  // Bump the reveal generation once when the initial demo load begins so the
  // stagger reveal runs for it.
  const demoRevealStartedRef = useRef(false);
  useEffect(() => {
    if (shouldUseDemo && !data && !demoRevealStartedRef.current) {
      demoRevealStartedRef.current = true;
      setRevealGeneration((value) => value + 1);
    }
  }, [shouldUseDemo, data]);

  // Apply Snowflake discovery + default-view results. On a hit, paint the cached
  // view and show the cached indicator; on a 204 miss or transport error, keep
  // the idle CTA (no auto-run).
  const discoveryData = discovery.data;
  const discoveryIsFetching = discovery.isFetching;
  const discoveryIsError = discovery.isError;
  const defaultViewData = defaultView.data;
  const defaultViewIsError = defaultView.isError;
  const defaultViewError = defaultView.error;
  useEffect(() => {
    if (shouldUseDemo || data || !runtime || discoveryDismissed) {
      return;
    }
    if (discoveryIsFetching) {
      // Still discovering: leave the idle state untouched (no loading UI) so a
      // concurrent Run analysis stays available and a miss stays idle.
      return;
    }
    if (discoveryIsError || discoveryData == null) {
      // 204 miss or transport error: keep the idle CTA, never auto-run.
      setRunInFlight(false);
      setLoadingReason(null);
      return;
    }
    // Cache hit.
    if (defaultViewData) {
      const captured = identityRef.current.capture();
      const runId = discoveryData.run.id;
      cacheView(captured, runId, DEFAULT_VIEW_RANGE, defaultViewData);
      setSectionReadiness(readinessForView(defaultViewData));
      applyDashboardView(defaultViewData);
      setCachedAsOf(discoveryData.cachedAsOf);
      prefetchRelativeWindows(runId);
      setRunInFlight(false);
      setLoadingReason(null);
      return;
    }
    if (defaultViewIsError) {
      // An identity-change cancellation is benign: the identity transition
      // already clears/repaints the surface, so mapping it through
      // dashboardFailure would paint a spurious user-visible failed dashboard.
      // Treat it as a no-op.
      if (isIdentityChangedError(defaultViewError)) {
        return;
      }
      setLoadState({ status: "failed", ...dashboardFailure(defaultViewError) });
      setRunInFlight(false);
      setLoadingReason(null);
      return;
    }
    // Hit, but the default view is still loading: show the cache-load skeletons.
    setRunInFlight(true);
    setLoadingReason("cache");
    setSectionReadiness(undefined);
  }, [
    applyDashboardView,
    cacheView,
    data,
    defaultViewData,
    defaultViewError,
    defaultViewIsError,
    discoveryData,
    discoveryDismissed,
    discoveryIsError,
    discoveryIsFetching,
    prefetchRelativeWindows,
    runtime,
    shouldUseDemo,
  ]);

  const loadRange = useCallback(
    async (request: DashboardViewRangeRequest) => {
      const currentView = loadState.view;
      if (!currentView) {
        return;
      }
      const runId = currentView.run.id;

      setRevealGeneration((value) => value + 1);
      setSectionReadiness(undefined);
      rangeRequestSeqRef.current += 1;
      const requestSeq = rangeRequestSeqRef.current;
      const runGeneration = runGenerationRef.current;
      const captured = identityRef.current.capture();
      const cached = readCachedView(runId, request);
      if (cached) {
        // Reuse the cached view's per-section statuses so a completed Snowflake
        // run with unavailable sections stays protected when switching ranges,
        // rather than falling back to the timed all-ready reveal. Demo views
        // always use the standard reveal.
        setSectionReadiness(shouldUseDemo ? undefined : readinessForView(cached));
        applyDashboardView(cached);
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
        const dashboardView = await fetchViewForRequest(runId, request);
        if (
          runGeneration !== runGenerationRef.current ||
          !identityRef.current.isCurrent(captured)
        ) {
          return;
        }
        cacheView(captured, runId, request, dashboardView);
        if (requestSeq === rangeRequestSeqRef.current) {
          setSectionReadiness(
            shouldUseDemo ? undefined : readinessForView(dashboardView),
          );
          applyDashboardView(dashboardView);
        }
      } catch (error) {
        if (
          runGeneration === runGenerationRef.current &&
          requestSeq === rangeRequestSeqRef.current &&
          identityRef.current.isCurrent(captured)
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
      fetchViewForRequest,
      loadState.view,
      readCachedView,
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
    const captured = identityRef.current.capture();
    const sourceKey = queryKeys.dashboard.source(
      userId,
      orgId,
      runId,
      AI_SOURCE_ID,
      request,
    );

    // A previously resolved source result is view-addressable from its own key:
    // paint it and skip the trigger/poll entirely.
    const cachedSource = queryClient.getQueryData<AIDetailViewModel>(sourceKey);
    if (cachedSource) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAiDetail({ status: "ready", viewModel: cachedSource });
      return;
    }

    // Intentional reset: when the active view/range changes, the AI detail must
    // immediately fall back to its loading skeleton before the async refetch
    // resolves. This is derived-state synchronization, not a cascading loop.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAiDetail({ status: "loading" });

    void (async () => {
      try {
        if (shouldUseDemo) {
          const result = await fetchDemoDashboardSource(AI_SOURCE_ID, request);
          // Identity may have switched (sign-out / account / org) while the
          // request was in flight; check immediately before ANY cache or React
          // state write so a stale result never repopulates the cache or paints.
          if (seq !== aiSeqRef.current || !identityRef.current.isCurrent(captured))
            return;
          if (result.view) {
            queryClient.setQueryData(sourceKey, result.view);
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
        await triggerDashboardSource(runId, AI_SOURCE_ID, {
          accessToken: accessTokenRef.current,
        });
        const result = await pollDashboardSource(runId, AI_SOURCE_ID, request, {
          accessToken: accessTokenRef.current,
        });
        if (seq !== aiSeqRef.current || !identityRef.current.isCurrent(captured))
          return;
        if (result.status === "completed" && result.view) {
          queryClient.setQueryData(sourceKey, result.view);
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
        if (seq === aiSeqRef.current && identityRef.current.isCurrent(captured)) {
          setAiDetail({ status: "error", ...dashboardFailure(error) });
        }
      }
    })();
  }, [aiSource, orgId, queryClient, shouldUseDemo, userId]);

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
