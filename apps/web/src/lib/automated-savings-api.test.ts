import { afterEach, describe, expect, it, vi } from "vitest";
import {
  agree,
  checkAccess,
  fetchStatus,
  fetchWarehouses,
  setGlobalSwitch,
  toggleWarehouse,
} from "./automated-savings-api";

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

  it("surfaces the API's error detail when the shared fetchJson helper hits a non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "org not found" }), { status: 500 }),
    );
    await expect(fetchStatus("org-1", { accessToken: "t" }))
      .rejects.toThrow(/500: org not found/);
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
