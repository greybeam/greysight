import type { DashboardData } from "./dashboard-contracts";

const FETCH_DAYS = 100;
const BILLING_THROUGH = "2026-06-08";
const ACCOUNT_USAGE_THROUGH = BILLING_THROUGH;
const ACCOUNT_LOCATOR = "DEMO123";
const CREDIT_RATE_USD = 2.25;
const STORAGE_RATE_USD = 25;
const CAPACITY_START_USD = 75_000;

const SERVICES: Array<[serviceType: string, ratingType: string, credits: number]> =
  [
    ["WAREHOUSE_METERING", "COMPUTE", 38],
    ["CLOUD_SERVICES", "COMPUTE", 4],
    ["AUTO_CLUSTERING", "COMPUTE", 1.5],
  ];
const WAREHOUSES: Array<[warehouseName: string, share: number]> = [
  ["BI_WH", 0.5],
  ["ETL_WH", 0.35],
  ["ADHOC_WH", 0.15],
];
const USERS: Array<[userName: string, warehouseName: string, share: number]> = [
  ["ANALYST_A", "BI_WH", 0.34],
  ["ANALYST_B", "ADHOC_WH", 0.22],
  ["DATA_ENGINEER", "ETL_WH", 0.3],
  ["AIRFLOW_SVC", "ETL_WH", 0.14],
];
const DATABASES: Array<[databaseName: string, baseTb: number]> = [
  ["RAW", 3.6],
  ["ANALYTICS", 2.3],
  ["APP", 1.1],
];

function round(value: number, digits = 3): number {
  return Number(value.toFixed(digits));
}

