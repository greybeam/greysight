"use client";

import {
  Card,
  Text,
} from "@tremor/react";

import type { DetailTablesViewModel } from "../../lib/dashboard-transforms";

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
};

function DetailTable({ title, headers, rows }: DetailTableProps) {
  return (
    <Card className="p-4">
      <Text>{title}</Text>
      <div className="mt-2 max-h-72 overflow-y-auto">
        <table
          aria-label={title}
          className="w-full text-left text-xs text-slate-700"
        >
          <thead className="text-slate-900">
            <tr>
              {headers.map((header) => (
                <th key={header} className="whitespace-nowrap px-4 py-3.5 font-semibold">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 align-top">
            {rows.map((row) => (
              <tr key={row.key}>
                {row.cells.map((cell) => (
                  <td key={cell.key} className="whitespace-nowrap px-4 py-1.5">
                    {typeof cell.value === "number"
                      ? formatNumber(cell.value)
                      : cell.value}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
