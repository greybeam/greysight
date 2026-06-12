import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { HeaderViewModel } from "../../lib/dashboard-contracts";
import DashboardHeader from "./dashboard-header";

const headerViewModel: HeaderViewModel = {
  dataModeLabel: "Billed",
  accountLocator: "TU24199",
  currency: "USD",
  throughDate: "2026-06-08",
  throughDateLabel: "Jun 8, 2026",
  freshnessLabel: "Billing data through Jun 8, 2026",
  estimatedCreditPriceLabel: "$3.00",
  storagePriceLabel: "$23.00",
};

describe("DashboardHeader", () => {
  afterEach(() => {
    cleanup();
  });

  it("shows product, mode, data mode, account, and freshness", () => {
    render(
      <DashboardHeader
        header={headerViewModel}
        modeLabel="Local Snowflake"
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    expect(screen.getByText("Greysight")).toBeInTheDocument();
    expect(screen.getByText("Local Snowflake")).toBeInTheDocument();
    expect(screen.getByText("Billed")).toBeInTheDocument();
    expect(screen.getByText("TU24199")).toBeInTheDocument();
    expect(screen.getByText("Billing data through Jun 8, 2026")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run analysis" })).toBeInTheDocument();
  });

  it("shows the estimated-rate assumption in estimated mode", () => {
    render(
      <DashboardHeader
        header={{
          ...headerViewModel,
          dataModeLabel: "Estimated",
          throughDate: "2026-06-09",
          throughDateLabel: "Jun 9, 2026",
          freshnessLabel: "Account Usage data through Jun 9, 2026",
        }}
        modeLabel="Local Snowflake"
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    expect(screen.getByText("Account Usage data through Jun 9, 2026")).toBeInTheDocument();
    expect(
      screen.getByText("Estimated spend at $3.00/credit - billed data unavailable"),
    ).toBeInTheDocument();
  });

  it("labels demo freshness as demo data instead of billing data", () => {
    render(
      <DashboardHeader
        header={{
          ...headerViewModel,
          dataModeLabel: "Demo",
          freshnessLabel: "Demo data through Jun 8, 2026",
        }}
        modeLabel="Demo"
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    expect(screen.getByText("Demo data through Jun 8, 2026")).toBeInTheDocument();
    expect(
      screen.queryByText("Billing data through Jun 8, 2026"),
    ).not.toBeInTheDocument();
  });
});