function addDays(usageDate: string, offset: number): string {
  const [year, month, day] = usageDate.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

function dailyMultiplier(index: number): number {
  const weekdayShape = (index % 7) * 0.025;
  const monthShape = (index % 30) * 0.004;
  return round(0.9 + weekdayShape + monthShape, 4);
}

const usageDates = Array.from({ length: FETCH_DAYS }, (_, index) =>
  addDays(BILLING_THROUGH, index - (FETCH_DAYS - 1)),
);

const serviceSpendDaily = usageDates.flatMap((usage_date, index) =>
  SERVICES.map(([service_type, , credits]) => ({
    usage_date,
    service_type,
    credits_used: round(credits * dailyMultiplier(index)),
  })),
);

const warehouseSpendDaily = usageDates.flatMap((usage_date, index) => {
  const meteredCredits = 38 * dailyMultiplier(index);
  return WAREHOUSES.map(([warehouse_name, share]) => {
    const computeCredits = round(meteredCredits * share);
    return {
      usage_date,
      warehouse_name,
      credits_used: round(computeCredits * 1.08),
      credits_used_compute: computeCredits,
      credits_attributed_queries: round(computeCredits * 0.6),
    };
  });
});

const queryComputeByUserDaily = usageDates.flatMap((usage_date, index) => {
  const meteredCredits = 38 * dailyMultiplier(index);
  return USERS.map(([user_name, warehouse_name, share]) => ({
    usage_date,
    user_name,
    warehouse_name,
    credits_attributed_compute: round(meteredCredits * share),
  }));
});

const databaseStorageDaily = usageDates.flatMap((usage_date, index) =>
  DATABASES.map(([database_name, baseTb]) => {
    const growthFactor = 1 + index / 500;
    const databaseBytes = Math.round(baseTb * growthFactor * 1_000_000_000_000);
    return {
      usage_date,
      database_name,
      average_database_bytes: databaseBytes,
      average_failsafe_bytes: Math.round(
        baseTb * 0.08 * growthFactor * 1_000_000_000_000,
      ),
      // Demo account has no hybrid (Unistore) tables, matching the common case
      // where Snowflake reports this column as null.
      average_hybrid_table_storage_bytes: null,
    };
  }),
);

const orgSpendDaily = serviceSpendDaily.map((row) => {
  const service = SERVICES.find(([serviceType]) => serviceType === row.service_type);
  return {
    usage_date: row.usage_date,
    service_type: row.service_type,
    rating_type: service?.[1] ?? "COMPUTE",
    billing_type: "CONSUMPTION",
    is_adjustment: false,
    currency: "USD",
    spend: round(row.credits_used * CREDIT_RATE_USD, 2),
  };
});

const rateSheetDaily = usageDates.flatMap((usage_date) =>
  SERVICES.map(([service_type, rating_type]) => ({
    usage_date,
    service_type,
    // Snowflake's rate sheet keys each rate by a usage type; the demo's compute
    // services all bill as credits.
    usage_type: "credits",
    rating_type,
    currency: "USD",
    effective_rate: CREDIT_RATE_USD,
  })),
);

let remainingCapacityBalance = CAPACITY_START_USD;
const capacityBalanceDaily = usageDates.map((usage_date) => {
  const dailySpend = orgSpendDaily
    .filter((row) => row.usage_date === usage_date)
    .reduce((total, row) => total + row.spend, 0);
  remainingCapacityBalance = round(remainingCapacityBalance - dailySpend, 2);

  return {
    usage_date,
    currency: "USD",
    balance: remainingCapacityBalance,
  };
});

const accountSpendDaily = usageDates.map((usage_date) => ({
  usage_date,
  credits_used: round(
    serviceSpendDaily
      .filter((row) => row.usage_date === usage_date)
      .reduce((total, row) => total + row.credits_used, 0),
  ),
}));

const topWarehousesTable = WAREHOUSES.map(([warehouse_name]) => ({
  warehouse_name,
  credits_used: round(
    warehouseSpendDaily
      .filter((row) => row.warehouse_name === warehouse_name)
      .reduce((total, row) => total + row.credits_used, 0),
  ),
}));

const completeAccountSpendDaily = accountSpendDaily.filter(
  (row) => row.usage_date < BILLING_THROUGH,
);
const totalCredits = round(
  completeAccountSpendDaily.reduce((total, row) => total + row.credits_used, 0),
);
const averageDailyCredits = Number((totalCredits / FETCH_DAYS).toFixed(5));
const estimatedMonthlyCredits = Number((averageDailyCredits * 30).toFixed(4));
const latestCompleteStorageDate = usageDates
  .filter((usageDate) => usageDate < BILLING_THROUGH)
  .at(-1);
const latestStorageBytes = databaseStorageDaily
  .filter((row) => row.usage_date === latestCompleteStorageDate)
  .reduce(
    (total, row) =>
      total + row.average_database_bytes + row.average_failsafe_bytes,
    0,
  );
const estimatedMonthlyStorageCostUsd =
  (latestStorageBytes / 1_000_000_000_000) * STORAGE_RATE_USD;

const demoDashboardData: DashboardData = {
  schema_version: 1,
  run: {
    id: "demo-run",
    status: "completed",
    source: "demo",
    window_days: FETCH_DAYS,
    started_at: "2026-06-10T00:00:00Z",
    completed_at: "2026-06-10T00:00:00Z",
    user_safe_message: null,
    error: null,
  },
  summary: {
    total_credits: totalCredits,
    average_daily_credits: averageDailyCredits,
    estimated_monthly_credits: estimatedMonthlyCredits,
    storage_bytes: latestStorageBytes,
    estimated_monthly_storage_cost_usd: estimatedMonthlyStorageCostUsd,
  },
  metadata: {
    data_mode: "demo",
    account_locator: ACCOUNT_LOCATOR,
    currency: "USD",
    billing_through_date: BILLING_THROUGH,
    account_usage_through_date: ACCOUNT_USAGE_THROUGH,
    estimated_credit_price_usd: CREDIT_RATE_USD,
    storage_price_usd_per_tb_month: STORAGE_RATE_USD,
    unsupported_reason: null,
    organization_usage: { available: true, detail: null, user_safe_message: null },
    account_usage: { available: true, detail: null, user_safe_message: null },
  },
  datasets: {
    account_spend_daily: accountSpendDaily,
    service_spend_daily: serviceSpendDaily,
    warehouse_spend_daily: warehouseSpendDaily,
    query_compute_by_user_daily: queryComputeByUserDaily,
    database_storage_daily: databaseStorageDaily,
    top_warehouses_table: topWarehousesTable,
    org_spend_daily: orgSpendDaily,
    rate_sheet_daily: rateSheetDaily,
    capacity_balance_daily: capacityBalanceDaily,
    current_account: [{ account_locator: ACCOUNT_LOCATOR }],
  },
};

export default demoDashboardData;
