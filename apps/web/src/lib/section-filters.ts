// In-memory, per-section filtering of already-retrieved dashboard view models
// (CEO-sanctioned interim step; a later DuckDB refactor moves this server-side).
// Every function is pure and returns new objects. Zero-drift default: an all /
// empty selection performs NO recompute — totals, labels, and bar widths pass
// through verbatim; only display-bucketing of the chart series always runs.

import { formatCurrency } from "./currency-format";
import {
  bucketStackedSeries,
  type StackedPoint,
} from "./stacked-series-bucketing";
import type {
  AIConsumptionPoint,
  AIDetailViewModel,
  RankedBarRow,
  ServicePoint,
  ServiceSpendViewModel,
  StorageDatabaseRow,
  StoragePoint,
  StorageSpendViewModel,
  WarehousePoint,
  WarehouseIdleBarRow,
  WarehouseSpendViewModel,
} from "./dashboard-contracts";

// Empty selection is treated as "all". A selection is "full" when it covers
// every available option (order-independent).
export function isFullSelection(selected: string[], all: string[]): boolean {
  if (selected.length === 0) return true;
  if (selected.length !== all.length) return false;
  const set = new Set(selected);
  return all.every((name) => set.has(name));
}

// Recompute bar widths relative to the new visible max. Guarded against
// divide-by-zero (max 0 → widths 0). Mirrors backend `_build_ranked_bar_rows`.
export function recomputeBarWidths(rows: RankedBarRow[]): RankedBarRow[] {
  const topSpend = rows.length > 0 ? rows[0].spend : 0;
  return rows.map((row) => ({
    ...row,
    barWidthPercent: topSpend > 0 ? Math.max(0, (row.spend / topSpend) * 100) : 0,
  }));
}

// Filter a complete stacked series to the selection, then display-bucket it.
export function filterStackedSeries(
  names: string[],
  dailySeries: StackedPoint[],
  selected: string[],
): { names: string[]; dailySeries: StackedPoint[] } {
  const selectedSet = new Set(selected);
  const filteredNames = names.filter((n) => selectedSet.has(n));
  const filteredSeries = dailySeries.map((point) => ({
    date: point.date,
    values: Object.fromEntries(
      Object.entries(point.values).filter(([name]) => selectedSet.has(name)),
    ),
  }));
  return bucketStackedSeries(filteredNames, filteredSeries);
}

export type FilteredServiceSpend = {
  serviceNames: string[];
  dailySeries: ServicePoint[];
  serviceBars: RankedBarRow[];
  // null in the zero-drift default; the Overview KPI then uses `totalSpend` verbatim.
  total: number | null;
  totalLabel: string | null;
};

export function filterServiceSpend(
  view: ServiceSpendViewModel,
  selected: string[],
  currency: string,
): FilteredServiceSpend {
  const full = isFullSelection(selected, view.serviceNames);
  const effective = full ? view.serviceNames : selected;

  const bucketed = full
    ? bucketStackedSeries(view.serviceNames, view.dailySeries)
    : filterStackedSeries(view.serviceNames, view.dailySeries, effective);

  if (full) {
    return {
      serviceNames: bucketed.names,
      dailySeries: bucketed.dailySeries,
      serviceBars: view.serviceBars,
      total: null,
      totalLabel: null,
    };
  }

  const selectedSet = new Set(effective);
  const total = view.rankedServices
    .filter((r) => selectedSet.has(r.name))
    .reduce((sum, r) => sum + r.spend, 0);
  const serviceBars = recomputeBarWidths(
    view.serviceBars.filter((b) => selectedSet.has(b.name)),
  );

  return {
    serviceNames: bucketed.names,
    dailySeries: bucketed.dailySeries,
    serviceBars,
    total,
    totalLabel: formatCurrency(total, currency),
  };
}

export type FilteredWarehouseSpend = {
  warehouseNames: string[];
  dailySeries: WarehousePoint[];
  warehouseBars: WarehouseIdleBarRow[];
  userBars: RankedBarRow[];
  total: number | null;
  totalLabel: string | null;
};

