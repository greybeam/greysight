import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import demoDashboardDatasets from "../../lib/demo-dashboard-data";
import DashboardPage from "./page";

describe("DashboardPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the dashboard run surface", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(demoDashboardDatasets), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    render(<DashboardPage />);

    expect(screen.getByText("Greysight")).toBeInTheDocument();
    expect(screen.getByText("Loading dashboard data")).toBeInTheDocument();
    expect(screen.getByLabelText("Loading dashboard")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run analysis" })).toBeDisabled();
  });
});
