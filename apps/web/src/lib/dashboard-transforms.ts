import type {
  DashboardData,
  DashboardDatasetMetadata,
  DatabaseStorageDaily,
  OrgSpendDaily,
  QueryComputeByUserDaily,
  RateSheetDaily,
  ServiceSpendDaily,
  WarehouseSpendDaily,
} from "./dashboard-contracts";

export const DEFAULT_WINDOW_DAYS = 30;
export const WINDOW_DAYS = [7, 30, 90] as const;

export type WindowDays = (typeof WINDOW_DAYS)[number];
export type SpendBasis = "billed" | "estimated";

export type DashboardTransformMetadata = DashboardDatasetMetadata;

type RateIndexEntry = {
  currency: string | null;
  effectiveRate: number;
};

export type RateIndex = Map<string, RateIndexEntry>;

function rateKey(
  usageDate: string,
  serviceType: string,
  ratingType?: string | null,
): string {
  return ratingType
    ? `${usageDate}|${serviceType}|${ratingType}`
    : `${usageDate}|${serviceType}`;
}

function isUsdOrUnspecified(currency: string | null): boolean {
  return currency === null || currency === "USD";
}

export function formatCurrency(value: number, currency: string | null): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency ?? "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatUsageDate(usageDate: string): string {
  const [year, month, day] = usageDate.split("-").map(Number);

  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function throughDateFor(
  metadata: DashboardTransformMetadata,
): string | null {
  if (metadata.data_mode === "estimated") {
    return metadata.account_usage_through_date;
  }

  return metadata.billing_through_date ?? metadata.account_usage_through_date;
}

export function windowStartFor(
  throughDate: string,
  windowDays: number,
): string {
  const [year, month, day] = throughDate.split("-").map(Number);
  const start = new Date(Date.UTC(year, month - 1, day));
  start.setUTCDate(start.getUTCDate() - (windowDays - 1));

  return start.toISOString().slice(0, 10);
}

export function buildRateIndex(rows: readonly RateSheetDaily[]): RateIndex {
  const rates: RateIndex = new Map();

  for (const row of rows) {
    const entry = {
      currency: row.currency,
      effectiveRate: row.effective_rate,
    };
    rates.set(rateKey(row.usage_date, row.service_type, row.rating_type), entry);

    const serviceOnlyKey = rateKey(row.usage_date, row.service_type);
    if (!rates.has(serviceOnlyKey) || row.rating_type === "COMPUTE") {
      rates.set(serviceOnlyKey, entry);
    }
  }

  return rates;
}

export function creditsToDollars(
  credits: number,
  usageDate: string,
  serviceType: string,
  rates: RateIndex,
  metadata: DashboardTransformMetadata,
  ratingType?: string | null,
): number | null {
  const rate =
    (ratingType ? rates.get(rateKey(usageDate, serviceType, ratingType)) : null) ??
    rates.get(rateKey(usageDate, serviceType));

  if (rate) {
    if (!isUsdOrUnspecified(rate.currency)) {
      return null;
    }

    return credits * rate.effectiveRate;
  }

  if (isUsdOrUnspecified(metadata.currency)) {
    return credits * metadata.estimated_credit_price_usd;
  }

  return null;
}

export function storageBytesToDailyDollars(
  bytes: number,
  pricePerTbMonth: number,
): number {
  return (bytes / 1_000_000_000_000) * (pricePerTbMonth / 30);
}

export type DollarPoint = {
  date: string;
  spend: number;
  spendLabel: string;
};

export type ServicePoint = {
  date: string;
  [serviceName: string]: string | number;
};

export type RankedSpendRow = {
  name: string;
  spend: number;
  spendLabel: string;
  credits: number | null;
};

export type HeaderViewModel = {
  dataModeLabel: "Billed" | "Estimated" | "Demo";
  accountLocator: string | null;
  currency: string;
  throughDate: string | null;
  throughDateLabel: string | null;
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
  isEmpty: boolean;
};

export type StorageSpendViewModel = {
  basis: SpendBasis;
  databaseBasis: SpendBasis;
  dailySeries: DollarPoint[];
  databases: Array<{
    name: string;
    bytes: number;
    monthlySpend: number;
    monthlySpendLabel: string;
  }>;
  isEmpty: boolean;
};

export type ServiceSpendViewModel = {
  basis: SpendBasis;
  dailySeries: ServicePoint[];
  serviceNames: string[];
  rankedServices: RankedSpendRow[];
  isEmpty: boolean;
};

