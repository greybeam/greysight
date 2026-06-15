"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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

export type SelectedOrganization = {
  id: string;
  name: string;
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

  useEffect(() => {
    if (membership.status !== "resolved") return;
    onOrganizationChangeRef.current?.(membership.organizations[0] ?? null);
  }, [membership]);

  const handleSignOut = useCallback(async () => {
    setSignOutError(null);
    const result = await authClient?.signOut();
    if (result?.error) {
      setSignOutError("We couldn’t sign you out. Please try again.");
      return;
    }
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
      <main className="min-h-screen bg-slate-50 p-6">
        <p className="text-sm text-slate-600">Loading authentication</p>
      </main>
    );
  }

  if (!authClient) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold text-slate-950">
            Authentication is not configured
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            Set public Supabase URL and anon key to enable login.
          </p>
        </section>
      </main>
    );
  }

  if (loadingSession) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <p className="text-sm text-slate-600">Loading authentication</p>
      </main>
    );
  }

  if (!session) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <LoginForm authClient={authClient} />
      </main>
    );
  }

  const signedInHeader = (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Signed in
          </p>
          <p className="text-sm font-semibold text-slate-950">
            {session.user?.email ?? "Authenticated user"}
          </p>
        </div>
        <button
          className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100"
          onClick={handleSignOut}
          type="button"
        >
          Sign out
        </button>
      </div>
      {signOutError ? (
        <p className="text-sm font-medium text-red-700" role="alert">
          {signOutError}
        </p>
      ) : null}
    </div>
  );

  if (membership.status === "idle" || membership.status === "loading") {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <p className="text-sm text-slate-600">Loading your workspace</p>
      </main>
    );
  }

  if (membership.status === "error") {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          {signedInHeader}
          <p className="text-sm text-red-700" role="alert">
            We couldn’t load your organizations. Please try again.
          </p>
          <button
            className="rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
            onClick={() => accessToken && void loadMemberships(accessToken)}
            type="button"
          >
            Retry
          </button>
        </section>
      </main>
    );
  }

  if (membership.organizations.length === 0) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          {signedInHeader}
          <p className="text-sm text-slate-700">
            You’re signed in. Connecting your Snowflake account is coming soon.
          </p>
        </section>
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        {signedInHeader}
      </section>
      {children}
    </div>
  );
}
