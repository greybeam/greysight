import { describe, expect, it } from "vitest";

import {
  getSeriesColors,
  orderCategoriesByTotal,
  resolveChartColor,
} from "./chart-colors";

describe("getSeriesColors", () => {
  it("maps a single category to brand purple", () => {
    expect(getSeriesColors(["Compute"])).toEqual(["chart-purple"]);
  });

  it("maps two categories to consecutive pastels", () => {
    expect(getSeriesColors(["Compute", "Storage"])).toEqual(["chart-1", "chart-2"]);
  });

  it("does not let a mid-list \"Other\" consume a pastel slot", () => {
    expect(getSeriesColors(["Compute", "Other", "Storage"])).toEqual([
      "chart-1",
      "chart-other",
      "chart-2",
    ]);
  });

  it("falls back to slate once the pastels are exhausted", () => {
    const categories = ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "c10"];

    expect(getSeriesColors(categories)).toEqual([
      "chart-1",
      "chart-2",
      "chart-3",
      "chart-4",
      "chart-5",
      "chart-6",
      "chart-7",
      "chart-8",
      "chart-other",
      "chart-other",
    ]);
  });
});

describe("resolveChartColor", () => {
  it("resolves a known token to its hex", () => {
    expect(resolveChartColor("chart-purple")).toBe("#9F57E7");
  });

  it("passes through a CSS color it does not know", () => {
    expect(resolveChartColor("gray")).toBe("gray");
  });

  it("returns undefined for undefined input", () => {
    expect(resolveChartColor(undefined)).toBeUndefined();
  });
});

describe("orderCategoriesByTotal", () => {
  it("orders categories by descending total value across rows", () => {
    const categories = ["AUTO_CLUSTERING", "CLOUD_SERVICES", "WAREHOUSE_METERING"];
    const rows = [
      { date: "2026-06-01", AUTO_CLUSTERING: 1, CLOUD_SERVICES: 5, WAREHOUSE_METERING: 10 },
      { date: "2026-06-02", AUTO_CLUSTERING: 2, CLOUD_SERVICES: 6, WAREHOUSE_METERING: 12 },
    ];

    expect(orderCategoriesByTotal(categories, rows)).toEqual([
      "WAREHOUSE_METERING",
      "CLOUD_SERVICES",
      "AUTO_CLUSTERING",
    ]);
  });

  it("preserves the original category order on ties", () => {
    const categories = ["A", "B", "C"];
    const rows = [
      { A: 5, B: 5, C: 5 },
      { A: 5, B: 5, C: 5 },
    ];

    expect(orderCategoriesByTotal(categories, rows)).toEqual(["A", "B", "C"]);
  });

  it("ignores non-numeric and unrelated fields like date", () => {
    const categories = ["Compute", "Storage"];
    const rows = [
      { date: "2026-06-01", Compute: "n/a" as unknown as number, Storage: 8 },
      { date: "2026-06-02", Compute: 3, Storage: 4 },
    ];

    // Compute only counts its single numeric value (3); Storage counts 12.
    expect(orderCategoriesByTotal(categories, rows)).toEqual(["Storage", "Compute"]);
  });

  it("ignores NaN and Infinity cell values so they do not poison totals", () => {
    const categories = ["Real", "Poisoned"];
    const rows = [
      { Real: 5, Poisoned: Number.NaN },
      { Real: 5, Poisoned: Number.POSITIVE_INFINITY },
    ];

    // Poisoned has no finite values, so it totals 0 and sorts after Real's 10.
    expect(orderCategoriesByTotal(categories, rows)).toEqual(["Real", "Poisoned"]);
  });

  it("returns an empty array for empty categories", () => {
    expect(orderCategoriesByTotal([], [])).toEqual([]);
  });

  it("returns categories unchanged when there are no rows", () => {
    expect(orderCategoriesByTotal(["A", "B"], [])).toEqual(["A", "B"]);
  });
});
