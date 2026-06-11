import { describe, expect, it } from "vitest";

import {
  DASHBOARD_DETAIL_ROW_LIMIT,
  DASHBOARD_RANKED_BAR_LIMIT,
  DEFAULT_WINDOW_DAYS,
  WINDOW_DAYS,
  buildDashboardViewModel,
  buildRateIndex,
  creditsToDollars,
  formatCurrency,
  formatUsageDate,
  storageBytesToDailyDollars,
  throughDateFor,
  windowStartFor,
  type DashboardTransformMetadata,
} from "./dashboard-transforms";
import demoDashboardData from "./demo-dashboard-data";
import type {
  DashboardData,
  OrgSpendDaily,
  RateSheetDaily,
} from "./dashboard-contracts";

const metadata: DashboardTransformMetadata = {
  data_mode: "billed",
  account_locator: "TU24199",
  currency: "USD",
  billing_through_date: "2026-06-08",
  account_usage_through_date: "2026-06-09",
  estimated_credit_price_usd: 3,
  storage_price_usd_per_tb_month: 23,
  unsupported_reason: null,
  organization_usage: { available: true, detail: null },
  account_usage: { available: true, detail: null },
};

describe("window constants", () => {
  it("exports the supported local dashboard windows", () => {
    expect(DEFAULT_WINDOW_DAYS).toBe(30);
    expect(WINDOW_DAYS).toEqual([7, 30, 90]);
  });
});

describe("formatCurrency", () => {
  it("formats dollars with two decimal places", () => {
    expect(formatCurrency(1234.5, "USD")).toBe("$1,234.50");
  });
});

describe("formatUsageDate", () => {
  it("formats usage dates in UTC without local timezone drift", () => {
    expect(formatUsageDate("2026-06-08")).toBe("Jun 8, 2026");
  });
});

describe("throughDateFor", () => {
  it("uses billing freshness for billed data", () => {
    expect(throughDateFor(metadata)).toBe("2026-06-08");
  });

  it("uses account usage freshness for estimated data", () => {
    expect(
      throughDateFor({
        ...metadata,
        data_mode: "estimated",
        billing_through_date: null,
      }),
    ).toBe("2026-06-09");
  });

  it("falls back to account usage freshness when billing freshness is absent", () => {
    expect(throughDateFor({ ...metadata, billing_through_date: null })).toBe(
      "2026-06-09",
    );
  });
});

describe("windowStartFor", () => {
  it("returns an inclusive UTC start date", () => {
    expect(windowStartFor("2026-06-08", 7)).toBe("2026-06-02");
    expect(windowStartFor("2026-01-03", 7)).toBe("2025-12-28");
  });
});

describe("creditsToDollars", () => {
  it("uses the effective rate for the usage date and service type", () => {
    const rates = buildRateIndex([
      {
        usage_date: "2026-06-07",
        service_type: "WAREHOUSE_METERING",
        rating_type: "COMPUTE",
        currency: "USD",
        effective_rate: 2,
      },
      {
        usage_date: "2026-06-08",
        service_type: "WAREHOUSE_METERING",
        rating_type: "COMPUTE",
        currency: "USD",
        effective_rate: 2.25,
      },
    ]);

    expect(
      creditsToDollars(
        10,
        "2026-06-08",
        "WAREHOUSE_METERING",
        rates,
        metadata,
      ),
    ).toBe(22.5);
  });

  it("distinguishes rates by rating type when supplied", () => {
    const rates = buildRateIndex([
      {
        usage_date: "2026-06-08",
        service_type: "WAREHOUSE_METERING",
        rating_type: "COMPUTE",
        currency: "USD",
        effective_rate: 2.25,
      },
      {
        usage_date: "2026-06-08",
        service_type: "WAREHOUSE_METERING",
        rating_type: "CLOUD_SERVICES",
        currency: "USD",
        effective_rate: 1.25,
      },
    ]);

    expect(
      creditsToDollars(
        10,
        "2026-06-08",
        "WAREHOUSE_METERING",
        rates,
        metadata,
        "CLOUD_SERVICES",
      ),
    ).toBe(12.5);
  });

  it("prefers compute rates for service-only conversion", () => {
    const rates = buildRateIndex([
      {
        usage_date: "2026-06-08",
        service_type: "WAREHOUSE_METERING",
        rating_type: "CLOUD_SERVICES",
        currency: "USD",
        effective_rate: 0.5,
      },
      {
        usage_date: "2026-06-08",
        service_type: "WAREHOUSE_METERING",
        rating_type: "COMPUTE",
        currency: "USD",
        effective_rate: 2.25,
      },
    ]);

    expect(
      creditsToDollars(
        10,
        "2026-06-08",
        "WAREHOUSE_METERING",
        rates,
        metadata,
      ),
    ).toBe(22.5);
  });

  it("falls back to metadata estimated USD price when no rate exists", () => {
    expect(
      creditsToDollars(10, "2026-06-08", "AUTO_CLUSTERING", new Map(), {
        ...metadata,
        currency: null,
      }),
    ).toBe(30);
  });

  it("returns null when fallback conversion would mix unsupported non-USD currencies", () => {
    expect(
      creditsToDollars(10, "2026-06-08", "AUTO_CLUSTERING", new Map(), {
        ...metadata,
        currency: "EUR",
      }),
    ).toBeNull();
  });

  it("returns null when an effective rate is not USD-denominated", () => {
    const rates = buildRateIndex([
      {
        usage_date: "2026-06-08",
        service_type: "WAREHOUSE_METERING",
        rating_type: "COMPUTE",
        currency: "EUR",
        effective_rate: 2.25,
      },
    ]);

    expect(
      creditsToDollars(
        10,
        "2026-06-08",
        "WAREHOUSE_METERING",
        rates,
        metadata,
        "COMPUTE",
      ),
    ).toBeNull();
  });
});

