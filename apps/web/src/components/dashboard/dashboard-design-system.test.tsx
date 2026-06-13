import type { CustomTooltipProps } from "@tremor/react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  buildEndingBalanceLabel,
  buildSpendPeriodLabel,
  buildTotalSpendLabel,
  buildTotalWarehouseSpendLabel,
  CapacityBalanceCard,
  createChartTooltip,
  createCurrencyTickFormatter,
  formatChartDateLabel,
  RankedSpendBars,
} from "./dashboard-design-system";
import type { DashboardViewRange } from "../../lib/dashboard-contracts";
import { getSeriesColors, orderCategoriesByTotal } from "../../lib/chart-colors";

afterEach(() => {
  cleanup();
});

describe("createCurrencyTickFormatter", () => {
  it("formats USD across the adaptive fraction-digit ranges", () => {
    const format = createCurrencyTickFormatter("USD");

    expect(format(0)).toBe("$0");
    expect(format(0.2)).toBe("$0.20");
    expect(format(12)).toBe("$12");
    expect(format(1200)).toBe("$1.2K");
  });

  it("uses the provided non-USD currency symbol", () => {
    const format = createCurrencyTickFormatter("EUR");

    expect(format(12)).toContain("€");
  });

  it("falls back to USD for a malformed currency code without throwing", () => {
    // Intl.NumberFormat throws RangeError on structurally invalid codes
    // (empty / wrong length / non-alphabetic). The formatter must fall back
    // to USD rather than crash the chart.
    const emptyFormat = createCurrencyTickFormatter("");
    expect(emptyFormat(12)).toBe("$12");

    const malformedFormat = createCurrencyTickFormatter("US");
    expect(malformedFormat(12)).toBe("$12");
  });
});

describe("formatChartDateLabel", () => {
  it("formats a valid ISO date to MMM dd", () => {
    expect(formatChartDateLabel("2026-06-09")).toBe("Jun 09");
    expect(formatChartDateLabel("2026-01-01")).toBe("Jan 01");
  });

  it("does not drift across timezone boundaries", () => {
    // UTC parsing must keep Jun 09 as Jun 09 regardless of the host timezone.
    expect(formatChartDateLabel("2026-06-09")).toBe("Jun 09");
  });

  it("returns the original string for non-ISO input", () => {
    expect(formatChartDateLabel("not-a-date")).toBe("not-a-date");
  });

  it("returns the original string for an out-of-range ISO-shaped value", () => {
    expect(formatChartDateLabel("2026-13-40")).toBe("2026-13-40");
  });
});

describe("buildTotalSpendLabel", () => {
  it("labels canonical relative windows", () => {
    const relative = (windowDays: number): DashboardViewRange => ({
      mode: "relative",
      windowDays,
      startDate: "2026-05-10",
      endDate: "2026-06-08",
    });

    expect(buildTotalSpendLabel(relative(7))).toBe(
      "Total Spend in Last 7 Days",
    );
    expect(buildTotalSpendLabel(relative(30))).toBe(
      "Total Spend in Last 30 Days",
    );
    expect(buildTotalSpendLabel(relative(90))).toBe(
      "Total Spend in Last 90 Days",
    );
  });

  it("falls back to a day count for non-canonical relative windows", () => {
    expect(
      buildTotalSpendLabel({
        mode: "relative",
        windowDays: 14,
        startDate: "2026-05-26",
        endDate: "2026-06-08",
      }),
    ).toBe("Total Spend in Last 14 Days");
  });

  it("formats a custom range as 'between … and …'", () => {
    expect(
      buildTotalSpendLabel({
        mode: "custom",
        windowDays: null,
        startDate: "2026-05-12",
        endDate: "2026-06-11",
      }),
    ).toBe("Total Spend between May 12 and Jun 11");
  });

  it("falls back to the bare label when range data is unavailable", () => {
    expect(buildTotalSpendLabel(null)).toBe("Total Spend");
    expect(buildTotalSpendLabel(undefined)).toBe("Total Spend");
    expect(
      buildTotalSpendLabel({
        mode: "relative",
        windowDays: null,
        startDate: "2026-05-10",
        endDate: "2026-06-08",
      }),
    ).toBe("Total Spend");
  });
});

