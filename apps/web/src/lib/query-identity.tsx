"use client";

import {
  createContext,
  useContext,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";

import { useAccountChrome, type AccountChrome } from "./account-context";
import { queryKeys } from "./query-keys";

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
  // Set while a user transition is in flight — after the cache is cleared and
  // the epoch bumped, but before a coherent identity for the NEW user (its own
  // memberships / active org) has been established. A transitioning snapshot is
  // deliberately uncapturable: no capture taken in this window can ever satisfy
  // sameQueryIdentity, so a deferred write can't repopulate the just-cleared
  // cache under a half-formed identity (e.g. real user paired with the demo-org
  // sentinel, or the new user paired with the previous user's org).
  transitioning?: boolean;
};

export function sameQueryIdentity(
  a: QueryIdentitySnapshot,
  b: QueryIdentitySnapshot,
): boolean {
  // A transition in progress on either side means identity is not yet coherent,
  // so nothing captured against it should pass the guard.
  if (a.transitioning || b.transitioning) return false;
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
  // Remove every cached entry scoped to the current user + the supplied org,
  // without touching any other org. Wired for the future browser-disconnect
  // action so a single disconnect can never wipe the whole cache.
  removeOrganizationQueries: (orgId: string) => void;
};

const QueryIdentityContext = createContext<QueryIdentityContextValue | null>(
  null,
);

function snapshotFromChrome(
  chrome: AccountChrome | null,
): QueryIdentitySnapshot {
  if (!chrome) {
    return { userId: DEMO_USER_ID, orgId: DEMO_ORG_ID, epoch: 0 };
  }
  const userId = chrome.userId || chrome.email || DEMO_USER_ID;
  const orgId = chrome.activeOrganizationId ?? DEMO_ORG_ID;
  const epoch = chrome.identityEpoch ?? 0;
  return { userId, orgId, epoch };
}

export type QueryIdentityValue = {
  snapshot: QueryIdentitySnapshot;
  // The live identity ref, so callers can pass it to guardedSetQueryData().
  ref: React.MutableRefObject<QueryIdentitySnapshot>;
  capture: () => QueryIdentitySnapshot;
  isCurrent: (captured: QueryIdentitySnapshot) => boolean;
  removeOrganizationQueries: (orgId: string) => void;
};

export function QueryIdentityProvider({
  children,
  identityRef,
}: {
  children: ReactNode;
  // When the owner (OrgShell) maintains the authoritative identity ref, it is
  // passed in so late async callbacks read the same live snapshot. Otherwise the
  // provider derives the snapshot from AccountChrome (isolated tests).
  identityRef?: React.MutableRefObject<QueryIdentitySnapshot>;
}) {
  const chrome = useAccountChrome();
  const queryClient = useQueryClient();
  const fallbackRef = useRef<QueryIdentitySnapshot>(snapshotFromChrome(chrome));
  if (!identityRef) {
    fallbackRef.current = snapshotFromChrome(chrome);
  }
  const current = identityRef ?? fallbackRef;

  const value = useMemo<QueryIdentityContextValue>(
    () => ({
      current,
      removeOrganizationQueries: (orgId: string) => {
        queryClient.removeQueries({
          queryKey: queryKeys.scope(current.current.userId, orgId),
        });
      },
    }),
    [current, queryClient],
  );

  return (
    <QueryIdentityContext.Provider value={value}>
      {children}
    </QueryIdentityContext.Provider>
  );
}

export function useQueryIdentity(): QueryIdentityValue {
  const ctx = useContext(QueryIdentityContext);
  const queryClient = useQueryClient();
  // Fall back to the AccountChrome-derived snapshot when no provider wraps the
  // tree (e.g. isolated tests). The local ref keeps capture()/isCurrent() stable.
  const chrome = useAccountChrome();
  const fallbackRef = useRef<QueryIdentitySnapshot>(snapshotFromChrome(chrome));
  if (!ctx) {
    fallbackRef.current = snapshotFromChrome(chrome);
  }
  const ref = ctx?.current ?? fallbackRef;
  const removeOrganizationQueries =
    ctx?.removeOrganizationQueries ??
    ((orgId: string) =>
      queryClient.removeQueries({
        queryKey: queryKeys.scope(ref.current.userId, orgId),
      }));

  return {
    snapshot: ref.current,
    ref,
    capture: () => ref.current,
    isCurrent: (captured: QueryIdentitySnapshot) =>
      sameQueryIdentity(captured, ref.current),
    removeOrganizationQueries,
  };
}
