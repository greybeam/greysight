import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ApiHealth from "./api-health";

describe("ApiHealth", () => {
  it("renders healthy state", () => {
    render(<ApiHealth status="ok" />);

    expect(screen.getByText("API healthy")).toBeInTheDocument();
  });

  it("renders unavailable state", () => {
    render(<ApiHealth status="error" />);

    expect(screen.getByText("API unavailable")).toBeInTheDocument();
  });

  it("renders pending state by default", () => {
    render(<ApiHealth />);

    expect(screen.getByText("API status pending")).toBeInTheDocument();
  });
});