describe("buildSpendPeriodLabel", () => {
  it("applies an arbitrary prefix to relative and custom ranges", () => {
    expect(
      buildSpendPeriodLabel("Total Warehouse Spend", {
        mode: "relative",
        windowDays: 30,
        startDate: "2026-05-10",
        endDate: "2026-06-08",
      }),
    ).toBe("Total Warehouse Spend in Last 30 Days");
    expect(
      buildSpendPeriodLabel("Total Warehouse Spend", {
        mode: "custom",
        windowDays: null,
        startDate: "2026-05-20",
        endDate: "2026-06-11",
      }),
    ).toBe("Total Warehouse Spend between May 20 and Jun 11");
  });

  it("falls back to the bare prefix when range data is unavailable", () => {
    expect(buildSpendPeriodLabel("Total Warehouse Spend", null)).toBe(
      "Total Warehouse Spend",
    );
    expect(buildSpendPeriodLabel("Total Warehouse Spend", undefined)).toBe(
      "Total Warehouse Spend",
    );
  });
});

describe("buildTotalWarehouseSpendLabel", () => {
  it("prefixes the warehouse KPI label with the active range", () => {
    expect(
      buildTotalWarehouseSpendLabel({
        mode: "relative",
        windowDays: 30,
        startDate: "2026-05-10",
        endDate: "2026-06-08",
      }),
    ).toBe("Total Warehouse Spend in Last 30 Days");
    expect(
      buildTotalWarehouseSpendLabel({
        mode: "custom",
        windowDays: null,
        startDate: "2026-05-20",
        endDate: "2026-06-11",
      }),
    ).toBe("Total Warehouse Spend between May 20 and Jun 11");
    expect(buildTotalWarehouseSpendLabel(null)).toBe("Total Warehouse Spend");
  });
});

describe("buildEndingBalanceLabel", () => {
  it("appends the formatted current balance date", () => {
    expect(buildEndingBalanceLabel("2026-06-11")).toBe(
      "Ending Balance as of Jun 11",
    );
    expect(buildEndingBalanceLabel("2026-01-01")).toBe(
      "Ending Balance as of Jan 01",
    );
  });

  it("falls back to the plain label when no date is available", () => {
    expect(buildEndingBalanceLabel(null)).toBe("Ending Balance");
    expect(buildEndingBalanceLabel(undefined)).toBe("Ending Balance");
  });
});

