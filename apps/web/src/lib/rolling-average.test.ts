import { describe, expect, it } from "vitest";

import {
  ROLLING_AVERAGE_KEY,
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

describe("withRollingAverage", () => {
  it("attaches the trailing average of the stacked total to each row", () => {
    const rows = [
      { date: "Jun 01", a: 4, b: 6 }, // total 10
      { date: "Jun 02", a: 10, b: 10 }, // total 20
    ];
    const result = withRollingAverage(rows, ["a", "b"], 7);
    expect(result[0][ROLLING_AVERAGE_KEY]).toBe(10);
    expect(result[1][ROLLING_AVERAGE_KEY]).toBe(15);
  });

  it("does not mutate the input rows", () => {
    const rows = [{ date: "Jun 01", a: 1, b: 2 }];
    const snapshot = structuredClone(rows);
    withRollingAverage(rows, ["a", "b"]);
    expect(rows).toEqual(snapshot);
    expect(ROLLING_AVERAGE_KEY in rows[0]).toBe(false);
  });
});
