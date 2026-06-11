"use client";

import { WINDOW_DAYS, type WindowDays } from "../../lib/dashboard-transforms";

type FilterBarProps = {
  windowDays: WindowDays;
  currency: string;
  onWindowChange: (windowDays: WindowDays) => void;
};

export default function FilterBar({
  windowDays,
  currency,
  onWindowChange,
}: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div
        aria-label="Spend window"
        className="inline-flex rounded-md border border-slate-200 bg-white p-0.5"
        role="group"
      >
        {WINDOW_DAYS.map((option) => (
          <button
            key={option}
            aria-pressed={option === windowDays}
            className={
              option === windowDays
                ? "rounded bg-slate-950 px-3 py-1 text-xs font-semibold text-white"
                : "rounded px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
            }
            type="button"
            onClick={() => onWindowChange(option)}
          >
            {option} days
          </button>
        ))}
      </div>
      <span className="text-xs font-medium text-slate-500">{currency}</span>
    </div>
  );
}
