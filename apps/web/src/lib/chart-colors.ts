// Greybeam brand chart color system — single source of truth. The Tailwind
// config registers these token→hex pairs as custom colors; chart components pass
// the token names to Tremor and resolve hexes here for custom tooltip swatches.

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
  "chart-other": "#D6DBE4",
};

// Single-series / primary metric → brand purple.
export const PRIMARY_CHART_COLOR = "chart-purple";
// Multi-series pastels, used in this fixed order; never reordered.
export const SERIES_PALETTE = [
  "chart-1", "chart-2", "chart-3", "chart-4",
  "chart-5", "chart-6", "chart-7", "chart-8",
] as const;
// Grouped "Other" bucket.
export const OTHER_SERIES_COLOR = "chart-other";
export const OTHER_SERIES_LABEL = "Other";
// Reserved for projected-cost-without-Greybeam overlays, event markers, and
// savings callouts — never a normal data series.
export const GREYBEAM_VALUE_COLOR = "chart-lime";
// Anomalies / budget alerts only.
export const ANOMALY_COLOR = "red";

/**
 * Maps chart categories to stable Greybeam brand colors. One series resolves to
 * brand purple. Multiple series take pastels in a fixed, positional order; the
 * grouped "Other" bucket and any overflow beyond the 8 pastels fall back to slate.
 */
export function getSeriesColors(categories: readonly string[]): string[] {
  if (categories.length <= 1) {
    return categories.map(() => PRIMARY_CHART_COLOR);
  }
  return categories.map((category, index) =>
    category === OTHER_SERIES_LABEL
      ? OTHER_SERIES_COLOR
      : SERIES_PALETTE[index] ?? OTHER_SERIES_COLOR,
  );
}

/**
 * Orders chart series by their total value across all rows, descending. Ties
 * preserve the original category order. Used to stack the largest series at the
 * bottom and assign it the first palette color.
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
      if (typeof value === "number") {
        sum += value;
      }
    }
    totals.set(category, sum);
  }
  return [...categories].sort((a, b) => {
    const diff = (totals.get(b) ?? 0) - (totals.get(a) ?? 0);
    return diff !== 0 ? diff : categories.indexOf(a) - categories.indexOf(b);
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
