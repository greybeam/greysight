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
import createBrowserAuthClient from "../../lib/supabase-client";

vi.mock("../../lib/supabase-client", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../../lib/supabase-client")>();
  return {
    ...actual,
    default: vi.fn(() => null),
  };
});

const session: AuthSession = {
  accessToken: "test-access-token",
  user: {
    email: "owner@example.com",
    appMetadata: {
      organization_ids: ["22222222-2222-4222-8222-222222222222"],
    },
  },
};

const sessionWithoutOrganization: AuthSession = {
  accessToken: "test-access-token",
  user: { email: "owner@example.com", appMetadata: {} },
};

const sessionWithMultipleOrganizations: AuthSession = {
  accessToken: "test-access-token",
  user: {
    email: "owner@example.com",
    appMetadata: {
      organization_ids: [
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
      ],
    },
  },
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
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders bypass demo mode when auth is disabled", () => {
    render(
      <OrgShell authRequired={false}>
        <p>Dashboard body</p>
      </OrgShell>,
    );

    expect(screen.getByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByText("Dashboard body")).toBeInTheDocument();
    expect(createBrowserAuthClient).not.toHaveBeenCalled();
  });

  it("does not recreate the browser auth client on rerender", () => {
    const { rerender } = render(
      <OrgShell authRequired>
        <p>Dashboard body</p>
      </OrgShell>,
    );

    rerender(
      <OrgShell authRequired>
        <p>Dashboard body</p>
      </OrgShell>,
    );

    expect(createBrowserAuthClient).toHaveBeenCalledTimes(1);
  });

  it("preserves explicit null auth client", () => {
    render(
      <OrgShell authClient={null} authRequired>
        <p>Dashboard body</p>
      </OrgShell>,
    );

    expect(
      screen.getByText("Authentication is not configured"),
    ).toBeInTheDocument();
    expect(createBrowserAuthClient).not.toHaveBeenCalled();
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
        organizationIdGenerator={() => "11111111-1111-4111-8111-111111111111"}
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
      id: "22222222-2222-4222-8222-222222222222",
      name: "Acme Analytics",
    });
  });

  it("does not create a local organization when auth membership is missing", async () => {
    const organizationSpy = vi.fn();
    render(
      <OrgShell
        authClient={authClientWithSession(sessionWithoutOrganization)}
        authRequired
        organizationIdGenerator={() => "11111111-1111-4111-8111-111111111111"}
        onOrganizationChange={organizationSpy}
      >
        <p>Dashboard body</p>
      </OrgShell>,
    );

    expect(
      await screen.findByText("No organization membership is available for this session."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create organization" })).toBeDisabled();
    expect(organizationSpy).not.toHaveBeenCalled();
  });

  it("does not bind a typed name to an arbitrary membership when multiple orgs exist", async () => {
    const organizationSpy = vi.fn();
    render(
      <OrgShell
        authClient={authClientWithSession(sessionWithMultipleOrganizations)}
        authRequired
        onOrganizationChange={organizationSpy}
      >
        <p>Dashboard body</p>
      </OrgShell>,
    );

    expect(
      await screen.findByText(
        "Multiple organization memberships are available for this session.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create organization" })).toBeDisabled();
    expect(organizationSpy).not.toHaveBeenCalled();
  });
});
