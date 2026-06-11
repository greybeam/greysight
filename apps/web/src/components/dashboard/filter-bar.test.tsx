import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import FilterBar from "./filter-bar";

describe("FilterBar", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders 7/30/90 options, currency, and reports local changes", () => {
    const onWindowChange = vi.fn();

    render(
      <FilterBar windowDays={30} currency="USD" onWindowChange={onWindowChange} />,
    );

    expect(screen.getByText("USD")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "90 days" }));

    expect(onWindowChange).toHaveBeenCalledWith(90);
  });

  it("marks the active window", () => {
    render(<FilterBar windowDays={7} currency="USD" onWindowChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
