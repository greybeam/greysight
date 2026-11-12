import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SavingsChart } from "./savings-chart";

const fetchSavingsStatsMock = vi.fn();

vi.mock("../../lib/automated-savings-api", async () => {
  const actual = await vi.importActual<
    typeof import("../../lib/automated-savings-api")
  >("../../lib/automated-savings-api");
  return {
    ...actual,
    fetchSavingsStats: (...args: unknown[]) => fetchSavingsStatsMock(...args),
  };
});

function renderChart() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SavingsChart orgId="org-1" accessToken="t" />
    </QueryClientProvider>,
  );
}

describe("SavingsChart", () => {
  afterEach(() => {
    cleanup();
    fetchSavingsStatsMock.mockReset();
  });

  it("renders the estimated savings KPIs and assumption note", async () => {
    fetchSavingsStatsMock.mockResolvedValue({
      days: 7,
      pricePerCredit: 3,
      totalDollars: 1234.5,
      totalAvoidedHours: 12.25,
      totalSuspensions: 42,
      assumedSettingsCount: 2,
      warehouses: ["WH1"],
      buckets: [{ day: "2026-07-09", dollars: { WH1: 1234.5 } }],
    });

    renderChart();

    expect(await screen.findByText("$1,234.50")).toBeInTheDocument();
    expect(screen.getByText("12.3 h")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText(/\$3\.00 per credit/)).toBeInTheDocument();
    expect(screen.getByText(/2 older suspensions/)).toBeInTheDocument();
  });

  it("shows an empty state when nothing was suspended", async () => {
    fetchSavingsStatsMock.mockResolvedValue({
      days: 7,
      pricePerCredit: 3,
      totalDollars: 0,
      totalAvoidedHours: 0,
      totalSuspensions: 0,
      assumedSettingsCount: 0,
      warehouses: [],
      buckets: [],
    });

    renderChart();

    expect(
      await screen.findByText(/No recorded suspensions in the last 7 days/),
    ).toBeInTheDocument();
  });
});
