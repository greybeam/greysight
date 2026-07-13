import { describe, expect, it, vi } from "vitest";
import { fetchWarehouses, setManagedDefault, ManagedDefaultFloorError } from "./automated-savings-api";

describe("automated-savings-api", () => {
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
});
