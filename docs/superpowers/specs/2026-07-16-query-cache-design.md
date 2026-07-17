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
- `OrgShell` has ~7 early-return branches (demo at `org-shell.tsx:223`,
  unresolved/missing client, loading, login, membership states). The
  provider must wrap **every** branch that renders `children` — including
  the demo branch, which returns a separate tree and would otherwise have
  no provider. Simplest shape: one outer wrapper around the whole render.
- Add `@tanstack/react-query` to `apps/web/package.json` (and lockfile);
  no data-fetching library exists today.
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

- `OrgShell` tracks the current `userId` (`session.user.id`) and an
  **identity epoch** (integer). Whenever the observed `userId` changes — via
  `handleSignOut`, the `authClient.onAuthStateChange` subscription
  (`org-shell.tsx:115`; covers cross-tab sign-out, token expiry, account
  switch), or initial session resolution — call
  `queryClient.cancelQueries()` then `queryClient.clear()`, and increment
  the epoch. `clear()` alone is insufficient: in-flight query functions and
  the imperative polling loops resolve after clearing and would repopulate
  the cache with the previous identity's data.
- Every imperative callback that writes to the cache (terminal poll results
  via `setQueryData`, post-mutation invalidations) captures
  `{userId, orgId, epoch}` when it starts and re-checks the **full tuple**
  immediately before writing; on any mismatch it drops the result. Epoch
  alone is insufficient: org switches deliberately do not clear or bump the
  epoch, so the `orgId` comparison is what drops a late write after an org
  switch.
- **Prerequisite:** `AuthSession` currently omits the user id — the
  Supabase adapter discards it (`supabase-client.ts:68`). Add `id` to
  `AuthSession`, preserve it in `toAuthSession`, and expose it through
  `AccountChrome` before building keys or the epoch.
- **Demo / auth-off identity:** `AccountChromeProvider` never mounts in the
  demo branch and `CostDashboardRuntime` has no `userId` field. Key
  builders take identity from a small `useQueryIdentity()` helper that
  reads `AccountChrome` when present and falls back to the fixed demo
  sentinel when it is null.
- Org **disconnect** removes that org's entries via
  `queryClient.removeQueries({ queryKey: scope(userId, orgId) })`.
- Org **switch** requires no clearing: keys are prefixed by `orgId`, so the
  other org's data is simply never matched. Its entries age out via `gcTime`.
- `account-context.tsx` gains a `userId` field so key builders can consume it.

### Query key registry

New module `apps/web/src/lib/query-keys.ts` — the only place keys are built,
so shapes cannot drift. All org-scoped keys start with `[userId, orgId]`
(memberships is user-scoped). Keys contain only identifiers and parameters —
never tokens.

Because TanStack Query prefix-matching starts at element zero, suffix keys
like `["auto-savings"]` match nothing. The registry therefore also exports
**scope helpers** — `scope(userId, orgId)`, `dashboardScope(...)`,
`autoSavingsScope(...)` — and all invalidation/removal calls must go through
them; the mutation table below uses this notation as shorthand for the fully
prefixed keys.

