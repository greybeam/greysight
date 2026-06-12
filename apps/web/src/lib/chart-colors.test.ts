import { describe, expect, it } from "vitest";

import { orderCategoriesByTotal } from "./chart-colors";

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

  it("returns an empty array for empty categories", () => {
    expect(orderCategoriesByTotal([], [])).toEqual([]);
  });

  it("returns categories unchanged when there are no rows", () => {
    expect(orderCategoriesByTotal(["A", "B"], [])).toEqual(["A", "B"]);
  });
});
