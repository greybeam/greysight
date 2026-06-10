"use client";

import { Badge } from "@tremor/react";

import type { DashboardRunStatus } from "../../lib/dashboard-contracts";

type RunStatusProps = {
  status: DashboardRunStatus | "loading";
  message?: string | null;
};

const STATUS_COPY: Record<RunStatusProps["status"], string> = {
  loading: "Loading dashboard data",
  queued: "Analysis queued",
  running: "Analysis running",
  completed: "Analysis complete",
  failed: "Analysis failed",
  expired: "Run data expired",
  deleted: "Run deleted",
};

const STATUS_COLOR: Record<
  RunStatusProps["status"],
  "blue" | "emerald" | "rose" | "slate" | "amber"
> = {
  loading: "blue",
  queued: "slate",
  running: "blue",
  completed: "emerald",
  failed: "rose",
  expired: "amber",
  deleted: "slate",
};

export function RunStatus({ status, message }: RunStatusProps) {
  return (
    <div className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-6 py-4">
      <div className="flex items-center gap-3">
        <Badge color={STATUS_COLOR[status]}>{STATUS_COPY[status]}</Badge>
        {message ? (
          <p className="text-sm font-medium text-slate-700">{message}</p>
        ) : null}
      </div>
    </div>
  );
}

export default RunStatus;
