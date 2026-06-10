import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import OrgShell from "./org-shell";
import type {
  AuthSession,
  BrowserAuthClient,
  SessionChangeCallback,
} from "../../lib/supabase-client";

const session: AuthSession = {
  accessToken: "test-access-token",
  user: { email: "owner@example.com" },
};

function authClientWithSession(activeSession: AuthSession | null): BrowserAuthClient {
  return {
    getSession: vi.fn().mockResolvedValue({ session: activeSession, error: null }),
    onAuthStateChange: vi.fn((callback: SessionChangeCallback) => {
      callback(activeSession);
      return { unsubscribe: vi.fn() };
    }),
    signInWithOtp: vi.fn(),
    signOut: vi.fn(),
  };
}

describe("OrgShell", () => {
  afterEach(() => cleanup());

  it("renders bypass demo mode when auth is disabled", () => {
    render(
      <OrgShell authRequired={false}>
        <p>Dashboard body</p>
      </OrgShell>,
    );

    expect(screen.getByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByText("Dashboard body")).toBeInTheDocument();
  });

  it("renders authenticated org controls and exposes the access token", async () => {
    const tokenSpy = vi.fn();
    render(
      <OrgShell
        authClient={authClientWithSession(session)}
        authRequired
        onAccessTokenChange={tokenSpy}
      >
        <p>Dashboard body</p>
      </OrgShell>,
    );

    expect(await screen.findByText("owner@example.com")).toBeInTheDocument();
    expect(screen.getByLabelText("Organization name")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create organization" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Dashboard body")).toBeInTheDocument();
    await waitFor(() =>
      expect(tokenSpy).toHaveBeenCalledWith("test-access-token"),
    );
  });

  it("stores and selects the submitted organization", async () => {
    const organizationSpy = vi.fn();
    render(
      <OrgShell
        authClient={authClientWithSession(session)}
        authRequired
        onOrganizationChange={organizationSpy}
      >
        <p>Dashboard body</p>
      </OrgShell>,
    );

    fireEvent.change(await screen.findByLabelText("Organization name"), {
      target: { value: "Acme Analytics" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create organization" }));

    expect(await screen.findByText("Selected organization")).toBeInTheDocument();
    expect(screen.getByText("Acme Analytics")).toBeInTheDocument();
    expect(organizationSpy).toHaveBeenCalledWith({
      id: "Acme Analytics",
      name: "Acme Analytics",
    });
  });
});
