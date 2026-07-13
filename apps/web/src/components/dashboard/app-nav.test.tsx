import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/automated-savings" }));

import { AppNav } from "./app-nav";

describe("AppNav", () => {
  it("marks the active route", () => {
    render(<AppNav />);
    const active = screen.getByRole("link", { name: /automated savings/i });
    expect(active).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /home/i })).not.toHaveAttribute("aria-current");
  });
});
