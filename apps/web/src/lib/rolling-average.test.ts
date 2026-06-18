import { describe, expect, it } from "vitest";

import {
  ROLLING_AVERAGE_KEY,
  resolveRollingAverageKey,
  rollingAverage,
  stackedDailyTotals,
  withRollingAverage,
} from "./rolling-average";

describe("stackedDailyTotals", () => {
  it("sums the listed categories per row", () => {
    const rows = [
      { date: "Jun 01", a: 1, b: 2, c: 3 },
      { date: "Jun 02", a: 4, b: 5, c: 6 },
    ];
    expect(stackedDailyTotals(rows, ["a", "b", "c"])).toEqual([6, 15]);
  });

  it("ignores non-category fields and coerces gaps to 0", () => {
    const rows = [
      { date: "Jun 01", a: 10, b: undefined as unknown as number },
      { date: "Jun 02", a: 2, b: 3 },
    ];
    // The `date` field is not a category and must not be summed; the undefined
    // gap reads as 0 rather than NaN.
    expect(stackedDailyTotals(rows, ["a", "b"])).toEqual([10, 5]);
  });
});

describe("rollingAverage", () => {
  it("averages over a partial window for the first window-1 points", () => {
    // Window 7, four points: each early point averages over the days so far.
    expect(rollingAverage([10, 20, 30, 40], 7)).toEqual([10, 15, 20, 25]);
  });

  it("uses a trailing fixed window once enough points exist", () => {
    // Window 3: point i averages points [i-2, i].
    expect(rollingAverage([3, 6, 9, 12, 15], 3)).toEqual([3, 4.5, 6, 9, 12]);
  });

  it("treats a non-positive window as 1 (each point is its own value)", () => {
    expect(rollingAverage([5, 10, 15], 0)).toEqual([5, 10, 15]);
  });

  it("returns an empty array for empty input", () => {
    expect(rollingAverage([], 7)).toEqual([]);
  });
});

describe("resolveRollingAverageKey", () => {
  it("uses the reserved key when no category collides", () => {
    expect(resolveRollingAverageKey(["a", "b"])).toBe(ROLLING_AVERAGE_KEY);
  });

  it("derives a unique variant when a category equals the reserved key", () => {
    const key = resolveRollingAverageKey(["a", ROLLING_AVERAGE_KEY]);
    expect(key).not.toBe(ROLLING_AVERAGE_KEY);
    expect(["a", ROLLING_AVERAGE_KEY]).not.toContain(key);
  });

  it("keeps appending until the key clears every colliding category", () => {
    // Both the reserved key and the first fallback are taken, so the resolver
    // must skip past both.
    const categories = [ROLLING_AVERAGE_KEY, `${ROLLING_AVERAGE_KEY}_`];
    const key = resolveRollingAverageKey(categories);
    expect(categories).not.toContain(key);
  });
});

describe("withRollingAverage", () => {
  it("attaches the trailing average of the stacked total to each row", () => {
    const rows = [
      { date: "Jun 01", a: 4, b: 6 }, // total 10
      { date: "Jun 02", a: 10, b: 10 }, // total 20
    ];
    const { averageKey, rows: result } = withRollingAverage(rows, ["a", "b"], 7);
    expect(averageKey).toBe(ROLLING_AVERAGE_KEY);
    expect(result[0][averageKey]).toBe(10);
    expect(result[1][averageKey]).toBe(15);
  });

  it("stores the average under a non-colliding key when a category collides", () => {
    // A warehouse literally named like the reserved key must not have its real
    // value overwritten by the rolling average.
    const rows = [
      { date: "Jun 01", [ROLLING_AVERAGE_KEY]: 4, b: 6 }, // total 10
      { date: "Jun 02", [ROLLING_AVERAGE_KEY]: 10, b: 10 }, // total 20
    ];
    const categories = [ROLLING_AVERAGE_KEY, "b"];
    const { averageKey, rows: result } = withRollingAverage(rows, categories, 7);

    expect(averageKey).not.toBe(ROLLING_AVERAGE_KEY);
    // The real series keeps its values...
    expect(result[0][ROLLING_AVERAGE_KEY]).toBe(4);
    expect(result[1][ROLLING_AVERAGE_KEY]).toBe(10);
    // ...and the average lands on the derived key.
    expect(result[0][averageKey]).toBe(10);
    expect(result[1][averageKey]).toBe(15);
  });

  it("does not mutate the input rows", () => {
    const rows = [{ date: "Jun 01", a: 1, b: 2 }];
    const snapshot = structuredClone(rows);
    withRollingAverage(rows, ["a", "b"]);
    expect(rows).toEqual(snapshot);
    expect(ROLLING_AVERAGE_KEY in rows[0]).toBe(false);
  });
});
