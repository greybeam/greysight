import { describe, expect, it } from "vitest";
import {
  filterAiDetail,
  filterServiceSpend,
  filterStorageSpend,
  filterWarehouseSpend,
  recomputeBarWidths,
} from "./section-filters";
import { OTHER_BUCKET_KEY } from "./stacked-series-bucketing";
import type {
  AIDetailViewModel,
  ServiceSpendViewModel,
  StorageSpendViewModel,
  WarehouseSpendViewModel,
} from "./dashboard-contracts";

function ranked(name: string, spend: number) {
  return { name, spend, spendLabel: `$${spend}`, credits: null, barWidthPercent: 0 };
}

const view: ServiceSpendViewModel = {
  basis: "estimated",
  serviceNames: ["compute", "storage", "transfer"],
  dailySeries: [
    { date: "2026-07-01", values: { compute: 100, storage: 40, transfer: 10 } },
    { date: "2026-07-02", values: { compute: 120, storage: 30, transfer: 5 } },
  ],
  rankedServices: [
    { name: "compute", spend: 220, spendLabel: "$220", credits: null },
    { name: "storage", spend: 70, spendLabel: "$70", credits: null },
    { name: "transfer", spend: 15, spendLabel: "$15", credits: null },
  ],
  serviceBars: [ranked("compute", 220), ranked("storage", 70), ranked("transfer", 15)],
  isEmpty: false,
};

describe("filterServiceSpend", () => {
  it("is identity when all services are selected (zero-drift default)", () => {
    const out = filterServiceSpend(view, view.serviceNames, "USD");
    expect(out.total).toBeNull(); // no recompute
    expect(out.totalLabel).toBeNull();
    expect(out.serviceBars).toEqual(view.serviceBars); // widths untouched
    expect(out.serviceNames).toEqual(view.serviceNames);
  });

  it("filters to nothing when the selection is empty", () => {
    const out = filterServiceSpend(view, [], "USD");
    expect(out.total).toBe(0);
    expect(out.totalLabel).toBe("$0.00");
    expect(out.serviceNames).toEqual([]);
    expect(out.serviceBars).toEqual([]);
  });

  it("recomputes total, label, filtered names, and bar widths for a subset", () => {
    const out = filterServiceSpend(view, ["compute", "transfer"], "USD");
    expect(out.total).toBe(220 + 15);
    expect(out.totalLabel).toBe("$235.00");
    expect(out.serviceNames).toEqual(["compute", "transfer"]);
    expect(out.serviceBars.map((b) => b.name)).toEqual(["compute", "transfer"]);
    // widths relative to new visible max (220): 100% and ~6.8%
    expect(out.serviceBars[0].barWidthPercent).toBe(100);
    expect(out.serviceBars[1].barWidthPercent).toBeCloseTo((15 / 220) * 100);
  });
});

describe("recomputeBarWidths", () => {
  it("guards divide-by-zero when the visible max is 0", () => {
    const out = recomputeBarWidths([ranked("a", 0), ranked("b", 0)]);
    expect(out.every((r) => r.barWidthPercent === 0)).toBe(true);
  });
});

describe("filterServiceSpend with a former-'Other' entity (requirement 6)", () => {
  function bigView(): ServiceSpendViewModel {
    const count = 20;
    const names = Array.from({ length: count }, (_, i) => `svc-${String(i).padStart(2, "0")}`);
    const spends = Array.from({ length: count }, (_, i) => count - i); // descending
    return {
      basis: "estimated",
      serviceNames: names,
      dailySeries: [
        {
          date: "2026-07-01",
          values: Object.fromEntries(names.map((n, i) => [n, spends[i]])),
        },
      ],
      rankedServices: names.map((n, i) => ({
        name: n,
        spend: spends[i],
        spendLabel: `$${spends[i]}`,
        credits: null,
      })),
      serviceBars: names.map((n, i) => ranked(n, spends[i])),
      isEmpty: false,
    };
  }

  it("charts a selected low-ranked (former-'Other') service individually, not folded into OTHER_BUCKET_KEY", () => {
    const view = bigView();
    // svc-19 has the lowest spend (1) — unfiltered, it would be folded into the
    // synthetic "Other" bucket since only the top 13 of 20 are kept.
    const out = filterServiceSpend(view, ["svc-19"], "USD");
    expect(out.serviceNames).toEqual(["svc-19"]);
    const day0 = out.dailySeries[0].values;
    expect(day0["svc-19"]).toBe(1);
    expect(day0).not.toHaveProperty(OTHER_BUCKET_KEY);
    expect(out.total).toBe(1);
  });
});