export type DetailTablesViewModel = {
  services: RankedSpendRow[];
  warehouses: Array<RankedSpendRow & { creditsCompute: number; creditsTotal: number }>;
  users: Array<RankedSpendRow & { warehouseName: string }>;
  storage: Array<{
    name: string;
    bytes: number;
    monthlySpend: number;
    monthlySpendLabel: string;
  }>;
};

export type UnsupportedViewModel = {
  title: string;
  detail: string;
};

export type DashboardViewModel = {
  windowDays: WindowDays;
  header: HeaderViewModel;
  unsupported: UnsupportedViewModel | null;
  totalSpend: TotalSpendViewModel;
  computeSpend: ComputeSpendViewModel;
  storageSpend: StorageSpendViewModel;
  serviceSpend: ServiceSpendViewModel;
  detailTables: DetailTablesViewModel;
};

type NamedAmount = {
  name: string;
  spend: number;
  credits: number;
};

function dateRange(startDate: string, endDate: string): string[] {
  const dates: string[] = [];
  let current = startDate;

  while (current <= endDate) {
    dates.push(current);
    current = addUtcDays(current, 1);
  }

  return dates;
}

function addUtcDays(usageDate: string, days: number): string {
  const [year, month, day] = usageDate.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function rowsInWindow<T extends { usage_date: string }>(
  rows: T[],
  throughDate: string,
  windowDays: WindowDays,
): T[] {
  const startDate = windowStartFor(throughDate, windowDays);
  return rows.filter(
    (row) => row.usage_date >= startDate && row.usage_date <= throughDate,
  );
}

function emptyTotalSpend(currency: string): TotalSpendViewModel {
  return {
    basis: "estimated",
    total: 0,
    totalLabel: formatCurrency(0, currency),
    averageDaily: 0,
    averageDailyLabel: formatCurrency(0, currency),
    projectedMonthly: 0,
    projectedMonthlyLabel: formatCurrency(0, currency),
    projectionBasisLabel: "0 days",
    dailySeries: [],
    topDriver: null,
    isEmpty: true,
  };
}

function emptyComputeSpend(): ComputeSpendViewModel {
  return {
    computeBasis: "estimated",
    dailySeries: [],
    rankedWarehouses: [],
    rankedUsers: [],
    isEmpty: true,
  };
}

function emptyStorageSpend(): StorageSpendViewModel {
  return {
    basis: "estimated",
    databaseBasis: "estimated",
    dailySeries: [],
    databases: [],
    isEmpty: true,
  };
}

function emptyServiceSpend(): ServiceSpendViewModel {
  return {
    basis: "estimated",
    dailySeries: [],
    serviceNames: [],
    rankedServices: [],
    isEmpty: true,
  };
}

function emptyDetails(): DetailTablesViewModel {
  return {
    services: [],
    warehouses: [],
    users: [],
    storage: [],
  };
}

export function buildDashboardViewModel(
  data: DashboardData,
  windowDays: WindowDays,
): DashboardViewModel {
  const metadata = data.metadata;
  const currency = metadata.currency ?? "USD";
  const throughDate = throughDateFor(metadata);
  const header = buildHeaderViewModel(metadata, currency, throughDate);

  if (metadata.unsupported_reason === "mixed_currency") {
    return {
      windowDays,
      header,
      unsupported: {
        title: "Mixed currencies are not supported",
        detail: "Select an account with a single billing currency to view spend.",
      },
      totalSpend: emptyTotalSpend(currency),
      computeSpend: emptyComputeSpend(),
      storageSpend: emptyStorageSpend(),
      serviceSpend: emptyServiceSpend(),
      detailTables: emptyDetails(),
    };
  }

  if (!throughDate) {
    return {
      windowDays,
      header,
      unsupported: null,
      totalSpend: emptyTotalSpend(currency),
      computeSpend: emptyComputeSpend(),
      storageSpend: emptyStorageSpend(),
      serviceSpend: emptyServiceSpend(),
      detailTables: emptyDetails(),
    };
  }

  const rates = buildRateIndex(data.datasets.rate_sheet_daily);
  const isBilled = metadata.data_mode === "billed" || metadata.data_mode === "demo";
  const basis: SpendBasis = isBilled ? "billed" : "estimated";
  const billedRows = rowsInWindow(
    data.datasets.org_spend_daily.filter(isConsumptionSpendRow),
    throughDate,
    windowDays,
  );
  const projectionBilledRows = rowsInWindow(
    data.datasets.org_spend_daily.filter(isConsumptionSpendRow),
    throughDate,
    DEFAULT_WINDOW_DAYS,
  );
  const serviceRows = rowsInWindow(
    data.datasets.service_spend_daily,
    throughDate,
    windowDays,
  );
  const projectionServiceRows = rowsInWindow(
    data.datasets.service_spend_daily,
    throughDate,
    DEFAULT_WINDOW_DAYS,
  );
  const warehouseRows = rowsInWindow(
    data.datasets.warehouse_spend_daily,
    throughDate,
    windowDays,
  );
  const userRows = rowsInWindow(
    data.datasets.query_compute_by_user_daily,
    throughDate,
    windowDays,
  );
  const storageRows = rowsInWindow(
    data.datasets.database_storage_daily,
    throughDate,
    windowDays,
  );
  const billedStorageRows = billedRows.filter(isStorageSpendRow);
  const dates = dateRange(windowStartFor(throughDate, windowDays), throughDate);
  const projectionDates = dateRange(
    windowStartFor(throughDate, DEFAULT_WINDOW_DAYS),
    throughDate,
  );
  const convert = (
    credits: number,
    usageDate: string,
    serviceType: string,
    ratingType?: string | null,
  ) =>
    creditsToDollars(credits, usageDate, serviceType, rates, metadata, ratingType) ??
    0;
  const totalDaily = isBilled
    ? dailyBilledTotals(dates, billedRows)
    : dailyEstimatedTotals(dates, serviceRows, convert);
  const projectionDaily = isBilled
    ? dailyBilledTotals(projectionDates, projectionBilledRows)
    : dailyEstimatedTotals(projectionDates, projectionServiceRows, convert);
  const serviceSpend = buildServiceSpend(
    dates,
    isBilled ? billedRows : serviceRows,
    basis,
    currency,
    convert,
  );
  const computeSpend = buildComputeSpend(
    dates,
    warehouseRows,
    userRows,
    currency,
    convert,
  );
  const storageSpend = buildStorageSpend(
    dates,
    storageRows,
    billedStorageRows,
    basis,
    metadata.storage_price_usd_per_tb_month,
    currency,
  );
  const totalSpend = buildTotalSpend(
    totalDaily,
    projectionDaily,
    serviceSpend.rankedServices,
    basis,
    currency,
    windowDays,
  );

  return {
    windowDays,
    header,
    unsupported: null,
    totalSpend,
    computeSpend,
    storageSpend,
    serviceSpend,
    detailTables: {
      services: serviceSpend.rankedServices,
      warehouses: buildWarehouseDetails(warehouseRows, currency, convert),
      users: computeSpend.rankedUsers.map((row) => ({
        ...row,
        warehouseName: "Multiple warehouses",
      })),
      storage: storageSpend.databases,
    },
  };
}

function buildHeaderViewModel(
  metadata: DashboardTransformMetadata,
  currency: string,
  throughDate: string | null,
): HeaderViewModel {
  return {
    dataModeLabel: dataModeLabel(metadata.data_mode),
    accountLocator: metadata.account_locator,
    currency,
    throughDate,
    throughDateLabel: throughDate ? formatUsageDate(throughDate) : null,
    estimatedCreditPriceLabel: formatCurrency(
      metadata.estimated_credit_price_usd,
      currency,
    ),
    storagePriceLabel: formatCurrency(
      metadata.storage_price_usd_per_tb_month,
      currency,
    ),
  };
}

function dataModeLabel(
  mode: DashboardTransformMetadata["data_mode"],
): HeaderViewModel["dataModeLabel"] {
  if (mode === "demo") return "Demo";
  if (mode === "billed") return "Billed";
  return "Estimated";
}

function isConsumptionSpendRow(row: OrgSpendDaily): boolean {
  return row.billing_type === "CONSUMPTION";
}

function isStorageSpendRow(row: OrgSpendDaily): boolean {
  return row.rating_type === "STORAGE" || row.service_type === "STORAGE";
}

function dailyBilledTotals(
  dates: string[],
  rows: OrgSpendDaily[],
): DollarPoint[] {
  return dates.map((date) => ({
    date,
    spend: rows
      .filter((row) => row.usage_date === date)
      .reduce((total, row) => total + row.spend, 0),
    spendLabel: "",
  }));
}

function dailyEstimatedTotals(
  dates: string[],
  rows: ServiceSpendDaily[],
  convert: (
    credits: number,
    usageDate: string,
    serviceType: string,
    ratingType?: string | null,
  ) => number,
): DollarPoint[] {
  return dates.map((date) => ({
    date,
    spend: rows
      .filter((row) => row.usage_date === date)
      .reduce(
        (total, row) =>
          total + convert(row.credits_used, row.usage_date, row.service_type),
        0,
      ),
    spendLabel: "",
  }));
}

function buildTotalSpend(
  dailySeries: DollarPoint[],
  projectionDailySeries: DollarPoint[],
  rankedServices: RankedSpendRow[],
  basis: SpendBasis,
  currency: string,
  windowDays: WindowDays,
): TotalSpendViewModel {
  const labeledSeries = dailySeries.map((row) => ({
    ...row,
    spendLabel: formatCurrency(row.spend, currency),
  }));
  const total = labeledSeries.reduce((sum, row) => sum + row.spend, 0);
  const averageDaily = windowDays > 0 ? total / windowDays : 0;
  const projectedMonthly =
    (projectionDailySeries.reduce((sum, row) => sum + row.spend, 0) /
      DEFAULT_WINDOW_DAYS) *
    30;

  return {
    basis,
    total,
    totalLabel: formatCurrency(total, currency),
    averageDaily,
    averageDailyLabel: formatCurrency(averageDaily, currency),
    projectedMonthly,
    projectedMonthlyLabel: formatCurrency(projectedMonthly, currency),
    projectionBasisLabel: "latest 30 days",
    dailySeries: labeledSeries,
    topDriver: rankedServices[0] ?? null,
    isEmpty: labeledSeries.every((row) => row.spend === 0),
  };
}

function buildComputeSpend(
  dates: string[],
  warehouseRows: WarehouseSpendDaily[],
  userRows: QueryComputeByUserDaily[],
  currency: string,
  convert: (
    credits: number,
    usageDate: string,
    serviceType: string,
    ratingType?: string | null,
  ) => number,
): ComputeSpendViewModel {
  const dailySeries = dates.map((date) => {
    const spend = warehouseRows
      .filter((row) => row.usage_date === date)
      .reduce(
        (total, row) =>
          total +
          convert(
            row.credits_used_compute,
            row.usage_date,
            "WAREHOUSE_METERING",
            "COMPUTE",
          ),
        0,
      );
    return { date, spend, spendLabel: formatCurrency(spend, currency) };
  });

  return {
    computeBasis: "estimated",
    dailySeries,
    rankedWarehouses: rankNamedAmounts(
      warehouseRows.map((row) => ({
        name: row.warehouse_name,
        credits: row.credits_used_compute,
        spend: convert(
          row.credits_used_compute,
          row.usage_date,
          "WAREHOUSE_METERING",
          "COMPUTE",
        ),
      })),
      currency,
    ),
    rankedUsers: rankNamedAmounts(
      userRows.map((row) => ({
        name: row.user_name,
        credits: row.credits_attributed_compute,
        spend: convert(
          row.credits_attributed_compute,
          row.usage_date,
          "WAREHOUSE_METERING",
          "COMPUTE",
        ),
      })),
      currency,
    ),
    isEmpty: dailySeries.every((row) => row.spend === 0),
  };
}

function buildStorageSpend(
  dates: string[],
  rows: DatabaseStorageDaily[],
  billedRows: OrgSpendDaily[],
  basis: SpendBasis,
  pricePerTbMonth: number,
  currency: string,
): StorageSpendViewModel {
  const dailySeries =
    basis === "billed"
      ? dates.map((date) => {
          const spend = billedRows
            .filter((row) => row.usage_date === date)
            .reduce((total, row) => total + row.spend, 0);
          return { date, spend, spendLabel: formatCurrency(spend, currency) };
        })
      : dates.map((date) => {
          const spend = rows
            .filter((row) => row.usage_date === date)
            .reduce(
              (total, row) =>
                total +
                storageBytesToDailyDollars(
                  row.average_database_bytes + row.average_failsafe_bytes,
                  pricePerTbMonth,
                ),
              0,
            );
          return { date, spend, spendLabel: formatCurrency(spend, currency) };
        });
  const latestDate = rows.reduce<string | null>(
    (latest, row) => (!latest || row.usage_date > latest ? row.usage_date : latest),
    null,
  );
  const databases = rankStorageRows(
    latestDate ? rows.filter((row) => row.usage_date === latestDate) : [],
    pricePerTbMonth,
    currency,
  );

  return {
    basis,
    databaseBasis: "estimated",
    dailySeries,
    databases,
    isEmpty: dailySeries.every((row) => row.spend === 0),
  };
}

function buildServiceSpend(
  dates: string[],
  rows: OrgSpendDaily[] | ServiceSpendDaily[],
  basis: SpendBasis,
  currency: string,
  convert: (
    credits: number,
    usageDate: string,
    serviceType: string,
    ratingType?: string | null,
  ) => number,
): ServiceSpendViewModel {
  const serviceNames = Array.from(
    new Set(rows.map((row) => row.service_type)),
  ).sort();
  const dailySeries = dates.map((date) => {
    const point: ServicePoint = { date };
    for (const serviceName of serviceNames) {
      point[serviceName] = rows
        .filter(
          (row) => row.usage_date === date && row.service_type === serviceName,
        )
        .reduce((total, row) => total + serviceSpend(row, basis, convert), 0);
    }
    return point;
  });
  const rankedServices = rankNamedAmounts(
    rows.map((row) => ({
      name: row.service_type,
      credits: "credits_used" in row ? row.credits_used : 0,
      spend: serviceSpend(row, basis, convert),
    })),
    currency,
  );

  return {
    basis,
    dailySeries,
    serviceNames,
    rankedServices,
    isEmpty: rankedServices.length === 0,
  };
}

function serviceSpend(
  row: OrgSpendDaily | ServiceSpendDaily,
  basis: SpendBasis,
  convert: (
    credits: number,
    usageDate: string,
    serviceType: string,
    ratingType?: string | null,
  ) => number,
): number {
  if (basis === "billed" && "spend" in row) return row.spend;
  if ("credits_used" in row) {
    return convert(row.credits_used, row.usage_date, row.service_type);
  }
  return 0;
}

function rankNamedAmounts(
  rows: NamedAmount[],
  currency: string,
): RankedSpendRow[] {
  const byName = new Map<string, { spend: number; credits: number }>();
  for (const row of rows) {
    const current = byName.get(row.name) ?? { spend: 0, credits: 0 };
    byName.set(row.name, {
      spend: current.spend + row.spend,
      credits: current.credits + row.credits,
    });
  }

  return Array.from(byName, ([name, value]) => ({
    name,
    spend: value.spend,
    spendLabel: formatCurrency(value.spend, currency),
    credits: value.credits,
  })).sort((a, b) => b.spend - a.spend);
}

function rankStorageRows(
  rows: DatabaseStorageDaily[],
  pricePerTbMonth: number,
  currency: string,
): StorageSpendViewModel["databases"] {
  return rows
    .map((row) => {
      const bytes = row.average_database_bytes + row.average_failsafe_bytes;
      const monthlySpend =
        (bytes / 1_000_000_000_000) * pricePerTbMonth;
      return {
        name: row.database_name ?? "Unknown database",
        bytes,
        monthlySpend,
        monthlySpendLabel: formatCurrency(monthlySpend, currency),
      };
    })
    .sort((a, b) => b.monthlySpend - a.monthlySpend);
}

function buildWarehouseDetails(
  rows: WarehouseSpendDaily[],
  currency: string,
  convert: (
    credits: number,
    usageDate: string,
    serviceType: string,
    ratingType?: string | null,
  ) => number,
): DetailTablesViewModel["warehouses"] {
  const byWarehouse = new Map<
    string,
    { spend: number; creditsCompute: number; creditsTotal: number }
  >();

  for (const row of rows) {
    const current = byWarehouse.get(row.warehouse_name) ?? {
      spend: 0,
      creditsCompute: 0,
      creditsTotal: 0,
    };
    byWarehouse.set(row.warehouse_name, {
      spend:
        current.spend +
        convert(
          row.credits_used_compute,
          row.usage_date,
          "WAREHOUSE_METERING",
          "COMPUTE",
        ),
      creditsCompute: current.creditsCompute + row.credits_used_compute,
      creditsTotal: current.creditsTotal + row.credits_used,
    });
  }

  return Array.from(byWarehouse, ([name, value]) => ({
    name,
    spend: value.spend,
    spendLabel: formatCurrency(value.spend, currency),
    credits: value.creditsCompute,
    creditsCompute: value.creditsCompute,
    creditsTotal: value.creditsTotal,
  })).sort((a, b) => b.spend - a.spend);
}
