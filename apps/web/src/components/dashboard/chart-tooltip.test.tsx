import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { createChartTooltip } from "./chart-tooltip";
import { OTHER_BUCKET_KEY } from "../../lib/stacked-series-bucketing";

// The shared vitest setup registers no automatic DOM cleanup, so unmount each
// render between tests to keep queries scoped to the current test's output.
afterEach(cleanup);

describe("chart tooltip sentinel label", () => {
  it("renders the sentinel bucket's row as 'Other', never '__other__'", () => {
    const Tooltip = createChartTooltip((v) => `$${v}`);
    render(
      <Tooltip
        active
        label="Jul 1"
        payload={[
          { dataKey: OTHER_BUCKET_KEY, name: OTHER_BUCKET_KEY, value: 3, color: "chart-14" },
          { dataKey: "a", name: "a", value: 5, color: "chart-1" },
        ]}
      />,
    );
    expect(screen.getByText("Other")).toBeInTheDocument();
    expect(screen.queryByText(OTHER_BUCKET_KEY)).toBeNull();
  });

  it("renders a real 'Other' and the sentinel as DISTINCT labels, never two 'Other' rows", () => {
    const Tooltip = createChartTooltip((v) => `$${v}`);
    render(
      <Tooltip
        active
        label="Jul 1"
        payload={[
          { dataKey: "Other", name: "Other", value: 9, color: "chart-1" },
          {
            dataKey: OTHER_BUCKET_KEY,
            name: OTHER_BUCKET_KEY,
            value: 3,
            color: "chart-14",
          },
          { dataKey: "a", name: "a", value: 5, color: "chart-2" },
        ]}
      />,
    );
    // The real entity keeps the plain "Other" label; the sentinel is disambiguated.
    expect(screen.getByText("Other")).toBeInTheDocument();
    expect(screen.getByText("Other (grouped)")).toBeInTheDocument();
    // Never two identical "Other" rows.
    expect(screen.queryAllByText("Other")).toHaveLength(1);
    expect(screen.queryByText(OTHER_BUCKET_KEY)).toBeNull();
  });
});
