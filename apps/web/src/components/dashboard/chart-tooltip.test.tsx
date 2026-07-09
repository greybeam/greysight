import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { createChartTooltip } from "./chart-tooltip";
import { OTHER_BUCKET_KEY } from "../../lib/stacked-series-bucketing";

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
});
