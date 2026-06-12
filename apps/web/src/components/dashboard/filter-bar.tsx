"use client";

import type { DashboardViewRange } from "../../lib/dashboard-contracts";

export const WINDOW_DAYS = [7, 30, 90] as const;
export type WindowDays = (typeof WINDOW_DAYS)[number];

type FilterBarProps = {
  range: DashboardViewRange;
  currency: string;
  startDate: string;
  endDate: string;
  onWindowChange: (windowDays: WindowDays) => void;
  onStartDateChange: (startDate: string) => void;
  onEndDateChange: (endDate: string) => void;
  onApplyDateRange: () => void;
};

export function canApplyDateRange(
  startDate: string,
  endDate: string,
): boolean {
  return startDate.length > 0 && endDate.length > 0 && startDate <= endDate;
}

export default function FilterBar({
  range,
  currency,
  startDate,
  endDate,
  onWindowChange,
  onStartDateChange,
  onEndDateChange,
  onApplyDateRange,
}: FilterBarProps) {
  const applyDisabled = !canApplyDateRange(startDate, endDate);

  return (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex flex-wrap items-end gap-4">
        <div
          aria-label="Spend window"
          className="inline-flex h-8 rounded-md border border-hairline bg-surface p-0.5"
          role="group"
        >
          {WINDOW_DAYS.map((option) => {
            const isActive =
              range.mode === "relative" && option === range.windowDays;

            return (
              <button
                key={option}
                aria-pressed={isActive}
                className={
                  isActive
                    ? "h-full rounded bg-chart-purple px-4 text-xs font-semibold text-white"
                    : "h-full rounded px-4 text-xs font-medium text-slate-400 hover:bg-white/5"
                }
                type="button"
                onClick={() => onWindowChange(option)}
              >
                {option} days
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="grid gap-1 text-xs font-medium text-slate-400">
            Start date
            <input
              className="h-8 rounded-md border border-hairline bg-surface px-2 text-xs text-slate-100"
              type="date"
              value={startDate}
              onChange={(event) => onStartDateChange(event.target.value)}
            />
          </label>
          <label className="grid gap-1 text-xs font-medium text-slate-400">
            End date
            <input
              className="h-8 rounded-md border border-hairline bg-surface px-2 text-xs text-slate-100"
              type="date"
              value={endDate}
              onChange={(event) => onEndDateChange(event.target.value)}
            />
          </label>
          <button
            className="h-8 rounded-md border border-hairline bg-surface px-4 text-xs font-semibold text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:bg-surface disabled:text-slate-500"
            disabled={applyDisabled}
            type="button"
            onClick={onApplyDateRange}
          >
            Apply date range
          </button>
        </div>
      </div>
      <span className="text-xs font-medium text-slate-400">{currency}</span>
    </div>
  );
}
