import { describe, expect, it } from "vitest";
import demoDashboardView from "./demo-dashboard-view";

describe("demoDashboardView", () => {
  it("reflects the full demo dataset's database bucketing (>14 databases)", () => {
    expect(demoDashboardView.storageSpend.databaseNames.length).toBeGreaterThan(14);
  });
});
