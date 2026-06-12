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

  it("safelists Greybeam chart color utilities with no shade suffix", () => {
    const safelistPatterns = tremorChartColorSafelist.map((entry) => entry.pattern);

    expect(safelistPatterns.some((pattern) => pattern.test("bg-chart-purple"))).toBe(true);
    expect(safelistPatterns.some((pattern) => pattern.test("stroke-chart-other"))).toBe(true);
    expect(safelistPatterns.some((pattern) => pattern.test("fill-chart-1"))).toBe(true);

    // The Greybeam no-shade pattern must not match a "-500" shaded form.
    expect(safelistPatterns.some((pattern) => pattern.test("bg-chart-purple-500"))).toBe(false);
  });
});
