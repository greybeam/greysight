"use client";

import { useQuery, type QueryKey } from "@tanstack/react-query";

import { useLatestRef } from "./use-latest-ref";

export type LoadState = "loading" | "ready" | "error";

export type OrgScopedFetchResult<T> = {
  data: T | null;
  loadState: LoadState;
  retry: () => void;
};

// A thin adapter over TanStack Query that preserves the `{ data, loadState,
// retry }` contract every org-scoped panel already consumes, so callers keep
// their loading/error/retry markup unchanged while their reads move into the
// shared session cache.
//
// Callers supply the canonical query key (built from `queryKeys`), which scopes
// the entry to the current user + org. Because the key already carries identity,
// an org switch swaps to a different cache entry automatically — no manual
// request-sequence guard is needed.
//
// `accessToken` and `fetchFn` are read from refs refreshed on every render
// rather than living in the query key, so a Supabase access-token rotation while
// a panel is mounted does not invalidate the cache entry or restart the fetch.
export function useOrgScopedFetch<T>(
  queryKey: QueryKey,
  orgId: string,
  accessToken: string | null,
  fetchFn: (orgId: string, accessToken: string | null) => Promise<T>,
): OrgScopedFetchResult<T> {
  const accessTokenRef = useLatestRef(accessToken);
  const fetchFnRef = useLatestRef(fetchFn);

  const query = useQuery({
    queryKey,
    queryFn: () => fetchFnRef.current(orgId, accessTokenRef.current),
  });

  return {
    data: query.data ?? null,
    loadState: query.isPending
      ? "loading"
      : query.isError
        ? "error"
        : "ready",
    retry: () => {
      void query.refetch();
    },
  };
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