| Key | Fetcher | Notes |
| --- | --- | --- |
| `[u, o, "dashboard", "cached-run"]` | `fetchCachedDashboardRun` | run discovery (issue #41 endpoint) |
| `[u, o, "dashboard", "view", runId, range]` | `fetchDashboardView` | replaces `cacheRef`; both requested- and resolved-range keys written, as today |
| `[u, o, "dashboard", "cache-settings"]` | `fetchCacheSettings` | |
| `[u, o, "auto-savings", "status"]` | `fetchStatus` | currently fetched by `automated-savings-shell.tsx` |
| `[u, o, "auto-savings", "warehouses"]` | `fetchWarehouses` | currently fetched by `automated-savings-shell.tsx` |
| `[u, o, "auto-savings", "access"]` | `checkAccess` | |
| `[u, o, "auto-savings", "suspension-stats", params]` | `fetchSuspensionStats` | window params in key |
| `[u, o, "auto-savings", "suspension-events", cursor]` | `fetchSuspensionEvents` | one entry per page; the API accepts only a cursor today — add pageSize/filter params to the key if/when the API grows them |
| `[u, o, "dashboard", "source", runId, sourceId, range]` | `pollDashboardSource` terminal result | `AIDetailViewModel`, distinct from `DashboardView` — never written to a view key |
| `[u, "memberships"]` | `fetchSessionMemberships` | user-scoped (no org prefix); optional, low-risk follow-up |

Demo mode (`authRequired=false`) uses a fixed sentinel user/org segment so
demo data still caches without a session.

### Migration scope (Codex finding 1)

All fetch-on-mount paths on both pages move to `useQuery`, not just
`useOrgScopedFetch` consumers:

1. **`useOrgScopedFetch`** becomes a thin wrapper over `useQuery`,
   preserving its actual return contract `{ data, loadState, retry }`
   (`use-org-scoped-fetch.tsx:7-11`): map `isPending` → `"loading"`,
   resolved → `"ready"`, error → `"error"`. Consumers (e.g.
   `suspensions-chart.tsx`) migrate without churn; `LoadStatePanel` and
   per-panel empty states are unchanged. **Deliberate behavior change:**
   the current hook resets to `data=null` + `"loading"` on org change; the
   migrated hook renders cached data with no loading state during
   background revalidation. Set `retry: false` in the client defaults —
   today's fetches make one attempt and expose a manual Retry, and that
   semantic is kept.
2. **`automated-savings-shell.tsx`** — its private fetch-on-mount lifecycle
   for status/warehouses moves to `useQuery` with the keys above.
3. **`suspension-events-table.tsx`** — per-cursor page fetches become
   `useQuery` keyed by cursor so previously visited pages render instantly.
4. **`cost-dashboard.tsx`** — `cacheRef`, `cacheView`, and
   `prefetchRelativeWindows` are replaced by the query cache and
   `queryClient.prefetchQuery` for the non-default windows. The dual-key
   behavior (`cost-dashboard.tsx:232-236`) does not fall out of `useQuery`
   automatically: after a view resolves, explicitly alias it with
   `queryClient.setQueryData(viewKey(runId, resolvedRange), view)` so
   switching to an already-resolved window stays instant.
5. **`cache-settings.tsx`** — owns its own fetch lifecycle today; migrate
   it to `useQuery` on the cache-settings key.
6. **Auto Savings semantics preserved:** `checkAccess` results continue to
   merge grant fields into the rendered status
   (`automated-savings-shell.tsx:127`) — status and access remain separate
   query keys, merged at render. The post-enrollment authoritative
   warehouse read (`warehouse-table.tsx:204`) becomes a
   `setQueryData` write to the warehouses key, guarded by the captured
   identity tuple like any imperative write.

### Dashboard run/polling model (Codex finding 3)

Runs are created dynamically, so plain invalidation cannot target a
not-yet-known key. The model splits into:

- **Discovery:** `["dashboard", "cached-run"]` answers "which run should this
  org render?".
- **Views:** `["dashboard", "view", runId, range]` hold prepared views.
- **Polling stays outside React Query.** `pollDashboardRun` /
  `pollDashboardSource` keep their imperative loops. On terminal success the
  caller writes the result into the cache and invalidates the discovery key —
  populating the new run's entries directly instead of hoping an
  invalidation refetches an unknown key. Payload types differ:
  `pollDashboardRun` yields a `DashboardView` →
  `setQueryData(viewKey(runId, range), view)`; `pollDashboardSource` yields
  an `AIDetailViewModel` (`dashboard-api.ts:187`) →
  `setQueryData(sourceKey(runId, sourceId, range), model)` — never a view
  key.
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

- `_lifespan` in `apps/api/app/main.py` creates the pooled clients and
  closes them on shutdown:
  - One small dedicated `httpx.AsyncClient` for the **auth verifier**
    (`auth.py` token verification runs on every authenticated request and
    must not queue behind slow admin operations).
  - One shared `httpx.AsyncClient` and one shared `httpx.Client` for the
    remaining services.
  - All clients get explicit `httpx.Limits` (e.g. `max_connections=20`,
    `max_keepalive_connections=10`) **and** an explicit `httpx.Timeout`
    including `pool=` so pool exhaustion fails fast instead of blocking.
- **Credential-neutral pool invariant:** pooled clients are constructed with
  no default `headers`, `auth`, `cookies`, or `params`, and shared-client
  configuration is never mutated after construction. Every call site passes
  credentials (`apikey`, `authorization`) per request via `headers=`, as
  today. A client-level or mutated auth header on a shared client would
  bleed credentials across concurrent tenants.
- **Pool exhaustion maps to 503, not 401 — on both auth paths:**
  `auth.py:55` converts any `httpx.HTTPError` into an authentication
  failure, and `membership_directory.py:73` has the identical collapse
  (`MembershipLookupError` → 401 via `auth.py`). Membership lookup runs on
  every authenticated request *on the shared pool* — the saturable one. Both
  paths must carry an explicit unavailable/pool-exhaustion error
  (`httpx.PoolTimeout` and kin) through to a 503 so saturation is not
  misreported as invalid credentials.
- **Per-request timeouts must preserve the pool timeout:** passing today's
  scalar `timeout=` per request (`membership_directory.py:47`,
  `dashboard_run_cache.py:154`) replaces the entire timeout configuration,
  silently discarding the client-level `pool=`. Provide a small helper that
  builds a per-request `httpx.Timeout` from the service's `timeout_seconds`
  while keeping the explicit pool timeout; all pooled call sites use it.
- **No per-call context management on pooled clients:** the current
  `with httpx.Client(...)` shape (`dashboard_run_cache.py:154`,
  `automated_savings_store.py:117`) would close the singleton after its
  first request. Only the `transport=` override clients (tests) remain
  short-lived and context-managed.
- **Import-time configuration mismatch (Codex finding 4):** services are
  configured at module import (`main.py:141-149`), before the lifespan runs.
  Resolution: a small `app/services/http_pool.py` holder module exposing
  `get_async_client()` / `get_sync_client()` / `get_auth_client()` (one
  getter per client role — the auth verifier is also configured at import
  time, `main.py:142`, so it too must resolve its dedicated client lazily).
  The lifespan populates the holder on startup and **clears it on
  shutdown**, so a post-shutdown getter raises rather than returning a
  closed client. Services resolve the client lazily per call from the
  holder instead of receiving one at construction. Construction-time params keep the existing
  `transport=` injection: when a `transport` is provided (tests), the service
  builds its own short-lived client exactly as today, bypassing the pool.
  If `get_async_client()` / `get_sync_client()` is called before the
  lifespan has populated the holder (and no `transport` override is in
  play), it raises `RuntimeError` — never silently constructs an unpooled
  fallback client that would mask a startup-ordering bug.
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
- Identity races (deferred promises): an in-flight query or poll resolving
  *after* sign-out, account switch, or org switch does not repopulate the
  cache — the captured `{userId, orgId, epoch}` tuple check drops the late
  result (org switch is caught by the `orgId` comparison, since the epoch
  does not bump on org changes).
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
- Credential isolation: pooled clients carry no default auth headers after
  construction and after requests; concurrent verifier/service-role calls
  each receive only their own per-request headers.
- Pool exhaustion: a saturated pool surfaces as 503 — from both the auth
  verifier and the membership lookup — never 401.
- Sequential reuse: a pooled store performs two consecutive requests on the
  same client without error (guards against a leftover `with` closing it).
- Holder guard: any getter before lifespan population, or after shutdown
  clears the holder, raises `RuntimeError`.

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