describe("storageBytesToDailyDollars", () => {
  it("converts bytes to a daily USD value from TB-month pricing", () => {
    expect(storageBytesToDailyDollars(1_000_000_000_000, 30)).toBe(1);
  });
});

function dataWith(overrides: Partial<DashboardData>): DashboardData {
  return {
    ...demoDashboardData,
    ...overrides,
    metadata: {
      ...demoDashboardData.metadata,
      ...overrides.metadata,
    },
    datasets: {
      ...demoDashboardData.datasets,
      ...overrides.datasets,
    },
  };
}

function sumOrgSpend(rows: OrgSpendDaily[]): number {
  return rows
    .filter((row) => row.billing_type === "CONSUMPTION")
    .reduce((total, row) => total + row.spend, 0);
}

describe("buildDashboardViewModel", () => {
  it("renders demo header metadata and billed totals from org consumption rows", () => {
    const vm = buildDashboardViewModel(demoDashboardData, 30);
    const windowDates = new Set(vm.totalSpend.dailySeries.map((row) => row.date));
    const expected = sumOrgSpend(
      demoDashboardData.datasets.org_spend_daily.filter((row) =>
        windowDates.has(row.usage_date),
      ),
    );

    expect(vm.header.dataModeLabel).toBe("Demo");
    expect(vm.header.accountLocator).toBe("DEMO123");
    expect(vm.header.currency).toBe("USD");
    expect(vm.header.freshnessLabel).toBe("Demo data through Jun 8, 2026");
    expect(vm.unsupported).toBeNull();
    expect(vm.totalSpend.basis).toBe("billed");
    expect(vm.totalSpend.total).toBeCloseTo(expected, 2);
    expect(vm.totalSpend.totalLabel).toMatch(/^\$/);
  });

  it("includes negative billed adjustments in totals", () => {
    const adjustment: OrgSpendDaily = {
      usage_date: "2026-06-08",
      service_type: "CLOUD_SERVICES",
      rating_type: "COMPUTE",
      billing_type: "CONSUMPTION",
      is_adjustment: true,
      currency: "USD",
      spend: -10,
    };
    const baseline = buildDashboardViewModel(demoDashboardData, 7);
    const adjusted = buildDashboardViewModel(
      dataWith({
        datasets: {
          ...demoDashboardData.datasets,
          org_spend_daily: [
            ...demoDashboardData.datasets.org_spend_daily,
            adjustment,
          ],
        },
      }),
      7,
    );

    expect(adjusted.totalSpend.total).toBeCloseTo(
      baseline.totalSpend.total - 10,
      2,
    );
  });

  it("windows daily series locally without mutating the fetched payload", () => {
    const seven = buildDashboardViewModel(demoDashboardData, 7);
    const thirty = buildDashboardViewModel(demoDashboardData, 30);
    const ninety = buildDashboardViewModel(demoDashboardData, 90);

    expect(seven.totalSpend.dailySeries).toHaveLength(7);
    expect(thirty.totalSpend.dailySeries).toHaveLength(30);
    expect(ninety.totalSpend.dailySeries).toHaveLength(90);
    expect(demoDashboardData.datasets.account_spend_daily).toHaveLength(100);
  });

  it("uses account-usage through date for estimated mode", () => {
    const estimated = buildDashboardViewModel(
      dataWith({
        metadata: {
          ...demoDashboardData.metadata,
          data_mode: "estimated",
          billing_through_date: null,
        },
        datasets: {
          ...demoDashboardData.datasets,
          org_spend_daily: [],
        },
      }),
      7,
    );

    expect(estimated.header.dataModeLabel).toBe("Estimated");
    expect(estimated.header.throughDate).toBe("2026-06-09");
    expect(estimated.totalSpend.basis).toBe("estimated");
    expect(estimated.computeSpend.computeBasis).toBe("estimated");
    expect(estimated.computeSpend.rankedWarehouses.length).toBeGreaterThan(0);
  });

  it("returns an unsupported view model for mixed currencies", () => {
    const vm = buildDashboardViewModel(
      dataWith({
        metadata: {
          ...demoDashboardData.metadata,
          data_mode: "billed",
          currency: null,
          unsupported_reason: "mixed_currency",
        },
      }),
      30,
    );

    expect(vm.unsupported).toEqual({
      title: "Mixed currencies are not supported",
      detail: "Select an account with a single billing currency to view spend.",
    });
  });

  it("keeps billed basis from metadata even when billed rows are empty", () => {
    const vm = buildDashboardViewModel(
      dataWith({
        metadata: {
          ...demoDashboardData.metadata,
          data_mode: "billed",
        },
        datasets: {
          ...demoDashboardData.datasets,
          org_spend_daily: [],
        },
      }),
      7,
    );

    expect(vm.header.dataModeLabel).toBe("Billed");
    expect(vm.header.freshnessLabel).toBe("Billing data through Jun 8, 2026");
    expect(vm.totalSpend.basis).toBe("billed");
    expect(vm.totalSpend.total).toBe(0);
  });

  it("labels billed freshness fallback as Account Usage data", () => {
    const vm = buildDashboardViewModel(
      dataWith({
        metadata: {
          ...demoDashboardData.metadata,
          data_mode: "billed",
          billing_through_date: null,
          account_usage_through_date: "2026-06-09",
        },
      }),
      7,
    );

    expect(vm.header.throughDate).toBe("2026-06-09");
    expect(vm.header.freshnessLabel).toBe(
      "Account Usage data through Jun 9, 2026",
    );
  });

  it("uses billed organization usage storage rows when billed data is available", () => {
    const storageRow: OrgSpendDaily = {
      usage_date: "2026-06-08",
      service_type: "STORAGE",
      rating_type: "STORAGE",
      billing_type: "CONSUMPTION",
      is_adjustment: false,
      currency: "USD",
      spend: 123.45,
    };
    const vm = buildDashboardViewModel(
      dataWith({
        metadata: {
          ...demoDashboardData.metadata,
          data_mode: "billed",
        },
        datasets: {
          ...demoDashboardData.datasets,
          org_spend_daily: [storageRow],
        },
      }),
      7,
    );

    expect(vm.storageSpend.basis).toBe("billed");
    expect(vm.storageSpend.databaseBasis).toBe("estimated");
    expect(vm.storageSpend.dailySeries.at(-1)?.spend).toBe(123.45);
  });

  it("projects monthly spend from the latest 30 days regardless of selected window", () => {
    const seven = buildDashboardViewModel(demoDashboardData, 7);
    const thirty = buildDashboardViewModel(demoDashboardData, 30);
    const ninety = buildDashboardViewModel(demoDashboardData, 90);

    expect(seven.totalSpend.projectedMonthly).toBeCloseTo(
      thirty.totalSpend.projectedMonthly,
      2,
    );
    expect(ninety.totalSpend.projectedMonthly).toBeCloseTo(
      thirty.totalSpend.projectedMonthly,
      2,
    );
    expect(seven.totalSpend.projectionBasisLabel).toBe("latest 30 days");
  });

  it("aggregates detail table users to one row per user", () => {
    const vm = buildDashboardViewModel(demoDashboardData, 7);
    const userNames = vm.detailTables.users.map((row) => row.name);

    expect(userNames).toEqual(Array.from(new Set(userNames)));
    expect(vm.detailTables.users).toHaveLength(vm.computeSpend.rankedUsers.length);
    expect(vm.detailTables.users[0]?.spend).toBe(
      vm.computeSpend.rankedUsers[0]?.spend,
    );
  });

  it("labels aggregated users with a single warehouse only when applicable", () => {
    const vm = buildDashboardViewModel(
      dataWith({
        datasets: {
          ...demoDashboardData.datasets,
          query_compute_by_user_daily: [
            {
              usage_date: "2026-06-08",
              user_name: "SINGLE_WH_USER",
              warehouse_name: "BI_WH",
              credits_attributed_compute: 10,
            },
            {
              usage_date: "2026-06-08",
              user_name: "MULTI_WH_USER",
              warehouse_name: "BI_WH",
              credits_attributed_compute: 5,
            },
            {
              usage_date: "2026-06-08",
              user_name: "MULTI_WH_USER",
              warehouse_name: "ETL_WH",
              credits_attributed_compute: 5,
            },
          ],
        },
      }),
      7,
    );

    expect(
      vm.detailTables.users.find((row) => row.name === "SINGLE_WH_USER")
        ?.warehouseName,
    ).toBe("BI_WH");
    expect(
      vm.detailTables.users.find((row) => row.name === "MULTI_WH_USER")
        ?.warehouseName,
    ).toBe("Multiple warehouses");
  });

  it("keeps warehouse total credits distinct from compute credits", () => {
    const vm = buildDashboardViewModel(demoDashboardData, 7);

    expect(vm.detailTables.warehouses.length).toBeGreaterThan(0);
    expect(
      vm.detailTables.warehouses.some(
        (row) => row.creditsTotal > row.creditsCompute,
      ),
    ).toBe(true);
  });

  it("keeps billed storage database breakdown marked as estimated", () => {
    const vm = buildDashboardViewModel(
      dataWith({
        metadata: {
          ...demoDashboardData.metadata,
          data_mode: "billed",
        },
      }),
      7,
    );

    expect(vm.storageSpend.basis).toBe("billed");
    expect(vm.storageSpend.databaseBasis).toBe("estimated");
    expect(vm.storageSpend.databases.length).toBeGreaterThan(0);
  });

  it("prepares capped ranked bar rows for dashboard sections", () => {
    const serviceRows: OrgSpendDaily[] = Array.from({ length: 12 }, (_, index) => {
      const serviceNumber = index + 1;
      return {
        usage_date: "2026-06-08",
        service_type: `SERVICE_${String(serviceNumber).padStart(2, "0")}`,
        rating_type: "COMPUTE",
        billing_type: "CONSUMPTION",
        is_adjustment: false,
        currency: "USD",
        spend: serviceNumber,
      };
    });

    const vm = buildDashboardViewModel(
      dataWith({
        datasets: {
          ...demoDashboardData.datasets,
          org_spend_daily: serviceRows,
          service_spend_daily: [],
        },
      }),
      7,
    );

    expect(vm.serviceSpend.rankedServices).toHaveLength(12);
    expect(vm.serviceSpend.serviceBars).toHaveLength(DASHBOARD_RANKED_BAR_LIMIT);
    expect(vm.serviceSpend.serviceBars[0]).toMatchObject({
      name: "SERVICE_12",
      spend: 12,
      barWidthPercent: 100,
    });
    expect(vm.serviceSpend.serviceBars[1]?.barWidthPercent).toBeCloseTo(
      (11 / 12) * 100,
      2,
    );
    expect(vm.serviceSpend.serviceBars.at(-1)?.name).toBe("SERVICE_05");
  });

  it("prepares capped ranked bar rows for compute and storage sections", () => {
    const vm = buildDashboardViewModel(demoDashboardData, 30);

    expect(vm.computeSpend.warehouseBars.length).toBeLessThanOrEqual(
      DASHBOARD_RANKED_BAR_LIMIT,
    );
    expect(vm.computeSpend.userBars.length).toBeLessThanOrEqual(
      DASHBOARD_RANKED_BAR_LIMIT,
    );
    expect(vm.storageSpend.databaseBars.length).toBeLessThanOrEqual(
      DASHBOARD_RANKED_BAR_LIMIT,
    );
    expect(vm.computeSpend.warehouseBars[0]?.barWidthPercent).toBe(100);
    expect(vm.computeSpend.userBars[0]?.barWidthPercent).toBe(100);
    expect(vm.storageSpend.databaseBars[0]?.barWidthPercent).toBe(100);
  });

  it("caps detail table view-model rows before rendering", () => {
    const serviceRows: OrgSpendDaily[] = Array.from(
      { length: DASHBOARD_DETAIL_ROW_LIMIT + 5 },
      (_, index) => {
        const serviceNumber = index + 1;
        return {
          usage_date: "2026-06-08",
          service_type: `SERVICE_${String(serviceNumber).padStart(2, "0")}`,
          rating_type: "COMPUTE",
          billing_type: "CONSUMPTION",
          is_adjustment: false,
          currency: "USD",
          spend: serviceNumber,
        };
      },
    );

    const vm = buildDashboardViewModel(
      dataWith({
        datasets: {
          ...demoDashboardData.datasets,
          org_spend_daily: serviceRows,
          service_spend_daily: [],
        },
      }),
      7,
    );

    expect(vm.serviceSpend.rankedServices).toHaveLength(
      DASHBOARD_DETAIL_ROW_LIMIT + 5,
    );
    expect(vm.detailTables.services).toHaveLength(DASHBOARD_DETAIL_ROW_LIMIT);
    expect(vm.detailTables.services.at(-1)?.name).toBe("SERVICE_06");
  });
});

const _rateSheetTypeCheck: RateSheetDaily[] =
  demoDashboardData.datasets.rate_sheet_daily;
void _rateSheetTypeCheck;
