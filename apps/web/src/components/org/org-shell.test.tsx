import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import OrgShell from "./org-shell";
import { useAccountChrome, type AccountChrome } from "../../lib/account-context";
import {
  guardedSetQueryData,
  useQueryIdentity,
  type QueryIdentityValue,
} from "../../lib/query-identity";
import { queryKeys } from "../../lib/query-keys";
import type { MembershipOrganization } from "../../lib/session-memberships";
import type {
  AuthSession,
  BrowserAuthClient,
  SessionChangeCallback,
} from "../../lib/supabase-client";

// Stand-in for the dashboard: the signed-in identity and Sign out button now
// live in the dashboard's app bar, fed by OrgShell through account context.
// This probe consumes that context so the sign-out tests exercise the same
// wiring the real header uses.
function AccountChromeProbe() {
  const account = useAccountChrome();
  if (!account) return <p>dashboard</p>;
  return (
    <div>
      <p>dashboard</p>
      <p>Signed in</p>
      <p>{account.email}</p>
      <button onClick={account.onSignOut} type="button">
        Sign out
      </button>
      <button onClick={account.openAddAccount} type="button">
        Add account
      </button>
      {account.signOutError ? <p role="alert">{account.signOutError}</p> : null}
    </div>
  );
}

function authClient(
  session: AuthSession | null,
  overrides: Partial<BrowserAuthClient> = {},
): BrowserAuthClient {
  return {
    getSession: vi.fn().mockResolvedValue({ session, error: null }),
    onAuthStateChange: vi.fn(() => ({ unsubscribe: vi.fn() })),
    signInWithOtp: vi.fn(),
    verifyOtp: vi.fn(),
    verifyEmailCode: vi.fn(),
    verifyEmailOtp: vi.fn(),
    signOut: vi.fn().mockResolvedValue({ error: null }),
    ...overrides,
  };
}

