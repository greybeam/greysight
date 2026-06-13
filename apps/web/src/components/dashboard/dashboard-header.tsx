"use client";

import { Badge } from "@tremor/react";

import type { HeaderViewModel } from "../../lib/dashboard-contracts";

export type DashboardModeLabel =
  | "Demo"
  | "Local Snowflake"
  | "Authenticated Snowflake";

type DashboardHeaderProps = {
  header: HeaderViewModel | null;
  modeLabel: DashboardModeLabel;
  runDisabled: boolean;
  onRun: () => void;
};

export default function DashboardHeader({
  header,
  modeLabel,
  runDisabled,
  onRun,
}: DashboardHeaderProps) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline bg-surface px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold text-slate-50">Greysight</h1>
        <Badge color="slate">{modeLabel}</Badge>
        {header ? (
          <>
            <Badge color={header.dataModeLabel === "Estimated" ? "amber" : "blue"}>
              {header.dataModeLabel}
            </Badge>
            {header.accountLocator ? (
              <span className="font-mono text-xs text-slate-400">
                {header.accountLocator}
              </span>
            ) : null}
            {header.freshnessLabel ? (
              <span className="text-xs text-slate-400">
                {header.freshnessLabel}
              </span>
            ) : null}
          </>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-3">
        {header?.dataModeLabel === "Estimated" ? (
          <span className="text-xs font-medium text-amber-400">
            Estimated spend at {header.estimatedCreditPriceLabel}/credit - billed data
            unavailable
          </span>
        ) : null}
        <button
          className="h-9 rounded-md bg-chart-purple px-3 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:bg-surface disabled:text-slate-500 disabled:opacity-100"
          disabled={runDisabled}
          type="button"
          onClick={onRun}
        >
          Run analysis
        </button>
      </div>
    </header>
  );
}
