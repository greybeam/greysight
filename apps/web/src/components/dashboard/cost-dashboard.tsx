"use client";

import {
  BarChart,
  Card,
  LineChart,
  Metric,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  Text,
  Title,
} from "@tremor/react";
import { useCallback, useEffect, useState } from "react";

import {
  fetchDashboardDatasets,
  fetchDemoDashboardDatasets,
  pollDashboardRun,
  startDashboardRun,
} from "../../lib/dashboard-api";
import type {
  DashboardData,
  DashboardRunStatus,
} from "../../lib/dashboard-contracts";
import RunStatus from "./run-status";

export type CostDashboardRuntime = {
  accessToken: string | null;
  organizationId: string;
  organizationName: string;
};

type CostDashboardProps = {
  data?: DashboardData;
  demoMode?: boolean;
  runtime?: CostDashboardRuntime | null;
  windowDays?: number;
};

type LoadState = {
  status: DashboardRunStatus | "loading";
  message?: string | null;
  data?: DashboardData;
};

export default function CostDashboard({
  data,
  demoMode,
  runtime,
  windowDays = 30,
}: CostDashboardProps) {
  const shouldUseDemo = demoMode ?? !runtime;
  const [loadState, setLoadState] = useState<LoadState>({
    status: data?.run.status ?? (shouldUseDemo ? "loading" : "queued"),
    data,
  });

  const applyDashboardData = useCallback(
    (dashboardData: DashboardData) => {
      setLoadState({
        status: dashboardData.run.status,
        message: dashboardData.run.error ?? dashboardData.run.user_safe_message,
        data: dashboardData,
      });
    },
    [],
  );

  const loadDemoRun = useCallback(async () => {
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
    setLoadState((current) => ({ ...current, status: "loading" }));

    try {
      const run = await startDashboardRun(
        { organizationId: runtime.organizationId, windowDays },
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
    }
  }, [applyDashboardData, runtime, windowDays]);

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
  const runDisabled =
    loadState.status === "loading" ||
    loadState.status === "running" ||
    (!shouldUseDemo && !runtime);

  return (
    <main className="min-h-screen bg-slate-50">
      <RunStatus status={loadState.status} message={loadState.message} />
      <div className="mx-auto grid w-full max-w-7xl gap-6 px-6 py-6">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-slate-500">
              Snowflake cost dashboard
            </p>
            <h1 className="text-2xl font-semibold text-slate-950">Greysight</h1>
          </div>
          <button
            className="h-10 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
            disabled={runDisabled}
            type="button"
            onClick={() => {
              void startRun();
            }}
          >
            Start run
          </button>
        </header>

        {dashboardData ? (
          <DashboardSections data={dashboardData} />
        ) : (
          <section className="min-h-96 rounded-lg border border-slate-200 bg-white p-6">
            <p className="text-sm font-medium text-slate-600">
              Loading dashboard data
            </p>
          </section>
        )}
      </div>
    </main>
  );
}

function DashboardSections({ data }: { data: DashboardData }) {
  const accountSpend = data.datasets.account_spend_daily.map((row) => ({
    date: row.usage_date,
    credits: row.credits_used,
  }));
  const warehouseSpend = data.datasets.top_warehouses_table.map((row) => ({
    warehouse: row.warehouse_name,
    credits: row.credits_used,
  }));
  const serviceSpend = data.datasets.service_spend_daily.map((row) => ({
    date: row.usage_date,
    service: row.service_type,
    credits: row.credits_used,
  }));

  return (
    <>
      <section className="grid gap-4 md:grid-cols-3">
        <Card>
          <Text>Total credits</Text>
          <Metric>{formatNumber(data.summary.total_credits)}</Metric>
        </Card>
        <Card>
          <Text>Average daily credits</Text>
          <Metric>{formatNumber(data.summary.average_daily_credits)}</Metric>
        </Card>
        <Card>
          <Text>Estimated monthly credits</Text>
          <Metric>{formatNumber(data.summary.estimated_monthly_credits)}</Metric>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Title>Account spend</Title>
          <LineChart
            className="mt-4 h-72"
            data={accountSpend}
            index="date"
            categories={["credits"]}
            colors={["blue"]}
            yAxisWidth={48}
          />
        </Card>
        <Card>
          <Title>Warehouse spend</Title>
          <BarChart
            className="mt-4 h-72"
            data={warehouseSpend}
            index="warehouse"
            categories={["credits"]}
            colors={["emerald"]}
            yAxisWidth={48}
          />
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Title>Service spend</Title>
          <Table className="mt-4">
            <TableHead>
              <TableRow>
                <TableHeaderCell>Date</TableHeaderCell>
                <TableHeaderCell>Service</TableHeaderCell>
                <TableHeaderCell>Credits</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {serviceSpend.map((row) => (
                <TableRow key={`${row.date}-${row.service}`}>
                  <TableCell>{row.date}</TableCell>
                  <TableCell>{row.service}</TableCell>
                  <TableCell>{formatNumber(row.credits)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
        <Card>
          <Title>Compute by user</Title>
          <Table className="mt-4">
            <TableHead>
              <TableRow>
                <TableHeaderCell>User</TableHeaderCell>
                <TableHeaderCell>Warehouse</TableHeaderCell>
                <TableHeaderCell>Credits</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.datasets.query_compute_by_user_daily.map((row) => (
                <TableRow
                  key={`${row.usage_date}-${row.user_name}-${row.warehouse_name}`}
                >
                  <TableCell>{row.user_name}</TableCell>
                  <TableCell>{row.warehouse_name}</TableCell>
                  <TableCell>
                    {formatNumber(row.credits_attributed_compute)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Title>Storage by database</Title>
          <Table className="mt-4">
            <TableHead>
              <TableRow>
                <TableHeaderCell>Database</TableHeaderCell>
                <TableHeaderCell>Average bytes</TableHeaderCell>
                <TableHeaderCell>Failsafe bytes</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.datasets.database_storage_daily.map((row) => (
                <TableRow
                  key={`${row.usage_date}-${row.database_name ?? "unknown"}`}
                >
                  <TableCell>{row.database_name ?? "Unknown"}</TableCell>
                  <TableCell>{formatNumber(row.average_database_bytes)}</TableCell>
                  <TableCell>{formatNumber(row.average_failsafe_bytes)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
        <Card>
          <Title>Top warehouses</Title>
          <Table className="mt-4">
            <TableHead>
              <TableRow>
                <TableHeaderCell>Warehouse</TableHeaderCell>
                <TableHeaderCell>Credits</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.datasets.top_warehouses_table.map((row) => (
                <TableRow key={row.warehouse_name}>
                  <TableCell>{row.warehouse_name}</TableCell>
                  <TableCell>{formatNumber(row.credits_used)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      </section>
    </>
  );
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(
    value,
  );
}
