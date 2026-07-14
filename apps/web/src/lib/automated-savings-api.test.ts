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

  it("maps 422 on managed-default to a floor error with API detail", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ detail: "must be at least 60 seconds" }), { status: 422 }));
    await expect(setManagedDefault("org-1", "WH1", 45, { accessToken: "t" }))
      .rejects.toMatchObject({
        name: "ManagedDefaultFloorError",
        message: "must be at least 60 seconds",
      } satisfies Partial<ManagedDefaultFloorError>);
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

  it("accepts null cluster counts and size/state/type from a Standard-edition account", async () => {
    // SHOW WAREHOUSES on Standard edition omits the Enterprise-only cluster
    // columns, so the API emits max_cluster_count: null (and size/state/type
    // can be null too). The parser must tolerate the nullable API contract
    // instead of throwing and blanking the whole page.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([{
      name: "WH3", size: null, state: null, type: null, supported: false,
      min_cluster_count: null, max_cluster_count: null, started_clusters: null,
      auto_resume_ok: false, managed_default: null, stored_default: null,
      enabled: false, drift_state: "ok", drifted_value: null, cooldown_ts: null,
      status: "unsupported",
    }]), { status: 200 }));
    const [row] = await fetchWarehouses("org-1", { accessToken: "t" });
    expect(row.size).toBeNull();
    expect(row.state).toBeNull();
    expect(row.type).toBeNull();
    expect(row.minClusterCount).toBeNull();
    expect(row.maxClusterCount).toBeNull();
    expect(row.startedClusters).toBeNull();
  });

  it("surfaces API detail on a non-422 setManagedDefault failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ detail: "warehouse enrollment disappeared" }),
      { status: 500 },
    ));
    await expect(setManagedDefault("org-1", "WH1", 120, { accessToken: "t" }))
      .rejects.toThrow(/500: warehouse enrollment disappeared/);
  });

  it("surfaces the API's error detail when the shared fetchJson helper hits a non-ok response", async () => {
    // fetchStatus (like fetchWarehouses/toggleWarehouse/reconcileWarehouse/
    // checkAccess/agree) goes through the shared fetchJson helper, distinct
    // from setManagedDefault's standalone fetch call above.
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
    ["managed default", () => setManagedDefault("org-1", "WH1", 120, { accessToken: "t" }), "/warehouses/WH1/managed-default", { value: 120 }],
    ["reconcile", () => reconcileWarehouse("org-1", "WH1", true, { accessToken: "t" }), "/warehouses/WH1/reconcile", { accept: true }],
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

  it("maps a missing role_name on check-access to null", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      grant_present: true, grant_checked_at: null,
    }), { status: 200 }));
    const result = await checkAccess("org-1", { accessToken: "t" });
    expect(result.roleName).toBeNull();
  });
});
