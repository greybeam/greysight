import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { WarehouseWasteViewModel } from "../../lib/dashboard-contracts";
import { WasteEstimatorCard } from "./waste-estimator-card";

afterEach(() => {
  cleanup();
});

function wasteModel(
  overrides: Partial<WarehouseWasteViewModel> = {},
): WarehouseWasteViewModel {
  return {
    basis: "estimated",
    totalPeriodIdleSpend: 150,
    totalPeriodIdleSpendLabel: "$150.00",
    totalProjectedMonthlyIdleSpend: 450,
    totalProjectedMonthlyIdleSpendLabel: "$450.00",
    rows: [
      {
        name: "BI_WH",
        idlePct: 0.75,
        periodIdleSpend: 100,
        periodIdleSpendLabel: "$100.00",
        projectedMonthlyIdleSpend: 300,
        projectedMonthlyIdleSpendLabel: "$300.00",
      },
      {
        name: "ETL_WH",
        idlePct: 0.2,
        periodIdleSpend: 50,
        periodIdleSpendLabel: "$50.00",
        projectedMonthlyIdleSpend: 150,
        projectedMonthlyIdleSpendLabel: "$150.00",
      },
    ],
    isEmpty: false,
    ...overrides,
  };
}

describe("WasteEstimatorCard", () => {
  it("renders nothing when the prepared view reports no idle spend", () => {
    const { container } = render(
      <WasteEstimatorCard
        model={wasteModel({ isEmpty: true, rows: [], totalPeriodIdleSpend: 0 })}
      />,
    );

    expect(container.firstChild).toBeNull();
    expect(
      screen.queryByTestId("waste-estimator-auto-savings-cta"),
    ).not.toBeInTheDocument();
  });

  it("preserves server-supplied row order instead of re-sorting client-side", () => {
    render(
      <WasteEstimatorCard
        model={wasteModel({
          rows: [
            {
              name: "SMALL_IDLE",
              idlePct: 0.9,
              periodIdleSpend: 10,
              periodIdleSpendLabel: "$10.00",
              projectedMonthlyIdleSpend: 30,
              projectedMonthlyIdleSpendLabel: "$30.00",
            },
            {
              name: "BIG_IDLE",
              idlePct: 0.1,
              periodIdleSpend: 200,
              periodIdleSpendLabel: "$200.00",
              projectedMonthlyIdleSpend: 600,
              projectedMonthlyIdleSpendLabel: "$600.00",
            },
          ],
        })}
      />,
    );

    expect(
      screen
        .getAllByTestId("waste-estimator-row")
        .map((row) => row.getAttribute("data-warehouse-name")),
    ).toEqual(["SMALL_IDLE", "BIG_IDLE"]);
  });

  it("renders an em dash when idlePct is null instead of a numeric percentage", () => {
    render(
      <WasteEstimatorCard
        model={wasteModel({
          rows: [
            {
              name: "ADAPT_WH",
              idlePct: null,
              periodIdleSpend: 0,
              periodIdleSpendLabel: "$0.00",
              projectedMonthlyIdleSpend: 0,
              projectedMonthlyIdleSpendLabel: "$0.00",
            },
          ],
        })}
      />,
    );

    const idlePct = screen.getByTestId("waste-estimator-idle-pct");
    expect(idlePct).toHaveTextContent("–");
    expect(idlePct.textContent).not.toMatch(/NaN/);
    expect(idlePct.textContent).not.toMatch(/%/);
  });

  it("links the CTA to the Auto Savings page", () => {
    render(<WasteEstimatorCard model={wasteModel()} />);

    expect(screen.getByTestId("waste-estimator-auto-savings-cta")).toHaveAttribute(
      "href",
      "/automated-savings",
    );
  });
});
