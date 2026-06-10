"use client";

import { useEffect, useState } from "react";
import LoginForm from "../auth/login-form";
import { getAuthMode } from "../../lib/auth-mode";
import createBrowserAuthClient, {
  type AuthSession,
  type BrowserAuthClient,
} from "../../lib/supabase-client";

export type SelectedOrganization = {
  id: string;
  name: string;
};

type OrganizationIdGenerator = () => string;

type OrgShellProps = {
  authClient?: BrowserAuthClient | null;
  authRequired?: boolean;
  children: React.ReactNode;
  organizationIdGenerator?: OrganizationIdGenerator;
  onAccessTokenChange?: (accessToken: string | null) => void;
  onOrganizationChange?: (organization: SelectedOrganization | null) => void;
};

function createLocalOrganizationId(): string {
  const randomUUID = globalThis.crypto?.randomUUID?.bind(globalThis.crypto);
  if (randomUUID) return randomUUID();

  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

export default function OrgShell({
  authClient: providedAuthClient,
  authRequired = getAuthMode().authRequired,
  children,
  organizationIdGenerator = createLocalOrganizationId,
  onAccessTokenChange,
  onOrganizationChange,
}: OrgShellProps) {
  const [authClient] = useState(() =>
    providedAuthClient === undefined && authRequired
      ? createBrowserAuthClient()
      : (providedAuthClient ?? null),
  );
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(authRequired && Boolean(authClient));
  const [organizationName, setOrganizationName] = useState("");
  const [selectedOrganization, setSelectedOrganization] =
    useState<SelectedOrganization | null>(null);
  const accessToken = session?.accessToken ?? null;

  useEffect(() => {
    if (!authRequired || !authClient) return;

    let active = true;
    void authClient.getSession().then((result) => {
      if (!active) return;
      setSession(result.session);
      setLoading(false);
    });

    const subscription = authClient.onAuthStateChange((nextSession) => {
      if (!active) return;
      setSession(nextSession);
      setLoading(false);
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, [authClient, authRequired]);

  useEffect(() => {
    onAccessTokenChange?.(accessToken);
  }, [accessToken, onAccessTokenChange]);

  function submitOrganization(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = organizationName.trim();
    if (!trimmedName) return;

    const organization = { id: organizationIdGenerator(), name: trimmedName };
    setSelectedOrganization(organization);
    onOrganizationChange?.(organization);
  }

  if (!authRequired) {
    return (
      <div className="min-h-screen bg-slate-50">
        <div className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-900">
          Demo mode
        </div>
        {children}
      </div>
    );
  }

  if (!authClient) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <section className="mx-auto max-w-md rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold text-slate-950">
            Authentication is not configured
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            Set the public Supabase URL and anon key to enable login.
          </p>
        </section>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <p className="text-sm font-medium text-slate-600">
          Loading authentication
        </p>
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

  return (
    <div className="min-h-screen bg-slate-50">
      <section className="border-b border-slate-200 bg-white px-6 py-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Signed in
            </p>
            <p className="text-sm font-semibold text-slate-950">
              {session.user?.email ?? "Authenticated user"}
            </p>
          </div>
          <form
            className="flex flex-col gap-2 sm:flex-row sm:items-end"
            onSubmit={submitOrganization}
          >
            <label
              className="flex flex-col gap-1 text-sm font-medium text-slate-700"
              htmlFor="organization-name"
            >
              Organization name
              <input
                id="organization-name"
                className="h-10 min-w-72 rounded-md border border-slate-300 px-3 text-sm text-slate-950 shadow-sm"
                name="organizationName"
                onChange={(event) => setOrganizationName(event.target.value)}
                required
                type="text"
                value={organizationName}
              />
            </label>
            <button
              className="h-10 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800"
              type="submit"
            >
              Create organization
            </button>
          </form>
        </div>
        {selectedOrganization ? (
          <div className="mt-4 text-sm text-slate-700">
            <p className="font-medium text-slate-500">Selected organization</p>
            <p className="font-semibold text-slate-950">
              {selectedOrganization.name}
            </p>
          </div>
        ) : null}
      </section>
      {children}
    </div>
  );
}
