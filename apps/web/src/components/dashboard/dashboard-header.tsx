"use client";

import type { HeaderViewModel } from "../../lib/dashboard-contracts";
import Spinner from "../ui/spinner";
import { AppHeader } from "./app-header";
import { formatCachedAsOfLabel } from "./dashboard-design-system";

export type DashboardModeLabel =
  "Demo" | "Local Snowflake" | "Authenticated Snowflake";

type DashboardHeaderProps = {
  header: HeaderViewModel | null;
  // Snowflake account locator sourced from the persisted org connection so it
  // can render before any analysis run; falls back to the run's view model.
  accountLocator?: string | null;
  runDisabled: boolean;
  // True while an analysis run is in flight; swaps the button label for a
  // spinner so the in-progress state is visible while the button is disabled.
  running?: boolean;
  onRun: () => void;
  // ISO8601 timestamp of the cached run currently on screen, or null/undefined
  // when the view came from a fresh run. Purely frontend-derived — not part of
  // the HeaderViewModel contract.
  cachedAsOf?: string | null;
};

export default function DashboardHeader({
  header,
  runDisabled,
  running = false,
  onRun,
  cachedAsOf,
}: DashboardHeaderProps) {
  return (
    <AppHeader>
      {cachedAsOf ? (
        <span className="text-xs font-medium text-slate-400" role="status">
          Using cached view as of {formatCachedAsOfLabel(cachedAsOf)}
        </span>
      ) : null}
      {header?.dataModeLabel === "Estimated" ? (
        <span className="text-xs font-medium text-amber-400">
          Estimated spend at {header.estimatedCreditPriceLabel}/credit - billed
          data unavailable
        </span>
      ) : null}
      <button
        aria-busy={running}
        className="flex h-9 items-center gap-2 rounded-md bg-chart-purple px-3 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:bg-surface disabled:text-slate-500 disabled:opacity-100"
        disabled={runDisabled}
        type="button"
        onClick={onRun}
      >
        {running ? (
          <>
            <Spinner />
            Running…
          </>
        ) : (
          "Run analysis"
        )}
      </button>
    </AppHeader>
  );
}
