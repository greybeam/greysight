import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import FilterBar from "./filter-bar";

const relativeRange = {
  mode: "relative" as const,
  windowDays: 30,
  startDate: "2026-05-10",
  endDate: "2026-06-08",
};

describe("FilterBar", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders 7/30/90 options, currency, and reports relative changes", () => {
    const onWindowChange = vi.fn();

    render(
      <FilterBar
        range={relativeRange}
        currency="USD"
        startDate={relativeRange.startDate}
        endDate={relativeRange.endDate}
        onWindowChange={onWindowChange}
        onStartDateChange={vi.fn()}
        onEndDateChange={vi.fn()}
        onApplyDateRange={vi.fn()}
      />,
    );

    expect(screen.getByText("USD")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "90 days" }));

    expect(onWindowChange).toHaveBeenCalledWith(90);
  });

  it("marks the active window", () => {
    render(
      <FilterBar
        range={{ ...relativeRange, windowDays: 7 }}
        currency="USD"
        startDate={relativeRange.startDate}
        endDate={relativeRange.endDate}
        onWindowChange={vi.fn()}
        onStartDateChange={vi.fn()}
        onEndDateChange={vi.fn()}
        onApplyDateRange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("renders controlled custom date inputs and reports input changes", () => {
    const onStartDateChange = vi.fn();
    const onEndDateChange = vi.fn();

    render(
      <FilterBar
        range={relativeRange}
        currency="USD"
        startDate="2026-06-01"
        endDate="2026-06-08"
        onWindowChange={vi.fn()}
        onStartDateChange={onStartDateChange}
        onEndDateChange={onEndDateChange}
        onApplyDateRange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Start date")).toHaveValue("2026-06-01");
    expect(screen.getByLabelText("End date")).toHaveValue("2026-06-08");

    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: "2026-06-02" },
    });
    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: "2026-06-09" },
    });

    expect(onStartDateChange).toHaveBeenCalledWith("2026-06-02");
    expect(onEndDateChange).toHaveBeenCalledWith("2026-06-09");
  });

  it("reports apply clicks without owning the custom date state", () => {
    const onApplyDateRange = vi.fn();

    render(
      <FilterBar
        range={relativeRange}
        currency="USD"
        startDate="2026-06-01"
        endDate="2026-06-08"
        onWindowChange={vi.fn()}
        onStartDateChange={vi.fn()}
        onEndDateChange={vi.fn()}
        onApplyDateRange={onApplyDateRange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Apply date range" }));

    expect(onApplyDateRange).toHaveBeenCalledTimes(1);
  });

  it("disables apply for empty or reversed custom date ranges", () => {
    const { rerender } = render(
      <FilterBar
        range={relativeRange}
        currency="USD"
        startDate=""
        endDate="2026-06-08"
        onWindowChange={vi.fn()}
        onStartDateChange={vi.fn()}
        onEndDateChange={vi.fn()}
        onApplyDateRange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Apply date range" }),
    ).toBeDisabled();

    rerender(
      <FilterBar
        range={relativeRange}
        currency="USD"
        startDate="2026-06-09"
        endDate="2026-06-08"
        onWindowChange={vi.fn()}
        onStartDateChange={vi.fn()}
        onEndDateChange={vi.fn()}
        onApplyDateRange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Apply date range" }),
    ).toBeDisabled();
  });
});
