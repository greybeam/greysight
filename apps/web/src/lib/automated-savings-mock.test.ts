import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchSavingsStats,
  fetchStatus,
  fetchSuspensionEvents,
  fetchSuspensionStats,
  fetchWarehouses,
  toggleWarehouse,
} from "./automated-savings-api";
import {
  isAutoSavingsMockEnabled,
  MOCK_ORG_ID,
} from "./automated-savings-mock";

describe("Auto Savings mock mode", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("is off unless the flag is exactly true", () => {
    expect(isAutoSavingsMockEnabled(undefined)).toBe(false);
    expect(isAutoSavingsMockEnabled("1")).toBe(false);
    expect(isAutoSavingsMockEnabled(" TRUE ")).toBe(true);
  });

  it("serves every read through the real parsers without hitting the network", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTO_SAVINGS_MOCK", "true");
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const status = await fetchStatus(MOCK_ORG_ID);
    const warehouses = await fetchWarehouses(MOCK_ORG_ID);
    const suspensions = await fetchSuspensionStats(MOCK_ORG_ID, 7);
    const savings = await fetchSavingsStats(MOCK_ORG_ID, 7);
    const page1 = await fetchSuspensionEvents(MOCK_ORG_ID);
    const page2 = await fetchSuspensionEvents(MOCK_ORG_ID, page1.nextCursor);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(status.agreed).toBe(true);
    expect(warehouses).toHaveLength(41);
    expect(suspensions.buckets).toHaveLength(7);
    expect(savings.buckets).toHaveLength(7);
    expect(savings.totalDollars).toBeGreaterThan(0);
    expect(page1.events).toHaveLength(25);
    expect(page2.events[0].observedAt < page1.events[24].observedAt).toBe(true);
  });

  it("reflects warehouse toggles in later reads", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTO_SAVINGS_MOCK", "true");

    await toggleWarehouse(MOCK_ORG_ID, "WH_DEV", true);
    const rows = await fetchWarehouses(MOCK_ORG_ID);
    await toggleWarehouse(MOCK_ORG_ID, "WH_DEV", false);

    expect(rows.find((row) => row.name === "WH_DEV")?.enabled).toBe(true);
  });
});
