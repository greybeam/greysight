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

export type DashboardViewRange = {
  mode: "relative" | "custom";
  windowDays: number | null;
  startDate: string;
  endDate: string;
};

export type DashboardProjectionRange = {
  startDate: string;
  endDate: string;
};

export type SpendBasis = "billed" | "estimated";

export type DollarPoint = {
  date: string;
  spend: number;
  spendLabel: string;
};

export type ServicePoint = {
  date: string;
  values: Record<string, number>;
};

export type RankedSpendRow = {
  name: string;
  spend: number;
  spendLabel: string;
  credits: number | null;
};

export type RankedBarRow = RankedSpendRow & {
  barWidthPercent: number;
};

export type HeaderViewModel = {
  dataModeLabel: "Billed" | "Estimated" | "Demo";
  accountLocator: string | null;
  currency: string;
  throughDate: string | null;
  throughDateLabel: string | null;
  freshnessLabel: string | null;
  estimatedCreditPriceLabel: string;
  storagePriceLabel: string;
};

export type TotalSpendViewModel = {
  basis: SpendBasis;
  total: number;
  totalLabel: string;
  averageDaily: number;
  averageDailyLabel: string;
  projectedMonthly: number;
  projectedMonthlyLabel: string;
  projectionBasisLabel: string;
  dailySeries: DollarPoint[];
  topDriver: RankedSpendRow | null;
  isEmpty: boolean;
};

export type ComputeSpendViewModel = {
  computeBasis: SpendBasis;
  dailySeries: DollarPoint[];
  rankedWarehouses: RankedSpendRow[];
  rankedUsers: RankedSpendRow[];
  warehouseBars: RankedBarRow[];
  userBars: RankedBarRow[];
  isEmpty: boolean;
};

export type StorageDatabaseRow = {
  name: string;
  bytes: number;
  monthlySpend: number;
  monthlySpendLabel: string;
};

export type StorageSpendViewModel = {
  basis: SpendBasis;
  databaseBasis: SpendBasis;
  dailySeries: DollarPoint[];
  databases: StorageDatabaseRow[];
  databaseBars: RankedBarRow[];
  isEmpty: boolean;
};

export type ServiceSpendViewModel = {
  basis: SpendBasis;
  dailySeries: ServicePoint[];
  serviceNames: string[];
  rankedServices: RankedSpendRow[];
  serviceBars: RankedBarRow[];
  isEmpty: boolean;
};

export type WarehouseDetailRow = RankedSpendRow & {
  creditsCompute: number;
  creditsTotal: number;
};

export type UserDetailRow = RankedSpendRow & {
  warehouseName: string;
};

export type DetailTablesViewModel = {
  services: RankedSpendRow[];
  warehouses: WarehouseDetailRow[];
  users: UserDetailRow[];
  storage: StorageDatabaseRow[];
};

export type UnsupportedViewModel = {
  title: string;
  detail: string;
};