describe("RankedSpendBars", () => {
  it("exposes list semantics and aligns rows on the shared grid tracks", () => {
    render(
      <RankedSpendBars
        rows={[
          {
            name: "WAREHOUSE_METERING",
            spend: 10,
            spendLabel: "$10.00",
            credits: 10,
            barWidthPercent: 100,
          },
          {
            name: "CLOUD_SERVICES",
            spend: 4,
            spendLabel: "$4.00",
            credits: 4,
            barWidthPercent: 40,
          },
        ]}
      />,
    );

    // Explicit role="list" / role="listitem" keep list semantics that a
    // `contents` <li> can otherwise drop in some screen readers; the grid
    // tracks live on the <ul> so every row aligns to the same columns.
    const list = screen.getByRole("list");
    expect(list).toHaveClass(
      "grid",
      "grid-cols-[minmax(0,9rem)_minmax(1.5rem,1fr)_auto]",
      "overflow-y-auto",
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(2);

    // The truncating name cell (not the `contents` li, which cannot truncate)
    // carries truncate + min-w-0 so long names ellipsize within the track, and
    // exposes the full name via title= for hover.
    const nameCell = screen.getByText("WAREHOUSE_METERING");
    expect(nameCell).toHaveClass("truncate", "min-w-0");
    expect(nameCell).toHaveAttribute("title", "WAREHOUSE_METERING");

    // $10 is not under $10, so the cents are dropped; the $4 row keeps them.
    expect(screen.getByText("$10")).toHaveClass("tabular-nums");
    expect(screen.getByText("$4.00")).toHaveClass("tabular-nums");
  });

  it("rounds the compact label to whole units instead of truncating cents", () => {
    render(
      <RankedSpendBars
        rows={[
          {
            name: "ROUND_UP",
            spend: 10.99,
            spendLabel: "$10.99",
            credits: 11,
            barWidthPercent: 100,
          },
          {
            name: "ROUND_DOWN",
            spend: 10.49,
            spendLabel: "$10.49",
            credits: 10,
            barWidthPercent: 95,
          },
          {
            name: "WITH_GROUPING",
            spend: 1234.56,
            spendLabel: "$1,234.56",
            credits: 1235,
            barWidthPercent: 90,
          },
        ]}
      />,
    );

    // $10.99 must read as $11, not the understated $10 a plain cents-strip gives.
    expect(screen.getByText("$11")).toBeInTheDocument();
    expect(screen.getByText("$10")).toBeInTheDocument();
    // Thousands grouping survives the round.
    expect(screen.getByText("$1,235")).toBeInTheDocument();
    expect(screen.queryByText("$1,234")).not.toBeInTheDocument();
  });
});

describe("CapacityBalanceCard", () => {
  it("renders the KPI with the dark dashboard metric color", () => {
    render(
      <CapacityBalanceCard
        ariaLabel="Capacity balance summary"
        chartTestId="capacity-balance-chart"
        currency="USD"
        data={[
          {
            date: "2026-06-11",
            balance: 12345,
            balanceLabel: "$12,345.00",
          },
        ]}
        label="Ending Balance"
        value="$12,345.00"
        testId="capacity-balance-card"
      />,
    );

    expect(screen.getByText("$12,345.00")).toHaveClass("text-slate-50");
  });
});

describe("createChartTooltip", () => {
  const usdFormatter = createCurrencyTickFormatter("USD");
  const sampleProps: CustomTooltipProps = {
    active: true,
    label: "Jun 09",
    payload: [
      {
        color: "#3b82f6",
        dataKey: "spend",
        name: "spend",
        value: 12.5,
      },
    ],
  };

  it("renders an opaque card with the label and formatted value", () => {
    const Tooltip = createChartTooltip(usdFormatter);

    render(<Tooltip {...sampleProps} />);

    expect(screen.getByText("Jun 09")).toBeInTheDocument();
    expect(screen.getByText(usdFormatter(12.5))).toBeInTheDocument();

    const container = screen.getByText("Jun 09").parentElement;
    expect(container).toHaveClass("bg-surface");
  });

  it("renders nothing when inactive", () => {
    const Tooltip = createChartTooltip(usdFormatter);

    const { container } = render(<Tooltip {...sampleProps} active={false} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("orders multi-series rows by value descending regardless of payload order", () => {
    const Tooltip = createChartTooltip(usdFormatter);
    const multiSeriesProps: CustomTooltipProps = {
      active: true,
      label: "Jun 09",
      payload: [
        { color: "chart-3", dataKey: "AUTO_CLUSTERING", name: "AUTO_CLUSTERING", value: 1 },
        { color: "chart-2", dataKey: "CLOUD_SERVICES", name: "CLOUD_SERVICES", value: 5 },
        { color: "chart-1", dataKey: "WAREHOUSE_METERING", name: "WAREHOUSE_METERING", value: 10 },
      ],
    };

    render(<Tooltip {...multiSeriesProps} />);

    const renderedNames = screen
      .getAllByText(/AUTO_CLUSTERING|CLOUD_SERVICES|WAREHOUSE_METERING/)
      .map((node) => node.textContent);

    expect(renderedNames).toEqual([
      "WAREHOUSE_METERING",
      "CLOUD_SERVICES",
      "AUTO_CLUSTERING",
    ]);
  });

  it("appends a Total row summing the values for multi-series points", () => {
    const Tooltip = createChartTooltip(usdFormatter);
    const multiSeriesProps: CustomTooltipProps = {
      active: true,
      label: "Jun 09",
      payload: [
        { color: "chart-1", dataKey: "WAREHOUSE_METERING", name: "WAREHOUSE_METERING", value: 10 },
        { color: "chart-2", dataKey: "CLOUD_SERVICES", name: "CLOUD_SERVICES", value: 5 },
      ],
    };

    render(<Tooltip {...multiSeriesProps} />);

    const totalLabel = screen.getByText("Total");
    expect(totalLabel).toBeInTheDocument();
    // 10 + 5 = 15, formatted with the same value formatter.
    expect(screen.getByText(usdFormatter(15))).toBeInTheDocument();
    // The total row is hairline-separated and weighted distinctly.
    expect(totalLabel.parentElement).toHaveClass("border-t", "font-medium");
  });

  it("coerces a non-numeric entry to 0 for both its row and the Total", () => {
    const Tooltip = createChartTooltip(usdFormatter);
    const multiSeriesProps: CustomTooltipProps = {
      active: true,
      label: "Jun 09",
      payload: [
        { color: "chart-1", dataKey: "WAREHOUSE_METERING", name: "WAREHOUSE_METERING", value: 10 },
        // A non-numeric value (e.g. a null/undefined gap) must read as 0 in the
        // row and contribute 0 to the Total, never rendering NaN.
        {
          color: "chart-2",
          dataKey: "CLOUD_SERVICES",
          name: "CLOUD_SERVICES",
          value: undefined as unknown as number,
        },
      ],
    };

    render(<Tooltip {...multiSeriesProps} />);

    // No NaN anywhere in the tooltip.
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
    // The non-numeric row displays $0, not NaN.
    expect(screen.getByText(usdFormatter(0))).toBeInTheDocument();
    // The Total row equals the sum of the displayed coerced values (10 + 0).
    const totalRow = screen.getByText("Total").parentElement;
    expect(totalRow).not.toBeNull();
    expect(within(totalRow as HTMLElement).getByText(usdFormatter(10))).toBeInTheDocument();
  });

  it("omits the Total row for single-series points", () => {
    const Tooltip = createChartTooltip(usdFormatter);

    render(<Tooltip {...sampleProps} />);

    expect(screen.queryByText("Total")).not.toBeInTheDocument();
  });
});

describe("stacked service spend ordering", () => {
  it("puts the largest-total series first (bottom of stack) with the first palette color", () => {
    // Tremor's BarChart emits categories[0] as the first stacked Recharts <Bar>,
    // which renders at the bottom/base of the stack. Ordering by descending total
    // therefore places the largest series at the bottom and, via positional
    // getSeriesColors, gives it chart-1.
    const categories = ["AUTO_CLUSTERING", "CLOUD_SERVICES", "WAREHOUSE_METERING"];
    const chartData = [
      { date: "Jun 01", AUTO_CLUSTERING: 1, CLOUD_SERVICES: 5, WAREHOUSE_METERING: 10 },
      { date: "Jun 02", AUTO_CLUSTERING: 2, CLOUD_SERVICES: 6, WAREHOUSE_METERING: 12 },
    ];

    const orderedCategories = orderCategoriesByTotal(categories, chartData);
    const colors = getSeriesColors(orderedCategories);

    expect(orderedCategories[0]).toBe("WAREHOUSE_METERING");
    expect(colors[0]).toBe("chart-1");
    expect(orderedCategories).toEqual([
      "WAREHOUSE_METERING",
      "CLOUD_SERVICES",
      "AUTO_CLUSTERING",
    ]);
  });
});
