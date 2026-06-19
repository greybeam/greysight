import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import AuthStatus from "./auth-status";

afterEach(() => cleanup());

describe("AuthStatus", () => {
  it("announces the label via a status region", () => {
    render(<AuthStatus label="Authenticating" />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Authenticating");
    expect(status).toHaveAttribute("aria-live", "polite");
  });
});
