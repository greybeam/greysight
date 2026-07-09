import { describe, expect, it } from "vitest";

import {
  getSeriesColors,
  OTHER_SERIES_COLOR,
  orderCategoriesByTotal,
  PRIMARY_CHART_COLOR,
  resolveChartColor,
  seriesDisplayLabel,
} from "./chart-colors";
import { OTHER_BUCKET_KEY, OTHER_BUCKET_LABEL } from "./stacked-series-bucketing";

describe("getSeriesColors", () => {
  it("maps a single category to brand purple", () => {
    expect(getSeriesColors(["Compute"])).toEqual([PRIMARY_CHART_COLOR]);
  });

  it('pins a lone sentinel bucket to the neutral, not brand purple', () => {
    expect(getSeriesColors([OTHER_BUCKET_KEY])).toEqual([OTHER_SERIES_COLOR]);
  });

  it("maps two categories to consecutive pastels", () => {
    expect(getSeriesColors(["Compute", "Storage"])).toEqual(["chart-1", "chart-2"]);
  });

  it('gives the sentinel bucket the last palette color without consuming a pastel slot', () => {
    expect(getSeriesColors(["Compute", OTHER_BUCKET_KEY, "Storage"])).toEqual([
      "chart-1",
      "chart-14",
      "chart-2",
    ]);
  });

  it("uses the pastel palette for a lone series when singleSeriesPrimary is opted out", () => {
    expect(getSeriesColors(["a"], { singleSeriesPrimary: false })).toEqual(["chart-1"]);
  });

  it("still defaults to brand purple for a lone series", () => {
    expect(getSeriesColors(["a"])).toEqual([PRIMARY_CHART_COLOR]);
  });

  it("still pins a lone sentinel bucket to the neutral when singleSeriesPrimary is opted out", () => {
    expect(getSeriesColors([OTHER_BUCKET_KEY], { singleSeriesPrimary: false })).toEqual([
      OTHER_SERIES_COLOR,
    ]);
  });

  it("assigns all 14 pastels before falling back to the neutral", () => {
    const categories = Array.from({ length: 16 }, (_, index) => `c${index + 1}`);

    expect(getSeriesColors(categories)).toEqual([
      "chart-1",
      "chart-2",
      "chart-3",
      "chart-4",
      "chart-5",
      "chart-6",
      "chart-7",
      "chart-8",
      "chart-9",
      "chart-10",
      "chart-11",
      "chart-12",
      "chart-13",
      "chart-14",
      "chart-14",
      "chart-14",
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

  it('pins the sentinel bucket last even when its total is the largest', () => {
    const categories = ["Small", OTHER_BUCKET_KEY, "Big"];
    const rows = [
      { Small: 1, [OTHER_BUCKET_KEY]: 100, Big: 10 },
      { Small: 1, [OTHER_BUCKET_KEY]: 100, Big: 10 },
    ];

    // Big and Small order by descending total; the sentinel stays last regardless.
    expect(orderCategoriesByTotal(categories, rows)).toEqual([
      "Big",
      "Small",
      OTHER_BUCKET_KEY,
    ]);
  });
});

describe("chart-colors sentinel bucket", () => {
  it("pins the sentinel bucket to chart-14, not a real 'Other' entity", () => {
    const colors = getSeriesColors(["a", "Other", OTHER_BUCKET_KEY]);
    // Real "Other" takes a normal palette slot; only the sentinel pins to chart-14.
    expect(colors).toEqual(["chart-1", "chart-2", "chart-14"]);
  });

  it("maps the sentinel to the display label, passing real names through", () => {
    expect(seriesDisplayLabel(OTHER_BUCKET_KEY)).toBe(OTHER_BUCKET_LABEL);
    expect(seriesDisplayLabel("Other")).toBe("Other");
    expect(seriesDisplayLabel("Warehouse A")).toBe("Warehouse A");
  });
});