const session: AuthSession = {
  accessToken: "access-token",
  user: { id: "user-a", email: "owner@example.com", appMetadata: null },
};

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

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
      expect(markup).toContain("Authenticating");
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
      expect(markup).toContain("Authenticating");
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

  it("shows the login form inside the dark brand card when there is no session", async () => {
    render(
      <OrgShell authClient={authClient(null)} authRequired>
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(await screen.findByRole("button", { name: "Email me a code" })).toBeInTheDocument();
    // The "Greybeam" wordmark is rendered only by AuthCard, so asserting it
    // proves the login form is wrapped in the new card (the old shell rendered
    // no wordmark). This fails before Task 5's wiring and passes after.
    expect(screen.getByText("Greybeam")).toBeInTheDocument();
  });

  it("shows the workspace-loading status while memberships resolve", async () => {
    let resolveMemberships: (orgs: MembershipOrganization[]) => void = () => {};
    const fetchMemberships = vi.fn(
      () =>
        new Promise<MembershipOrganization[]>((resolve) => {
          resolveMemberships = resolve;
        }),
    );
    render(
      <OrgShell
        authClient={authClient(session)}
        authRequired
        fetchMemberships={fetchMemberships}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(await screen.findByText("Loading workspace")).toBeInTheDocument();
    resolveMemberships([]);
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

  it("renders the dashboard and selects the org when membership resolves", async () => {
    const onOrganizationChange = vi.fn();
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
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
        accountLocator: null,
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
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
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

    let resolveTokenA: ((orgs: MembershipOrganization[]) => void) | undefined;
    const fetchMemberships = vi.fn((token: string) => {
      if (token === "access-token") {
        return new Promise<MembershipOrganization[]>((resolve) => {
          resolveTokenA = resolve;
        });
      }
      return Promise.resolve([
        { id: "org-b", name: "Bravo", role: "member" as const, accountLocator: null },
      ]);
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
      user: { id: "user-a", email: "owner@example.com", appMetadata: null },
    });

    await screen.findByText("dashboard");
    await waitFor(() =>
      expect(onOrganizationChange).toHaveBeenCalledWith({
        id: "org-b",
        name: "Bravo",
        role: "member",
        accountLocator: null,
      }),
    );

    // Now let the stale token-A request resolve last.
    resolveTokenA?.([{ id: "org-a", name: "Alpha", role: "member", accountLocator: null }]);

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(onOrganizationChange).not.toHaveBeenCalledWith({
      id: "org-a",
      name: "Alpha",
      accountLocator: null,
    });
  });

  it("discards a previous-user membership result still in flight across a user transition", async () => {
    // Finding: transitionUser must synchronously invalidate latestTokenRef.
    // Here user-a's membership fetch is left pending, then the user transitions
    // (the new session carries the SAME access token, so the access-token effect
    // does not re-run and therefore never updates latestTokenRef on its own). If
    // transitionUser does not invalidate the ref, user-a's late-resolving result
    // passes the token guard and pairs the NEW user with user-a's org.
    let onAuthStateChange: SessionChangeCallback | undefined;
    const client = authClient(session, {
      onAuthStateChange: vi.fn((callback: SessionChangeCallback) => {
        onAuthStateChange = callback;
        return { unsubscribe: vi.fn() };
      }),
    });

    let resolveA: ((orgs: MembershipOrganization[]) => void) | undefined;
    const fetchMemberships = vi.fn(
      () =>
        new Promise<MembershipOrganization[]>((resolve) => {
          resolveA = resolve;
        }),
    );

    const onOrganizationChange = vi.fn();
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={client}
        fetchMemberships={fetchMemberships}
        onOrganizationChange={onOrganizationChange}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() => expect(resolveA).toBeDefined());

    // Transition to a new user that reuses the same access token, so the
    // access-token effect will not re-run to supersede the token ref.
    const sameTokenNewUser: AuthSession = {
      accessToken: "access-token",
      user: { id: "user-b", email: "b@example.com", appMetadata: null },
    };

    // Let user-a's in-flight request resolve after the transition.
    await act(async () => {
      onAuthStateChange?.(sameTokenNewUser);
      resolveA?.([
        { id: "org-a", name: "Alpha", role: "member", accountLocator: null },
      ]);
    });

    // The stale user-a result must be discarded: the new user is never paired
    // with user-a's org, and the identity snapshot never settles on org-a.
    expect(onOrganizationChange).not.toHaveBeenCalledWith(
      expect.objectContaining({ id: "org-a" }),
    );
    expect(latest?.identity.snapshot.orgId).not.toBe("org-a");
  });

  it("does not refetch when an inline onOrganizationChange changes identity", async () => {
    const fetchMemberships = vi
      .fn()
      .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }]);
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
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
        onOrganizationChange={onOrganizationChange}
      >
        <AccountChromeProbe />
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
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
        onAccessTokenChange={onAccessTokenChange}
        onOrganizationChange={onOrganizationChange}
      >
        <AccountChromeProbe />
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

  it("selects the persisted org from localStorage when still a member", async () => {
    window.localStorage.setItem("greysight.activeOrganizationId", "org-2");
    const onOrganizationChange = vi.fn();
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi.fn().mockResolvedValue([
          { id: "org-1", name: "Alpha", accountLocator: "AAA" },
          { id: "org-2", name: "Beta", accountLocator: "BBB" },
        ])}
        onOrganizationChange={onOrganizationChange}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    await waitFor(() =>
      expect(onOrganizationChange).toHaveBeenCalledWith(
        expect.objectContaining({ id: "org-2" }),
      ),
    );
    // A null notify is allowed while loading (loading-screen path), but the
    // dashboard must be notified with the CORRECT org exactly once and never
    // with a different/wrong org (the transient wrong-org the reconcile effect
    // was designed to prevent).
    const orgCalls = onOrganizationChange.mock.calls.map((c) => c[0]).filter(Boolean);
    expect(orgCalls).toHaveLength(1);
    expect(orgCalls[0]).toEqual(expect.objectContaining({ id: "org-2" }));
  });

  it("falls back to the first org and clears a stale persisted id", async () => {
    window.localStorage.setItem("greysight.activeOrganizationId", "gone");
    const onOrganizationChange = vi.fn();
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Alpha", accountLocator: "AAA" }])}
        onOrganizationChange={onOrganizationChange}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    await waitFor(() =>
      expect(onOrganizationChange).toHaveBeenCalledWith(
        expect.objectContaining({ id: "org-1" }),
      ),
    );
    // A null notify is allowed while loading (loading-screen path), but the
    // dashboard must be notified with the CORRECT org exactly once and never
    // with a different/wrong org (the transient wrong-org the reconcile effect
    // was designed to prevent).
    const orgCalls = onOrganizationChange.mock.calls.map((c) => c[0]).filter(Boolean);
    expect(orgCalls).toHaveLength(1);
    expect(orgCalls[0]).toEqual(expect.objectContaining({ id: "org-1" }));
    expect(
      window.localStorage.getItem("greysight.activeOrganizationId"),
    ).toBeNull();
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
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
        onOrganizationChange={onOrganizationChange}
      >
        <AccountChromeProbe />
      </OrgShell>,
    );
    await screen.findByText("dashboard");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(signOut).toHaveBeenCalled());
    expect(onOrganizationChange).toHaveBeenLastCalledWith(null);
  });

  async function openAddAccountModal() {
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
      >
        <AccountChromeProbe />
      </OrgShell>,
    );
    await screen.findByText("dashboard");
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
    return await screen.findByRole("dialog");
  }

  it("closes the add-account modal when the Cancel button is clicked", async () => {
    await openAddAccountModal();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("closes the add-account modal when the backdrop is pressed", async () => {
    const dialog = await openAddAccountModal();
    // mouseDown on the dialog element itself: target === currentTarget, so the
    // backdrop's outside-press guard fires and the modal closes.
    fireEvent.mouseDown(dialog);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("keeps the add-account modal open when pressing inside the modal content", async () => {
    await openAddAccountModal();
    // mouseDown on a descendant of the content (the wizard heading): the
    // backdrop guard sees target !== currentTarget, so it must not close. This
    // protects a drag/text-selection that starts inside and releases on the
    // backdrop from discarding partially-entered credentials.
    const wizardHeading = screen.getByText(/connect your snowflake account/i);
    fireEvent.mouseDown(wizardHeading);
    // Give any (incorrect) close handler a chance to run.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  // ---- Identity / cache-lifecycle probes ------------------------------------

  type ProbeGrab = {
    queryClient: QueryClient;
    identity: QueryIdentityValue;
    account: AccountChrome | null;
  };

  function IdentityProbe({ grab }: { grab: (v: ProbeGrab) => void }) {
    const queryClient = useQueryClient();
    const identity = useQueryIdentity();
    const account = useAccountChrome();
    grab({ queryClient, identity, account });
    return <p data-testid="user-id">{account?.userId ?? "none"}</p>;
  }

  function withCapturedAuthCallback() {
    let onAuthStateChange: SessionChangeCallback | undefined;
    const client = authClient(session, {
      onAuthStateChange: vi.fn((callback: SessionChangeCallback) => {
        onAuthStateChange = callback;
        return { unsubscribe: vi.fn() };
      }),
    });
    return {
      client,
      trigger: (next: AuthSession | null) => onAuthStateChange?.(next),
    };
  }

  const userB: AuthSession = {
    accessToken: "token-b",
    user: { id: "user-b", email: "b@example.com", appMetadata: null },
  };

  it("clears the cache and preserves identity when the user changes via the auth callback", async () => {
    const { client, trigger } = withCapturedAuthCallback();
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={client}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("user-id")).toHaveTextContent("user-a"),
    );
    const queryClient = latest!.queryClient;
    // Seed data belonging to user A's org.
    queryClient.setQueryData(
      queryKeys.dashboard.scope("user-a", "org-1"),
      { stale: true },
    );
    expect(queryClient.getQueryCache().getAll().length).toBeGreaterThan(0);

    act(() => trigger(userB));

    await waitFor(() =>
      expect(queryClient.getQueryCache().getAll()).toHaveLength(0),
    );
    await waitFor(() =>
      expect(screen.getByTestId("user-id")).toHaveTextContent("user-b"),
    );
  });

  it("drops a deferred write captured before the user changes", async () => {
    const { client, trigger } = withCapturedAuthCallback();
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={client}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("user-id")).toHaveTextContent("user-a"),
    );
    // Capture user A's identity as an in-flight poll would.
    const captured = latest!.identity.capture();
    const staleKey = queryKeys.dashboard.scope("user-a", "org-1");

    // The user transitions to B before the deferred work resolves.
    act(() => trigger(userB));
    await waitFor(() =>
      expect(screen.getByTestId("user-id")).toHaveTextContent("user-b"),
    );

    const wrote = guardedSetQueryData(
      latest!.queryClient,
      latest!.identity.ref,
      captured,
      staleKey,
      { stale: true },
    );

    expect(wrote).toBe(false);
    expect(latest!.queryClient.getQueryData(staleKey)).toBeUndefined();
  });

  it("drops a deferred write when the org changes even though the epoch is unchanged", async () => {
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi.fn().mockResolvedValue([
          { id: "org-1", name: "Alpha", accountLocator: null },
          { id: "org-2", name: "Bravo", accountLocator: null },
        ])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() =>
      expect(latest?.identity.snapshot.orgId).toBe("org-1"),
    );
    const capturedEpoch = latest!.identity.snapshot.epoch;
    const captured = latest!.identity.capture();
    const staleKey = queryKeys.dashboard.scope("user-a", "org-1");

    // Switch the active org without any re-auth: the epoch stays fixed.
    act(() => latest!.account?.setActiveOrganization("org-2"));
    await waitFor(() =>
      expect(latest?.identity.snapshot.orgId).toBe("org-2"),
    );
    expect(latest!.identity.snapshot.epoch).toBe(capturedEpoch);

    const wrote = guardedSetQueryData(
      latest!.queryClient,
      latest!.identity.ref,
      captured,
      staleKey,
      { stale: true },
    );

    expect(wrote).toBe(false);
    expect(latest!.queryClient.getQueryData(staleKey)).toBeUndefined();
  });

  it("removeOrganizationQueries removes only the supplied org's entries", async () => {
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi.fn().mockResolvedValue([
          { id: "org-1", name: "Alpha", accountLocator: null },
          { id: "org-2", name: "Bravo", accountLocator: null },
        ])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() =>
      expect(latest?.identity.snapshot.orgId).toBe("org-1"),
    );
    const queryClient = latest!.queryClient;
    const orgOneKey = queryKeys.dashboard.scope("user-a", "org-1");
    const orgTwoKey = queryKeys.dashboard.scope("user-a", "org-2");
    queryClient.setQueryData(orgOneKey, { org: 1 });
    queryClient.setQueryData(orgTwoKey, { org: 2 });

    act(() => latest!.identity.removeOrganizationQueries("org-1"));

    expect(queryClient.getQueryData(orgOneKey)).toBeUndefined();
    expect(queryClient.getQueryData(orgTwoKey)).toEqual({ org: 2 });
  });

  it("drops a deferred write attempted synchronously after a user transition, before render commits", async () => {
    const { client, trigger } = withCapturedAuthCallback();
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={client}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("user-id")).toHaveTextContent("user-a"),
    );
    const captured = latest!.identity.capture();
    const staleKey = queryKeys.dashboard.scope("user-a", "org-1");

    // Fire the transition WITHOUT act(): React has cleared the cache and bumped
    // the epoch, but has not yet re-rendered/committed. A guarded write landing
    // in this window must still be dropped because identityRef was updated
    // synchronously inside transitionUser.
    trigger(userB);
    const wrote = guardedSetQueryData(
      latest!.queryClient,
      latest!.identity.ref,
      captured,
      staleKey,
      { stale: true },
    );

    expect(wrote).toBe(false);
    expect(latest!.queryClient.getQueryData(staleKey)).toBeUndefined();

    // Let the scheduled render/effects settle to avoid act warnings.
    await act(async () => {});
  });

  it("does not expose the previous user's identity in a capture taken after a transition, before render commits", async () => {
    const { client, trigger } = withCapturedAuthCallback();
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={client}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("user-id")).toHaveTextContent("user-a"),
    );

    // Fire the transition WITHOUT act(): the cache is cleared and the epoch is
    // bumped, but React has not yet re-rendered/committed. A capture taken in
    // this window must reflect the NEW user, never the prior user-a paired with
    // the freshly-bumped epoch (the stale-userId + new-epoch combination the
    // whole-snapshot reset prevents).
    trigger(userB);
    const captured = latest!.identity.capture();
    expect(captured.userId).not.toBe("user-a");

    // Once render commits to the new identity, a guarded write scoped to the
    // OLD user must be dropped and never land in the just-cleared cache.
    await act(async () => {});
    const staleKey = queryKeys.dashboard.scope("user-a", "org-1");
    const wrote = guardedSetQueryData(
      latest!.queryClient,
      latest!.identity.ref,
      captured,
      staleKey,
      { stale: true },
    );

    expect(wrote).toBe(false);
    expect(latest!.queryClient.getQueryData(staleKey)).toBeUndefined();
  });

  it("drops a deferred write attempted synchronously after an org switch, before render commits", async () => {
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi.fn().mockResolvedValue([
          { id: "org-1", name: "Alpha", accountLocator: null },
          { id: "org-2", name: "Bravo", accountLocator: null },
        ])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() => expect(latest?.identity.snapshot.orgId).toBe("org-1"));
    const captured = latest!.identity.capture();
    const staleKey = queryKeys.dashboard.scope("user-a", "org-1");

    // Switch org WITHOUT act(): activeOrgId state update is scheduled but not
    // yet committed. The guarded write for the old org must still be dropped
    // because identityRef.current.orgId was updated synchronously.
    latest!.account?.setActiveOrganization("org-2");
    const wrote = guardedSetQueryData(
      latest!.queryClient,
      latest!.identity.ref,
      captured,
      staleKey,
      { stale: true },
    );

    expect(wrote).toBe(false);
    expect(latest!.queryClient.getQueryData(staleKey)).toBeUndefined();

    await act(async () => {});
  });

  it("ignores a late initial getSession result after an auth-state callback fired", async () => {
    let resolveInitial:
      | ((result: { session: AuthSession | null; error: null }) => void)
      | undefined;
    let onAuthStateChange: SessionChangeCallback | undefined;
    const client = authClient(session, {
      getSession: vi.fn(
        () =>
          new Promise<{ session: AuthSession | null; error: null }>(
            (resolve) => {
              resolveInitial = resolve;
            },
          ),
      ),
      onAuthStateChange: vi.fn((callback: SessionChangeCallback) => {
        onAuthStateChange = callback;
        return { unsubscribe: vi.fn() };
      }),
    });
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={client}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    // Auth-state callback delivers user B while the initial getSession is
    // still pending.
    await waitFor(() => expect(onAuthStateChange).toBeDefined());
    act(() => onAuthStateChange?.(userB));
    await waitFor(() =>
      expect(screen.getByTestId("user-id")).toHaveTextContent("user-b"),
    );

    const queryClient = latest!.queryClient;
    const key = queryKeys.dashboard.scope("user-b", "org-1");
    queryClient.setQueryData(key, { keep: true });

    // The stale initial getSession resolves last with user A. It must be
    // ignored: no transition back to A, no second cache clear.
    await act(async () => {
      resolveInitial?.({ session, error: null });
      await Promise.resolve();
    });

    expect(screen.getByTestId("user-id")).toHaveTextContent("user-b");
    expect(queryClient.getQueryData(key)).toEqual({ keep: true });
  });

  it("drops a write captured pre-render under the captured snapshot's own demo-org key after a user transition", async () => {
    // Finding 1: during a signed-in user->user transition the live ref is
    // synchronously {newUser, DEMO_ORG_ID, newEpoch}. A capture in the
    // pre-render window returns exactly that; if it passed the guard, a guarded
    // write under scope(newUser, demo-org) would land a demo-scoped cache entry
    // for a real user. The transitioning marker must make it uncapturable.
    const { client, trigger } = withCapturedAuthCallback();
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={client}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("user-id")).toHaveTextContent("user-a"),
    );

    // Transition WITHOUT act(): cache cleared + epoch bumped, no render commit.
    trigger(userB);
    const captured = latest!.identity.capture();
    // The captured snapshot's OWN key (its userId + orgId) — the exact path
    // finding 1 warns about.
    const ownKey = queryKeys.dashboard.scope(captured.userId, captured.orgId);

    const wrote = guardedSetQueryData(
      latest!.queryClient,
      latest!.identity.ref,
      captured,
      ownKey,
      { stale: true },
    );

    expect(wrote).toBe(false);
    expect(latest!.queryClient.getQueryData(ownKey)).toBeUndefined();

    await act(async () => {});
  });

  it("never pairs the new user with the previous user's org before the new memberships load", async () => {
    // Finding 3: the render-time refresh derives orgId from activeOrganization,
    // which is computed from the PREVIOUS user's membership state. Without a
    // reset, the first new-user render could pair {newUser, oldOrg} and let a
    // guarded write for that combination land. transitionUser must reset stale
    // membership and keep the snapshot transitioning until the new user's
    // memberships resolve.
    const { client, trigger } = withCapturedAuthCallback();
    let resolveB: ((orgs: MembershipOrganization[]) => void) | undefined;
    const fetchMemberships = vi.fn((token: string) => {
      if (token === "access-token") {
        return Promise.resolve<MembershipOrganization[]>([
          { id: "org-1", name: "Acme", role: "member", accountLocator: null },
        ]);
      }
      // user-b's memberships stay pending, holding the transition window open.
      return new Promise<MembershipOrganization[]>((resolve) => {
        resolveB = resolve;
      });
    });
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell authRequired authClient={client} fetchMemberships={fetchMemberships}>
        <IdentityProbe grab={(v) => (latest = v)} />
      </OrgShell>,
    );

    await waitFor(() => expect(latest?.identity.snapshot.orgId).toBe("org-1"));
    const queryClient = latest!.queryClient;
    const ref = latest!.identity.ref;

    // Transition to user-b; its memberships never resolve during this window.
    await act(async () => {
      trigger(userB);
    });

    // A capture that would pair the new user with the OLD org must not pass the
    // guard, and the live ref must not have settled to {user-b, org-1}.
    const staleKey = queryKeys.dashboard.scope("user-b", "org-1");
    const fabricated = {
      userId: "user-b",
      orgId: "org-1",
      epoch: ref.current.epoch,
    };
    const wrote = guardedSetQueryData(
      queryClient,
      ref,
      fabricated,
      staleKey,
      { stale: true },
    );

    expect(wrote).toBe(false);
    expect(queryClient.getQueryData(staleKey)).toBeUndefined();
    expect(ref.current.transitioning).toBe(true);

    // Cleanup: let user-b's memberships resolve so pending effects settle.
    resolveB?.([
      { id: "org-9", name: "Nine", role: "member", accountLocator: null },
    ]);
    await act(async () => {});
  });

  it("does not clear the cache when a sign-out fails", async () => {
    const signOut = vi
      .fn()
      .mockResolvedValue({ error: { message: "Sign out failed" } });
    let latest: ProbeGrab | undefined;
    render(
      <OrgShell
        authRequired
        authClient={authClient(session, { signOut })}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme", accountLocator: null }])}
      >
        <IdentityProbe grab={(v) => (latest = v)} />
        <AccountChromeProbe />
      </OrgShell>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("user-id")).toHaveTextContent("user-a"),
    );
    const queryClient = latest!.queryClient;
    queryClient.setQueryData(queryKeys.dashboard.scope("user-a", "org-1"), {
      keep: true,
    });

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(signOut).toHaveBeenCalled());
    await screen.findByText(/couldn’t sign you out/i);

    // Failed sign-out must not clear the cache or the identity.
    expect(queryClient.getQueryCache().getAll().length).toBeGreaterThan(0);
    expect(screen.getByTestId("user-id")).toHaveTextContent("user-a");
  });
});
