import { describe, expect, it } from "vitest";

import parseDashboardDatasets, { FETCH_WINDOW_DAYS } from "./dashboard-contracts";
import demoDashboardDatasets from "./demo-dashboard-data";

describe("parseDashboardDatasets", () => {
  it("accepts the demo dashboard response shape", () => {
    const parsed = parseDashboardDatasets(demoDashboardDatasets);

    expect(FETCH_WINDOW_DAYS).toBe(100);
    expect(parsed.schema_version).toBe(1);
    expect(parsed.run.status).toBe("completed");
    expect(parsed.run.window_days).toBe(100);
    expect(parsed.metadata).toEqual({
      data_mode: "demo",
      account_locator: "DEMO123",
      currency: "USD",
      billing_through_date: "2026-06-08",
      account_usage_through_date: "2026-06-09",
      estimated_credit_price_usd: 2.25,
      storage_price_usd_per_tb_month: 25,
      unsupported_reason: null,
      organization_usage: { available: true, detail: null },
      account_usage: { available: true, detail: null },
    });
    expect(parsed.datasets.account_spend_daily).toHaveLength(100);
    expect(parsed.datasets.service_spend_daily.length).toBeGreaterThan(0);
    expect(parsed.datasets.org_spend_daily.length).toBeGreaterThan(0);
    expect(parsed.datasets.rate_sheet_daily.length).toBeGreaterThan(0);
    expect(parsed.datasets.current_account).toEqual([
      { account_locator: "DEMO123" },
    ]);
    expect(parsed.datasets.account_spend_daily.at(0)?.usage_date).toBe(
      "2026-03-01",
    );
    expect(parsed.datasets.account_spend_daily.at(-1)?.usage_date).toBe(
      "2026-06-08",
    );
    expect(parsed.datasets.org_spend_daily[0]).toEqual(
      expect.objectContaining({
        billing_type: "CONSUMPTION",
        currency: "USD",
        is_adjustment: false,
        rating_type: expect.any(String),
        spend: expect.any(Number),
      }),
    );
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

  it("rejects payloads missing the current schema version", () => {
    const payload = {
      ...demoDashboardDatasets,
      schema_version: undefined,
    };

    expect(() => parseDashboardDatasets(payload)).toThrow(
      "Dashboard schema_version must be 1",
    );
  });

  it("rejects payloads missing metadata", () => {
    const payload = {
      ...demoDashboardDatasets,
      metadata: undefined,
    };

    expect(() => parseDashboardDatasets(payload)).toThrow(
      "Dashboard metadata is required",
    );
  });

  it("rejects payloads with unknown data modes", () => {
    const payload = {
      ...demoDashboardDatasets,
      metadata: {
        ...demoDashboardDatasets.metadata,
        data_mode: "live",
      },
    };

    expect(() => parseDashboardDatasets(payload)).toThrow(
      "Dashboard metadata data_mode is invalid",
    );
  });

  it("accepts mixed-currency unsupported metadata", () => {
    const parsed = parseDashboardDatasets({
      ...demoDashboardDatasets,
      metadata: {
        ...demoDashboardDatasets.metadata,
        data_mode: "billed",
        currency: null,
        unsupported_reason: "mixed_currency",
      },
    });

    expect(parsed.metadata.data_mode).toBe("billed");
    expect(parsed.metadata.currency).toBeNull();
    expect(parsed.metadata.unsupported_reason).toBe("mixed_currency");
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
