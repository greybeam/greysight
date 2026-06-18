// 7-day rolling-average trendline support for the stacked spend bar charts.
// Pure, framework-free helpers: compute the per-day stacked total, the trailing
// rolling average, and an immutable augmentation that attaches the average to
// each chart row under a reserved key the chart and tooltip both recognize.

// Reserved data key the rolling-average <Line> plots and the tooltip splits out.
// Double-underscore keeps it clear of any real series name.
export const ROLLING_AVERAGE_KEY = "__rollingAvg7d";
export const ROLLING_AVERAGE_LABEL = "7-day avg";
export const ROLLING_AVERAGE_WINDOW = 7;

function toFiniteNumber(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

/**
 * Sums the numeric values of the given categories for each row, yielding the
 * stacked total per day. Non-numeric or missing cells read as 0 so a gap never
 * produces NaN.
 */
export function stackedDailyTotals(
  rows: ReadonlyArray<Record<string, unknown>>,
  categories: readonly string[],
): number[] {
  return rows.map((row) =>
    categories.reduce((sum, category) => sum + toFiniteNumber(row[category]), 0),
  );
}

/**
 * Trailing rolling average over `window` points. The first `window - 1` points
 * average over whatever days exist so far (a partial window) rather than being
 * blank, so the trendline starts at the first data point. A non-positive window
 * is treated as 1.
 */
export function rollingAverage(
  values: readonly number[],
  window: number = ROLLING_AVERAGE_WINDOW,
): number[] {
  const size = Math.max(1, Math.floor(window));
  return values.map((_, index) => {
    const start = Math.max(0, index - size + 1);
    let sum = 0;
    for (let i = start; i <= index; i += 1) {
      sum += toFiniteNumber(values[i]);
    }
    return sum / (index - start + 1);
  });
}

/**
 * Returns new rows, each a copy of the original carrying a `ROLLING_AVERAGE_KEY`
 * field holding the trailing rolling average of the stacked total up to that
 * row. The input rows are never mutated.
 */
export function withRollingAverage<T extends Record<string, unknown>>(
  rows: readonly T[],
  categories: readonly string[],
  window: number = ROLLING_AVERAGE_WINDOW,
): Array<T & { [ROLLING_AVERAGE_KEY]: number }> {
  const averages = rollingAverage(stackedDailyTotals(rows, categories), window);
  return rows.map((row, index) => ({
    ...row,
    [ROLLING_AVERAGE_KEY]: averages[index],
  }));
}
