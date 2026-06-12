import { describe, expect, it } from "vitest";

import tailwindConfig, { tremorChartColorSafelist } from "../../tailwind.config";

describe("Tailwind config", () => {
  it("safelists Tremor chart color utilities used by dashboard charts", () => {
    expect(tailwindConfig.safelist).toBe(tremorChartColorSafelist);

    const safelistPatterns = tremorChartColorSafelist.map((entry) => entry.pattern);

    expect(safelistPatterns.some((pattern) => pattern.test("stroke-blue-500"))).toBe(true);
    expect(safelistPatterns.some((pattern) => pattern.test("fill-blue-500"))).toBe(true);
    expect(safelistPatterns.some((pattern) => pattern.test("text-blue-500"))).toBe(true);
    expect(safelistPatterns.some((pattern) => pattern.test("stroke-emerald-500"))).toBe(true);
    expect(safelistPatterns.some((pattern) => pattern.test("fill-emerald-500"))).toBe(true);
    expect(safelistPatterns.some((pattern) => pattern.test("text-emerald-500"))).toBe(true);
  });
});
