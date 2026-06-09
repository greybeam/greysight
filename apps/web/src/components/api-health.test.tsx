import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ApiHealth from "./api-health";

describe("ApiHealth", () => {
  it("renders healthy state", () => {
    render(<ApiHealth status="ok" />);

    expect(screen.getByText("API healthy")).toBeInTheDocument();
  });
});
