import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchDashboardDatasets,
  fetchDemoDashboardDatasets,
  pollDashboardRun,
  startDashboardRun,
} from "./dashboard-api";
import demoDashboardDatasets from "./demo-dashboard-data";

describe("dashboard-api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches demo dashboard datasets", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(demoDashboardDatasets), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    const data = await fetchDemoDashboardDatasets();

    expect(data.run.id).toBe(demoDashboardDatasets.run.id);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/dashboard-runs/demo/datasets",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("sends bearer auth when fetching run datasets with an access token", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(demoDashboardDatasets), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await fetchDashboardDatasets("run-123", { accessToken: "token-123" });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("authorization")).toBe(
      "Bearer token-123",
    );
  });

  it("preserves json headers and sends bearer auth when starting a run", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(demoDashboardDatasets.run), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await startDashboardRun(
      { organizationId: "org-123", windowDays: 7 },
      { accessToken: "token-123" },
    );

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("authorization")).toBe("Bearer token-123");
    expect(headers.get("content-type")).toBe("application/json");
    expect(init.body).toBe(
      JSON.stringify({
        organization_id: "org-123",
        source: "snowflake",
        window_days: 7,
      }),
    );
  });

  it("polls until a run reaches a terminal state", async () => {
    const runningRun = { ...demoDashboardDatasets.run, status: "running" };
    const completedRun = { ...demoDashboardDatasets.run, status: "completed" };
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(runningRun), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(completedRun), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );

    const run = await pollDashboardRun("run-123", {
      intervalMs: 0,
      maxAttempts: 2,
    });

    expect(run.status).toBe("completed");
  });
});
