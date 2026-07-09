import { describe, expect, it } from "vitest";
import {
  bucketStackedSeries,
  OTHER_BUCKET_KEY,
  OTHER_BUCKET_LABEL,
  STACKED_SERIES_LIMIT,
  type StackedPoint,
} from "./stacked-series-bucketing";

// Build a `count`-entity series named a,b,c... where entity i has constant
// daily spend `spends[i]` over `days` days.
function series(spends: number[], days = 2) {
  const names = spends.map((_, i) => String.fromCharCode(97 + i)); // a, b, ...
  const dailySeries = Array.from({ length: days }, (_, d) => ({
    date: `2026-07-0${d + 1}`,
    values: Object.fromEntries(names.map((n, i) => [n, spends[i]])),
  }));
  return { names, dailySeries };
}

describe("bucketStackedSeries", () => {
  it("returns series at or under the limit unchanged (new objects)", () => {
    const { names, dailySeries } = series([3, 1, 2]);
    const out = bucketStackedSeries(names, dailySeries);
    expect(out.names).toEqual(["a", "b", "c"]);
    expect(out.dailySeries).toEqual(dailySeries);
    expect(out.dailySeries).not.toBe(dailySeries); // immutable copy
  });

  it("collapses overflow into top-13 by (spend desc, name asc) + sentinel Other", () => {
    // 15 entities: a..o. Give descending spend so a>b>...>o.
    const spends = Array.from({ length: 15 }, (_, i) => 15 - i);
    const { names, dailySeries } = series(spends);
    const out = bucketStackedSeries(names, dailySeries);
    // Top 13 kept (a..m), n+o folded into the sentinel bucket appended last.
    expect(out.names).toEqual([...names.slice(0, 13), OTHER_BUCKET_KEY]);
    const day0 = out.dailySeries[0].values;
    expect(day0[OTHER_BUCKET_KEY]).toBe(spends[13] + spends[14]); // n + o = 2 + 1
    expect(day0).not.toHaveProperty("n");
  });

  it("breaks total ties by ascending name", () => {
    // 15 entities all equal spend → tie broken by name asc → a..m kept, n+o bucketed.
    const { names, dailySeries } = series(new Array(15).fill(5));
    const out = bucketStackedSeries(names, dailySeries);
    expect(out.names).toEqual([...names.slice(0, 13), OTHER_BUCKET_KEY]);
  });

  it("treats a real entity named 'Other' as a normal entity (never merged)", () => {
    // 15 entities where one is literally "Other" with high spend → it survives
    // in the kept set under its own key, distinct from the sentinel bucket.
    const spends = Array.from({ length: 15 }, (_, i) => 15 - i);
    const { dailySeries } = series(spends);
    const names = spends.map((_, i) => (i === 0 ? "Other" : String.fromCharCode(97 + i)));
    const relabeled = dailySeries.map((p) => ({
      date: p.date,
      values: Object.fromEntries(
        names.map((n, i) => [n, Object.values(p.values)[i]]),
      ),
    }));
    const out = bucketStackedSeries(names, relabeled);
    expect(out.names).toContain("Other"); // real entity kept
    expect(out.names).toContain(OTHER_BUCKET_KEY); // synthetic bucket distinct
    expect(out.names.filter((n) => n === "Other")).toHaveLength(1);
  });
});

// Emulates the backend `_bucket_stacked_series` output (literal "Other" key).
function legacyBackendBucket(names: string[], dailySeries: StackedPoint[]) {
  if (names.length <= 14) {
    return { names: [...names], dailySeries };
  }
  const totals = new Map<string, number>(names.map((n) => [n, 0]));
  for (const p of dailySeries) {
    for (const [n, v] of Object.entries(p.values)) {
      totals.set(n, (totals.get(n) ?? 0) + v);
    }
  }
  const order = new Map(names.map((n, i) => [n, i] as const));
  const ranked = names
    .filter((n) => n !== "Other")
    .sort((a, b) => {
      const d = (totals.get(b) ?? 0) - (totals.get(a) ?? 0);
      return d !== 0 ? d : (order.get(a) ?? 0) - (order.get(b) ?? 0);
    });
  const kept = ranked.slice(0, 13);
  const keptSet = new Set(kept);
  return {
    names: [...kept, "Other"],
    dailySeries: dailySeries.map((p) => {
      const values: Record<string, number> = {};
      for (const n of kept) values[n] = p.values[n] ?? 0;
      values.Other = Object.entries(p.values).reduce(
        (s, [n, v]) => (keptSet.has(n) ? s : s + v),
        0,
      );
      return { date: p.date, values };
    }),
  };
}

