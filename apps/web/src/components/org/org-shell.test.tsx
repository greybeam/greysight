import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import OrgShell from "./org-shell";
import type {
  AuthSession,
  BrowserAuthClient,
  SessionChangeCallback,
} from "../../lib/supabase-client";

function authClient(
  session: AuthSession | null,
  overrides: Partial<BrowserAuthClient> = {},
): BrowserAuthClient {
  return {
    getSession: vi.fn().mockResolvedValue({ session, error: null }),
    onAuthStateChange: vi.fn(() => ({ unsubscribe: vi.fn() })),
    signInWithOtp: vi.fn(),
    verifyOtp: vi.fn(),
    signOut: vi.fn().mockResolvedValue({ error: null }),
    ...overrides,
  };
}

const session: AuthSession = {
  accessToken: "access-token",
  user: { email: "owner@example.com", appMetadata: null },
};

afterEach(() => cleanup());

describe("OrgShell", () => {
  it("renders children with the demo banner when auth is not required", () => {
    render(
      <OrgShell authRequired={false}>
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(screen.getByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByText("dashboard")).toBeInTheDocument();
  });

  it("renders a deterministic loading placeholder on the server when no authClient prop is provided", () => {
    // Mimic an empty server runtime env so the OLD code (which called
    // createBrowserAuthClient in a useState initializer) would have produced
    // the "not configured" branch on the server render. The fixed component
    // must defer client creation and show the loading placeholder instead,
    // regardless of env, keeping SSR markup deterministic.
    const originalUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const originalKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    delete process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    try {
      const markup = renderToStaticMarkup(
        <OrgShell authRequired>
          <p>dashboard</p>
        </OrgShell>,
      );
      expect(markup).toContain("Loading authentication");
      expect(markup).not.toContain("Authentication is not configured");
    } finally {
      if (originalUrl === undefined) {
        delete process.env.NEXT_PUBLIC_SUPABASE_URL;
      } else {
        process.env.NEXT_PUBLIC_SUPABASE_URL = originalUrl;
      }
      if (originalKey === undefined) {
        delete process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
      } else {
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = originalKey;
      }
    }
  });

  it("renders the same loading placeholder on the server even when the public env vars are present", () => {
    // The hydration invariant is that the FIRST render is identical whether or
    // not the public env vars exist. With non-empty env, createBrowserAuthClient()
    // WOULD return a real client post-mount, but the server/first-paint markup must
    // still defer client creation and show the loading placeholder — identical to
    // the empty-env case — so the first paint never diverges based on env presence.
    const originalUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const originalKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co";
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon-key";
    try {
      const markup = renderToStaticMarkup(
        <OrgShell authRequired>
          <p>dashboard</p>
        </OrgShell>,
      );
      expect(markup).toContain("Loading authentication");
      expect(markup).not.toContain("Authentication is not configured");
    } finally {
      if (originalUrl === undefined) {
        delete process.env.NEXT_PUBLIC_SUPABASE_URL;
      } else {
        process.env.NEXT_PUBLIC_SUPABASE_URL = originalUrl;
      }
      if (originalKey === undefined) {
        delete process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
      } else {
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = originalKey;
      }
    }
  });

  it("renders the login form when there is no session", async () => {
    render(
      <OrgShell authRequired authClient={authClient(null)}>
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByText("dashboard")).not.toBeInTheDocument();
  });

  it("shows the connect wizard (not the dashboard) when the user has no organization", async () => {
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi.fn().mockResolvedValue([])}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(
      await screen.findByText(/connect your snowflake account/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("dashboard")).not.toBeInTheDocument();
  });

  it("shows the connect wizard when the user has no organizations", async () => {
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi.fn().mockResolvedValue([])}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(
      await screen.findByText(/connect your snowflake account/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
  });

  it("renders the dashboard and selects the org when membership resolves", async () => {
    const onOrganizationChange = vi.fn();
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme" }])}
        onOrganizationChange={onOrganizationChange}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
    await waitFor(() =>
      expect(onOrganizationChange).toHaveBeenCalledWith({
        id: "org-1",
        name: "Acme",
      }),
    );
  });

  it("shows an error state (not the no-org screen) when the lookup fails", async () => {
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi.fn().mockRejectedValue(new Error("boom"))}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(
      await screen.findByText(/couldn’t load your organizations/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/coming soon/i),
    ).not.toBeInTheDocument();
  });

  it("discards a stale membership result when the token has changed", async () => {
    let onAuthStateChange: SessionChangeCallback | undefined;
    const client = authClient(session, {
      onAuthStateChange: vi.fn((callback: SessionChangeCallback) => {
        onAuthStateChange = callback;
        return { unsubscribe: vi.fn() };
      }),
    });

    let resolveTokenA: ((orgs: { id: string; name: string }[]) => void) | undefined;
    const fetchMemberships = vi.fn((token: string) => {
      if (token === "access-token") {
        return new Promise<{ id: string; name: string }[]>((resolve) => {
          resolveTokenA = resolve;
        });
      }
      return Promise.resolve([{ id: "org-b", name: "Bravo" }]);
    });

    const onOrganizationChange = vi.fn();
    render(
      <OrgShell
        authRequired
        authClient={client}
        fetchMemberships={fetchMemberships}
        onOrganizationChange={onOrganizationChange}
      >
        <p>dashboard</p>
      </OrgShell>,
    );

    await waitFor(() => expect(resolveTokenA).toBeDefined());

    // Sign back in with token B before A resolves.
    onAuthStateChange?.({
      accessToken: "token-b",
      user: { email: "owner@example.com", appMetadata: null },
    });

    await screen.findByText("dashboard");
    await waitFor(() =>
      expect(onOrganizationChange).toHaveBeenCalledWith({
        id: "org-b",
        name: "Bravo",
      }),
    );

    // Now let the stale token-A request resolve last.
    resolveTokenA?.([{ id: "org-a", name: "Alpha" }]);

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(onOrganizationChange).not.toHaveBeenCalledWith({
      id: "org-a",
      name: "Alpha",
    });
  });

  it("does not refetch when an inline onOrganizationChange changes identity", async () => {
    const fetchMemberships = vi
      .fn()
      .mockResolvedValue([{ id: "org-1", name: "Acme" }]);
    const client = authClient(session);

    function Parent() {
      const [, setTick] = useState(0);
      return (
        <OrgShell
          authRequired
          authClient={client}
          fetchMemberships={fetchMemberships}
          // Inline callback: new identity each render.
          onOrganizationChange={() => setTick((value) => value + 1)}
        >
          <p>dashboard</p>
        </OrgShell>
      );
    }

    render(<Parent />);
    await screen.findByText("dashboard");

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchMemberships).toHaveBeenCalledTimes(1);
  });

  it("surfaces a sign-out error and does not clear the organization", async () => {
    const signOut = vi
      .fn()
      .mockResolvedValue({ error: { message: "Sign out failed" } });
    const onOrganizationChange = vi.fn();
    render(
      <OrgShell
        authRequired
        authClient={authClient(session, { signOut })}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme" }])}
        onOrganizationChange={onOrganizationChange}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    await screen.findByText("dashboard");
    onOrganizationChange.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(signOut).toHaveBeenCalled());
    expect(
      await screen.findByText(/couldn’t sign you out/i),
    ).toBeInTheDocument();
    expect(onOrganizationChange).not.toHaveBeenCalledWith(null);
  });

  it("clears the signed-in state synchronously on sign-out without the auth-state callback firing", async () => {
    const signOut = vi.fn().mockResolvedValue({ error: null });
    const onAccessTokenChange = vi.fn();
    const onOrganizationChange = vi.fn();
    // The default onAuthStateChange mock captures the callback but never
    // invokes it, simulating a delayed/never-firing auth-state event. The
    // component must still fall back to the login form on its own.
    render(
      <OrgShell
        authRequired
        authClient={authClient(session, { signOut })}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme" }])}
        onAccessTokenChange={onAccessTokenChange}
        onOrganizationChange={onOrganizationChange}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    await screen.findByText("dashboard");
    onAccessTokenChange.mockClear();
    onOrganizationChange.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(signOut).toHaveBeenCalled());

    // Login form appears and the signed-in header is gone, proving local
    // session state was cleared without the onAuthStateChange callback.
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByText("Signed in")).not.toBeInTheDocument();
    expect(screen.queryByText("dashboard")).not.toBeInTheDocument();

    // The cascade settled token and org callbacks to the signed-out values.
    await waitFor(() =>
      expect(onAccessTokenChange).toHaveBeenLastCalledWith(null),
    );
    expect(onOrganizationChange).toHaveBeenLastCalledWith(null);
  });

  it("signs out and clears the selected organization", async () => {
    const signOut = vi.fn().mockResolvedValue({ error: null });
    const onOrganizationChange = vi.fn();
    render(
      <OrgShell
        authRequired
        authClient={authClient(session, { signOut })}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme" }])}
        onOrganizationChange={onOrganizationChange}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    await screen.findByText("dashboard");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(signOut).toHaveBeenCalled());
    expect(onOrganizationChange).toHaveBeenLastCalledWith(null);
  });
});
