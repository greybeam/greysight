import { describe, expect, it } from "vitest";

import parseDashboardDatasets, {
  parseDashboardView,
  parseAIDetailViewModel,
} from "./dashboard-contracts";
import demoDashboardDatasets from "./demo-dashboard-data";

describe("parseDashboardDatasets", () => {
  it("accepts the demo dashboard response shape", () => {
    const parsed = parseDashboardDatasets(demoDashboardDatasets);

    expect(parsed.schema_version).toBe(1);
    expect(parsed.run.status).toBe("completed");
    expect(parsed.run.window_days).toBe(100);
    expect(parsed.run.started_at).toBe("2026-06-10T00:00:00Z");
    expect(parsed.run.completed_at).toBe("2026-06-10T00:00:00Z");
    expect(parsed.summary).toMatchObject({
      total_credits: 4428.898,
      average_daily_credits: 44.28898,
      estimated_monthly_credits: 1328.6694,
      storage_bytes: 9041760000000,
      estimated_monthly_storage_cost_usd: 226.044,
    });
    expect(parsed.metadata).toEqual({
      data_mode: "demo",
      account_locator: "DEMO123",
      currency: "USD",
      billing_through_date: "2026-06-08",
      account_usage_through_date: "2026-06-08",
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
    expect(parsed.datasets.capacity_balance_daily).toHaveLength(100);
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
    expect(parsed.datasets.capacity_balance_daily[0]).toEqual(
      expect.objectContaining({
        balance: expect.any(Number),
        currency: "USD",
        usage_date: expect.any(String),
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

describe("parseDashboardView", () => {
  const preparedViewPayload = {
    schema_version: 1,
    run: demoDashboardDatasets.run,
    range: {
      mode: "relative",
      window_days: 30,
      start_date: "2026-05-10",
      end_date: "2026-06-08",
    },
    projection_range: {
      start_date: "2026-05-10",
      end_date: "2026-06-08",
    },
    header: {
      data_mode_label: "Demo",
      account_locator: "DEMO123",
      currency: "USD",
      through_date: "2026-06-08",
      through_date_label: "Jun 8, 2026",
      freshness_label: "Demo data through Jun 8, 2026",
      estimated_credit_price_label: "$2.25 / credit",
      storage_price_label: "$25.00 / TB-month",
    },
    unsupported: null,
    capacity_balance: {
      current_balance: 11875.25,
      current_balance_label: "$11,875.25",
      current_balance_date: "2026-06-08",
      daily_series: [
        {
          date: "2026-06-08",
          balance: 11875.25,
          balance_label: "$11,875.25",
        },
      ],
      is_empty: false,
    },
    total_spend: {
      basis: "billed",
      total: 123.45,
      total_label: "$123.45",
      average_daily: 4.12,
      average_daily_label: "$4.12",
      projected_monthly: 127.72,
      projected_monthly_label: "$127.72",
      projection_basis_label: "latest 30 days",
      daily_series: [{ date: "2026-06-08", spend: 123.45, spend_label: "$123.45" }],
      top_driver: {
        name: "CLOUD_SERVICES",
        spend: 123.45,
        spend_label: "$123.45",
        credits: null,
      },
      is_empty: false,
    },
    warehouse_spend: {
      basis: "estimated",
      total: 12,
      total_label: "$12.00",
      daily_series: [{ date: "2026-06-08", values: { COMPUTE_WH: 12 } }],
      warehouse_names: ["COMPUTE_WH"],
      ranked_warehouses: [
        {
          name: "COMPUTE_WH",
          spend: 12,
          spend_label: "$12.00",
          credits: 4,
        },
      ],
      ranked_users: [],
      warehouse_bars: [],
      user_bars: [],
      is_empty: false,
    },
    storage_spend: {
      basis: "estimated",
      database_basis: "estimated",
      daily_series: [],
      databases: [],
      database_bars: [],
      is_empty: true,
    },
    service_spend: {
      basis: "billed",
      daily_series: [{ date: "2026-06-08", values: { CLOUD_SERVICES: 123.45 } }],
      service_names: ["CLOUD_SERVICES"],
      ranked_services: [],
      service_bars: [],
      is_empty: false,
    },
    detail_tables: {
      services: [],
      warehouses: [
        {
          name: "COMPUTE_WH",
          spend: 12,
          spend_label: "$12.00",
          credits: 4,
          credits_compute: 3,
          credits_total: 4,
        },
      ],
      users: [
        {
          name: "ANALYST",
          warehouse_name: "COMPUTE_WH",
          spend: 6,
          spend_label: "$6.00",
          credits: 2,
        },
      ],
      storage: [
        {
          name: "APP_DB",
          bytes: 1000,
          monthly_spend: 0.01,
          monthly_spend_label: "$0.01",
        },
      ],
    },
  };

  it("maps a prepared dashboard view response to camelCase fields", () => {
    const parsed = parseDashboardView(preparedViewPayload);

    expect(parsed.range.windowDays).toBe(30);
    expect(parsed.projectionRange.startDate).toBe("2026-05-10");
    expect(parsed.header.dataModeLabel).toBe("Demo");
    expect(parsed.totalSpend.dailySeries[0]).toEqual({
      date: "2026-06-08",
      spend: 123.45,
      spendLabel: "$123.45",
    });
    expect(parsed.capacityBalance.currentBalanceLabel).toBe("$11,875.25");
    expect(parsed.capacityBalance.dailySeries[0]).toEqual({
      date: "2026-06-08",
      balance: 11875.25,
      balanceLabel: "$11,875.25",
    });
    expect(parsed.serviceSpend.dailySeries[0]).toEqual({
      date: "2026-06-08",
      values: { CLOUD_SERVICES: 123.45 },
    });
    expect(parsed.warehouseSpend.basis).toBe("estimated");
    expect(parsed.warehouseSpend.total).toBe(12);
    expect(parsed.warehouseSpend.totalLabel).toBe("$12.00");
    expect(parsed.warehouseSpend.warehouseNames).toEqual(["COMPUTE_WH"]);
    expect(parsed.warehouseSpend.dailySeries[0]).toEqual({
      date: "2026-06-08",
      values: { COMPUTE_WH: 12 },
    });
    expect(parsed.detailTables.warehouses[0]?.creditsCompute).toBe(3);
    expect(parsed.detailTables.users[0]?.warehouseName).toBe("COMPUTE_WH");
    expect(parsed.detailTables.storage[0]?.monthlySpendLabel).toBe("$0.01");
  });

  it("defaults missing capacity balance on older prepared dashboard views", () => {
    const legacyPayload: Record<string, unknown> = { ...preparedViewPayload };
    delete legacyPayload.capacity_balance;

    const parsed = parseDashboardView(legacyPayload);

    expect(parsed.capacityBalance).toEqual({
      currentBalance: 0,
      currentBalanceLabel: "$0.00",
      currentBalanceDate: null,
      dailySeries: [],
      isEmpty: true,
    });
  });

  it("formats the fallback capacity balance label from the header currency", () => {
    const legacyPayload: Record<string, unknown> = {
      ...preparedViewPayload,
      header: {
        ...(preparedViewPayload.header as Record<string, unknown>),
        currency: "EUR",
      },
    };
    delete legacyPayload.capacity_balance;

    const parsed = parseDashboardView(legacyPayload);

    expect(parsed.capacityBalance.currentBalanceLabel).toBe("€0.00");
  });

  it("falls back to a zeroed storage total and empty series for legacy views", () => {
    // The shared preparedViewPayload's storage_spend predates the storage KPI
    // and stacked-series fields, so it exercises the legacy fallback directly.
    const parsed = parseDashboardView(preparedViewPayload);

    expect(parsed.storageSpend.total).toBe(0);
    expect(parsed.storageSpend.totalLabel).toBe("$0.00");
    expect(parsed.storageSpend.databaseNames).toEqual([]);
    expect(parsed.storageSpend.databaseDailySeries).toEqual([]);
  });

  it("formats the legacy storage fallback label from the header currency", () => {
    const legacyPayload: Record<string, unknown> = {
      ...preparedViewPayload,
      header: {
        ...(preparedViewPayload.header as Record<string, unknown>),
        currency: "EUR",
      },
    };

    const parsed = parseDashboardView(legacyPayload);

    expect(parsed.storageSpend.totalLabel).toBe("€0.00");
  });

  it("parses storage KPI, stacked series, and bytes_label from snake_case", () => {
    const parsed = parseDashboardView({
      ...preparedViewPayload,
      storage_spend: {
        basis: "estimated",
        database_basis: "estimated",
        total: 226.42,
        total_label: "$226.42",
        daily_series: [],
        database_names: ["RAW", "ANALYTICS", "APP"],
        database_daily_series: [
          {
            date: "2026-06-08",
            values: { RAW: 3.88, ANALYTICS: 2.48, APP: 1.19 },
          },
        ],
        databases: [
          {
            name: "RAW",
            bytes: 4657824000000,
            bytes_label: "4.7 TB",
            monthly_spend: 116.45,
            monthly_spend_label: "$116.45",
            period_spend: 116.44,
            period_spend_label: "$116.44",
          },
        ],
        database_bars: [],
        is_empty: false,
      },
    });

    expect(parsed.storageSpend.total).toBe(226.42);
    expect(parsed.storageSpend.totalLabel).toBe("$226.42");
    expect(parsed.storageSpend.databaseNames).toEqual([
      "RAW",
      "ANALYTICS",
      "APP",
    ]);
    expect(parsed.storageSpend.databaseDailySeries[0]).toEqual({
      date: "2026-06-08",
      values: { RAW: 3.88, ANALYTICS: 2.48, APP: 1.19 },
    });
    expect(parsed.storageSpend.databases[0]?.bytesLabel).toBe("4.7 TB");
    expect(parsed.storageSpend.databases[0]?.periodSpend).toBe(116.44);
    expect(parsed.storageSpend.databases[0]?.periodSpendLabel).toBe("$116.44");
  });

  it("parses storage stacked series and bytes_label from camelCase", () => {
    const parsed = parseDashboardView({
      ...preparedViewPayload,
      storage_spend: {
        basis: "estimated",
        databaseBasis: "estimated",
        total: 100,
        totalLabel: "$100.00",
        dailySeries: [],
        databaseNames: ["RAW"],
        databaseDailySeries: [{ date: "2026-06-08", values: { RAW: 100 } }],
        databases: [
          {
            name: "RAW",
            bytes: 1000000000000,
            bytesLabel: "1.0 TB",
            monthlySpend: 100,
            monthlySpendLabel: "$100.00",
            periodSpend: 95,
            periodSpendLabel: "$95.00",
          },
        ],
        databaseBars: [],
        isEmpty: false,
      },
    });

    expect(parsed.storageSpend.databaseNames).toEqual(["RAW"]);
    expect(parsed.storageSpend.databaseDailySeries[0]?.values).toEqual({
      RAW: 100,
    });
    expect(parsed.storageSpend.databases[0]?.bytesLabel).toBe("1.0 TB");
    expect(parsed.storageSpend.databases[0]?.periodSpend).toBe(95);
    expect(parsed.storageSpend.databases[0]?.periodSpendLabel).toBe("$95.00");
  });

  it("falls back to zero period spend and the monthly label for legacy storage rows", () => {
    // detail_tables.storage rows on the legacy payload omit the period fields;
    // the parser zeros the numeric value and reuses the monthly label as text.
    const parsed = parseDashboardView(preparedViewPayload);
    const row = parsed.detailTables.storage[0];

    expect(row?.periodSpend).toBe(0);
    expect(row?.periodSpendLabel).toBe("$0.01");
  });

  it("derives a bytes_label fallback for legacy storage rows", () => {
    // detail_tables.storage rows on the legacy payload omit bytes_label; the
    // parser humanizes the raw byte count (1000-base, one decimal) instead.
    const parsed = parseDashboardView(preparedViewPayload);

    expect(parsed.detailTables.storage[0]?.bytesLabel).toBe("1.0 KB");
  });

  it("rejects malformed prepared dashboard view responses", () => {
    expect(() =>
      parseDashboardView({
        ...preparedViewPayload,
        total_spend: {
          ...preparedViewPayload.total_spend,
          total: "123.45",
        },
      }),
    ).toThrow("Dashboard view response is invalid");
  });
});

describe("parseAIDetailViewModel", () => {
  it("parses snake_case detail payload", () => {
    const view = parseAIDetailViewModel({
      daily_series: [{ date: "2026-06-01", values: { CORTEX_ANALYST: 4 } }],
      consumption_type_names: ["CORTEX_ANALYST"],
      ranked_consumption_types: [
        { name: "CORTEX_ANALYST", spend: 4, spend_label: "$4.00", credits: 2 },
      ],
      consumption_bars: [
        { name: "CORTEX_ANALYST", spend: 4, spend_label: "$4.00", credits: 2, bar_width_percent: 100 },
      ],
      is_empty: false,
      partial: true,
      skipped_branches: ["cortex_code_cli"],
    });
    expect(view.consumptionTypeNames).toEqual(["CORTEX_ANALYST"]);
    expect(view.partial).toBe(true);
    expect(view.skippedBranches).toEqual(["cortex_code_cli"]);
    expect(view.consumptionBars[0].barWidthPercent).toBe(100);
  });
});
