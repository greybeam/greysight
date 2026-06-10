import { describe, expect, it } from "vitest";

import parseDashboardDatasets from "./dashboard-contracts";
import demoDashboardDatasets from "./demo-dashboard-data";

describe("parseDashboardDatasets", () => {
  it("accepts the demo dashboard response shape", () => {
    const parsed = parseDashboardDatasets(demoDashboardDatasets);

    expect(parsed.run.status).toBe("completed");
    expect(parsed.datasets.service_spend_daily.length).toBeGreaterThan(0);
  });

  it("rejects payloads missing required datasets", () => {
    const payload = {
      ...demoDashboardDatasets,
      datasets: {
        ...demoDashboardDatasets.datasets,
        service_spend_daily: undefined,
      },
    };

    expect(() => parseDashboardDatasets(payload)).toThrow(
      "Dashboard dataset service_spend_daily is required",
    );
  });
});
