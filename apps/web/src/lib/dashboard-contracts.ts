export type DashboardRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "expired"
  | "deleted";

export type DashboardRun = {
  id: string;
  status: DashboardRunStatus;
  source: "demo" | "snowflake";
  window_days: number;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  user_safe_message?: string | null;
};

export type DashboardSummary = {
  total_credits: number;
  average_daily_credits: number;
  estimated_monthly_credits: number;
  storage_bytes: number;
  estimated_monthly_storage_cost_usd: number | null;
};

export type AccountSpendDaily = {
  usage_date: string;
  credits_used: number;
};

export type ServiceSpendDaily = AccountSpendDaily & {
  service_type: string;
};

export type WarehouseSpendDaily = AccountSpendDaily & {
  warehouse_name: string;
};

export type QueryComputeByUserDaily = AccountSpendDaily & {
  user_name: string;
  warehouse_name: string;
};

export type DatabaseStorageDaily = {
  usage_date: string;
  database_name: string | null;
  average_database_bytes: number;
  average_failsafe_bytes: number;
};

export type TopWarehouse = {
  warehouse_name: string;
  credits_used: number;
};

export type DashboardDatasets = {
  account_spend_daily: AccountSpendDaily[];
  warehouse_spend_daily: WarehouseSpendDaily[];
  service_spend_daily: ServiceSpendDaily[];
  query_compute_by_user_daily: QueryComputeByUserDaily[];
  database_storage_daily: DatabaseStorageDaily[];
  top_warehouses_table: TopWarehouse[];
};

export type DashboardData = {
  run: DashboardRun;
  summary: DashboardSummary;
  datasets: DashboardDatasets;
};

const REQUIRED_DATASET_KEYS = [
  "account_spend_daily",
  "warehouse_spend_daily",
  "service_spend_daily",
  "query_compute_by_user_daily",
  "database_storage_daily",
  "top_warehouses_table",
] as const;

export default function parseDashboardDatasets(payload: unknown): DashboardData {
  if (!isRecord(payload)) {
    throw new Error("Dashboard response must be an object");
  }
  if (!isRecord(payload.run)) {
    throw new Error("Dashboard run is required");
  }
  if (!isRecord(payload.summary)) {
    throw new Error("Dashboard summary is required");
  }
  if (!isRecord(payload.datasets)) {
    throw new Error("Dashboard datasets are required");
  }

  for (const key of REQUIRED_DATASET_KEYS) {
    if (!Array.isArray(payload.datasets[key])) {
      throw new Error(`Dashboard dataset ${key} is required`);
    }
  }

  return payload as DashboardData;
}

export function parseDashboardRun(payload: unknown): DashboardRun {
  if (!isRecord(payload)) {
    throw new Error("Dashboard run response must be an object");
  }
  if (
    typeof payload.id !== "string" ||
    typeof payload.status !== "string" ||
    typeof payload.source !== "string" ||
    typeof payload.window_days !== "number"
  ) {
    throw new Error("Dashboard run response is invalid");
  }

  return payload as DashboardRun;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
