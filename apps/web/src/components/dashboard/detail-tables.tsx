"use client";

import {
  Card,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  Text,
} from "@tremor/react";

import type { DetailTablesViewModel } from "../../lib/dashboard-transforms";

const MAX_ROWS = 50;

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
}

type DetailTableProps = {
  title: string;
  headers: string[];
  rows: Array<Array<string | number>>;
};

function DetailTable({ title, headers, rows }: DetailTableProps) {
  return (
    <Card className="p-4">
      <Text>{title}</Text>
      <div className="mt-2 max-h-72 overflow-y-auto">
        <Table>
          <TableHead>
            <TableRow>
              {headers.map((header) => (
                <TableHeaderCell key={header}>{header}</TableHeaderCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.slice(0, MAX_ROWS).map((row, index) => (
              <TableRow key={`${String(row[0])}-${index}`}>
                {row.map((cell, cellIndex) => (
                  <TableCell key={cellIndex} className="py-1.5 text-xs">
                    {typeof cell === "number" ? formatNumber(cell) : cell}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
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
        rows={viewModel.services.map((row) => [
          row.name,
          row.spendLabel,
          row.credits ?? 0,
        ])}
      />
      <DetailTable
        title="Warehouse spend"
        headers={["Warehouse", "Est. spend", "Compute credits", "Total credits"]}
        rows={viewModel.warehouses.map((row) => [
          row.name,
          row.spendLabel,
          row.creditsCompute,
          row.creditsTotal,
        ])}
      />
      <DetailTable
        title="User compute spend"
        headers={["User", "Warehouse", "Est. spend", "Credits"]}
        rows={viewModel.users.map((row) => [
          row.name,
          row.warehouseName,
          row.spendLabel,
          row.credits ?? 0,
        ])}
      />
      <DetailTable
        title="Storage by database"
        headers={["Database", "Est. monthly spend", "Bytes"]}
        rows={viewModel.storage.map((row) => [
          row.name,
          row.monthlySpendLabel,
          row.bytes,
        ])}
      />
    </section>
  );
}
