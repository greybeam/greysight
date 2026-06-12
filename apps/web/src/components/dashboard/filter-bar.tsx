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
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div
          aria-label="Spend window"
          className="inline-flex rounded-md border border-slate-200 bg-white p-0.5"
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
                    ? "rounded bg-slate-950 px-3 py-1 text-xs font-semibold text-white"
                    : "rounded px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
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
          <label className="grid gap-1 text-xs font-medium text-slate-600">
            Start date
            <input
              className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
              type="date"
              value={startDate}
              onChange={(event) => onStartDateChange(event.target.value)}
            />
          </label>
          <label className="grid gap-1 text-xs font-medium text-slate-600">
            End date
            <input
              className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
              type="date"
              value={endDate}
              onChange={(event) => onEndDateChange(event.target.value)}
            />
          </label>
          <button
            className="h-8 rounded-md border border-slate-300 bg-white px-3 text-xs font-semibold text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
            disabled={applyDisabled}
            type="button"
            onClick={onApplyDateRange}
          >
            Apply date range
          </button>
        </div>
      </div>
      <span className="text-xs font-medium text-slate-500">{currency}</span>
    </div>
  );
}
