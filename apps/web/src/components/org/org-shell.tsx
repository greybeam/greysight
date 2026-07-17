"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AccountChromeProvider } from "../../lib/account-context";
import {
  DEMO_ORG_ID,
  DEMO_USER_ID,
  QueryIdentityProvider,
  type QueryIdentitySnapshot,
} from "../../lib/query-identity";
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

  // OrgShell owns a single QueryClient for the whole authenticated tree so the
  // cache lives and dies with this shell's identity, not with any child.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            gcTime: 30 * 60_000,
            retry: false,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  // Identity epoch bumps on every user transition. `observedUserIdRef` starts
  // `undefined` (nothing observed yet) so the first real/null resolution counts
  // as a transition exactly once; repeated identical callbacks are ignored.
  const [identityEpoch, setIdentityEpoch] = useState(0);
  const identityEpochRef = useRef(0);
  const observedUserIdRef = useRef<string | null | undefined>(undefined);
  const identityRef = useRef<QueryIdentitySnapshot>({
    userId: DEMO_USER_ID,
    orgId: DEMO_ORG_ID,
    epoch: 0,
  });

  // Track the latest token so stale in-flight membership requests can be
  // discarded. Declared before transitionUser so a transition can synchronously
  // invalidate it, dropping a previous user's in-flight membership request.
  const latestTokenRef = useRef<string | null>(accessToken);

  // Centralize user transitions: cancel in-flight queries, wipe the cache, and
  // bump the epoch whenever the signed-in user id actually changes (including
  // null <-> user). A no-op when the user id is unchanged, so repeated null/user
  // callbacks never double-bump the epoch or needlessly clear the cache.
  const transitionUser = useCallback(
    (nextUserId: string | null) => {
      if (observedUserIdRef.current === nextUserId) return;
      observedUserIdRef.current = nextUserId;
      void queryClient.cancelQueries();
      queryClient.clear();
      // Synchronously invalidate the latest-token ref so a PREVIOUS user's
      // membership request still in flight is discarded on resolution, even if
      // the access-token effect has not yet run to record the new token (e.g.
      // the new session reuses the same access token, so that effect never
      // re-runs). Null can never equal a real token, so loadMemberships's guard
      // rejects the stale result rather than pairing the new user with the old
      // user's organizations.
      latestTokenRef.current = null;
      identityEpochRef.current += 1;
      // Replace the WHOLE live snapshot synchronously and mark it transitioning.
      // Until the NEW user's memberships resolve, no coherent identity exists:
      // the userId is known but the org is not. Marking the snapshot
      // `transitioning` makes it uncapturable (sameQueryIdentity always returns
      // false), so a write captured in the pre-commit window can neither
      // repopulate the just-cleared cache with the previous user's data (stale
      // userId), nor land a demo-org-scoped entry for a real user, nor pair the
      // new user with the previous user's org. The render-time refresh clears
      // the marker once the new user's real org is established.
      identityRef.current = {
        userId: nextUserId ?? DEMO_USER_ID,
        orgId: DEMO_ORG_ID,
        epoch: identityEpochRef.current,
        transitioning: true,
      };
      // Reset membership/active-org state that belonged to the PREVIOUS user so
      // the next render can't derive `activeOrganization` from the old user's
      // memberships and pair it with the new user. The accessToken effect
      // reloads memberships for the new session.
      setMembership({ status: "idle" });
      setActiveOrgId(null);
      setIdentityEpoch(identityEpochRef.current);
    },
    [queryClient],
  );

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

  // Store callback props in refs so changing callback identity never retriggers
  // the membership effect.
  const onAccessTokenChangeRef = useRef(onAccessTokenChange);
  const onOrganizationChangeRef = useRef(onOrganizationChange);

  useEffect(() => {
    onAccessTokenChangeRef.current = onAccessTokenChange;
    onOrganizationChangeRef.current = onOrganizationChange;
  });

  useEffect(() => {
    if (!authRequired || !authClient) return;

    let active = true;
    // Once any auth-state callback has fired, its session is authoritative. A
    // slower initial getSession() resolving afterwards could otherwise transition
    // back to a stale user (e.g. B signs in while A's getSession is pending),
    // so the late initial result is ignored.
    let authEventSeen = false;
    void authClient.getSession().then((result) => {
      if (!active || authEventSeen) return;
      transitionUser(result.session?.user?.id ?? null);
      setSession(result.session);
      setLoadingSession(false);
    });

    const subscription = authClient.onAuthStateChange((nextSession) => {
      if (!active) return;
      authEventSeen = true;
      transitionUser(nextSession?.user?.id ?? null);
      setSession(nextSession);
      setLoadingSession(false);
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, [authClient, authRequired, transitionUser]);

  useEffect(() => {
    onAccessTokenChangeRef.current?.(accessToken);
  }, [accessToken]);

  const loadMemberships = useCallback(
    async (token: string) => {
      // Capture the identity epoch at request start. A user transition bumps the
      // epoch, so a previous user's in-flight request is discarded on resolution
      // even when the new user reuses the SAME access token (the token guard
      // alone can't tell same-token users apart).
      const requestEpoch = identityEpochRef.current;
      const isStale = () =>
        latestTokenRef.current !== token ||
        identityEpochRef.current !== requestEpoch;
      setMembership({ status: "loading" });
      try {
        const organizations = await fetchMemberships(token);
        // Discard results superseded by a later sign-in / user transition.
        if (isStale()) return;
        setMembership({ status: "resolved", organizations });
      } catch {
        if (isStale()) return;
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
    // identityEpoch is a dependency so a user transition ALWAYS reloads
    // memberships for the new session — even when user B reuses user A's exact
    // access token (the token string is unchanged, so this effect would not
    // otherwise re-run). transitionUser bumps identityEpoch and nulls
    // latestTokenRef; re-running here re-records the current token and starts
    // B's load, while loadMemberships's guard still discards A's stale result.
  }, [accessToken, authRequired, loadMemberships, identityEpoch]);

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
    // Update the live snapshot's orgId synchronously so a guarded write for the
    // previous org captured before this switch is dropped even though the epoch
    // is unchanged and React has not yet committed the activeOrgId state change.
    identityRef.current = { ...identityRef.current, orgId: id };
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
    // or never fires. A failed sign-out (handled above) never reaches here, so
    // it leaves the cache and identity intact.
    transitionUser(null);
    setSession(null);
    onOrganizationChangeRef.current?.(null);
  }, [authClient, transitionUser]);

  // Refresh the shared identity snapshot on every render so late async callbacks
  // (via QueryIdentityProvider) read the current user/org/epoch. Auth-off mode
  // uses the fixed demo sentinel; authenticated renders carry the real identity.
  if (!authRequired) {
    identityRef.current = { userId: DEMO_USER_ID, orgId: DEMO_ORG_ID, epoch: 0 };
  } else if (session?.user?.id) {
    if (membership.status === "resolved") {
      // Memberships for THIS session have resolved: a coherent identity (real
      // user + its own active org) exists, so clear the transitioning marker.
      identityRef.current = {
        userId: session.user.id,
        orgId: activeOrganization?.id ?? DEMO_ORG_ID,
        epoch: identityEpoch,
      };
    } else {
      // Session is known but memberships have not resolved yet (initial load or
      // mid user-transition). The active org can't be trusted — it may still be
      // derived from a previous user — so keep the snapshot uncapturable.
      identityRef.current = {
        userId: session.user.id,
        orgId: DEMO_ORG_ID,
        epoch: identityEpoch,
        transitioning: true,
      };
    }
  } else {
    identityRef.current = {
      userId: DEMO_USER_ID,
      orgId: DEMO_ORG_ID,
      epoch: identityEpoch,
    };
  }

  const content = (() => {
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
        userId: session.user?.id ?? DEMO_USER_ID,
        identityEpoch,
        email: session.user?.email ?? "Authenticated user",
        onSignOut: handleSignOut,
        signOutError,
        organizations,
        activeOrganizationId: activeOrganization?.id ?? null,
        setActiveOrganization,
        openAddAccount,
        accessToken,
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
  })();

  return (
    <QueryClientProvider client={queryClient}>
      <QueryIdentityProvider identityRef={identityRef}>
        {content}
      </QueryIdentityProvider>
    </QueryClientProvider>
  );
}
