# Design: Session-scoped query cache + backend Supabase connection reuse

**Issue:** [#65](https://github.com/greybeam/greysight/issues/65)
**Date:** 2026-07-16
**Status:** Approved design, pending implementation plan

## Problem

Navigating between Home (cost dashboard) and Auto Savings unmounts each page,
clears charts/tables, and repeats every API/Supabase request on return. The
only client-side cache today is a component-local `Map` in
`apps/web/src/components/dashboard/cost-dashboard.tsx` (`cacheRef`), which is
destroyed on navigation. Auto Savings panels (`useOrgScopedFetch`) have no
cache at all.

On the backend, ~7 service modules each open a brand-new `httpx.Client` /
`AsyncClient` per Supabase REST call — including token verification on every
authenticated request — so no HTTP connections are ever reused.

## Decision summary

- **Frontend:** adopt `@tanstack/react-query` (memory-only, no persister) with
  a single `QueryClient` owned by `OrgShell`. Query keys are namespaced by
  user and organization so isolation falls out of key structure.
- **Backend:** create lifespan-scoped shared `httpx.AsyncClient` and
  `httpx.Client` instances in the FastAPI app and inject them into the
  Supabase-touching services, replacing per-call client construction.

Alternatives rejected:

- **Hand-rolled shared cache** — the issue's requirements (dedup, TTL,
  stale-while-revalidate, invalidation, scoped clearing) are the feature list
  of a query-cache library. The hard 20% (races around sign-out/org-switch,
  concurrent-render-safe subscriptions, garbage collection) is exactly where
  cross-org data-leak bugs live. Not worth owning.
- **Keeping pages mounted** — fights the App Router and does not deduplicate
  requests.
- **SWR** — viable, but weaker mutation/invalidation ergonomics; TanStack
  Query's `invalidateQueries`/`setQueryData`/`clear` map directly onto the
  requirements.
- This design was cross-reviewed by Codex (verdict: approve-with-changes);
  its five findings are folded in below.

## Frontend design

### Provider and lifecycle

- Mount one `QueryClientProvider` inside `OrgShell`
  (`apps/web/src/components/org/org-shell.tsx`), which already owns the
  session, memberships, and active-organization state.
- The `QueryClient` lives in a `useState` initializer (stable across
  renders), created with defaults:
  - `staleTime: 60_000` — fresh data produces zero network on return
    navigation.
  - `gcTime: 30 * 60_000`.
  - `refetchOnWindowFocus: false` (matches current behavior).
  - No persister — memory only. Tokens, service-role keys, and Snowflake
    credentials never enter query data or query keys.

### Identity transitions (Codex finding 2)

Clearing must cover **every** auth identity transition, not just the sign-out
button:

- `OrgShell` tracks the current `userId` (`session.user.id`). Whenever the
  observed `userId` changes — via `handleSignOut`, the
  `authClient.onAuthStateChange` subscription (`org-shell.tsx:115`; covers
  cross-tab sign-out, token expiry, account switch), or initial session
  resolution — call `queryClient.clear()`.
- Org **disconnect** removes that org's entries via
  `queryClient.removeQueries({ queryKey: scope(userId, orgId) })`.
- Org **switch** requires no clearing: keys are prefixed by `orgId`, so the
  other org's data is simply never matched. Its entries age out via `gcTime`.
- `account-context.tsx` gains a `userId` field so key builders can consume it.

### Query key registry

New module `apps/web/src/lib/query-keys.ts` — the only place keys are built,
so shapes cannot drift. All keys start with `[userId, orgId]`. Keys contain
only identifiers and parameters — never tokens.