describe("bucketStackedSeries parity with legacy backend bucketing", () => {
  it("matches legacy output (same 13 + Other, same order) across sizes and ties", () => {
    for (const count of [14, 15, 30, 100]) {
      const spends = Array.from({ length: count }, (_, i) => (count - i) % 7); // includes ties
      const names = spends.map((_, i) => `svc-${String(i).padStart(3, "0")}`);
      const dailySeries = Array.from({ length: 5 }, (_, d) => ({
        date: `2026-07-${String(d + 1).padStart(2, "0")}`,
        values: Object.fromEntries(names.map((n, i) => [n, spends[i]])),
      }));
      const legacy = legacyBackendBucket(names, dailySeries);
      const actual = bucketStackedSeries(names, dailySeries);
      // Names identical except the bucket key.
      const rekey = (arr: string[]) =>
        arr.map((n) => (n === OTHER_BUCKET_KEY ? OTHER_BUCKET_LABEL : n));
      expect(rekey(actual.names)).toEqual(legacy.names);
      // Values identical after re-keying the sentinel to the legacy label.
      const rekeyValues = (v: Record<string, number>) =>
        Object.fromEntries(
          Object.entries(v).map(([k, val]) => [
            k === OTHER_BUCKET_KEY ? OTHER_BUCKET_LABEL : k,
            val,
          ]),
        );
      expect(actual.dailySeries.map((p) => rekeyValues(p.values))).toEqual(
        legacy.dailySeries.map((p) => p.values),
      );
    }
  });
});

describe("bucketStackedSeries reserves two slots when a real 'Other' is present", () => {
  it("keeps a high-spend real 'Other' separate with its TRUE value, still within the limit", () => {
    // 15 entities, one of which is literally "Other" with the highest spend.
    const spends = Array.from({ length: 15 }, (_, i) => 15 - i);
    const names = spends.map((_, i) => (i === 0 ? "Other" : `svc-${String(i).padStart(3, "0")}`));
    const dailySeries = [
      {
        date: "2026-07-01",
        values: Object.fromEntries(names.map((n, i) => [n, spends[i]])),
      },
    ];

    const actual = bucketStackedSeries(names, dailySeries);

    // Displayed series count never exceeds the palette limit, and for a >14
    // dataset with a real "Other" it is exactly STACKED_SERIES_LIMIT (14):
    // 12 ranked non-"Other" + the pinned real "Other" + the sentinel.
    expect(actual.names.length).toBeLessThanOrEqual(STACKED_SERIES_LIMIT);
    expect(actual.names).toHaveLength(STACKED_SERIES_LIMIT);

    // The real "Other" survives under its own key, distinct from the sentinel.
    expect(actual.names).toContain("Other");
    expect(actual.names).toContain(OTHER_BUCKET_KEY);
    expect(actual.names.filter((n) => n === "Other")).toHaveLength(1);
    expect(actual.names.filter((n) => n === OTHER_BUCKET_KEY)).toHaveLength(1);

    const day0 = actual.dailySeries[0].values;
    // Real "Other" carries its TRUE value (15), never merged/inflated.
    expect(day0.Other).toBe(15);
    // Top 12 ranked non-"Other" entities kept: svc-001..svc-012 (spends 14..3).
    // svc-013 (2) + svc-014 (1) fold into the sentinel = 3.
    expect(day0[OTHER_BUCKET_KEY]).toBe(spends[13] + spends[14]);
    expect(day0).not.toHaveProperty("svc-013");
    expect(day0).not.toHaveProperty("svc-014");
  });
});