const whView: WarehouseSpendViewModel = {
  basis: "estimated",
  total: 300,
  totalLabel: "$300.00",
  warehouseNames: ["WH_A", "WH_B"],
  dailySeries: [{ date: "2026-07-01", values: { WH_A: 200, WH_B: 100 } }],
  rankedWarehouses: [
    { name: "WH_A", spend: 200, spendLabel: "$200", credits: null },
    { name: "WH_B", spend: 100, spendLabel: "$100", credits: null },
  ],
  rankedUsers: [{ name: "alice", spend: 50, spendLabel: "$50", credits: null }],
  warehouseBars: [
    { name: "WH_A", spend: 200, spendLabel: "$200", credits: null, idlePct: 0.3 },
    { name: "WH_B", spend: 100, spendLabel: "$100", credits: null, idlePct: 0.5 },
  ],
  userBars: [
    { name: "alice", spend: 50, spendLabel: "$50", credits: null, barWidthPercent: 100 },
  ],
  isEmpty: false,
};

describe("filterWarehouseSpend", () => {
  it("is identity when all warehouses selected", () => {
    const out = filterWarehouseSpend(whView, whView.warehouseNames, "USD");
    expect(out.total).toBeNull();
    expect(out.warehouseBars).toEqual(whView.warehouseBars);
    expect(out.userBars).toEqual(whView.userBars);
  });

  it("drops unselected idle bars without recomputing idlePct, keeps userBars, recomputes KPI", () => {
    const out = filterWarehouseSpend(whView, ["WH_B"], "USD");
    expect(out.warehouseBars).toHaveLength(1);
    expect(out.warehouseBars[0].name).toBe("WH_B");
    expect(out.warehouseBars[0].idlePct).toBe(0.5); // unchanged
    expect(out.userBars).toEqual(whView.userBars); // not filtered
    expect(out.total).toBe(100);
    expect(out.totalLabel).toBe("$100.00");
    expect(out.warehouseNames).toEqual(["WH_B"]);
  });
});

function dbRow(name: string, periodSpend: number) {
  return {
    name,
    bytes: periodSpend * 1000,
    bytesLabel: `${periodSpend} KB`,
    monthlySpend: periodSpend * 2,
    monthlySpendLabel: `$${periodSpend * 2}`,
    periodSpend,
    periodSpendLabel: `$${periodSpend}`,
  };
}

const stView: StorageSpendViewModel = {
  basis: "estimated",
  databaseBasis: "estimated",
  total: 90,
  totalLabel: "$90.00",
  dailySeries: [],
  databaseNames: ["DB_A", "DB_B"],
  databaseDailySeries: [{ date: "2026-07-01", values: { DB_A: 60, DB_B: 30 } }],
  databases: [dbRow("DB_A", 60), dbRow("DB_B", 30)],
  databaseBars: [
    { name: "DB_A", spend: 60, spendLabel: "$60", credits: null, barWidthPercent: 100 },
    { name: "DB_B", spend: 30, spendLabel: "$30", credits: null, barWidthPercent: 50 },
  ],
  isEmpty: false,
};

