import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { AccountChromeProvider, type AccountChrome } from "./account-context";

// A QueryClient tuned for deterministic tests: no retries (failures surface
// immediately) and no garbage collection (cached entries persist for the run).
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
}

type TestIdentity = {
  userId?: string;
  identityEpoch?: number;
  activeOrganizationId?: string | null;
};

type QueryTestProviderProps = {
  children: ReactNode;
  client?: QueryClient;
  // When provided, wrap the tree in AccountChromeProvider so components resolve
  // an authenticated identity. userId/identityEpoch are forward-compatible with
  // the fields a later task adds to AccountChrome.
  identity?: TestIdentity;
};

function buildChrome(identity: TestIdentity): AccountChrome {
  const chrome = {
    email: identity.userId ?? "test-user@example.com",
    onSignOut: () => {},
    signOutError: null,
    organizations: [],
    activeOrganizationId: identity.activeOrganizationId ?? "org-1",
    setActiveOrganization: () => {},
    openAddAccount: () => {},
    accessToken: null,
    userId: identity.userId,
    identityEpoch: identity.identityEpoch,
  } as AccountChrome & { userId?: string; identityEpoch?: number };
  return chrome;
}

export function QueryTestProvider({
  children,
  client,
  identity,
}: QueryTestProviderProps) {
  const queryClient = client ?? createTestQueryClient();
  const tree = (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  if (!identity) {
    return tree;
  }
  return (
    <AccountChromeProvider value={buildChrome(identity)}>
      {tree}
    </AccountChromeProvider>
  );
}