export function filterWarehouseSpend(
  view: WarehouseSpendViewModel,
  selected: string[],
  currency: string,
): FilteredWarehouseSpend {
  const full = isFullSelection(selected, view.warehouseNames);
  const effective = full ? view.warehouseNames : selected;

  const bucketed = full
    ? bucketStackedSeries(view.warehouseNames, view.dailySeries)
    : filterStackedSeries(view.warehouseNames, view.dailySeries, effective);

  if (full) {
    return {
      warehouseNames: bucketed.names,
      dailySeries: bucketed.dailySeries,
      warehouseBars: view.warehouseBars,
      userBars: view.userBars,
      total: null,
      totalLabel: null,
    };
  }

  const selectedSet = new Set(effective);
  const total = view.rankedWarehouses
    .filter((r) => selectedSet.has(r.name))
    .reduce((sum, r) => sum + r.spend, 0);

  return {
    warehouseNames: bucketed.names,
    dailySeries: bucketed.dailySeries,
    // Idle bars: width is per-warehouse idlePct, so no recompute — just drop rows.
    warehouseBars: view.warehouseBars.filter((b) => selectedSet.has(b.name)),
    // userBars carry no per-warehouse breakdown; never filtered (see "filter not
    // applied" note in the section). Deferred to the DuckDB refactor.
    userBars: view.userBars,
    total,
    totalLabel: formatCurrency(total, currency),
  };
}

export type FilteredStorageSpend = {
  databaseNames: string[];
  databaseDailySeries: StoragePoint[];
  databaseBars: RankedBarRow[];
  databases: StorageDatabaseRow[];
  total: number | null;
  totalLabel: string | null;
};

export function filterStorageSpend(
  view: StorageSpendViewModel,
  selected: string[],
  currency: string,
): FilteredStorageSpend {
  const full = isFullSelection(selected, view.databaseNames);
  const effective = full ? view.databaseNames : selected;

  const bucketed = full
    ? bucketStackedSeries(view.databaseNames, view.databaseDailySeries)
    : filterStackedSeries(view.databaseNames, view.databaseDailySeries, effective);

  if (full) {
    return {
      databaseNames: bucketed.names,
      databaseDailySeries: bucketed.dailySeries,
      databaseBars: view.databaseBars,
      databases: view.databases,
      total: null,
      totalLabel: null,
    };
  }

  const selectedSet = new Set(effective);
  // Table total is the sum of selected databases' period spend.
  const total = view.databases
    .filter((d) => selectedSet.has(d.name))
    .reduce((sum, d) => sum + d.periodSpend, 0);

  return {
    databaseNames: bucketed.names,
    databaseDailySeries: bucketed.dailySeries,
    databaseBars: recomputeBarWidths(
      view.databaseBars.filter((b) => selectedSet.has(b.name)),
    ),
    databases: view.databases.filter((d) => selectedSet.has(d.name)),
    total,
    totalLabel: formatCurrency(total, currency),
  };
}

export type FilteredAiDetail = {
  consumptionTypeNames: string[];
  dailySeries: AIConsumptionPoint[];
  consumptionBars: RankedBarRow[];
  // null when unfiltered → section shows the verbatim billed KPI. Non-null when
  // filtered → section shows this detail-derived sum + "estimated from detail"
  // microcopy. Billed metering is deliberately decoupled from the detail sum;
  // this billed-vs-detail switch is confined to the AI section (decision 2b).
  // Retired by the DuckDB refactor.
  detailTotal: number | null;
  detailTotalLabel: string | null;
};

export function filterAiDetail(
  view: AIDetailViewModel,
  selected: string[],
  currency: string,
): FilteredAiDetail {
  const full = isFullSelection(selected, view.consumptionTypeNames);
  const effective = full ? view.consumptionTypeNames : selected;

  const bucketed = full
    ? bucketStackedSeries(view.consumptionTypeNames, view.dailySeries)
    : filterStackedSeries(view.consumptionTypeNames, view.dailySeries, effective);

  if (full) {
    return {
      consumptionTypeNames: bucketed.names,
      dailySeries: bucketed.dailySeries,
      consumptionBars: view.consumptionBars,
      detailTotal: null,
      detailTotalLabel: null,
    };
  }

  const selectedSet = new Set(effective);
  const detailTotal = view.rankedConsumptionTypes
    .filter((r) => selectedSet.has(r.name))
    .reduce((sum, r) => sum + r.spend, 0);

  return {
    consumptionTypeNames: bucketed.names,
    dailySeries: bucketed.dailySeries,
    consumptionBars: recomputeBarWidths(
      view.consumptionBars.filter((b) => selectedSet.has(b.name)),
    ),
    detailTotal,
    detailTotalLabel: formatCurrency(detailTotal, currency),
  };
}