describe("filterStorageSpend", () => {
  it("is identity when all databases selected", () => {
    const out = filterStorageSpend(stView, stView.databaseNames, "USD");
    expect(out.total).toBeNull();
    expect(out.databases).toEqual(stView.databases);
    expect(out.databaseBars).toEqual(stView.databaseBars);
  });

  it("filters both the table and the bars and recomputes KPI + widths", () => {
    const out = filterStorageSpend(stView, ["DB_B"], "USD");
    expect(out.databases.map((d) => d.name)).toEqual(["DB_B"]);
    expect(out.databaseBars.map((b) => b.name)).toEqual(["DB_B"]);
    expect(out.databaseBars[0].barWidthPercent).toBe(100); // recomputed vs new max
    expect(out.total).toBe(30);
    expect(out.totalLabel).toBe("$30.00");
  });
});

const aiView: AIDetailViewModel = {
  consumptionTypeNames: ["cortex", "copilot"],
  dailySeries: [{ date: "2026-07-01", values: { cortex: 80, copilot: 20 } }],
  rankedConsumptionTypes: [
    { name: "cortex", spend: 80, spendLabel: "$80", credits: null },
    { name: "copilot", spend: 20, spendLabel: "$20", credits: null },
  ],
  consumptionBars: [
    { name: "cortex", spend: 80, spendLabel: "$80", credits: null, barWidthPercent: 100 },
    { name: "copilot", spend: 20, spendLabel: "$20", credits: null, barWidthPercent: 25 },
  ],
  isEmpty: false,
  partial: false,
  skippedBranches: [],
};

describe("filterAiDetail", () => {
  it("keeps the billed KPI (null detail total) when unfiltered", () => {
    const out = filterAiDetail(aiView, aiView.consumptionTypeNames, "USD");
    expect(out.detailTotal).toBeNull();
    expect(out.detailTotalLabel).toBeNull();
    expect(out.consumptionBars).toEqual(aiView.consumptionBars);
  });

  it("derives KPI from selected detail rows when filtered", () => {
    const out = filterAiDetail(aiView, ["cortex"], "USD");
    expect(out.detailTotal).toBe(80);
    expect(out.detailTotalLabel).toBe("$80.00");
    expect(out.consumptionTypeNames).toEqual(["cortex"]);
    expect(out.consumptionBars[0].barWidthPercent).toBe(100);
  });
});

describe("filterAiDetail with a former-'Other' entity (requirement 6)", () => {
  function bigView(): AIDetailViewModel {
    const count = 20;
    const names = Array.from({ length: count }, (_, i) => `ai-${String(i).padStart(2, "0")}`);
    const spends = Array.from({ length: count }, (_, i) => count - i); // descending
    return {
      consumptionTypeNames: names,
      dailySeries: [
        {
          date: "2026-07-01",
          values: Object.fromEntries(names.map((n, i) => [n, spends[i]])),
        },
      ],
      rankedConsumptionTypes: names.map((n, i) => ({
        name: n,
        spend: spends[i],
        spendLabel: `$${spends[i]}`,
        credits: null,
      })),
      consumptionBars: names.map((n, i) => ({
        name: n,
        spend: spends[i],
        spendLabel: `$${spends[i]}`,
        credits: null,
        barWidthPercent: 0,
      })),
      isEmpty: false,
      partial: false,
      skippedBranches: [],
    };
  }

  it("charts a selected low-ranked (former-'Other') consumption type individually, not folded into OTHER_BUCKET_KEY", () => {
    const view = bigView();
    // ai-19 has the lowest spend (1) — unfiltered, it would be folded into the
    // synthetic "Other" bucket since only the top 13 of 20 are kept.
    const out = filterAiDetail(view, ["ai-19"], "USD");
    expect(out.consumptionTypeNames).toEqual(["ai-19"]);
    const day0 = out.dailySeries[0].values;
    expect(day0["ai-19"]).toBe(1);
    expect(day0).not.toHaveProperty(OTHER_BUCKET_KEY);
    expect(out.detailTotal).toBe(1);
    expect(out.consumptionBars.map((b) => b.name)).toEqual(["ai-19"]);
  });
});
