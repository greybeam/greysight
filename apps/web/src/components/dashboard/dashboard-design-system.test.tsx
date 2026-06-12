import type { CustomTooltipProps } from "@tremor/react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  CapacityBalanceCard,
  createChartTooltip,
  createCurrencyTickFormatter,
  formatChartDateLabel,
} from "./dashboard-design-system";
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
        label="Current Balance"
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
