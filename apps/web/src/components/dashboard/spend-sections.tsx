"use client";

import { BarChart, Card, LineChart, Metric, Text, Title } from "@tremor/react";

import type {
  ComputeSpendViewModel,
  RankedBarRow,
  ServiceSpendViewModel,
  StorageSpendViewModel,
  TotalSpendViewModel,
} from "../../lib/dashboard-transforms";
import SectionEmptyState from "./section-empty-state";

function EstimatedBadge() {
  return (
    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-800">
      Estimated
    </span>
  );
}

function RankedBars({ rows }: { rows: RankedBarRow[] }) {
  if (rows.length === 0) {
    return <p className="mt-3 text-xs text-slate-500">No ranked spend data</p>;
  }

  return (
    <ul className="mt-3 grid gap-1.5">
      {rows.map((row) => (
        <li
          key={row.name}
          className="grid grid-cols-[minmax(6rem,8rem)_1fr_auto] items-center gap-2"
        >
          <span className="truncate text-xs text-slate-600">{row.name}</span>
          <span className="h-2 rounded bg-slate-200">
            <span
              className="block h-2 rounded bg-blue-600"
              style={{ width: `${row.barWidthPercent}%` }}
            />
          </span>
          <span className="text-xs font-semibold tabular-nums text-slate-900">
            {row.spendLabel}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function TotalSpendSection({
  viewModel,
}: {
  viewModel: TotalSpendViewModel;
}) {
  return (
    <section aria-label="Total spend" className="grid gap-3">
      <Title>Total spend</Title>
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No total spend data" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-[18rem_1fr]">
          <div className="grid gap-3">
            <Card className="p-4">
              <Text>
                Window total {viewModel.basis === "estimated" ? <EstimatedBadge /> : null}
              </Text>
              <Metric>{viewModel.totalLabel}</Metric>
            </Card>
            <Card className="p-4">
              <Text>Average daily</Text>
              <Metric className="text-xl">{viewModel.averageDailyLabel}</Metric>
            </Card>
            <Card className="p-4">
              <Text>Projected monthly ({viewModel.projectionBasisLabel})</Text>
              <Metric className="text-xl">{viewModel.projectedMonthlyLabel}</Metric>
            </Card>
            {viewModel.topDriver ? (
              <Card className="p-4">
                <Text>Top driver</Text>
                <p className="text-sm font-semibold text-slate-900">
                  {viewModel.topDriver.name} - {viewModel.topDriver.spendLabel}
                </p>
              </Card>
            ) : null}
          </div>
          <Card className="p-4">
            <Text>Daily spend</Text>
            <LineChart
              className="mt-2 h-44"
              data={viewModel.dailySeries}
              index="date"
              categories={["spend"]}
              colors={["blue"]}
              yAxisWidth={56}
            />
          </Card>
        </div>
      )}
    </section>
  );
}

export function ComputeSpendSection({
  viewModel,
}: {
  viewModel: ComputeSpendViewModel;
}) {
  return (
    <section aria-label="Compute spend" className="grid gap-3">
      <Title>Compute spend</Title>
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No compute spend data" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-3">
          <Card className="p-4">
            <Text>
              Daily compute{" "}
              {viewModel.computeBasis === "estimated" ? <EstimatedBadge /> : null}
            </Text>
            <LineChart
              className="mt-2 h-40"
              data={viewModel.dailySeries}
              index="date"
              categories={["spend"]}
              colors={["blue"]}
              yAxisWidth={56}
            />
          </Card>
          <Card className="p-4">
            <Text>
              Warehouses <EstimatedBadge />
            </Text>
            <RankedBars rows={viewModel.warehouseBars} />
          </Card>
          <Card className="p-4">
            <Text>
              Users <EstimatedBadge />
            </Text>
            <RankedBars rows={viewModel.userBars} />
          </Card>
        </div>
      )}
    </section>
  );
}

export function StorageSpendSection({
  viewModel,
}: {
  viewModel: StorageSpendViewModel;
}) {
  return (
    <section aria-label="Storage spend" className="grid gap-3">
      <Title>Storage spend</Title>
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No storage spend data" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <Card className="p-4">
            <Text>
              Daily storage{" "}
              {viewModel.basis === "estimated" ? <EstimatedBadge /> : null}
            </Text>
            <LineChart
              className="mt-2 h-40"
              data={viewModel.dailySeries}
              index="date"
              categories={["spend"]}
              colors={["emerald"]}
              yAxisWidth={56}
            />
          </Card>
          <Card className="p-4">
            <Text>
              Latest storage by database{" "}
              {viewModel.databaseBasis === "estimated" ? <EstimatedBadge /> : null}
            </Text>
            <RankedBars rows={viewModel.databaseBars} />
          </Card>
        </div>
      )}
    </section>
  );
}

export function ServiceSpendSection({
  viewModel,
}: {
  viewModel: ServiceSpendViewModel;
}) {
  return (
    <section aria-label="Service spend" className="grid gap-3">
      <Title>Service spend</Title>
      {viewModel.isEmpty ? (
        <SectionEmptyState message="No service spend data" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <Card className="p-4">
            <Text>
              Daily by service{" "}
              {viewModel.basis === "estimated" ? <EstimatedBadge /> : null}
            </Text>
            <BarChart
              className="mt-2 h-44"
              data={viewModel.dailySeries}
              index="date"
              categories={viewModel.serviceNames}
              stack
              yAxisWidth={56}
            />
          </Card>
          <Card className="p-4">
            <Text>Ranked services</Text>
            <RankedBars rows={viewModel.serviceBars} />
          </Card>
        </div>
      )}
    </section>
  );
}
