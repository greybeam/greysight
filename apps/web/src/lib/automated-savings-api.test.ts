import { afterEach, describe, expect, it, vi } from "vitest";
import {
  agree,
  checkAccess,
  fetchStatus,
  fetchWarehouses,
  reconcileWarehouse,
  setGlobalSwitch,
  setManagedDefault,
  toggleWarehouse,
  ManagedDefaultFloorError,
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

  it("maps 422 on managed-default to a floor error", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ detail: "floor" }), { status: 422 }));
    await expect(setManagedDefault("org-1", "WH1", 45, { accessToken: "t" }))
      .rejects.toBeInstanceOf(ManagedDefaultFloorError);
  });

  it("maps snake_case API JSON to camelCase WarehouseRow", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([{
      name: "WH1", size: "X-Small", state: "STARTED", type: "STANDARD", supported: true,
      min_cluster_count: 1, max_cluster_count: 2, started_clusters: 1, auto_resume_ok: true,
      managed_default: 300, stored_default: 300, enabled: true, drift_state: "ok",
      drifted_value: null, cooldown_ts: null, status: "idle",
    }]), { status: 200 }));
    const [row] = await fetchWarehouses("org-1", { accessToken: "t" });
    expect(row.minClusterCount).toBe(1);
    expect(row.autoResumeOk).toBe(true);
    expect(row.managedDefault).toBe(300);
    expect(row.driftState).toBe("ok");
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

  it("fetches status from the API's actual status route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      agreed: true, global_enabled: false, grant_present: true, grant_checked_at: null,
    }), { status: 200 }));
    await fetchStatus("org-1", { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/automated-savings/org-1/status");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("accepts null managed_default/stored_default without throwing", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([{
      name: "WH2", size: "X-Small", state: "SUSPENDED", type: "STANDARD", supported: true,
      min_cluster_count: 1, max_cluster_count: 2, started_clusters: 0, auto_resume_ok: true,
      managed_default: null, stored_default: null, enabled: false, drift_state: "ok",
      drifted_value: null, cooldown_ts: null, status: "idle",
    }]), { status: 200 }));
    const [row] = await fetchWarehouses("org-1", { accessToken: "t" });
    expect(row.managedDefault).toBeNull();
    expect(row.storedDefault).toBeNull();
  });

  it("fetches warehouses from the API's actual warehouses route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }));
    await fetchWarehouses("org-1", { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/automated-savings/org-1/warehouses");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("throws on a non-ok response other than 422 from setManagedDefault", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({}), { status: 500 }));
    await expect(setManagedDefault("org-1", "WH1", 120, { accessToken: "t" }))
      .rejects.toThrow(/500/);
  });

  it("resolves setManagedDefault on a 200 response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await expect(setManagedDefault("org-1", "WH1", 120, { accessToken: "t" })).resolves.toBeUndefined();
  });

  it("throws when fetchWarehouses returns a non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({}), { status: 500 }));
    await expect(fetchWarehouses("org-1", { accessToken: "t" })).rejects.toThrow(/500/);
  });

  it("posts to agree at the API's actual route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await agree("org-1", { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/automated-savings/org-1/agree");
    expect(init?.method).toBe("POST");
  });

  it("posts the global switch state to the API's actual route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await setGlobalSwitch("org-1", true, { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/automated-savings/org-1/global-switch");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ enabled: true }));
  });

  it("posts the per-warehouse toggle state to the API's actual route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await toggleWarehouse("org-1", "WH1", false, { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/automated-savings/org-1/warehouses/WH1/toggle");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ enabled: false }));
  });

  it("posts the managed-default value to the API's actual route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await setManagedDefault("org-1", "WH1", 120, { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/automated-savings/org-1/warehouses/WH1/managed-default");
    expect(init?.method).toBe("POST");
  });

  it("posts the reconcile decision to the API's actual route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await reconcileWarehouse("org-1", "WH1", true, { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/automated-savings/org-1/warehouses/WH1/reconcile");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ accept: true }));
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

  it("maps a missing role_name on check-access to null", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      grant_present: true, grant_checked_at: null,
    }), { status: 200 }));
    const result = await checkAccess("org-1", { accessToken: "t" });
    expect(result.roleName).toBeNull();
  });

  it("throws when the underlying request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({}), { status: 500 }));
    await expect(agree("org-1", { accessToken: "t" })).rejects.toThrow(/500/);
  });
});
