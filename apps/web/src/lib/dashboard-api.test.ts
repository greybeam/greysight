import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchDashboardDatasets,
  fetchDemoDashboardDatasets,
  pollDashboardRun,
} from "./dashboard-api";
import demoDashboardDatasets from "./demo-dashboard-data";

describe("dashboard-api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches and parses the API demo endpoint response", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(demoDashboardDatasets), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    const data = await fetchDemoDashboardDatasets();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/dashboard-runs/demo/datasets",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(data.run.status).toBe("completed");
    expect(data.datasets.service_spend_daily.length).toBeGreaterThan(0);
  });

  it("polls run metadata until completion and then fetches datasets", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "00000000-0000-0000-0000-000000000001",
            status: "running",
            source: "snowflake",
            window_days: 30,
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "00000000-0000-0000-0000-000000000001",
            status: "completed",
            source: "snowflake",
            window_days: 30,
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(demoDashboardDatasets), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );

    const run = await pollDashboardRun(
      "00000000-0000-0000-0000-000000000001",
      { intervalMs: 0, maxAttempts: 3 },
    );
    const data = await fetchDashboardDatasets(run.id);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/dashboard-runs/00000000-0000-0000-0000-000000000001",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/dashboard-runs/00000000-0000-0000-0000-000000000001/datasets",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(run.status).toBe("completed");
    expect(data.run.status).toBe("completed");
  });
});
