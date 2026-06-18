import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DashboardRuntimeShell from "./dashboard-runtime-shell";

vi.mock("../org/org-shell", () => ({
  default: ({
    authRequired,
    bypassModeLabel,
    children,
    onAccessTokenChange,
    onOrganizationChange,
  }: {
    authRequired?: boolean;
    bypassModeLabel?: string;
    children: React.ReactNode;
    onAccessTokenChange?: (accessToken: string | null) => void;
    onOrganizationChange?: (
      organization: {
        id: string;
        name: string;
        accountLocator: string | null;
      } | null,
    ) => void;
  }) => (
    <section>
      <span>Auth required: {String(authRequired)}</span>
      <span>Bypass label: {bypassModeLabel}</span>
      <button
        type="button"
        onClick={() => {
          onAccessTokenChange?.("test-access-token");
          onOrganizationChange?.({
            id: "org-123",
            name: "Acme Analytics",
            accountLocator: null,
          });
        }}
      >
        Select organization
      </button>
      {children}
    </section>
  ),
}));

vi.mock("./cost-dashboard", () => ({
  default: (props: unknown) => (
    <pre data-testid="dashboard-props">{JSON.stringify(props)}</pre>
  ),
}));

describe("DashboardRuntimeShell", () => {
  afterEach(() => {
    cleanup();
  });

  it("keeps demo mode for auth bypass", () => {
    render(<DashboardRuntimeShell authRequired={false} />);

    expect(screen.getByText("Bypass label: Demo mode")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-props")).toHaveTextContent(
      JSON.stringify({ demoMode: true, modeLabel: "Demo", runtime: null }),
    );
  });

  it("uses a local runtime for unauthenticated Snowflake mode", () => {
    render(<DashboardRuntimeShell authRequired={false} dataSource="snowflake" />);

    expect(
      screen.getByText("Bypass label: Local Snowflake mode"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-props")).toHaveTextContent(
      JSON.stringify({
        demoMode: false,
        modeLabel: "Local Snowflake",
        runtime: {
          accessToken: null,
          organizationId: "00000000-0000-4000-8000-000000000001",
          organizationName: "Local Snowflake",
        },
      }),
    );
  });

  it("passes selected organization and access token to dashboard runtime", async () => {
    render(<DashboardRuntimeShell authRequired dataSource="snowflake" />);

    fireEvent.click(screen.getByRole("button", { name: "Select organization" }));

    await waitFor(() =>
      expect(screen.getByTestId("dashboard-props")).toHaveTextContent(
        JSON.stringify({
          demoMode: false,
          modeLabel: "Authenticated Snowflake",
          runtime: {
            accessToken: "test-access-token",
            organizationId: "org-123",
            organizationName: "Acme Analytics",
            accountLocator: null,
          },
        }),
      ),
    );
  });

  it("uses the authenticated runtime for auth-required demo data", async () => {
    render(<DashboardRuntimeShell authRequired />);

    fireEvent.click(screen.getByRole("button", { name: "Select organization" }));

    await waitFor(() =>
      expect(screen.getByTestId("dashboard-props")).toHaveTextContent(
        JSON.stringify({
          demoMode: false,
          modeLabel: "Authenticated Snowflake",
          runtime: {
            accessToken: "test-access-token",
            organizationId: "org-123",
            organizationName: "Acme Analytics",
            accountLocator: null,
          },
        }),
      ),
    );
  });
});
