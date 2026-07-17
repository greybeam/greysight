"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type LoadState = "loading" | "ready" | "error";

export type OrgScopedFetchResult<T> = {
  data: T | null;
  loadState: LoadState;
  retry: () => void;
};

// Owns the single-fetch lifecycle shared by every org-scoped panel: fetch on
// mount and whenever `orgId` changes, drop out-of-order responses with a
// request-sequence guard, and expose a stable `retry` for the error state's
// Retry button.
//
// That guard is not dead weight even though callers key their panel on
// `orgId` (remounting it on an org switch, which resets this hook's state
// for free): a user can still click Retry more than once before the first
// attempt resolves, starting a second fetch while the first is in flight.
// Without the guard, the first request could resolve after the second and
// overwrite its (newer, correct) result with stale data.
//
// `accessToken` and `fetchFn` are read from refs updated on every render
// rather than being fetch dependencies, so a Supabase access-token rotation
// while a panel is mounted does not re-run the fetch and reset the loaded
// state.
export function useOrgScopedFetch<T>(
  orgId: string,
  accessToken: string | null,
  fetchFn: (orgId: string, accessToken: string | null) => Promise<T>,
): OrgScopedFetchResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const requestSequenceRef = useRef(0);
  const accessTokenRef = useRef(accessToken);
  const fetchFnRef = useRef(fetchFn);
  useEffect(() => {
    accessTokenRef.current = accessToken;
    fetchFnRef.current = fetchFn;
  });

  const load = useCallback(() => {
    const requestSequence = ++requestSequenceRef.current;
    setLoadState("loading");
    setData(null);
    fetchFnRef
      .current(orgId, accessTokenRef.current)
      .then((result) => {
        if (requestSequenceRef.current !== requestSequence) return;
        setData(result);
        setLoadState("ready");
      })
      .catch(() => {
        if (requestSequenceRef.current !== requestSequence) return;
        setLoadState("error");
      });
  }, [orgId]);

  useEffect(() => {
    // Reset to the loading state before the async fetch resolves so a stale
    // prior org's content is never shown while the new org's data loads.
    // This is derived-state synchronization with the fetch, not a cascading
    // render loop.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  return { data, loadState, retry: load };
}

export type LoadStatePanelProps = {
  loadState: LoadState;
  loadingMessage: string;
  errorMessage: string;
  onRetry: () => void;
  retryButtonClassName?: string;
  children: React.ReactNode;
};

const DEFAULT_RETRY_BUTTON_CLASS =
  "mt-3 rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-slate-300 hover:bg-white/5";

// The loading/error/retry markup shared by every org-scoped panel. Callers
// own the "ready" branch (including empty states), since those differ
// per-panel; this only covers the two states that are otherwise byte-for-byte
// duplicated across panels.
export function LoadStatePanel({
  loadState,
  loadingMessage,
  errorMessage,
  onRetry,
  retryButtonClassName = DEFAULT_RETRY_BUTTON_CLASS,
  children,
}: LoadStatePanelProps) {
  if (loadState === "loading") {
    return (
      <p className="mt-4 text-sm text-slate-400" role="status">
        {loadingMessage}
      </p>
    );
  }

  if (loadState === "error") {
    return (
      <div className="mt-4">
        <p className="text-sm font-medium text-red-400" role="alert">
          {errorMessage}
        </p>
        <button
          type="button"
          className={retryButtonClassName}
          onClick={onRetry}
        >
          Retry
        </button>
      </div>
    );
  }

  return <>{children}</>;
}
