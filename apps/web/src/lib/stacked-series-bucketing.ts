// Display-side port of the backend `_bucket_stacked_series`
// (apps/api/app/services/dashboard_view_builder.py). The backend now sends the
// COMPLETE per-entity series; this collapses it to at most STACKED_SERIES_LIMIT
// displayed series for the chart. Unfiltered output must equal the old backend
// output (locked by the parity test in this file). Pure — inputs are never
// mutated.

// Shared cap. chart-colors.ts imports this so the palette length and the bucket
// threshold can never drift.
export const STACKED_SERIES_LIMIT = 14;

// Non-colliding sentinel data key for the synthetic bucket (Codex H3). A real
// entity named "Other" flows through under its own key and is never merged here.
export const OTHER_BUCKET_KEY = "__other__";
// Human display label for the sentinel bucket.
export const OTHER_BUCKET_LABEL = "Other";

export type StackedPoint = { date: string; values: Record<string, number> };

export function bucketStackedSeries(
  names: string[],
  dailySeries: StackedPoint[],
): { names: string[]; dailySeries: StackedPoint[] } {
  if (names.length <= STACKED_SERIES_LIMIT) {
    return {
      names: [...names],
      dailySeries: dailySeries.map((p) => ({ date: p.date, values: { ...p.values } })),
    };
  }

  const totals = new Map<string, number>(names.map((n) => [n, 0]));
  for (const point of dailySeries) {
    for (const name of names) {
      totals.set(name, (totals.get(name) ?? 0) + (point.values[name] ?? 0));
    }
  }

  const order = new Map(names.map((n, i) => [n, i] as const));
  // A real entity literally named "Other" is excluded from the ranking/cap
  // pool and always passes through under its own key, uncapped — it is never
  // folded into the synthetic overflow bucket (Codex H3). This is a
  // deliberate contract change from the legacy backend, which merged a real
  // "Other" entity into its single aggregated bucket.
  const hasRealOther = names.includes(OTHER_BUCKET_LABEL);
  const rankable = names.filter((n) => n !== OTHER_BUCKET_LABEL);
  // (spend desc, name asc) — name asc is the incoming index since names arrive
  // alphabetical. Matches the backend tie-break exactly.
  const ranked = [...rankable].sort((a, b) => {
    const diff = (totals.get(b) ?? 0) - (totals.get(a) ?? 0);
    return diff !== 0 ? diff : (order.get(a) ?? 0) - (order.get(b) ?? 0);
  });
  const kept = ranked.slice(0, STACKED_SERIES_LIMIT - 1);
  const keptSet = new Set(kept);

  const bucketedNames = hasRealOther
    ? [...kept, OTHER_BUCKET_LABEL, OTHER_BUCKET_KEY]
    : [...kept, OTHER_BUCKET_KEY];
  const bucketedSeries = dailySeries.map((point) => {
    const values: Record<string, number> = {};
    for (const name of kept) {
      values[name] = point.values[name] ?? 0;
    }
    if (hasRealOther) {
      values[OTHER_BUCKET_LABEL] = point.values[OTHER_BUCKET_LABEL] ?? 0;
    }
    values[OTHER_BUCKET_KEY] = Object.entries(point.values).reduce(
      (sum, [name, amount]) =>
        keptSet.has(name) || name === OTHER_BUCKET_LABEL ? sum : sum + amount,
      0,
    );
    return { date: point.date, values };
  });

  return { names: bucketedNames, dailySeries: bucketedSeries };
}
