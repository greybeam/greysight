import { afterEach, describe, expect, it, vi } from "vitest";
import {
  agree,
  checkAccess,
  fetchStatus,
  fetchWarehouses,
  parseSuspensionEvent,
  parseSuspensionStatsBucket,
  setGlobalSwitch,
  toggleWarehouse,
} from "./automated-savings-api";
import { DashboardApiError } from "./dashboard-errors";

describe("automated-savings-api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends the bearer token", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }));
    await fetchWarehouses("org-1", { accessToken: "tok" });
    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer tok");
  });

  it("maps the complete warehouse contract to camelCase", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([{
      name: "WH1", size: "X-Small", state: "STARTED", type: "STANDARD", supported: true,
      min_cluster_count: 1, max_cluster_count: 2, started_clusters: 1, auto_resume_ok: true,
      auto_suspend: 300, quiescing: 0, enabled: true, status: "idle",
    }]), { status: 200 }));

    const [row] = await fetchWarehouses("org-1", { accessToken: "t" });

    expect(row).toEqual({
      name: "WH1",
      size: "X-Small",
      state: "STARTED",
      type: "STANDARD",
      supported: true,
      minClusterCount: 1,
      maxClusterCount: 2,
      startedClusters: 1,
      autoResumeOk: true,
      autoSuspend: 300,
      quiescing: 0,
      enabled: true,
      status: "idle",
    });
  });

  it("maps snake_case status JSON to camelCase AutomatedSavingsStatus", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      agreed: true, global_enabled: false, grant_present: true, grant_checked_at: "2026-01-01T00:00:00Z",
      role_name: "GREYSIGHT_ROLE",
    }), { status: 200 }));
    const status = await fetchStatus("org-1", { accessToken: "t" });
    expect(status).toEqual({
      agreed: true, globalEnabled: false, grantPresent: true, grantCheckedAt: "2026-01-01T00:00:00Z",
      roleName: "GREYSIGHT_ROLE",
    });
  });

  it("maps a missing role_name on status to null", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      agreed: true, global_enabled: false, grant_present: true, grant_checked_at: null,
    }), { status: 200 }));
    const status = await fetchStatus("org-1", { accessToken: "t" });
    expect(status.roleName).toBeNull();
  });

  it("accepts nullable live warehouse fields", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([{
      name: "WH2", size: null, state: null, type: null, supported: false,
      min_cluster_count: null, max_cluster_count: null, started_clusters: null,
      auto_resume_ok: false, auto_suspend: null, quiescing: null,
      enabled: false, status: "unsupported",
    }]), { status: 200 }));
    const [row] = await fetchWarehouses("org-1", { accessToken: "t" });
    expect(row).toMatchObject({
      size: null,
      state: null,
      type: null,
      minClusterCount: null,
      maxClusterCount: null,
      startedClusters: null,
      autoSuspend: null,
      quiescing: null,
    });
  });

  it("rejects a warehouse response with an unknown status", async () => {
    const raw = {
      name: "WH1", size: "X-Small", state: "STARTED", type: "STANDARD", supported: true,
      min_cluster_count: 1, max_cluster_count: 2, started_clusters: 1, auto_resume_ok: true,
      auto_suspend: 300, quiescing: 0, enabled: true, status: "mid_suspend",
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([raw]), { status: 200 }),
    );

    await expect(fetchWarehouses("org-1", { accessToken: "t" }))
      .rejects.toThrow("Malformed automated-savings API response");
  });

  it("surfaces only the API's structured user-safe error detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: { user_safe_message: "Snowflake network policy blocked access." },
        }),
        { status: 502 },
      ),
    );

    const error = await fetchStatus("org-1", { accessToken: "t" }).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(DashboardApiError);
    expect((error as DashboardApiError).userSafeMessage).toBe(
      "Snowflake network policy blocked access.",
    );
  });

  it("surfaces a plain string FastAPI detail as the user-safe message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "Could not list Snowflake warehouses." }),
        { status: 502 },
      ),
    );

    const error = await fetchWarehouses("org-1", { accessToken: "t" }).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(DashboardApiError);
    expect((error as DashboardApiError).userSafeMessage).toBe(
      "Could not list Snowflake warehouses.",
    );
  });

  it.each([
    ["agree", () => agree("org-1", { accessToken: "t" }), "/agree", undefined],
    ["global switch", () => setGlobalSwitch("org-1", true, { accessToken: "t" }), "/global-switch", { enabled: true }],
    ["warehouse toggle", () => toggleWarehouse("org-1", "WH1", false, { accessToken: "t" }), "/warehouses/WH1/toggle", { enabled: false }],
  ])("posts the %s mutation contract", async (_name, invoke, route, body) => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await invoke();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain(`/api/automated-savings/org-1${route}`);
    expect(init?.method).toBe("POST");
    if (body) expect(init?.body).toBe(JSON.stringify(body));
  });

  it("posts check-access to the API's actual route and parses the smaller shape", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      grant_present: false, grant_checked_at: "2026-01-01T00:00:00Z", role_name: "GREYSIGHT_ROLE",
    }), { status: 200 }));
    const result = await checkAccess("org-1", { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/automated-savings/org-1/check-access");
    expect(init?.method).toBe("POST");
    expect(result).toEqual({
      grantPresent: false, grantCheckedAt: "2026-01-01T00:00:00Z", roleName: "GREYSIGHT_ROLE",
    });
  });
});