| Key | Fetcher | Notes |
| --- | --- | --- |
| `[u, o, "dashboard", "cached-run"]` | `fetchCachedDashboardRun` | run discovery (issue #41 endpoint) |
| `[u, o, "dashboard", "view", runId, range]` | `fetchDashboardView` | replaces `cacheRef`; both requested- and resolved-range keys written, as today |
| `[u, o, "dashboard", "cache-settings"]` | `fetchCacheSettings` | |
| `[u, o, "auto-savings", "status"]` | `fetchStatus` | currently fetched by `automated-savings-shell.tsx` |
| `[u, o, "auto-savings", "warehouses"]` | `fetchWarehouses` | currently fetched by `automated-savings-shell.tsx` |
| `[u, o, "auto-savings", "access"]` | `checkAccess` | |
| `[u, o, "auto-savings", "suspension-stats", params]` | `fetchSuspensionStats` | window params in key |
| `[u, o, "auto-savings", "suspension-events", { cursor, pageSize, filters }]` | `fetchSuspensionEvents` | one entry per page; pages cached independently |
| `[u, "memberships"]` | `fetchSessionMemberships` | user-scoped (no org prefix); optional, low-risk follow-up |

Demo mode (`authRequired=false`) uses a fixed sentinel user/org segment so
demo data still caches without a session.

### Migration scope (Codex finding 1)

All fetch-on-mount paths on both pages move to `useQuery`, not just
`useOrgScopedFetch` consumers:

1. **`useOrgScopedFetch`** becomes a thin wrapper over `useQuery` (same
   return contract: `{ state, retry }`), so its consumers (e.g.
   `suspensions-chart.tsx`) migrate without churn. `LoadStatePanel` and
   per-panel empty states are unchanged; loading state renders only when
   there is no cached data (`isPending`), never during background
   revalidation.
2. **`automated-savings-shell.tsx`** — its private fetch-on-mount lifecycle
   for status/warehouses moves to `useQuery` with the keys above.
3. **`suspension-events-table.tsx`** — per-cursor page fetches become
   `useQuery` keyed by cursor/filters so previously visited pages render
   instantly.
4. **`cost-dashboard.tsx`** — `cacheRef`, `cacheView`, and
   `prefetchRelativeWindows` are replaced by the query cache and
   `queryClient.prefetchQuery` for the non-default windows.

### Dashboard run/polling model (Codex finding 3)

Runs are created dynamically, so plain invalidation cannot target a
not-yet-known key. The model splits into:

- **Discovery:** `["dashboard", "cached-run"]` answers "which run should this
  org render?".
- **Views:** `["dashboard", "view", runId, range]` hold prepared views.
- **Polling stays outside React Query.** `pollDashboardRun` /
  `pollDashboardSource` keep their imperative loops. On terminal success the
  caller writes the result into the cache with
  `queryClient.setQueryData(viewKey(runId, range), view)` and invalidates the
  discovery key — populating the new run's entries directly instead of hoping
  an invalidation refetches an unknown key.
- `startDashboardRun` clears `cachedAsOf` (as today) and invalidates the
  discovery key.

### Mutation → invalidation map (Codex findings 1 & 5)

| Mutation | Invalidates / updates |
| --- | --- |
| `startDashboardRun`, `triggerDashboardSource` | dashboard discovery key; terminal poll results land via `setQueryData` |
| `updateCacheSettings` | `["dashboard", "cache-settings"]`, dashboard discovery |
| `agree` (opt-in-gate.tsx) | all `["auto-savings"]` keys |
| `setGlobalSwitch` (automated-savings-shell.tsx) | `["auto-savings", "status"]`, `["auto-savings", "warehouses"]` |
| `toggleWarehouse` (warehouse-table.tsx) | `["auto-savings", "warehouses"]`, `["auto-savings", "status"]` |
| org connect/provision (`onboarding-api.ts`), invitations | `["memberships"]` (if migrated), affected org scope |
| org disconnect | `removeQueries` for that org's scope |

Mutations remain plain async functions (no `useMutation` requirement);
call sites invalidate via `useQueryClient()`.

## Backend design

### Shared httpx clients

- `_lifespan` in `apps/api/app/main.py` creates one `httpx.AsyncClient` and
  one `httpx.Client` with explicit `httpx.Limits` (e.g.
  `max_connections=20`, `max_keepalive_connections=10`) and closes them on
  shutdown.
- **Import-time configuration mismatch (Codex finding 4):** services are
  configured at module import (`main.py:141-149`), before the lifespan runs.
  Resolution: a small `app/services/http_pool.py` holder module exposing
  `get_async_client()` / `get_sync_client()`. The lifespan populates it;
  services resolve the client lazily per call from the holder instead of
  receiving one at construction. Construction-time params keep the existing
  `transport=` injection: when a `transport` is provided (tests), the service
  builds its own short-lived client exactly as today, bypassing the pool.
- Services migrated (all current per-call constructors):
  `auth.py` (token verification — hottest path), `membership_directory.py`,
  `dashboard_run_cache.py`, `automated_savings_store.py`,
  `org_invitations.py`, `org_provisioning.py`,
  `dashboard_cache_settings.py`.
- Timeouts stay per-request (`timeout=` on each call), preserving each
  service's existing `timeout_seconds`.

## Testing (Codex finding 5)

**Web (Vitest)** — behavior-focused, no restating the library:

- Concurrent dedupe: two components mounting the same key trigger one fetch.
- Stale-while-revalidate: cached data renders (no loading state) while a
  background refetch is in flight; data swaps on resolve.
- Identity clearing: an `onAuthStateChange`-driven user change (not just the
  sign-out button) empties the cache.
- Org isolation: switching orgs never renders the previous org's data;
  paginated suspension-event keys are isolated per org and per cursor.
- Mutation invalidation: `agree`, `setGlobalSwitch`, `toggleWarehouse` each
  refetch the mapped keys.
- Key hygiene: a static assertion that no query-key builder accepts or embeds
  an access token.
- Dashboard polling: terminal poll result is readable via the view key
  without an extra network call.

**API (pytest)**

- Stores resolve the shared pooled client (same object identity across
  calls) when no `transport` override is given.
- `transport=` injection still yields hermetic per-test clients.
- Lifespan closes the pooled clients on shutdown.

## Acceptance criteria (from issue #65)

- Returning to Home or Auto Savings renders previously loaded charts/tables
  immediately (fresh → no request; stale → cached render + background
  revalidation).
- Simultaneous consumers share one in-flight request.
- Org switching and sign-out (any identity transition) never display another
  context's data.
- Mutations refresh the mapped cached data.
- Backend Supabase requests reuse pooled HTTP connections.
- No tokens, service-role keys, or decrypted Snowflake credentials in the
  browser cache.

## Out of scope

- Persistence across full browser refreshes (issue explicitly memory-only).
- Migrating write paths to `useMutation`.
- The backend #41 per-org run cache semantics (unchanged; only its transport
  is pooled).
