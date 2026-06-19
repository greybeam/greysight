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
  const originalBrand = process.env.NEXT_PUBLIC_BRAND;

  afterEach(() => {
    cleanup();
    if (originalBrand === undefined) {
      delete process.env.NEXT_PUBLIC_BRAND;
    } else {
      process.env.NEXT_PUBLIC_BRAND = originalBrand;
    }
  });

  it("shows the product and run action", () => {
    delete process.env.NEXT_PUBLIC_BRAND;
    render(
      <DashboardHeader
        header={headerViewModel}
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    expect(screen.getByText("Greybeam")).toBeInTheDocument();
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
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    expect(
      screen.getByText("Estimated spend at $3.00/credit - billed data unavailable"),
    ).toBeInTheDocument();
  });

  it("omits the Greybeam logo image when NEXT_PUBLIC_BRAND is unset", () => {
    delete process.env.NEXT_PUBLIC_BRAND;
    render(
      <DashboardHeader
        header={headerViewModel}
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    // No logo image, but the Greybeam wordmark text still renders in every build.
    expect(screen.queryByAltText("Greybeam")).not.toBeInTheDocument();
    expect(screen.getByText("Greybeam")).toBeInTheDocument();
  });

  it("shows the Greybeam logo image when NEXT_PUBLIC_BRAND=greybeam", () => {
    process.env.NEXT_PUBLIC_BRAND = "greybeam";
    render(
      <DashboardHeader
        header={headerViewModel}
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    const logo = screen.getByAltText("Greybeam");
    expect(logo).toBeInTheDocument();
    expect(logo).toHaveAttribute("src", "/greybeam_assets/greybeam_logo.svg");
    expect(screen.getByText("Greybeam")).toBeInTheDocument();
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

  it("shows the idle run label when no run is in flight", () => {
    render(
      <DashboardHeader
        header={headerViewModel}
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    const runButton = screen.getByRole("button", { name: "Run analysis" });
    expect(runButton).toHaveAttribute("aria-busy", "false");
  });

  it("no longer renders the freshness / billing-through label", () => {
    render(
      <DashboardHeader
        header={headerViewModel}
        runDisabled={false}
        onRun={vi.fn()}
      />,
    );

    expect(
      screen.queryByText("Billing data through Jun 8, 2026"),
    ).not.toBeInTheDocument();
  });
});
