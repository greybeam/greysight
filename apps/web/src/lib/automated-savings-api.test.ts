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
      agreed: true, global_enabled: false, grant_present: true, role_name: "GREYSIGHT_RL",
    }), { status: 200 }));
    const status = await fetchStatus("org-1", { accessToken: "t" });
    expect(status).toEqual({
      agreed: true, globalEnabled: false, grantPresent: true, roleName: "GREYSIGHT_RL",
    });
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

  it("posts to agree", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await agree("org-1", { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/agree");
    expect(init?.method).toBe("POST");
  });

  it("puts the global switch state", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await setGlobalSwitch("org-1", true, { accessToken: "t" });
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.method).toBe("PUT");
    expect(init?.body).toBe(JSON.stringify({ enabled: true }));
  });

  it("puts the per-warehouse toggle state", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await toggleWarehouse("org-1", "WH1", false, { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/warehouses/WH1/toggle");
    expect(init?.body).toBe(JSON.stringify({ enabled: false }));
  });

  it("posts the reconcile decision", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    await reconcileWarehouse("org-1", "WH1", true, { accessToken: "t" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/reconcile");
    expect(init?.body).toBe(JSON.stringify({ accept: true }));
  });

  it("posts check-access and parses the returned status", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      agreed: true, global_enabled: true, grant_present: false, role_name: "GREYSIGHT_RL",
    }), { status: 200 }));
    const status = await checkAccess("org-1", { accessToken: "t" });
    expect(status.grantPresent).toBe(false);
  });

  it("throws when the underlying request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({}), { status: 500 }));
    await expect(agree("org-1", { accessToken: "t" })).rejects.toThrow(/500/);
  });
});
