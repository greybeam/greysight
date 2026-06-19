import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchDashboardDatasets,
  fetchDashboardSource,
  fetchDashboardView,
  fetchDemoDashboardDatasets,
  fetchDemoDashboardView,
  pollDashboardRun,
  pollUntilTerminal,
  startDashboardRun,
  triggerDashboardSource,
} from "./dashboard-api";
import * as contracts from "./dashboard-contracts";
import demoDashboardDatasets from "./demo-dashboard-data";
import demoDashboardView from "./demo-dashboard-view";

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

  it("fetches demo prepared dashboard view", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(demoDashboardView), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    const view = await fetchDemoDashboardView({ windowDays: 30 });

    expect(view.header.dataModeLabel).toBe("Demo");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/dashboard-runs/demo/view?window_days=30",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("fetches run prepared view with bearer auth and custom range", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(demoDashboardView), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await fetchDashboardView(
      "run-123",
      { startDate: "2026-06-01", endDate: "2026-06-08" },
      { accessToken: "token-123" },
    );

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/dashboard-runs/run-123/view?start_date=2026-06-01&end_date=2026-06-08",
    );
    expect(new Headers(init.headers).get("authorization")).toBe(
      "Bearer token-123",
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

  it("throws a user-safe error when a dashboard request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("nope", { status: 503 }),
    );

    await expect(fetchDemoDashboardDatasets()).rejects.toThrow(
      "Dashboard API request failed with 503",
    );
  });

  it("throws when polling does not reach a terminal state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ ...demoDashboardDatasets.run, status: "running" }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );

    await expect(
      pollDashboardRun("run-123", { intervalMs: 0, maxAttempts: 1 }),
    ).rejects.toThrow("Dashboard run polling timed out");
  });
});

describe("pollUntilTerminal", () => {
  afterEach(() => vi.restoreAllMocks());

  it("polls until completed and reports every provisional view", async () => {
    const running = makeView("running", { overview: "pending", warehouse: "ready", storage: "pending" });
    const done = makeView("completed", { overview: "ready", warehouse: "ready", storage: "ready" });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(running), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(done), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.spyOn(contracts, "parseDashboardView").mockImplementation((p) => p as contracts.DashboardView);

    const seen: string[] = [];
    const result = await pollUntilTerminal(
      () => fetchDashboardView("run-1", { windowDays: 30 }),
      (view) => view.run.status === "completed",
      { intervalMs: 0, onResult: (v) => seen.push(v.run.status) },
    );

    expect(seen).toEqual(["running", "completed"]);
    expect(result.run.status).toBe("completed");
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("throws when maxAttempts is exhausted before terminal status", async () => {
    const running = makeView("running", { overview: "pending", warehouse: "pending", storage: "pending" });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(running), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    vi.spyOn(contracts, "parseDashboardView").mockImplementation((p) => p as contracts.DashboardView);

    await expect(
      pollUntilTerminal(
        () => fetchDashboardView("run-1", { windowDays: 30 }),
        (view) => view.run.status === "completed",
        { intervalMs: 0, maxAttempts: 2 },
      ),
    ).rejects.toThrow(/timed out/i);

    // Polling must stop at exactly maxAttempts — one fetch per attempt, no more
    // (no over-polling past the cap) and no fewer (no early bail).
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});

function makeView(status: string, sectionStatuses: Record<string, string>) {
  return { run: { id: "run-1", status }, sectionStatuses } as unknown as contracts.DashboardView;
}

describe("dashboard source api", () => {
  afterEach(() => vi.restoreAllMocks());

  it("encodes window_days on GET", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "pending" }), { status: 200 }),
    );
    await fetchDashboardSource("run1", "ai_consumption_daily", { windowDays: 30 });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("/api/dashboard-runs/run1/sources/ai_consumption_daily");
    expect(url).toContain("window_days=30");
  });

  it("encodes custom range on GET", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "pending" }), { status: 200 }),
    );
    await fetchDashboardSource("run1", "ai_consumption_daily", {
      startDate: "2026-05-01",
      endDate: "2026-05-31",
    });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("start_date=2026-05-01");
    expect(url).toContain("end_date=2026-05-31");
  });

  it("POST triggers the source", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "completed" }), { status: 202 }),
    );
    await triggerDashboardSource("run1", "ai_consumption_daily");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
  });
});
