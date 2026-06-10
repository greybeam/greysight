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
  estimated_monthly_storage_cost_usd?: number | null;
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

type RequiredSummaryNumericKey = keyof Pick<
  DashboardSummary,
  | "total_credits"
  | "average_daily_credits"
  | "estimated_monthly_credits"
  | "storage_bytes"
>;

const DASHBOARD_RUN_STATUSES = [
  "queued",
  "running",
  "completed",
  "failed",
  "expired",
  "deleted",
] as const satisfies readonly DashboardRunStatus[];

const DASHBOARD_RUN_SOURCES = [
  "demo",
  "snowflake",
] as const satisfies readonly DashboardRun["source"][];

const OPTIONAL_RUN_STRING_KEYS = [
  "started_at",
  "completed_at",
  "error",
  "user_safe_message",
] as const satisfies readonly (keyof DashboardRun)[];

export default function parseDashboardDatasets(payload: unknown): DashboardData {
  if (!isRecord(payload)) {
    throw new Error("Dashboard response must be an object");
  }

  const run = parseDashboardRun(payload.run);

  if (!isRecord(payload.summary)) {
    throw new Error("Dashboard summary is required");
  }

  const summary = parseDashboardSummary(payload.summary);

  if (!isRecord(payload.datasets)) {
    throw new Error("Dashboard datasets are required");
  }

  for (const key of REQUIRED_DATASET_KEYS) {
    if (!Array.isArray(payload.datasets[key])) {
      throw new Error(`Dashboard dataset ${key} is required`);
    }
  }

  return {
    run,
    summary,
    datasets: {
      account_spend_daily: payload.datasets.account_spend_daily,
      warehouse_spend_daily: payload.datasets.warehouse_spend_daily,
      service_spend_daily: payload.datasets.service_spend_daily,
      query_compute_by_user_daily:
        payload.datasets.query_compute_by_user_daily,
      database_storage_daily: payload.datasets.database_storage_daily,
      top_warehouses_table: payload.datasets.top_warehouses_table,
    } as DashboardDatasets,
  };
}

export function parseDashboardRun(payload: unknown): DashboardRun {
  if (!isRecord(payload)) {
    throw new Error("Dashboard run response must be an object");
  }
  if (
    typeof payload.id !== "string" ||
    !isDashboardRunStatus(payload.status) ||
    !isDashboardRunSource(payload.source) ||
    !isFiniteNumber(payload.window_days)
  ) {
    throw new Error("Dashboard run response is invalid");
  }

  const run: DashboardRun = {
    id: payload.id,
    status: payload.status,
    source: payload.source,
    window_days: payload.window_days,
  };

  for (const key of OPTIONAL_RUN_STRING_KEYS) {
    const value = payload[key];
    if (value === undefined) {
      continue;
    }
    if (value !== null && typeof value !== "string") {
      throw new Error("Dashboard run response is invalid");
    }
    run[key] = value;
  }

  return run;
}

function parseDashboardSummary(payload: Record<string, unknown>): DashboardSummary {
  const summary: DashboardSummary = {
    total_credits: readRequiredSummaryNumber(payload, "total_credits"),
    average_daily_credits: readRequiredSummaryNumber(
      payload,
      "average_daily_credits",
    ),
    estimated_monthly_credits: readRequiredSummaryNumber(
      payload,
      "estimated_monthly_credits",
    ),
    storage_bytes: readRequiredSummaryNumber(payload, "storage_bytes"),
  };

  const storageCost = payload.estimated_monthly_storage_cost_usd;
  if (storageCost !== undefined) {
    if (storageCost !== null && !isFiniteNumber(storageCost)) {
      throw new Error(
        "Dashboard summary estimated_monthly_storage_cost_usd must be a number or null",
      );
    }
    summary.estimated_monthly_storage_cost_usd = storageCost;
  }

  return summary;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isDashboardRunStatus(value: unknown): value is DashboardRunStatus {
  return (
    typeof value === "string" &&
    (DASHBOARD_RUN_STATUSES as readonly string[]).includes(value)
  );
}

function isDashboardRunSource(
  value: unknown,
): value is DashboardRun["source"] {
  return (
    typeof value === "string" &&
    (DASHBOARD_RUN_SOURCES as readonly string[]).includes(value)
  );
}

function readRequiredSummaryNumber(
  payload: Record<string, unknown>,
  key: RequiredSummaryNumericKey,
): number {
  const value = payload[key];
  if (value === undefined) {
    throw new Error(`Dashboard summary ${key} is required`);
  }
  if (!isFiniteNumber(value)) {
    throw new Error(`Dashboard summary ${key} must be a number`);
  }

  return value;
}