export type DashboardView = {
  schema_version: 1;
  run: DashboardRun;
  range: DashboardViewRange;
  projectionRange: DashboardProjectionRange;
  header: HeaderViewModel;
  unsupported: UnsupportedViewModel | null;
  totalSpend: TotalSpendViewModel;
  computeSpend: ComputeSpendViewModel;
  storageSpend: StorageSpendViewModel;
  serviceSpend: ServiceSpendViewModel;
  detailTables: DetailTablesViewModel;
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

const DASHBOARD_VIEW_RANGE_MODES = [
  "relative",
  "custom",
] as const satisfies readonly DashboardViewRange["mode"][];

const DASHBOARD_DATA_MODE_LABELS = [
  "Billed",
  "Estimated",
  "Demo",
] as const satisfies readonly HeaderViewModel["dataModeLabel"][];

const SPEND_BASES = [
  "billed",
  "estimated",
] as const satisfies readonly SpendBasis[];

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

export function parseDashboardView(payload: unknown): DashboardView {
  if (!isRecord(payload) || payload.schema_version !== 1) {
    throwInvalidDashboardView();
  }

  return {
    schema_version: 1,
    run: parseDashboardViewRun(readViewRecord(payload, "run")),
    range: parseDashboardViewRange(readViewRecord(payload, "range")),
    projectionRange: parseDashboardProjectionRange(
      readViewRecord(payload, "projection_range", "projectionRange"),
    ),
    header: parseHeaderViewModel(readViewRecord(payload, "header")),
    unsupported: parseUnsupportedViewModel(readViewValue(payload, "unsupported")),
    totalSpend: parseTotalSpendViewModel(
      readViewRecord(payload, "total_spend", "totalSpend"),
    ),
    computeSpend: parseComputeSpendViewModel(
      readViewRecord(payload, "compute_spend", "computeSpend"),
    ),
    storageSpend: parseStorageSpendViewModel(
      readViewRecord(payload, "storage_spend", "storageSpend"),
    ),
    serviceSpend: parseServiceSpendViewModel(
      readViewRecord(payload, "service_spend", "serviceSpend"),
    ),
    detailTables: parseDetailTablesViewModel(
      readViewRecord(payload, "detail_tables", "detailTables"),
    ),
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

function parseDashboardViewRun(payload: Record<string, unknown>): DashboardRun {
  try {
    return parseDashboardRun(payload);
  } catch {
    throwInvalidDashboardView();
  }
}

function parseDashboardViewRange(
  payload: Record<string, unknown>,
): DashboardViewRange {
  const mode = readViewString(payload, "mode");
  if (!isDashboardViewRangeMode(mode)) {
    throwInvalidDashboardView();
  }

  return {
    mode,
    windowDays: readViewNullableNumber(payload, "window_days", "windowDays"),
    startDate: readViewString(payload, "start_date", "startDate"),
    endDate: readViewString(payload, "end_date", "endDate"),
  };
}

function parseDashboardProjectionRange(
  payload: Record<string, unknown>,
): DashboardProjectionRange {
  return {
    startDate: readViewString(payload, "start_date", "startDate"),
    endDate: readViewString(payload, "end_date", "endDate"),
  };
}

function parseHeaderViewModel(
  payload: Record<string, unknown>,
): HeaderViewModel {
  const dataModeLabel = readViewString(
    payload,
    "data_mode_label",
    "dataModeLabel",
  );
  if (!isDashboardDataModeLabel(dataModeLabel)) {
    throwInvalidDashboardView();
  }

  return {
    dataModeLabel,
    accountLocator: readViewNullableString(
      payload,
      "account_locator",
      "accountLocator",
    ),
    currency: readViewString(payload, "currency"),
    throughDate: readViewNullableString(payload, "through_date", "throughDate"),
    throughDateLabel: readViewNullableString(
      payload,
      "through_date_label",
      "throughDateLabel",
    ),
    freshnessLabel: readViewNullableString(
      payload,
      "freshness_label",
      "freshnessLabel",
    ),
    estimatedCreditPriceLabel: readViewString(
      payload,
      "estimated_credit_price_label",
      "estimatedCreditPriceLabel",
    ),
    storagePriceLabel: readViewString(
      payload,
      "storage_price_label",
      "storagePriceLabel",
    ),
  };
}

function parseTotalSpendViewModel(
  payload: Record<string, unknown>,
): TotalSpendViewModel {
  return {
    basis: readViewSpendBasis(payload, "basis"),
    total: readViewNumber(payload, "total"),
    totalLabel: readViewString(payload, "total_label", "totalLabel"),
    averageDaily: readViewNumber(payload, "average_daily", "averageDaily"),
    averageDailyLabel: readViewString(
      payload,
      "average_daily_label",
      "averageDailyLabel",
    ),
    projectedMonthly: readViewNumber(
      payload,
      "projected_monthly",
      "projectedMonthly",
    ),
    projectedMonthlyLabel: readViewString(
      payload,
      "projected_monthly_label",
      "projectedMonthlyLabel",
    ),
    projectionBasisLabel: readViewString(
      payload,
      "projection_basis_label",
      "projectionBasisLabel",
    ),
    dailySeries: readViewArray(payload, "daily_series", "dailySeries").map(
      parseDollarPoint,
    ),
    topDriver: parseNullableRankedSpendRow(
      readViewValue(payload, "top_driver", "topDriver"),
    ),
    isEmpty: readViewBoolean(payload, "is_empty", "isEmpty"),
  };
}

function parseComputeSpendViewModel(
  payload: Record<string, unknown>,
): ComputeSpendViewModel {
  return {
    computeBasis: readViewSpendBasis(payload, "compute_basis", "computeBasis"),
    dailySeries: readViewArray(payload, "daily_series", "dailySeries").map(
      parseDollarPoint,
    ),
    rankedWarehouses: readViewArray(
      payload,
      "ranked_warehouses",
      "rankedWarehouses",
    ).map(parseRankedSpendRow),
    rankedUsers: readViewArray(payload, "ranked_users", "rankedUsers").map(
      parseRankedSpendRow,
    ),
    warehouseBars: readViewArray(
      payload,
      "warehouse_bars",
      "warehouseBars",
    ).map(parseRankedBarRow),
    userBars: readViewArray(payload, "user_bars", "userBars").map(
      parseRankedBarRow,
    ),
    isEmpty: readViewBoolean(payload, "is_empty", "isEmpty"),
  };
}

function parseStorageSpendViewModel(
  payload: Record<string, unknown>,
): StorageSpendViewModel {
  return {
    basis: readViewSpendBasis(payload, "basis"),
    databaseBasis: readViewSpendBasis(
      payload,
      "database_basis",
      "databaseBasis",
    ),
    dailySeries: readViewArray(payload, "daily_series", "dailySeries").map(
      parseDollarPoint,
    ),
    databases: readViewArray(payload, "databases").map(parseStorageDatabaseRow),
    databaseBars: readViewArray(
      payload,
      "database_bars",
      "databaseBars",
    ).map(parseRankedBarRow),
    isEmpty: readViewBoolean(payload, "is_empty", "isEmpty"),
  };
}

function parseServiceSpendViewModel(
  payload: Record<string, unknown>,
): ServiceSpendViewModel {
  return {
    basis: readViewSpendBasis(payload, "basis"),
    dailySeries: readViewArray(payload, "daily_series", "dailySeries").map(
      parseServicePoint,
    ),
    serviceNames: readViewArray(payload, "service_names", "serviceNames").map(
      readViewArrayString,
    ),
    rankedServices: readViewArray(
      payload,
      "ranked_services",
      "rankedServices",
    ).map(parseRankedSpendRow),
    serviceBars: readViewArray(payload, "service_bars", "serviceBars").map(
      parseRankedBarRow,
    ),
    isEmpty: readViewBoolean(payload, "is_empty", "isEmpty"),
  };
}

function parseDetailTablesViewModel(
  payload: Record<string, unknown>,
): DetailTablesViewModel {
  return {
    services: readViewArray(payload, "services").map(parseRankedSpendRow),
    warehouses: readViewArray(payload, "warehouses").map(
      parseWarehouseDetailRow,
    ),
    users: readViewArray(payload, "users").map(parseUserDetailRow),
    storage: readViewArray(payload, "storage").map(parseStorageDatabaseRow),
  };
}

function parseDollarPoint(payload: unknown): DollarPoint {
  const record = asViewRecord(payload);
  return {
    date: readViewString(record, "date"),
    spend: readViewNumber(record, "spend"),
    spendLabel: readViewString(record, "spend_label", "spendLabel"),
  };
}

function parseServicePoint(payload: unknown): ServicePoint {
  const record = asViewRecord(payload);
  const values = readViewRecord(record, "values");
  const parsedValues: Record<string, number> = {};

  for (const [key, value] of Object.entries(values)) {
    if (!isFiniteNumber(value)) {
      throwInvalidDashboardView();
    }
    parsedValues[key] = value;
  }

  return {
    date: readViewString(record, "date"),
    values: parsedValues,
  };
}

function parseRankedSpendRow(payload: unknown): RankedSpendRow {
  const record = asViewRecord(payload);
  return {
    name: readViewString(record, "name"),
    spend: readViewNumber(record, "spend"),
    spendLabel: readViewString(record, "spend_label", "spendLabel"),
    credits: readViewNullableNumber(record, "credits"),
  };
}

function parseNullableRankedSpendRow(payload: unknown): RankedSpendRow | null {
  if (payload === null) {
    return null;
  }
  return parseRankedSpendRow(payload);
}

function parseRankedBarRow(payload: unknown): RankedBarRow {
  const record = asViewRecord(payload);
  return {
    ...parseRankedSpendRow(record),
    barWidthPercent: readViewNumber(
      record,
      "bar_width_percent",
      "barWidthPercent",
    ),
  };
}

function parseStorageDatabaseRow(payload: unknown): StorageDatabaseRow {
  const record = asViewRecord(payload);
  return {
    name: readViewString(record, "name"),
    bytes: readViewNumber(record, "bytes"),
    monthlySpend: readViewNumber(record, "monthly_spend", "monthlySpend"),
    monthlySpendLabel: readViewString(
      record,
      "monthly_spend_label",
      "monthlySpendLabel",
    ),
  };
}

function parseWarehouseDetailRow(payload: unknown): WarehouseDetailRow {
  const record = asViewRecord(payload);
  return {
    ...parseRankedSpendRow(record),
    creditsCompute: readViewNumber(
      record,
      "credits_compute",
      "creditsCompute",
    ),
    creditsTotal: readViewNumber(record, "credits_total", "creditsTotal"),
  };
}

function parseUserDetailRow(payload: unknown): UserDetailRow {
  const record = asViewRecord(payload);
  return {
    ...parseRankedSpendRow(record),
    warehouseName: readViewString(record, "warehouse_name", "warehouseName"),
  };
}

function parseUnsupportedViewModel(payload: unknown): UnsupportedViewModel | null {
  if (payload === null) {
    return null;
  }

  const record = asViewRecord(payload);
  return {
    title: readViewString(record, "title"),
    detail: readViewString(record, "detail"),
  };
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

function asViewRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value) || Array.isArray(value)) {
    throwInvalidDashboardView();
  }
  return value;
}

function readViewValue(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): unknown {
  if (Object.hasOwn(payload, snakeKey)) {
    return payload[snakeKey];
  }
  if (camelKey !== snakeKey && Object.hasOwn(payload, camelKey)) {
    return payload[camelKey];
  }
  throwInvalidDashboardView();
}

function readViewRecord(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): Record<string, unknown> {
  return asViewRecord(readViewValue(payload, snakeKey, camelKey));
}

function readViewArray(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): unknown[] {
  const value = readViewValue(payload, snakeKey, camelKey);
  if (!Array.isArray(value)) {
    throwInvalidDashboardView();
  }
  return value;
}

function readViewString(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): string {
  const value = readViewValue(payload, snakeKey, camelKey);
  if (typeof value !== "string") {
    throwInvalidDashboardView();
  }
  return value;
}

function readViewArrayString(value: unknown): string {
  if (typeof value !== "string") {
    throwInvalidDashboardView();
  }
  return value;
}

function readViewNullableString(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): string | null {
  const value = readViewValue(payload, snakeKey, camelKey);
  if (!isNullableString(value)) {
    throwInvalidDashboardView();
  }
  return value;
}

function readViewNumber(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): number {
  const value = readViewValue(payload, snakeKey, camelKey);
  if (!isFiniteNumber(value)) {
    throwInvalidDashboardView();
  }
  return value;
}

function readViewNullableNumber(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): number | null {
  const value = readViewValue(payload, snakeKey, camelKey);
  if (value !== null && !isFiniteNumber(value)) {
    throwInvalidDashboardView();
  }
  return value;
}

function readViewBoolean(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): boolean {
  const value = readViewValue(payload, snakeKey, camelKey);
  if (typeof value !== "boolean") {
    throwInvalidDashboardView();
  }
  return value;
}

function readViewSpendBasis(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): SpendBasis {
  const value = readViewString(payload, snakeKey, camelKey);
  if (!isSpendBasis(value)) {
    throwInvalidDashboardView();
  }
  return value;
}

function throwInvalidDashboardView(): never {
  throw new Error("Dashboard view response is invalid");
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

function isDashboardViewRangeMode(
  value: unknown,
): value is DashboardViewRange["mode"] {
  return (
    typeof value === "string" &&
    (DASHBOARD_VIEW_RANGE_MODES as readonly string[]).includes(value)
  );
}

function isDashboardDataModeLabel(
  value: unknown,
): value is HeaderViewModel["dataModeLabel"] {
  return (
    typeof value === "string" &&
    (DASHBOARD_DATA_MODE_LABELS as readonly string[]).includes(value)
  );
}

function isSpendBasis(value: unknown): value is SpendBasis {
  return (
    typeof value === "string" &&
    (SPEND_BASES as readonly string[]).includes(value)
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
