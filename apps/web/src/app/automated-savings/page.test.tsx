import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../components/automated-savings/automated-savings-shell", () => ({
  AutomatedSavingsShell: ({ authRequired }: { authRequired: boolean }) =>
    <div data-testid="shell" data-auth={String(authRequired)} />,
}));

import AutomatedSavingsPage from "./page";

describe("AutomatedSavingsPage", () => {
  const original = process.env.AUTH_REQUIRED;
  afterEach(() => { process.env.AUTH_REQUIRED = original; });

  it("passes authRequired from env", () => {
    process.env.AUTH_REQUIRED = "true";
    render(AutomatedSavingsPage());
    expect(screen.getByTestId("shell")).toHaveAttribute("data-auth", "true");
  });
});
