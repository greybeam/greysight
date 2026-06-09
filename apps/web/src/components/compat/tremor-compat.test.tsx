import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import TremorCompat from "./tremor-compat";

describe("TremorCompat", () => {
  it("renders required dashboard primitives", () => {
    render(createElement(TremorCompat));

    expect(screen.getByText("Compatibility")).toBeInTheDocument();
    expect(screen.getByText("Warehouse spend")).toBeInTheDocument();
    expect(screen.getByText("Top warehouses")).toBeInTheDocument();
  });
});
