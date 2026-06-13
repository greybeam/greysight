"use client";

import {
  Card,
  Text,
} from "@tremor/react";

import type { DetailTablesViewModel } from "../../lib/dashboard-contracts";

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
}

type DetailTableProps = {
  title: string;
  headers: string[];
  rows: Array<{
    key: string;
    cells: Array<{ key: string; value: string | number }>;
  }>;
  // Opt-in: stretch the card to fill its (stretched) grid cell so the scroll
  // list grows/scrolls inside it instead of floating at content height. Used by
  // the Storage section's right card; the bottom 2x2 grid leaves it off.
  fillHeight?: boolean;
  // Opt-in: lock the table to a fixed layout and truncate the first (name)
  // column with an ellipsis so long names can't force horizontal overflow and
  // push later columns off-screen. The bottom 2x2 grid leaves it off.
  truncateFirstColumn?: boolean;
};

export function DetailTable({
  title,
  headers,
  rows,
  fillHeight = false,
  truncateFirstColumn = false,
}: DetailTableProps) {
  const table = (
    <table
      aria-label={title}
      className={`w-full text-left text-xs text-slate-300${
        truncateFirstColumn ? " table-fixed" : ""
      }`}
    >
      <thead className="text-slate-100">
        <tr>
          {headers.map((header, index) => {
            // In truncate mode the first column absorbs the leftover width and
            // ellipsizes, while the trailing numeric columns take a fixed width
            // and right-align so their values stay visible and can't overlap.
            // The bottom 2x2 tables keep auto layout (left-aligned, nowrap).
            const isFirst = index === 0;
            const cellClass = truncateFirstColumn
              ? isFirst
                ? "px-4 py-3.5 font-semibold"
                : "w-24 whitespace-nowrap px-4 py-3.5 text-right font-semibold"
              : "whitespace-nowrap px-4 py-3.5 font-semibold";
            return (
              <th key={header} className={cellClass}>
                {header}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody className="divide-y divide-hairline align-top">
        {rows.map((row) => (
          <tr key={row.key}>
            {row.cells.map((cell, index) => {
              const isFirst = index === 0;
              const rendered =
                typeof cell.value === "number"
                  ? formatNumber(cell.value)
                  : cell.value;
              if (truncateFirstColumn && isFirst) {
                const fullName =
                  typeof cell.value === "string" ? cell.value : undefined;
                return (
                  <td key={cell.key} className="px-4 py-1.5">
                    <span className="block truncate" title={fullName}>
                      {rendered}
                    </span>
                  </td>
                );
              }
              const cellClass = truncateFirstColumn
                ? "w-24 whitespace-nowrap px-4 py-1.5 text-right"
                : "whitespace-nowrap px-4 py-1.5";
              return (
                <td key={cell.key} className={cellClass}>
                  {rendered}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <Card className={fillHeight ? "flex h-full flex-col p-4" : "p-4"}>
      <Text>{title}</Text>
      {fillHeight ? (
        // Absolute-fill scroll, mirroring RankedSpendBars: the relative wrapper
        // claims the leftover flex height, and the absolutely-positioned scroll
        // child contributes zero intrinsic height — so the row height is driven
        // by the sibling chart card and the table scrolls within it. A <table>
        // in a plain `flex-1 overflow-y-auto` div ignores min-height:0 and would
        // grow the row instead.
        <div className="relative mt-2 min-h-0 flex-1">
          <div className="dashboard-scroll absolute inset-0 overflow-y-auto">
            {table}
          </div>
        </div>
      ) : (
        <div className="mt-2 max-h-72 overflow-y-auto">{table}</div>
      )}
    </Card>
  );
}

export default function DetailTables({
  viewModel,
}: {
  viewModel: DetailTablesViewModel;
}) {
  return (
    <section aria-label="Detail tables" className="grid gap-3 lg:grid-cols-2">
      <DetailTable
        title="Service spend"
        headers={["Service", "Spend", "Credits"]}
        rows={viewModel.services.map((row) => ({
          key: row.name,
          cells: [
            { key: "name", value: row.name },
            { key: "spend", value: row.spendLabel },
            { key: "credits", value: row.credits ?? 0 },
          ],
        }))}
      />
      <DetailTable
        title="Warehouse spend"
        headers={["Warehouse", "Est. spend", "Compute credits", "Total credits"]}
        rows={viewModel.warehouses.map((row) => ({
          key: row.name,
          cells: [
            { key: "name", value: row.name },
            { key: "spend", value: row.spendLabel },
            { key: "creditsCompute", value: row.creditsCompute },
            { key: "creditsTotal", value: row.creditsTotal },
          ],
        }))}
      />
      <DetailTable
        title="User compute spend"
        headers={["User", "Warehouse", "Est. spend", "Credits"]}
        rows={viewModel.users.map((row) => ({
          key: `${row.name}-${row.warehouseName}`,
          cells: [
            { key: "name", value: row.name },
            { key: "warehouseName", value: row.warehouseName },
            { key: "spend", value: row.spendLabel },
            { key: "credits", value: row.credits ?? 0 },
          ],
        }))}
      />
      <DetailTable
        title="Storage by database"
        headers={["Database", "Est. monthly spend", "Bytes"]}
        rows={viewModel.storage.map((row) => ({
          key: row.name,
          cells: [
            { key: "name", value: row.name },
            { key: "monthlySpend", value: row.monthlySpendLabel },
            { key: "bytes", value: row.bytes },
          ],
        }))}
      />
    </section>
  );
}
