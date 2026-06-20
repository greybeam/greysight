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

  // "prefers connection account locator" and "shows locator before any run" were
  // dropped: after #31 DashboardHeader no longer accepts/renders accountLocator —
  // that responsibility moved to AccountSwitcher (covered in account-switcher.test.tsx).

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
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    expect(
      screen.getByText("Estimated spend at $3.00/credit - billed data unavailable"),
    ).toBeInTheDocument();
  });

  it("shows a running spinner on the run button while a run is in flight", () => {
    render(
      <DashboardHeader
        header={headerViewModel}
        runDisabled={true}
        running={true}
        onRun={vi.fn()}
      />,
    );

    const runButton = screen.getByRole("button", { name: /Running/ });
    expect(runButton).toBeDisabled();
    expect(runButton).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("button", { name: "Run analysis" })).not.toBeInTheDocument();
  });

});
