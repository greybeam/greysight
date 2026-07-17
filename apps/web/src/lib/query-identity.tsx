"use client";

import {
  createContext,
  useContext,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import type { QueryClient, QueryKey } from "@tanstack/react-query";

import { useAccountChrome, type AccountChrome } from "./account-context";

// Fixed sentinels for unauthenticated/demo contexts where no signed-in identity
// exists. They give the cache a stable, non-colliding scope so demo data never
// mixes with a real user's org data.
export const DEMO_USER_ID = "demo-user";
export const DEMO_ORG_ID = "demo-org";

// A point-in-time snapshot of who the cache is scoped to. `epoch` bumps whenever
// identity is invalidated (e.g. re-auth), so a stale write captured before a
// switch can be detected and dropped. No access tokens live here — keys and
// guards must never carry secrets.
export type QueryIdentitySnapshot = {
  userId: string;
  orgId: string;
  epoch: number;
};

export function sameQueryIdentity(
  a: QueryIdentitySnapshot,
  b: QueryIdentitySnapshot,
): boolean {
  return a.userId === b.userId && a.orgId === b.orgId && a.epoch === b.epoch;
}

export function guardedSetQueryData<T>(
  queryClient: QueryClient,
  identityRef: React.MutableRefObject<QueryIdentitySnapshot>,
  captured: QueryIdentitySnapshot,
  queryKey: QueryKey,
  value: T,
): boolean {
  if (!sameQueryIdentity(captured, identityRef.current)) return false;
  queryClient.setQueryData(queryKey, value);
  return true;
}

type QueryIdentityContextValue = {
  // Mutable ref so late async callbacks read the *current* identity at call time
  // rather than a value captured at render.
  current: React.MutableRefObject<QueryIdentitySnapshot>;
};

const QueryIdentityContext = createContext<QueryIdentityContextValue | null>(
  null,
);

// AccountChrome does not yet expose userId/identityEpoch (added in a later task).
// Read them defensively/optionally so this module compiles and behaves sanely
// today, falling back to the signed-in email and org selection when present.
type ChromeIdentityFields = {
  userId?: string | null;
  identityEpoch?: number | null;
};

function snapshotFromChrome(
  chrome: AccountChrome | null,
): QueryIdentitySnapshot {
  if (!chrome) {
    return { userId: DEMO_USER_ID, orgId: DEMO_ORG_ID, epoch: 0 };
  }
  const identity = chrome as AccountChrome & ChromeIdentityFields;
  const userId = identity.userId ?? chrome.email ?? DEMO_USER_ID;
  const orgId = chrome.activeOrganizationId ?? DEMO_ORG_ID;
  const epoch = identity.identityEpoch ?? 0;
  return { userId, orgId, epoch };
}

export type QueryIdentityValue = {
  snapshot: QueryIdentitySnapshot;
  capture: () => QueryIdentitySnapshot;
  isCurrent: (captured: QueryIdentitySnapshot) => boolean;
};

export function QueryIdentityProvider({ children }: { children: ReactNode }) {
  const chrome = useAccountChrome();
  const snapshot = snapshotFromChrome(chrome);
  const current = useRef<QueryIdentitySnapshot>(snapshot);
  current.current = snapshot;

  const value = useMemo<QueryIdentityContextValue>(() => ({ current }), []);

  return (
    <QueryIdentityContext.Provider value={value}>
      {children}
    </QueryIdentityContext.Provider>
  );
}

export function useQueryIdentity(): QueryIdentityValue {
  const ctx = useContext(QueryIdentityContext);
  // Fall back to the AccountChrome-derived snapshot when no provider wraps the
  // tree (e.g. isolated tests). The local ref keeps capture()/isCurrent() stable.
  const chrome = useAccountChrome();
  const fallbackRef = useRef<QueryIdentitySnapshot>(snapshotFromChrome(chrome));
  if (!ctx) {
    fallbackRef.current = snapshotFromChrome(chrome);
  }
  const ref = ctx?.current ?? fallbackRef;

  return {
    snapshot: ref.current,
    capture: () => ref.current,
    isCurrent: (captured: QueryIdentitySnapshot) =>
      sameQueryIdentity(captured, ref.current),
  };
}
