// Greybeam brand chart color system — single source of truth. The Tailwind
// config registers these token→hex pairs as custom colors; chart components pass
// the token names to Tremor and resolve hexes here for custom tooltip swatches.

import {
  OTHER_BUCKET_KEY,
  OTHER_BUCKET_LABEL,
  STACKED_SERIES_LIMIT,
} from "./stacked-series-bucketing";

export const CHART_COLORS: Record<string, string> = {
  "chart-purple": "#9F57E7",
  "chart-lime": "#C9E930",
  "chart-1": "#D9BFF7",
  "chart-2": "#A4DEF6",
  "chart-3": "#E6F39B",
  "chart-4": "#FFCBA6",
  "chart-5": "#F9BDD6",
  "chart-6": "#A8EBD6",
  "chart-7": "#BFCBFF",
  "chart-8": "#F7DE9E",
  "chart-9": "#E59BE9",
  "chart-10": "#B5ECA5",
  "chart-11": "#FFA8A8",
  "chart-12": "#8B9EF0",
  "chart-13": "#E8C56B",
  "chart-14": "#D6DBE4",
};

// Single-series / primary metric → brand purple.
export const PRIMARY_CHART_COLOR = "chart-purple";
// The palette length is the shared stacked-series cap: a stacked chart never
// exceeds STACKED_SERIES_LIMIT categories, so every displayed series — plus the
// synthetic "Other" bucket — always lands on a distinct color.
export const SERIES_PALETTE = [
  "chart-1", "chart-2", "chart-3", "chart-4",
  "chart-5", "chart-6", "chart-7", "chart-8",
  "chart-9", "chart-10", "chart-11", "chart-12",
  "chart-13", "chart-14",
] as const;
if (SERIES_PALETTE.length !== STACKED_SERIES_LIMIT) {
  throw new Error("SERIES_PALETTE length must equal STACKED_SERIES_LIMIT");
}
// Grouped "Other" bucket always takes the last palette color so it reads as a
// neutral catch-all distinct from the real series.
export const OTHER_SERIES_COLOR = "chart-14";
export const OTHER_SERIES_LABEL = "Other";
// Reserved for projected-cost-without-Greybeam overlays, event markers, and
// savings callouts — never a normal data series.
export const GREYBEAM_VALUE_COLOR = "chart-lime";
// Anomalies / budget alerts only.
export const ANOMALY_COLOR = "red";

/**
 * Maps chart categories to stable Greybeam brand colors. One series resolves to
 * brand purple. Multiple series take pastels in a fixed, positional order; the
 * grouped "Other" bucket always takes the last palette color (chart-14) and any
 * overflow beyond the 14 pastels falls back to that same neutral. The "Other"
 * bucket does not consume a pastel slot — real series keep getting consecutive
 * pastels even when "Other" appears mid-list.
 */
export function getSeriesColors(
  categories: readonly string[],
  options?: { singleSeriesPrimary?: boolean },
): string[] {
  const singleSeriesPrimary = options?.singleSeriesPrimary ?? true;
  // The single-series fast-path only applies to a lone real series; a lone
  // sentinel bucket still pins to its reserved neutral. Callers that never
  // want brand purple for a lone series (e.g. stacked spend charts) opt out
  // via singleSeriesPrimary: false.
  if (
    singleSeriesPrimary &&
    categories.length <= 1 &&
    categories[0] !== OTHER_BUCKET_KEY
  ) {
    return categories.map(() => PRIMARY_CHART_COLOR);
  }
  const colors: string[] = [];
  let paletteIndex = 0;
  for (const category of categories) {
    if (category === OTHER_BUCKET_KEY) {
      colors.push(OTHER_SERIES_COLOR);
      continue;
    }
    colors.push(SERIES_PALETTE[paletteIndex] ?? OTHER_SERIES_COLOR);
    paletteIndex += 1;
  }
  return colors;
}

/**
 * Orders chart series by their total value across all rows, descending. Ties
 * preserve the original category order. Used to stack the largest series at the
 * bottom and assign it the first palette color. The grouped "Other" bucket is
 * always pinned last regardless of its total so it reads as the catch-all at the
 * top of the stack and keeps its dedicated last palette color.
 */
export function orderCategoriesByTotal(
  categories: readonly string[],
  rows: ReadonlyArray<Record<string, unknown>>,
): string[] {
  const totals = new Map<string, number>();
  for (const category of categories) {
    let sum = 0;
    for (const row of rows) {
      const value = row[category];
      if (typeof value === "number" && Number.isFinite(value)) {
        sum += value;
      }
    }
    totals.set(category, sum);
  }
  const indexByCategory = new Map(
    categories.map((category, index) => [category, index] as const),
  );
  return [...categories].sort((a, b) => {
    // Pin the sentinel bucket last no matter its total so its neutral color
    // and catch-all role stay stable.
    if (a === OTHER_BUCKET_KEY || b === OTHER_BUCKET_KEY) {
      if (a === b) {
        return 0;
      }
      return a === OTHER_BUCKET_KEY ? 1 : -1;
    }
    const diff = (totals.get(b) ?? 0) - (totals.get(a) ?? 0);
    return diff !== 0 ? diff : (indexByCategory.get(a) ?? 0) - (indexByCategory.get(b) ?? 0);
  });
}

/**
 * Resolves a Tremor color token (e.g. "chart-purple") to a concrete hex for
 * inline styles. Passes through any value that is already a CSS color (e.g. the
 * "gray" fallback Tremor uses for unknown categories).
 */
export function resolveChartColor(color: string | undefined): string | undefined {
  if (!color) {
    return undefined;
  }
  return CHART_COLORS[color] ?? color;
}

/**
 * Display label for a chart category. The synthetic bucket's sentinel data key
 * renders as "Other"; every real entity renders as itself.
 */
export function seriesDisplayLabel(category: string): string {
  return category === OTHER_BUCKET_KEY ? OTHER_BUCKET_LABEL : category;
}