const validEvent = {
  id: "42",
  created_at: "2026-07-15T10:00:00+00:00",
  warehouse_name: "COMPUTE_WH",
  action: "suspend",
  reason: "idle",
  observed_started_clusters: 1,
  observed_resumed_on: "2026-07-15T08:00:00+00:00",
  observed_at: "2026-07-15T09:59:00+00:00",
};

describe("parseSuspensionEvent", () => {
  it("maps snake_case fields and preserves nullables", () => {
    expect(parseSuspensionEvent(validEvent)).toEqual({
      id: "42",
      createdAt: "2026-07-15T10:00:00+00:00",
      warehouseName: "COMPUTE_WH",
      action: "suspend",
      reason: "idle",
      observedStartedClusters: 1,
      observedResumedOn: "2026-07-15T08:00:00+00:00",
      observedAt: "2026-07-15T09:59:00+00:00",
    });
    expect(
      parseSuspensionEvent({
        ...validEvent,
        observed_started_clusters: null,
        observed_resumed_on: null,
      }),
    ).toMatchObject({ observedStartedClusters: null, observedResumedOn: null });
  });

  it.each([
    ["numeric id", { ...validEvent, id: 42 }],
    ["missing id", { ...validEvent, id: undefined }],
    ["empty warehouse", { ...validEvent, warehouse_name: "" }],
    ["non-string timestamp", { ...validEvent, observed_at: 12345 }],
    ["unparseable timestamp", { ...validEvent, created_at: "yesterday" }],
    ["timezone-naive timestamp", { ...validEvent, observed_at: "2026-07-15T09:59:00" }],
    ["rollover calendar date", { ...validEvent, observed_at: "2026-02-30T09:59:00Z" }],
    ["string clusters", { ...validEvent, observed_started_clusters: "1" }],
    ["non-object", "not-an-event"],
  ])("rejects %s", (_label, raw) => {
    expect(() => parseSuspensionEvent(raw)).toThrow(
      "Malformed automated-savings API response",
    );
  });

  it.each([
    ["observed_started_clusters", { ...validEvent }],
    ["observed_resumed_on", { ...validEvent }],
  ])("rejects a missing nullable key %s", (key, base) => {
    const raw = { ...base } as Record<string, unknown>;
    delete raw[key];
    expect(() => parseSuspensionEvent(raw)).toThrow(
      "Malformed automated-savings API response",
    );
  });
});

describe("parseSuspensionStatsBucket", () => {
  it("parses a valid bucket", () => {
    expect(
      parseSuspensionStatsBucket({
        day: "2026-07-15",
        counts: { ANALYTICS_WH: 0, COMPUTE_WH: 3 },
      }),
    ).toEqual({ day: "2026-07-15", counts: { ANALYTICS_WH: 0, COMPUTE_WH: 3 } });
  });

  it("parses an empty counts record", () => {
    expect(
      parseSuspensionStatsBucket({ day: "2026-07-15", counts: {} }),
    ).toEqual({ day: "2026-07-15", counts: {} });
  });

  it.each([
    ["missing day", { counts: { COMPUTE_WH: 3 } }],
    ["empty day", { day: "", counts: { COMPUTE_WH: 3 } }],
    ["non-canonical day format", { day: "07-15-2026", counts: { COMPUTE_WH: 3 } }],
    ["calendar-invalid day", { day: "2026-02-30", counts: { COMPUTE_WH: 3 } }],
    ["non-record counts", { day: "2026-07-15", counts: "not-a-record" }],
    ["missing counts", { day: "2026-07-15" }],
    [
      "negative count value",
      { day: "2026-07-15", counts: { COMPUTE_WH: -1 } },
    ],
    [
      "non-integer count value",
      { day: "2026-07-15", counts: { COMPUTE_WH: 1.5 } },
    ],
    [
      "string count value",
      { day: "2026-07-15", counts: { COMPUTE_WH: "3" } },
    ],
  ])("rejects %s", (_label, raw) => {
    expect(() => parseSuspensionStatsBucket(raw)).toThrow(
      "Malformed automated-savings API response",
    );
  });
});
