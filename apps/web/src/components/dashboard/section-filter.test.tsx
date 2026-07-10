import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SectionFilter } from "./section-filter";

describe("SectionFilter", () => {
  const options = ["gamma", "alpha", "beta"];

  afterEach(() => {
    cleanup();
  });

  it("renders options alphabetically when opened", () => {
    render(<SectionFilter options={options} selected={options} onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /filter/i }));
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.map((b) => b.getAttribute("value"))).toEqual(["alpha", "beta", "gamma"]);
  });

  it("shows a count badge only for a strict subset", () => {
    const { rerender } = render(
      <SectionFilter options={options} selected={options} onChange={() => {}} />,
    );
    expect(screen.queryByTestId("section-filter-count")).toBeNull(); // all selected
    rerender(<SectionFilter options={options} selected={["alpha"]} onChange={() => {}} />);
    expect(screen.getByTestId("section-filter-count")).toHaveTextContent("1");
  });

  it("toggles an option via onChange", () => {
    const onChange = vi.fn();
    render(<SectionFilter options={options} selected={options} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /filter/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: "alpha" }));
    expect(onChange).toHaveBeenCalledWith(["beta", "gamma"]); // alpha removed
  });

  it("supports select-all and clear", () => {
    const onChange = vi.fn();
    render(<SectionFilter options={options} selected={["alpha"]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /filter/i }));
    fireEvent.click(screen.getByRole("button", { name: /select all/i }));
    expect(onChange).toHaveBeenLastCalledWith(["alpha", "beta", "gamma"]);
    fireEvent.click(screen.getByRole("button", { name: /clear/i }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("closes the popover on outside click", () => {
    render(<SectionFilter options={options} selected={options} onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /filter/i }));
    expect(screen.getByTestId("section-filter-popover")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByTestId("section-filter-popover")).toBeNull();
  });

  it("renders all checkboxes unchecked when selection is empty (empty = nothing)", () => {
    render(<SectionFilter options={options} selected={[]} onChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /filter/i }));
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.every((b) => (b as HTMLInputElement).checked)).toBe(false);
  });

  it("checking a box from the empty state yields just that one", () => {
    const onChange = vi.fn();
    render(<SectionFilter options={options} selected={[]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /filter/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: "alpha" }));
    expect(onChange).toHaveBeenCalledWith(["alpha"]);
  });

  it("shows a count badge of 0 when selection is empty", () => {
    render(<SectionFilter options={options} selected={[]} onChange={() => {}} />);
    expect(screen.getByTestId("section-filter-count")).toHaveTextContent("0");
  });
});
