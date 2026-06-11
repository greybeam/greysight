export const FETCH_WINDOW_DAYS = 100;

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
  credits_used_compute: number;
};

export type QueryComputeByUserDaily = {
  usage_date: string;
  user_name: string;
  warehouse_name: string;
  credits_attributed_compute: number;
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

export type OrgSpendDaily = {
  usage_date: string;
  service_type: string;
  rating_type: string | null;
  billing_type: string | null;
  is_adjustment: boolean;
  currency: string | null;
  spend: number;
};

export type RateSheetDaily = {
  usage_date: string;
  service_type: string;
  rating_type: string | null;
  currency: string | null;
  effective_rate: number;
};

export type CurrentAccount = {
  account_locator: string;
};

export type SourceAvailability = {
  available: boolean;
  detail: string | null;
};

export type DashboardDataMode = "demo" | "billed" | "estimated";

export type UnsupportedReason = "mixed_currency";

export type DashboardDatasetMetadata = {
  data_mode: DashboardDataMode;
  account_locator: string | null;
  currency: string | null;
  billing_through_date: string | null;
  account_usage_through_date: string | null;
  estimated_credit_price_usd: number;
  storage_price_usd_per_tb_month: number;
  unsupported_reason?: UnsupportedReason | null;
  organization_usage: SourceAvailability;
  account_usage: SourceAvailability;
};

export type DashboardDatasets = {
  account_spend_daily: AccountSpendDaily[];
  warehouse_spend_daily: WarehouseSpendDaily[];
  service_spend_daily: ServiceSpendDaily[];
  query_compute_by_user_daily: QueryComputeByUserDaily[];
  database_storage_daily: DatabaseStorageDaily[];
  top_warehouses_table: TopWarehouse[];
  org_spend_daily: OrgSpendDaily[];
  rate_sheet_daily: RateSheetDaily[];
  current_account: CurrentAccount[];
};

export type DashboardData = {
  schema_version: 1;
  run: DashboardRun;
  summary: DashboardSummary;
  datasets: DashboardDatasets;
  metadata: DashboardDatasetMetadata;
};

const REQUIRED_DATASET_KEYS = [
  "account_spend_daily",
  "warehouse_spend_daily",
  "service_spend_daily",
  "query_compute_by_user_daily",
  "database_storage_daily",
  "top_warehouses_table",
  "org_spend_daily",
  "rate_sheet_daily",
  "current_account",
] as const satisfies readonly (keyof DashboardDatasets)[];

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

const DASHBOARD_DATA_MODES = [
  "demo",
  "billed",
  "estimated",
] as const satisfies readonly DashboardDataMode[];

const UNSUPPORTED_REASONS = [
  "mixed_currency",
] as const satisfies readonly UnsupportedReason[];

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

  if (payload.schema_version !== 1) {
    throw new Error("Dashboard schema_version must be 1");
  }

  const run = parseDashboardRun(payload.run);

  if (!isRecord(payload.summary)) {
    throw new Error("Dashboard summary is required");
  }

  const summary = parseDashboardSummary(payload.summary);

  if (!isRecord(payload.metadata)) {
    throw new Error("Dashboard metadata is required");
  }

  const metadata = parseDashboardMetadata(payload.metadata);

  if (!isRecord(payload.datasets)) {
    throw new Error("Dashboard datasets are required");
  }

  for (const key of REQUIRED_DATASET_KEYS) {
    if (!Array.isArray(payload.datasets[key])) {
      throw new Error(`Dashboard dataset ${key} is required`);
    }
  }

  return {
    schema_version: 1,
    run,
    summary,
    metadata,
    datasets: {
      account_spend_daily: payload.datasets.account_spend_daily,
      warehouse_spend_daily: payload.datasets.warehouse_spend_daily,
      service_spend_daily: payload.datasets.service_spend_daily,
      query_compute_by_user_daily:
        payload.datasets.query_compute_by_user_daily,
      database_storage_daily: payload.datasets.database_storage_daily,
      top_warehouses_table: payload.datasets.top_warehouses_table,
      org_spend_daily: payload.datasets.org_spend_daily,
      rate_sheet_daily: payload.datasets.rate_sheet_daily,
      current_account: payload.datasets.current_account,
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

function parseDashboardSummary(
  payload: Record<string, unknown>,
): DashboardSummary {
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

function parseDashboardMetadata(
  payload: Record<string, unknown>,
): DashboardDatasetMetadata {
  if (!isDashboardDataMode(payload.data_mode)) {
    throw new Error("Dashboard metadata data_mode is invalid");
  }
  if (!isNullableString(payload.account_locator)) {
    throw new Error("Dashboard metadata account_locator is invalid");
  }
  if (!isNullableString(payload.currency)) {
    throw new Error("Dashboard metadata currency is invalid");
  }
  if (!isNullableString(payload.billing_through_date)) {
    throw new Error("Dashboard metadata billing_through_date is invalid");
  }
  if (!isNullableString(payload.account_usage_through_date)) {
    throw new Error(
      "Dashboard metadata account_usage_through_date is invalid",
    );
  }
  if (!isFiniteNumber(payload.estimated_credit_price_usd)) {
    throw new Error(
      "Dashboard metadata estimated_credit_price_usd must be a number",
    );
  }
  if (!isFiniteNumber(payload.storage_price_usd_per_tb_month)) {
    throw new Error(
      "Dashboard metadata storage_price_usd_per_tb_month must be a number",
    );
  }
  if (
    payload.unsupported_reason !== undefined &&
    payload.unsupported_reason !== null &&
    !isUnsupportedReason(payload.unsupported_reason)
  ) {
    throw new Error("Dashboard metadata unsupported_reason is invalid");
  }

  return {
    data_mode: payload.data_mode,
    account_locator: payload.account_locator,
    currency: payload.currency,
    billing_through_date: payload.billing_through_date,
    account_usage_through_date: payload.account_usage_through_date,
    estimated_credit_price_usd: payload.estimated_credit_price_usd,
    storage_price_usd_per_tb_month: payload.storage_price_usd_per_tb_month,
    unsupported_reason: payload.unsupported_reason ?? null,
    organization_usage: parseSourceAvailability(
      payload.organization_usage,
      "organization_usage",
    ),
    account_usage: parseSourceAvailability(payload.account_usage, "account_usage"),
  };
}

function parseSourceAvailability(
  payload: unknown,
  key: string,
): SourceAvailability {
  if (!isRecord(payload) || typeof payload.available !== "boolean") {
    throw new Error(`Dashboard metadata ${key} is invalid`);
  }
  if (payload.detail !== undefined && !isNullableString(payload.detail)) {
    throw new Error(`Dashboard metadata ${key} detail is invalid`);
  }

  return {
    available: payload.available,
    detail: payload.detail ?? null,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isDashboardRunStatus(
  value: unknown,
): value is DashboardRunStatus {
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

function isDashboardDataMode(value: unknown): value is DashboardDataMode {
  return (
    typeof value === "string" &&
    (DASHBOARD_DATA_MODES as readonly string[]).includes(value)
  );
}

function isUnsupportedReason(value: unknown): value is UnsupportedReason {
  return (
    typeof value === "string" &&
    (UNSUPPORTED_REASONS as readonly string[]).includes(value)
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
