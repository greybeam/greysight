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

import { fetchDemoDashboardDatasets } from "../../lib/dashboard-api";
import type {
  DashboardData,
  DashboardRunStatus,
} from "../../lib/dashboard-contracts";
import RunStatus from "./run-status";

type CostDashboardProps = {
  data?: DashboardData;
  dataSource?: "demo" | "snowflake";
};

type LoadState = {
  status: DashboardRunStatus | "loading";
  message?: string | null;
  data?: DashboardData;
};

export default function CostDashboard({
  data,
}: CostDashboardProps) {
  const [loadState, setLoadState] = useState<LoadState>({
    status: data?.run.status ?? "loading",
    data,
  });

  const applyDemoDashboardData = useCallback(
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
      applyDemoDashboardData(dashboardData);
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
  }, [applyDemoDashboardData, data]);

  const startRun = useCallback(async () => {
    await loadDemoRun();
  }, [loadDemoRun]);

  useEffect(() => {
    if (data) {
      return;
    }
    let isActive = true;

    async function fetchInitialDemoData() {
      try {
        const dashboardData = await fetchDemoDashboardDatasets();
        if (isActive) {
          applyDemoDashboardData(dashboardData);
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
  }, [applyDemoDashboardData, data]);

  const dashboardData = loadState.data ?? data;

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
            disabled={
              loadState.status === "loading" || loadState.status === "running"
            }
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
                  <TableCell>{formatNumber(row.credits_used)}</TableCell>
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
