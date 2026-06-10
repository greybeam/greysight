import type { DashboardData } from "./dashboard-contracts";

const demoDashboardDatasets: DashboardData = {
  run: {
    id: "demo-run",
    status: "completed",
    source: "demo",
    window_days: 30,
    started_at: "2026-06-08T00:00:00Z",
    completed_at: "2026-06-08T00:00:01Z",
    error: null,
  },
  summary: {
    total_credits: 132,
    average_daily_credits: 44,
    estimated_monthly_credits: 1320,
    storage_bytes: 6700000000000,
    estimated_monthly_storage_cost_usd: null,
  },
  datasets: {
    account_spend_daily: [
      { usage_date: "2026-06-05", credits_used: 41.5 },
      { usage_date: "2026-06-06", credits_used: 43.5 },
      { usage_date: "2026-06-07", credits_used: 47 },
    ],
    service_spend_daily: [
      {
        usage_date: "2026-06-05",
        service_type: "WAREHOUSE_METERING",
        credits_used: 37.5,
      },
      {
        usage_date: "2026-06-05",
        service_type: "CLOUD_SERVICES",
        credits_used: 4,
      },
      {
        usage_date: "2026-06-06",
        service_type: "WAREHOUSE_METERING",
        credits_used: 39,
      },
      {
        usage_date: "2026-06-06",
        service_type: "CLOUD_SERVICES",
        credits_used: 4.5,
      },
      {
        usage_date: "2026-06-07",
        service_type: "WAREHOUSE_METERING",
        credits_used: 42,
      },
      {
        usage_date: "2026-06-07",
        service_type: "CLOUD_SERVICES",
        credits_used: 5,
      },
    ],
    warehouse_spend_daily: [
      { usage_date: "2026-06-05", warehouse_name: "BI_WH", credits_used: 18 },
      { usage_date: "2026-06-05", warehouse_name: "ETL_WH", credits_used: 14.5 },
      { usage_date: "2026-06-05", warehouse_name: "ADHOC_WH", credits_used: 5 },
      { usage_date: "2026-06-06", warehouse_name: "BI_WH", credits_used: 19 },
      { usage_date: "2026-06-06", warehouse_name: "ETL_WH", credits_used: 15.5 },
      { usage_date: "2026-06-06", warehouse_name: "ADHOC_WH", credits_used: 4.5 },
      { usage_date: "2026-06-07", warehouse_name: "BI_WH", credits_used: 21 },
      { usage_date: "2026-06-07", warehouse_name: "ETL_WH", credits_used: 16 },
      { usage_date: "2026-06-07", warehouse_name: "ADHOC_WH", credits_used: 5 },
    ],
    query_compute_by_user_daily: [
      {
        usage_date: "2026-06-05",
        user_name: "ANALYST_A",
        warehouse_name: "BI_WH",
        credits_used: 12,
      },
      {
        usage_date: "2026-06-05",
        user_name: "ANALYST_B",
        warehouse_name: "ADHOC_WH",
        credits_used: 8.5,
      },
      {
        usage_date: "2026-06-06",
        user_name: "ANALYST_A",
        warehouse_name: "BI_WH",
        credits_used: 13,
      },
      {
        usage_date: "2026-06-06",
        user_name: "DATA_ENGINEER",
        warehouse_name: "ETL_WH",
        credits_used: 10.5,
      },
      {
        usage_date: "2026-06-07",
        user_name: "DATA_ENGINEER",
        warehouse_name: "ETL_WH",
        credits_used: 14,
      },
      {
        usage_date: "2026-06-07",
        user_name: "ANALYST_B",
        warehouse_name: "ADHOC_WH",
        credits_used: 9,
      },
    ],
    database_storage_daily: [
      {
        usage_date: "2026-06-05",
        database_name: "RAW",
        average_database_bytes: 3500000000000,
        average_failsafe_bytes: 400000000000,
      },
      {
        usage_date: "2026-06-05",
        database_name: "ANALYTICS",
        average_database_bytes: 2200000000000,
        average_failsafe_bytes: 200000000000,
      },
      {
        usage_date: "2026-06-07",
        database_name: "RAW",
        average_database_bytes: 3700000000000,
        average_failsafe_bytes: 450000000000,
      },
      {
        usage_date: "2026-06-07",
        database_name: "ANALYTICS",
        average_database_bytes: 2300000000000,
        average_failsafe_bytes: 250000000000,
      },
    ],
    top_warehouses_table: [
      { warehouse_name: "BI_WH", credits_used: 58 },
      { warehouse_name: "ETL_WH", credits_used: 46 },
      { warehouse_name: "ADHOC_WH", credits_used: 14.5 },
    ],
  },
};

export default demoDashboardDatasets;
