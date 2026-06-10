"use client";

import {
  BarChart,
  Card,
  LineChart,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  Text,
  Title,
} from "@tremor/react";

const spendTrend = [
  { usage_date: "Jun 1", credits: 32 },
  { usage_date: "Jun 2", credits: 41 },
  { usage_date: "Jun 3", credits: 38 },
];

const warehouseSpend = [
  { warehouse: "BI_WH", credits: 128 },
  { warehouse: "LOAD_WH", credits: 84 },
  { warehouse: "APP_WH", credits: 56 },
];

export default function TremorCompat() {
  return (
    <section className="grid gap-4">
      <Card>
        <Title>Compatibility</Title>
        <Text>Warehouse spend</Text>
        <LineChart
          className="mt-4 h-48"
          data={spendTrend}
          index="usage_date"
          categories={["credits"]}
          colors={["blue"]}
          yAxisWidth={40}
        />
      </Card>

      <Card>
        <Title>Top warehouses</Title>
        <BarChart
          className="mt-4 h-48"
          data={warehouseSpend}
          index="warehouse"
          categories={["credits"]}
          colors={["emerald"]}
          yAxisWidth={40}
        />
        <Table className="mt-4">
          <TableHead>
            <TableRow>
              <TableHeaderCell>Warehouse</TableHeaderCell>
              <TableHeaderCell>Credits</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {warehouseSpend.map((row) => (
              <TableRow key={row.warehouse}>
                <TableCell>{row.warehouse}</TableCell>
                <TableCell>{row.credits}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </section>
  );
}
