import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DashboardPage from "./page";

describe("DashboardPage", () => {
  it("renders API health as pending until a real probe exists", () => {
    render(<DashboardPage />);

    expect(screen.getByText("API status pending")).toBeInTheDocument();
    expect(screen.queryByText("API healthy")).not.toBeInTheDocument();
  });
});
