"use client";

import Link from "next/link";

import type { WarehouseWasteViewModel } from "../../lib/dashboard-contracts";
import { DashboardSection } from "./dashboard-design-system";

export function WasteEstimatorCard({
  model,
}: {
  model: WarehouseWasteViewModel;
}) {
  if (model.isEmpty) {
    return null;
  }
  return (
    <DashboardSection
      ariaLabel="Recoverable idle compute"
      testId="dashboard-section-warehouse-waste"
      title="Recoverable idle compute"
    >
      <p className="text-xs text-slate-400">
        Idle credits × your compute rate, over the selected window.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Kpi
          label="Idle spend this period"
          value={model.totalPeriodIdleSpendLabel}
        />
        <Kpi
          label="Projected monthly waste"
          value={model.totalProjectedMonthlyIdleSpendLabel}
        />
      </div>
      <ul className="space-y-1 text-xs" role="list">
        <li className="grid grid-cols-[minmax(0,8rem)_auto_auto_auto] gap-3 pb-1 text-[10px] uppercase tracking-wide text-slate-500">
          <span>Warehouse</span>
          <span className="text-right">Idle %</span>
          <span className="text-right">Period</span>
          <span className="text-right">Projected / mo</span>
        </li>
        {model.rows.map((row) => (
          <li
            key={row.name}
            data-testid="waste-estimator-row"
            data-warehouse-name={row.name}
            className="grid grid-cols-[minmax(0,8rem)_auto_auto_auto] items-baseline gap-3 tabular-nums"
          >
            <span className="truncate text-slate-300" title={row.name}>
              {row.name}
            </span>
            <span
              className="text-right text-slate-400"
              data-testid="waste-estimator-idle-pct"
            >
              {row.idlePct === null ? "–" : `${Math.round(row.idlePct * 100)}%`}
            </span>
            <span className="text-right text-slate-200">
              {row.periodIdleSpendLabel}
            </span>
            <span className="text-right font-semibold text-slate-100">
              {row.projectedMonthlyIdleSpendLabel}
            </span>
          </li>
        ))}
      </ul>
      <Link
        href="/automated-savings"
        className="inline-flex items-center gap-2 rounded-md bg-emerald-500/90 px-3 py-2 text-xs font-semibold text-slate-950 hover:bg-emerald-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-300"
        data-testid="waste-estimator-auto-savings-cta"
      >
        Reduce this waste with Auto Savings →
      </Link>
    </DashboardSection>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-hairline bg-surface p-3">
      <p className="text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-slate-100">
        {value}
      </p>
    </div>
  );
}
