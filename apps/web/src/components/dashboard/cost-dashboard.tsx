"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchDashboardDatasets,
  fetchDemoDashboardDatasets,
  pollDashboardRun,
  startDashboardRun,
} from "../../lib/dashboard-api";
import {
  FETCH_WINDOW_DAYS,
  type DashboardData,
  type DashboardRunStatus,
} from "../../lib/dashboard-contracts";
import {
  DEFAULT_WINDOW_DAYS,
  buildDashboardViewModel,
  type WindowDays,
} from "../../lib/dashboard-transforms";
import DashboardHeader, {
  type DashboardModeLabel,
} from "./dashboard-header";
import DetailTables from "./detail-tables";
import FilterBar from "./filter-bar";
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
  data?: DashboardData;
  demoMode?: boolean;
  modeLabel?: DashboardModeLabel;
  runtime?: CostDashboardRuntime | null;
};

type LoadState = {
  status: DashboardRunStatus | "loading";
  message?: string | null;
  data?: DashboardData;
};

export default function CostDashboard({
  data,
  demoMode,
  modeLabel,
  runtime,
}: CostDashboardProps) {
  const shouldUseDemo = demoMode ?? !runtime;
  const [windowDays, setWindowDays] =
    useState<WindowDays>(DEFAULT_WINDOW_DAYS);
  const [runInFlight, setRunInFlight] = useState(false);
  const [loadState, setLoadState] = useState<LoadState>({
    status: data?.run.status ?? (shouldUseDemo ? "loading" : "queued"),
    data,
  });

  const applyDashboardData = useCallback((dashboardData: DashboardData) => {
    setLoadState({
      status: dashboardData.run.status,
      message: dashboardData.run.error ?? dashboardData.run.user_safe_message,
      data: dashboardData,
    });
  }, []);

  const loadDemoRun = useCallback(async () => {
    setRunInFlight(true);
    setLoadState((current) => ({ ...current, status: "loading" }));
    try {
      const dashboardData = await fetchDemoDashboardDatasets();
      applyDashboardData(dashboardData);
    } catch {
      if (data) {
        setLoadState({ status: data.run.status, data });
        return;
      }
      setLoadState({
        status: "failed",
        message: "Could not load dashboard data.",
      });
    } finally {
      setRunInFlight(false);
    }
  }, [applyDashboardData, data]);

  const loadSnowflakeRun = useCallback(async () => {
    if (!runtime) {
      setLoadState({
        status: "failed",
        message: "Select an organization before starting a run.",
      });
      return;
    }

    const options = { accessToken: runtime.accessToken };
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

      const dashboardData = await fetchDashboardDatasets(completedRun.id, options);
      applyDashboardData(dashboardData);
    } catch {
      setLoadState({
        status: "failed",
        message: "Could not load dashboard data.",
      });
    } finally {
      setRunInFlight(false);
    }
  }, [applyDashboardData, runtime]);

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

    async function fetchInitialDemoData() {
      try {
        const dashboardData = await fetchDemoDashboardDatasets();
        if (isActive) {
          applyDashboardData(dashboardData);
        }
      } catch {
        if (isActive) {
          setLoadState({
            status: "failed",
            message: "Could not load dashboard data.",
          });
        }
      }
    }

    void fetchInitialDemoData();

    return () => {
      isActive = false;
    };
  }, [applyDashboardData, data, shouldUseDemo]);

  const dashboardData = loadState.data ?? data;
  const viewModel = useMemo(
    () =>
      dashboardData ? buildDashboardViewModel(dashboardData, windowDays) : null,
    [dashboardData, windowDays],
  );
  const runDisabled =
    runInFlight ||
    loadState.status === "loading" ||
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
                windowDays={windowDays}
                currency={viewModel.header.currency}
                onWindowChange={setWindowDays}
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
