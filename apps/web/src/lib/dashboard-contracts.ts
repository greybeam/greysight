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
  credits_attributed_queries: number;
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
  average_hybrid_table_storage_bytes: number | null;
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
  usage_type: string;
  rating_type: string | null;
  currency: string | null;
  effective_rate: number;
};

export type CapacityBalanceDaily = {
  usage_date: string;
  currency: string;
  balance: number;
};

export type CurrentAccount = {
  account_locator: string;
};

export type SourceAvailability = {
  available: boolean;
  detail: string | null;
  user_safe_message: string | null;
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
  capacity_balance_daily: CapacityBalanceDaily[];
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

export type BalancePoint = {
  date: string;
  balance: number;
  balanceLabel: string;
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

export type WarehouseIdleBarRow = RankedSpendRow & {
  idlePct: number | null;
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

export type CapacityBalanceViewModel = {
  currentBalance: number;
  currentBalanceLabel: string;
  currentBalanceDate: string | null;
  dailySeries: BalancePoint[];
  forecastSeries: BalancePoint[];
  isEmpty: boolean;
};

export type WarehousePoint = {
  date: string;
  values: Record<string, number>;
};

export type WarehouseSpendViewModel = {
  basis: SpendBasis;
  total: number;
  totalLabel: string;
  dailySeries: WarehousePoint[];
  warehouseNames: string[];
  rankedWarehouses: RankedSpendRow[];
  rankedUsers: RankedSpendRow[];
  warehouseBars: WarehouseIdleBarRow[];
  userBars: RankedBarRow[];
  isEmpty: boolean;
};

export type StorageDatabaseRow = {
  name: string;
  bytes: number;
  bytesLabel: string;
  monthlySpend: number;
  monthlySpendLabel: string;
  periodSpend: number;
  periodSpendLabel: string;
};

export type StoragePoint = {
  date: string;
  values: Record<string, number>;
};

export type StorageSpendViewModel = {
  basis: SpendBasis;
  databaseBasis: SpendBasis;
  total: number;
  totalLabel: string;
  dailySeries: DollarPoint[];
  databases: StorageDatabaseRow[];
  databaseBars: RankedBarRow[];
  databaseNames: string[];
  databaseDailySeries: StoragePoint[];
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

export type AIConsumptionPoint = {
  date: string;
  values: Record<string, number>;
};

export type AISpendSummaryViewModel = {
  total: number;
  totalLabel: string;
  isEmpty: boolean;
};

export type AIDetailViewModel = {
  dailySeries: AIConsumptionPoint[];
  consumptionTypeNames: string[];
  rankedConsumptionTypes: RankedSpendRow[];
  consumptionBars: RankedBarRow[];
  isEmpty: boolean;
  partial: boolean;
  skippedBranches: string[];
};

export type DashboardSectionStatus = "pending" | "ready" | "unavailable";

export type DashboardViewSectionKey = "overview" | "warehouse" | "storage";

export type DashboardViewSectionStatuses = Record<
  DashboardViewSectionKey,
  DashboardSectionStatus
>;

export type DashboardView = {
  schema_version: 1;
  run: DashboardRun;
  // Source-group availability metadata, present on completed/partial views so the
  // classified `user_safe_message` for an unavailable group (account/organization
  // usage) can be surfaced per section. Optional: legacy stored views omit it.
  metadata?: DashboardDatasetMetadata;
  range: DashboardViewRange;
  projectionRange: DashboardProjectionRange;
  header: HeaderViewModel;
  unsupported: UnsupportedViewModel | null;
  capacityBalance: CapacityBalanceViewModel;
  totalSpend: TotalSpendViewModel;
  warehouseSpend: WarehouseSpendViewModel;
  storageSpend: StorageSpendViewModel;
  serviceSpend: ServiceSpendViewModel;
  detailTables: DetailTablesViewModel;
  aiSpendSummary: AISpendSummaryViewModel;
  sectionStatuses: DashboardViewSectionStatuses;
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
  "capacity_balance_daily",
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

const DASHBOARD_VIEW_SECTION_KEYS = [
  "overview",
  "warehouse",
  "storage",
] as const satisfies readonly DashboardViewSectionKey[];

const DASHBOARD_SECTION_STATUSES = [
  "pending",
  "ready",
  "unavailable",
] as const satisfies readonly DashboardSectionStatus[];

const ALL_READY_SECTION_STATUSES: DashboardViewSectionStatuses = {
  overview: "ready",
  warehouse: "ready",
  storage: "ready",
};

function isDashboardSectionStatus(
  value: unknown,
): value is DashboardSectionStatus {
  return (
    typeof value === "string" &&
    (DASHBOARD_SECTION_STATUSES as readonly string[]).includes(value)
  );
}

function parseSectionStatuses(
  payload: Record<string, unknown>,
): DashboardViewSectionStatuses {
  if (!hasViewValue(payload, "section_statuses", "sectionStatuses")) {
    return { ...ALL_READY_SECTION_STATUSES };
  }
  const record = readViewRecord(payload, "section_statuses", "sectionStatuses");
  const result: DashboardViewSectionStatuses = { ...ALL_READY_SECTION_STATUSES };
  for (const key of DASHBOARD_VIEW_SECTION_KEYS) {
    const value = record[key];
    if (value === undefined) {
      continue;
    }
    if (!isDashboardSectionStatus(value)) {
      throwInvalidDashboardView();
    }
    result[key] = value;
  }
  return result;
}

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
      capacity_balance_daily: payload.datasets.capacity_balance_daily,
      current_account: payload.datasets.current_account,
    } as DashboardDatasets,
  };
}

export function parseDashboardView(payload: unknown): DashboardView {
  if (!isRecord(payload) || payload.schema_version !== 1) {
    throwInvalidDashboardView();
  }

  const header = parseHeaderViewModel(readViewRecord(payload, "header"));

  return {
    schema_version: 1,
    run: parseDashboardViewRun(readViewRecord(payload, "run")),
    ...(hasViewValue(payload, "metadata")
      ? { metadata: parseDashboardMetadata(readViewRecord(payload, "metadata")) }
      : {}),
    range: parseDashboardViewRange(readViewRecord(payload, "range")),
    projectionRange: parseDashboardProjectionRange(
      readViewRecord(payload, "projection_range", "projectionRange"),
    ),
    header,
    unsupported: parseUnsupportedViewModel(readViewValue(payload, "unsupported")),
    capacityBalance: hasViewValue(payload, "capacity_balance", "capacityBalance")
      ? parseCapacityBalanceViewModel(
          readViewRecord(payload, "capacity_balance", "capacityBalance"),
        )
      : emptyCapacityBalanceViewModel(header.currency),
    totalSpend: parseTotalSpendViewModel(
      readViewRecord(payload, "total_spend", "totalSpend"),
    ),
    warehouseSpend: parseWarehouseSpendViewModel(
      readViewRecord(payload, "warehouse_spend", "warehouseSpend"),
    ),
    storageSpend: parseStorageSpendViewModel(
      readViewRecord(payload, "storage_spend", "storageSpend"),
      header.currency,
    ),
    serviceSpend: parseServiceSpendViewModel(
      readViewRecord(payload, "service_spend", "serviceSpend"),
    ),
    detailTables: parseDetailTablesViewModel(
      readViewRecord(payload, "detail_tables", "detailTables"),
    ),
    aiSpendSummary: hasViewValue(payload, "ai_spend_summary", "aiSpendSummary")
      ? parseAISpendSummaryViewModel(
          readViewRecord(payload, "ai_spend_summary", "aiSpendSummary"),
        )
      : { total: 0, totalLabel: "$0.00", isEmpty: true },
    sectionStatuses: parseSectionStatuses(payload),
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

function parseCapacityBalanceViewModel(
  payload: Record<string, unknown>,
): CapacityBalanceViewModel {
  return {
    currentBalance: readViewNumber(
      payload,
      "current_balance",
      "currentBalance",
    ),
    currentBalanceLabel: readViewString(
      payload,
      "current_balance_label",
      "currentBalanceLabel",
    ),
    currentBalanceDate: readViewNullableString(
      payload,
      "current_balance_date",
      "currentBalanceDate",
    ),
    dailySeries: readViewArray(payload, "daily_series", "dailySeries").map(
      parseBalancePoint,
    ),
    forecastSeries: hasViewValue(payload, "forecast_series", "forecastSeries")
      ? readViewArray(payload, "forecast_series", "forecastSeries").map(
          parseBalancePoint,
        )
      : [],
    isEmpty: readViewBoolean(payload, "is_empty", "isEmpty"),
  };
}

// Mirrors the backend's `_format_currency` in dashboard_view_builder.py so the
// fallback zero label matches the server's formatting for the dashboard's
// currency (e.g. "$0.00" for USD, "€0.00" for EUR, "¥0" for JPY).
const CURRENCY_SYMBOL_PREFIXES: Record<string, string> = {
  EUR: "€",
  GBP: "£",
  JPY: "¥",
  KRW: "₩",
  CAD: "CA$",
  AUD: "A$",
  NZD: "NZ$",
  MXN: "MX$",
  INR: "₹",
  CNY: "CN¥",
  HKD: "HK$",
  BRL: "R$",
  ILS: "₪",
  TWD: "NT$",
  PHP: "₱",
};
const CURRENCY_CODE_PREFIXES = new Set([
  "CHF",
  "CZK",
  "DKK",
  "HUF",
  "IDR",
  "MYR",
  "NOK",
  "PLN",
  "SEK",
  "SGD",
  "THB",
  "TRY",
  "ZAR",
]);
const CURRENCY_CODE_SEPARATOR = " ";
const CURRENCY_COMPACT_DECIMAL_CODES = new Set(["HUF", "IDR", "JPY", "KRW"]);

function formatZeroCurrencyLabel(currency: string): string {
  const resolvedCurrency = currency || "USD";
  const amount = CURRENCY_COMPACT_DECIMAL_CODES.has(resolvedCurrency)
    ? "0"
    : "0.00";

  if (resolvedCurrency === "USD") {
    return `$${amount}`;
  }
  const symbol = CURRENCY_SYMBOL_PREFIXES[resolvedCurrency];
  if (symbol !== undefined) {
    return `${symbol}${amount}`;
  }
  if (CURRENCY_CODE_PREFIXES.has(resolvedCurrency)) {
    return `${resolvedCurrency}${CURRENCY_CODE_SEPARATOR}${amount}`;
  }
  return `${amount} ${resolvedCurrency}`;
}

function emptyCapacityBalanceViewModel(
  currency: string,
): CapacityBalanceViewModel {
  return {
    currentBalance: 0,
    currentBalanceLabel: formatZeroCurrencyLabel(currency),
    currentBalanceDate: null,
    dailySeries: [],
    forecastSeries: [],
    isEmpty: true,
  };
}

function parseWarehouseSpendViewModel(
  payload: Record<string, unknown>,
): WarehouseSpendViewModel {
  return {
    basis: readViewSpendBasis(payload, "basis"),
    total: readViewNumber(payload, "total"),
    totalLabel: readViewString(payload, "total_label", "totalLabel"),
    dailySeries: readViewArray(payload, "daily_series", "dailySeries").map(
      parseWarehousePoint,
    ),
    warehouseNames: readViewArray(
      payload,
      "warehouse_names",
      "warehouseNames",
    ).map(readViewArrayString),
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
    ).map(parseWarehouseIdleBarRow),
    userBars: readViewArray(payload, "user_bars", "userBars").map(
      parseRankedBarRow,
    ),
    isEmpty: readViewBoolean(payload, "is_empty", "isEmpty"),
  };
}

function parseStorageSpendViewModel(
  payload: Record<string, unknown>,
  currency: string,
): StorageSpendViewModel {
  return {
    basis: readViewSpendBasis(payload, "basis"),
    databaseBasis: readViewSpendBasis(
      payload,
      "database_basis",
      "databaseBasis",
    ),
    // Older stored views predate the storage KPI fields; fall back to a zeroed
    // total with a currency-correct label and empty stacked-series arrays rather
    // than throwing on their absence.
    total: hasViewValue(payload, "total")
      ? readViewNumber(payload, "total")
      : 0,
    totalLabel: hasViewValue(payload, "total_label", "totalLabel")
      ? readViewString(payload, "total_label", "totalLabel")
      : formatZeroCurrencyLabel(currency),
    dailySeries: readViewArray(payload, "daily_series", "dailySeries").map(
      parseDollarPoint,
    ),
    databases: readViewArray(payload, "databases").map(parseStorageDatabaseRow),
    databaseBars: readViewArray(
      payload,
      "database_bars",
      "databaseBars",
    ).map(parseRankedBarRow),
    databaseNames: hasViewValue(payload, "database_names", "databaseNames")
      ? readViewArray(payload, "database_names", "databaseNames").map(
          readViewArrayString,
        )
      : [],
    databaseDailySeries: hasViewValue(
      payload,
      "database_daily_series",
      "databaseDailySeries",
    )
      ? readViewArray(
          payload,
          "database_daily_series",
          "databaseDailySeries",
        ).map(parseStoragePoint)
      : [],
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

function parseBalancePoint(payload: unknown): BalancePoint {
  const record = asViewRecord(payload);
  return {
    date: readViewString(record, "date"),
    balance: readViewNumber(record, "balance"),
    balanceLabel: readViewString(record, "balance_label", "balanceLabel"),
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

// WarehousePoint and ServicePoint share the same {date, values} shape, so the
// stacked-by-warehouse series reuses the service-point parser.
function parseWarehousePoint(payload: unknown): WarehousePoint {
  return parseServicePoint(payload);
}

// StoragePoint mirrors the warehouse/service {date, values} stacked-series shape
// (here keyed by database), so it reuses the same point parser.
function parseStoragePoint(payload: unknown): StoragePoint {
  return parseServicePoint(payload);
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

function parseWarehouseIdleBarRow(payload: unknown): WarehouseIdleBarRow {
  const record = asViewRecord(payload);
  return {
    ...parseRankedSpendRow(record),
    idlePct: readViewNullableNumber(record, "idle_pct", "idlePct"),
  };
}

function parseStorageDatabaseRow(payload: unknown): StorageDatabaseRow {
  const record = asViewRecord(payload);
  const bytes = readViewNumber(record, "bytes");
  const monthlySpendLabel = readViewString(
    record,
    "monthly_spend_label",
    "monthlySpendLabel",
  );
  return {
    name: readViewString(record, "name"),
    bytes,
    // Older stored views lack the pre-humanized size label; derive it
    // client-side from the raw byte count so the table still renders a size.
    bytesLabel: hasViewValue(record, "bytes_label", "bytesLabel")
      ? readViewString(record, "bytes_label", "bytesLabel")
      : humanizeBytes(bytes),
    monthlySpend: readViewNumber(record, "monthly_spend", "monthlySpend"),
    monthlySpendLabel,
    // Newer views carry per-database spend scoped to the active window. Legacy
    // payloads predate it, so mirror the bytes_label fallback convention: zero
    // the numeric value and reuse the monthly label as the displayable text.
    periodSpend: hasViewValue(record, "period_spend", "periodSpend")
      ? readViewNumber(record, "period_spend", "periodSpend")
      : 0,
    periodSpendLabel: hasViewValue(
      record,
      "period_spend_label",
      "periodSpendLabel",
    )
      ? readViewString(record, "period_spend_label", "periodSpendLabel")
      : monthlySpendLabel,
  };
}

// 1000-base, one-decimal byte humanizer mirroring the backend's `bytes_label`
// formatting (e.g. 10_500_000_000_000 → "10.5 TB"). Used only as a fallback for
// legacy stored views that predate the server-side label.
const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB", "EB"] as const;

function humanizeBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1000 && unitIndex < BYTE_UNITS.length - 1) {
    value /= 1000;
    unitIndex += 1;
  }
  // Bytes render as whole numbers; larger units carry one decimal.
  const formatted =
    unitIndex === 0 ? String(Math.round(value)) : value.toFixed(1);
  return `${formatted} ${BYTE_UNITS[unitIndex]}`;
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
  if (
    payload.user_safe_message !== undefined &&
    !isNullableString(payload.user_safe_message)
  ) {
    throw new Error(`Dashboard metadata ${key} user_safe_message is invalid`);
  }

  return {
    available: payload.available,
    detail: payload.detail ?? null,
    user_safe_message: payload.user_safe_message ?? null,
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

function hasViewValue(
  payload: Record<string, unknown>,
  snakeKey: string,
  camelKey = snakeKey,
): boolean {
  return (
    Object.hasOwn(payload, snakeKey) ||
    (camelKey !== snakeKey && Object.hasOwn(payload, camelKey))
  );
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

function parseAISpendSummaryViewModel(
  payload: Record<string, unknown>,
): AISpendSummaryViewModel {
  return {
    total: readViewNumber(payload, "total"),
    totalLabel: readViewString(payload, "total_label", "totalLabel"),
    isEmpty: readViewBoolean(payload, "is_empty", "isEmpty"),
  };
}

export function parseAIDetailViewModel(payload: unknown): AIDetailViewModel {
  const record = asViewRecord(payload);
  return {
    dailySeries: readViewArray(record, "daily_series", "dailySeries").map(
      parseServicePoint,
    ),
    consumptionTypeNames: readViewArray(
      record,
      "consumption_type_names",
      "consumptionTypeNames",
    ).map(readViewArrayString),
    rankedConsumptionTypes: readViewArray(
      record,
      "ranked_consumption_types",
      "rankedConsumptionTypes",
    ).map(parseRankedSpendRow),
    consumptionBars: readViewArray(
      record,
      "consumption_bars",
      "consumptionBars",
    ).map(parseRankedBarRow),
    isEmpty: readViewBoolean(record, "is_empty", "isEmpty"),
    partial: readViewBoolean(record, "partial"),
    skippedBranches: readViewArray(
      record,
      "skipped_branches",
      "skippedBranches",
    ).map(readViewArrayString),
  };
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
