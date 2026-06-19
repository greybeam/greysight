import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import AuthCard from "./auth-card";

afterEach(() => cleanup());

describe("AuthCard", () => {
  it("renders the brand wordmark and its children", () => {
    render(
      <AuthCard>
        <p>card body</p>
      </AuthCard>,
    );
    expect(screen.getByText("Greybeam")).toBeInTheDocument();
    expect(screen.getByText("card body")).toBeInTheDocument();
  });

  it("exposes a main landmark", () => {
    render(
      <AuthCard>
        <p>card body</p>
      </AuthCard>,
    );
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("renders the logo as decorative (empty alt)", () => {
    const { container } = render(
      <AuthCard>
        <p>card body</p>
      </AuthCard>,
    );
    const logo = container.querySelector('img[src="/greybeam_assets/greybeam_logo.svg"]');
    expect(logo).not.toBeNull();
    expect(logo?.getAttribute("alt")).toBe("");
  });
});
