"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AccountChromeProvider } from "../../lib/account-context";
import {
  readActiveOrganizationId,
  writeActiveOrganizationId,
} from "../../lib/active-organization";
import { getAuthMode } from "../../lib/auth-mode";
import createBrowserAuthClient, {
  type AuthSession,
  type BrowserAuthClient,
} from "../../lib/supabase-client";
import {
  fetchSessionMemberships,
  type MembershipOrganization,
} from "../../lib/session-memberships";
import LoginForm from "../auth/login-form";
import AuthCard from "../auth/auth-card";
import AuthStatus from "../auth/auth-status";
import ConnectWizard from "./connect-wizard";

export type SelectedOrganization = {
  id: string;
  name: string;
  accountLocator: string | null;
};

type MembershipState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error" }
  | { status: "resolved"; organizations: MembershipOrganization[] };

type OrgShellProps = {
  authClient?: BrowserAuthClient | null;
  authRequired?: boolean;
  bypassModeLabel?: string;
  children: React.ReactNode;
  fetchMemberships?: (accessToken: string) => Promise<MembershipOrganization[]>;
  onAccessTokenChange?: (accessToken: string | null) => void;
  onOrganizationChange?: (organization: SelectedOrganization | null) => void;
};

export default function OrgShell({
  authClient: providedAuthClient,
  authRequired = getAuthMode().authRequired,
  bypassModeLabel = "Demo mode",
  children,
  fetchMemberships = fetchSessionMemberships,
  onAccessTokenChange,
  onOrganizationChange,
}: OrgShellProps) {
  const [authClient, setAuthClient] = useState<BrowserAuthClient | null>(
    providedAuthClient ?? null,
  );
  // When a prop is supplied (a client OR an explicit null), the auth client is
  // resolved synchronously so existing behavior/tests are unchanged. Otherwise
  // it stays unresolved until a post-mount effect creates the browser client,
  // keeping the first render env-independent and hydration-safe.
  const [authClientResolved, setAuthClientResolved] = useState(
    providedAuthClient !== undefined,
  );
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loadingSession, setLoadingSession] = useState(
    authRequired && providedAuthClient !== undefined && Boolean(authClient),
  );
  const [membership, setMembership] = useState<MembershipState>({
    status: "idle",
  });
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [addAccountOpen, setAddAccountOpen] = useState(false);
  const accessToken = session?.accessToken ?? null;

  // When no client prop is supplied, defer createBrowserAuthClient() to a
  // post-mount effect. Until it runs, both the server render and the client's
  // first paint show the deterministic "Loading authentication" placeholder,
  // avoiding the env-dependent hydration mismatch. The synchronous setState is
  // intentional one-time resolution, not a cascading update loop.
  useEffect(() => {
    if (providedAuthClient !== undefined || !authRequired) return;
    const client = createBrowserAuthClient();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAuthClient(client);
    setAuthClientResolved(true);
    // Avoid flashing the login form before the deferred client fetches the
    // session: enter the loading state as soon as a real client resolves.
    if (client) setLoadingSession(true);
  }, [authRequired, providedAuthClient]);

  // Track the latest token so stale in-flight membership requests can be
  // discarded, and store callback props in refs so changing callback identity
  // never retriggers the membership effect.
  const latestTokenRef = useRef<string | null>(accessToken);
  const onAccessTokenChangeRef = useRef(onAccessTokenChange);
  const onOrganizationChangeRef = useRef(onOrganizationChange);

  useEffect(() => {
    onAccessTokenChangeRef.current = onAccessTokenChange;
    onOrganizationChangeRef.current = onOrganizationChange;
  });

  useEffect(() => {
    if (!authRequired || !authClient) return;

    let active = true;
    void authClient.getSession().then((result) => {
      if (!active) return;
      setSession(result.session);
      setLoadingSession(false);
    });

    const subscription = authClient.onAuthStateChange((nextSession) => {
      if (!active) return;
      setSession(nextSession);
      setLoadingSession(false);
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, [authClient, authRequired]);

  useEffect(() => {
    onAccessTokenChangeRef.current?.(accessToken);
  }, [accessToken]);

  const loadMemberships = useCallback(
    async (token: string) => {
      setMembership({ status: "loading" });
      try {
        const organizations = await fetchMemberships(token);
        // Discard results for a token that is no longer current (stale-token
        // race: a later sign-in superseded this request).
        if (latestTokenRef.current !== token) return;
        setMembership({ status: "resolved", organizations });
      } catch {
        if (latestTokenRef.current !== token) return;
        setMembership({ status: "error" });
      }
    },
    [fetchMemberships],
  );

  useEffect(() => {
    if (!authRequired) return;
    // Record the current token before any async work so a stale in-flight
    // request (an earlier token resolving last) can be discarded.
    latestTokenRef.current = accessToken;
    if (!accessToken) {
      // Reset to idle when the session is cleared (e.g. sign-out). This is
      // derived-state synchronization, not a cascading update loop.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMembership({ status: "idle" });
      onOrganizationChangeRef.current?.(null);
      return;
    }
    void loadMemberships(accessToken);
  }, [accessToken, authRequired, loadMemberships]);

  const organizations =
    membership.status === "resolved" ? membership.organizations : [];
  const activeOrganization =
    organizations.find((org) => org.id === activeOrgId) ??
    organizations[0] ??
    null;

  // Reconcile the active org from localStorage AND notify the parent in ONE
  // effect, so the dashboard runtime rebuilds exactly once with the correct org.
  //   1. Keep the persisted id if it is still a member; otherwise fall back to
  //      the first org and CLEAR the stale key (write null — we never persist the
  //      implicit first-org fallback; persistence happens only on an explicit
  //      selection via setActiveOrganization).
  //   2. Settle activeOrgId first (return), then notify on the next pass once
  //      activeOrgId equals the resolved selection. This avoids the transient
  //      wrong-org notify that a separate [activeOrganization] effect would emit
  //      (first org, then the persisted org) on the first resolved render.
  useEffect(() => {
    if (membership.status !== "resolved") return;
    const orgs = membership.organizations;
    const stored = readActiveOrganizationId();
    const valid =
      stored && orgs.some((org) => org.id === stored) ? stored : null;
    if (stored && !valid) writeActiveOrganizationId(null);
    const resolvedId = valid ?? orgs[0]?.id ?? null;
    if (resolvedId !== activeOrgId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActiveOrgId(resolvedId);
      return;
    }
    onOrganizationChangeRef.current?.(
      orgs.find((org) => org.id === resolvedId) ?? null,
    );
  }, [membership, activeOrgId]);

  const setActiveOrganization = useCallback((id: string) => {
    setActiveOrgId(id);
    writeActiveOrganizationId(id);
  }, []);

  const openAddAccount = useCallback(() => setAddAccountOpen(true), []);

  const handleSignOut = useCallback(async () => {
    setSignOutError(null);
    const result = await authClient?.signOut();
    if (result?.error) {
      setSignOutError("We couldn’t sign you out. Please try again.");
      return;
    }
    // Synchronously clear local auth state instead of waiting for the async
    // onAuthStateChange callback. Setting the session to null drives, via the
    // existing effects, the membership reset to idle, the latest-token ref
    // reset, onAccessTokenChange(null), and onOrganizationChange(null) — so the
    // component cannot keep rendering as signed-in if that callback is delayed
    // or never fires.
    setSession(null);
    onOrganizationChangeRef.current?.(null);
  }, [authClient]);

  if (!authRequired) {
    return (
      <div className="min-h-screen bg-slate-50">
        <div className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-900">
          {bypassModeLabel}
        </div>
        {children}
      </div>
    );
  }

  if (!authClientResolved) {
    return (
      <AuthCard>
        <AuthStatus label="Authenticating" />
      </AuthCard>
    );
  }

  if (!authClient) {
    return (
      <AuthCard>
        <div className="space-y-2 text-center">
          <h2 className="text-base font-semibold text-slate-50">
            Authentication is not configured
          </h2>
          <p className="text-sm text-slate-400">
            Set public Supabase URL and anon key to enable login.
          </p>
        </div>
      </AuthCard>
    );
  }

  if (loadingSession) {
    return (
      <AuthCard>
        <AuthStatus label="Authenticating" />
      </AuthCard>
    );
  }

  if (!session) {
    return (
      <AuthCard>
        <LoginForm authClient={authClient} />
      </AuthCard>
    );
  }

  const signedInHeader = (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Signed in
          </p>
          <p className="text-sm font-semibold text-slate-950 dark:text-slate-100">
            {session.user?.email ?? "Authenticated user"}
          </p>
        </div>
        <button
          className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:border-hairline dark:text-slate-300 dark:hover:bg-hairline"
          onClick={handleSignOut}
          type="button"
        >
          Sign out
        </button>
      </div>
      {signOutError ? (
        <p className="text-sm font-medium text-red-700 dark:text-red-400" role="alert">
          {signOutError}
        </p>
      ) : null}
    </div>
  );

  if (membership.status === "idle" || membership.status === "loading") {
    return (
      <AuthCard>
        <AuthStatus label="Loading workspace" />
      </AuthCard>
    );
  }

  if (membership.status === "error") {
    return (
      <AuthCard>
        <div className="space-y-4">
          {signedInHeader}
          <p className="text-sm text-red-400" role="alert">
            We couldn’t load your organizations. Please try again.
          </p>
          <button
            className="w-full rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chart-purple focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
            onClick={() => accessToken && void loadMemberships(accessToken)}
            type="button"
          >
            Retry
          </button>
        </div>
      </AuthCard>
    );
  }

  if (membership.organizations.length === 0) {
    return (
      <main className="dark min-h-screen bg-canvas p-6 [color-scheme:dark]">
        <section className="mb-6 rounded-lg border border-hairline bg-surface p-5 shadow-sm">
          {signedInHeader}
        </section>
        <ConnectWizard
          accessToken={accessToken}
          onConnected={() => accessToken && void loadMemberships(accessToken)}
        />
      </main>
    );
  }

  // The dashboard renders its own full-height dark app bar, so the signed-in
  // identity + sign-out are handed to it through context and surfaced inside
  // that single bar — no separate (light) account strip wrapping the dashboard.
  return (
    <AccountChromeProvider
      value={{
        email: session.user?.email ?? "Authenticated user",
        onSignOut: handleSignOut,
        signOutError,
        organizations,
        activeOrganizationId: activeOrganization?.id ?? null,
        setActiveOrganization,
        openAddAccount,
      }}
    >
      {children}
      {addAccountOpen ? (
        <div
          className="dark fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-6 [color-scheme:dark]"
          role="dialog"
          aria-modal="true"
          onMouseDown={(e) => {
            // Close only when the press both begins and ends on the backdrop
            // itself. Guarding on target === currentTarget prevents a drag or
            // text-selection that starts inside the wizard and releases over
            // the backdrop from dismissing the modal and discarding
            // partially-entered Snowflake credentials.
            if (e.target === e.currentTarget) setAddAccountOpen(false);
          }}
        >
          <div className="w-full max-w-4xl">
            <div className="mb-3 flex justify-end">
              <button
                className="h-9 rounded-md border border-hairline bg-surface px-3 text-sm font-medium text-slate-300 hover:bg-white/5"
                onClick={() => setAddAccountOpen(false)}
                type="button"
              >
                Cancel
              </button>
            </div>
            <ConnectWizard
              accessToken={accessToken}
              onConnected={(newOrgId) => {
                // Persist the new org and reload memberships; once the reload
                // includes it, the reconcile effect selects it (now a valid
                // member) and notifies the dashboard. No setActiveOrganization
                // call needed here.
                writeActiveOrganizationId(newOrgId);
                setAddAccountOpen(false);
                if (accessToken) void loadMemberships(accessToken);
              }}
            />
          </div>
        </div>
      ) : null}
    </AccountChromeProvider>
  );
}
