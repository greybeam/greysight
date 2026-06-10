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

  it("rejects payloads with malformed nested run data", () => {
    const payload = {
      ...demoDashboardDatasets,
      run: {
        ...demoDashboardDatasets.run,
        window_days: "30",
      },
    };

    expect(() => parseDashboardDatasets(payload)).toThrow(
      "Dashboard run response is invalid",
    );
  });

  it("rejects payloads with unknown nested run statuses", () => {
    const payload = {
      ...demoDashboardDatasets,
      run: {
        ...demoDashboardDatasets.run,
        status: "done",
      },
    };

    expect(() => parseDashboardDatasets(payload)).toThrow(
      "Dashboard run response is invalid",
    );
  });

  it("rejects payloads with missing required summary numbers", () => {
    const payload = {
      ...demoDashboardDatasets,
      summary: {
        ...demoDashboardDatasets.summary,
        storage_bytes: undefined,
      },
    };

    expect(() => parseDashboardDatasets(payload)).toThrow(
      "Dashboard summary storage_bytes is required",
    );
  });

  it("rejects payloads with malformed required summary numbers", () => {
    const payload = {
      ...demoDashboardDatasets,
      summary: {
        ...demoDashboardDatasets.summary,
        total_credits: "12.5",
      },
    };

    expect(() => parseDashboardDatasets(payload)).toThrow(
      "Dashboard summary total_credits must be a number",
    );
  });

  it("accepts payloads without the optional estimated monthly storage cost", () => {
    const payload = {
      ...demoDashboardDatasets,
      summary: {
        ...demoDashboardDatasets.summary,
        estimated_monthly_storage_cost_usd: undefined,
      },
    };

    const parsed = parseDashboardDatasets(payload);

    expect(parsed.summary.estimated_monthly_storage_cost_usd).toBeUndefined();
  });
});
