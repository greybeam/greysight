# Query Cache & Connection Pooling Architecture

Greysight keeps a single session-scoped read cache in the browser and a small set
of process-wide, credential-neutral HTTP client pools in the backend. Both are
built to make identity/credential leakage structurally hard rather than merely
unlikely. This document describes the invariants; keep it in sync when the cache
or the pool changes.

## Frontend session cache

### One shell-owned QueryClient

`OrgShell` (`apps/web/src/components/org/org-shell.tsx`) constructs exactly one
TanStack `QueryClient` for the whole authenticated tree and provides it via
`QueryClientProvider`. Its defaults are `staleTime` 60s, `gcTime` 30min,
`retry: false`, `refetchOnWindowFocus: false`. The cache is **memory-only** —
there is no persister. This is deliberate: the cache lives and dies with the tab,
and nothing sensitive is ever written to `localStorage`, `sessionStorage`, or
IndexedDB. The client is owned by the shell (not any child) so the cache is scoped
to the shell's identity lifecycle, not to a component's mount.

### Identity contract

The cache is scoped to a point-in-time identity snapshot
(`apps/web/src/lib/query-identity.tsx`):

```
QueryIdentitySnapshot { userId, orgId, epoch, transitioning? }
```

`OrgShell` maintains the authoritative snapshot in a live `identityRef` and
refreshes it on every render, then passes the ref into `QueryIdentityProvider`.
`useQueryIdentity()` exposes `capture()` (read the current snapshot) and
`isCurrent(captured)` (is that snapshot still the live identity?), backed by
`sameQueryIdentity`.

**The core rule: every imperative cache write must capture identity at the start
of the operation and re-check `isCurrent` immediately before writing.** A fetch
that resolves after an identity change must not repopulate the cache. See
`cost-dashboard.tsx` and `warehouse-table.tsx` for the pattern
(`const captured = ...capture()` at the top, `if (!...isCurrent(captured)) return`
right before `setQueryData`).

`guardedSetQueryData()` packages the capture/check/write sequence for the simple
case.

### The `transitioning` flag

During a user transition the live snapshot is marked `transitioning: true`, and
`sameQueryIdentity` returns `false` whenever either side is transitioning. The
net effect: **no snapshot captured during a transition can pass the guard** until
the new user's memberships resolve and `OrgShell`'s render-time refresh clears the
marker.

`transitionUser` (in `org-shell.tsx`) runs synchronously on every user change
(including `null <-> user`): it cancels in-flight queries, `queryClient.clear()`s
the cache, nulls `latestTokenRef`, bumps `identityEpoch`, and replaces the whole
snapshot with a `transitioning` one. This closes four concrete race windows —
each a real bug caught in review, not a hypothetical:

1. **Pre-render capture pairing a stale userId with the new epoch.** A write
   captured in the window after the epoch bumped but before React commits the new
   identity would otherwise look "current". The transitioning marker makes any
   such capture uncapturable.
2. **New user paired with the previous user's org.** Between the userId changing
   and the new user's memberships loading, `activeOrganization` can still be
   derived from the previous user's org list. Marking the snapshot transitioning
   (and resetting membership/active-org state) prevents a write from landing under
   `[newUser, oldOrg]`.
3. **Same-token user switches.** Two users can share the exact same access-token
   string, so the token can't distinguish them. **`epoch` — not the token — is
   the source of truth.** Membership results in `loadMemberships` are guarded by
   the epoch captured at request start (`requestEpoch`), so user A's in-flight
   membership response is discarded when user B reuses A's token; the
   `identityEpoch` effect dependency forces B's own membership reload even though
   the token string never changed.
4. **In-flight TanStack `queryFn`s repopulating a cleared cache.** Fetches already
   running when identity changes throw `IdentityChangedError`
   (`cost-dashboard.tsx`), so their resolved data can never be written back. That
   error is classified benign (`isIdentityChangedError`) and is never painted as a
   user-visible failure.

### Key registry

All query keys come **only** from the constructors in
`apps/web/src/lib/query-keys.ts`. Keys are readonly tuples prefixed
`[userId, orgId]` (via `orgScope`), so an org or user switch automatically selects
a different cache entry — no manual request-sequence guard needed for the read
path. Additional invariants:

- **Range normalization.** `normalizedRange` collapses semantically identical
  ranges (e.g. `{}` and `{ windowDays: 30 }`) to byte-identical keys so the cache
  doesn't split into redundant entries.
- **No credential objects in keys, by type.** `QueryKeyParamValue` restricts param
  values to primitives (`string | number | boolean | null | undefined`), so a
  credential *object* structurally cannot be smuggled into a key. String params
  (`userId`, `orgId`, `runId`, `cursor`, and any token strings) are still valid
  `QueryKeyParamValue`s, so the type does **not** prevent a secret *string* from
  entering a key — keeping raw secrets out of key strings is a convention (the
  registry accepts any string), upheld by code review rather than the type system.
- **Demo sentinels.** Unauthenticated/demo contexts use the fixed `demo-user` /
  `demo-org` sentinels (`DEMO_USER_ID` / `DEMO_ORG_ID`), giving demo data a stable,
  non-colliding scope that never mixes with a real user's org data.
- **Memberships stay imperative.** A `memberships` key exists in the registry, but
  memberships are deliberately **not** fetched through the cache — `OrgShell` owns
  that lifecycle (the transition/epoch machinery above depends on it).

### Mutation convention

- **Invalidate whole prefixes only.** Invalidate full user/org-scoped keys (e.g.
  `queryKeys.autoSavings.warehouses(userId, orgId)`), never a suffix-only fragment.
- **Busy vs. data.** Per-operation busy/pending UI state is cleared via
  per-operation tokens (`operationRef`) regardless of identity staleness — busy is
  local UI state, and a switch back must not leave a control permanently disabled.
  Data and error *paints*, by contrast, are identity-guarded (`isCurrent`).

### The latest-ref pattern

`apps/web/src/lib/use-latest-ref.ts` assigns `ref.current = value` during render
so async callbacks read the freshest value at call time without re-subscribing or
re-keying. This render-time assignment is intentional and load-bearing (it is how
`useOrgScopedFetch` reads a rotated access token without invalidating the cache
entry). The `react-hooks/refs` suppression lives **inside the hook**, once, rather
than at every call site.

### Deliberate trade-offs

- **Warehouse enrollment writes AND invalidates.** First enrollment does an
  authoritative `setQueryData` from a fresh `fetchWarehouses`, then invalidates
  both `warehouses` and `status` (`warehouse-table.tsx`). The extra idempotent GET
  buys convergence: if a concurrent mutation landed, the refetch reconciles it.
- **`removeOrganizationQueries` is reserved.** It is exported and tested but
  currently unused — wired for the future browser org-disconnect action so a
  single disconnect can scope-remove one org's entries without wiping the cache.

### Testing conventions

- Use `QueryTestProvider` (`apps/web/src/lib/query-test-utils.tsx`), passing an
  `identity` to wrap the tree in an authenticated `AccountChromeProvider`.
- Control async with **deferred promises, never timers**.
- Assert observable behavior — fetch counts and rendered UI — not TanStack
  internals.

## Backend connection pooling

### Three lifespan-owned clients

`apps/api/app/services/http_pool.py` holds three process-wide httpx clients: an
auth `AsyncClient`, a general `AsyncClient`, and a sync `Client`. The FastAPI
lifespan (`apps/api/app/main.py`, `_lifespan`) constructs them at startup and
calls `install_clients`; on shutdown it clears the holder **before** closing the
clients (`clear_clients()` then `aclose()`/`close()`), so no getter can hand out a
client that is mid-close.

- Getters (`get_auth_client`, `get_async_client`, `get_sync_client`) raise
  `RuntimeError` if called outside the installed window.
- `install_clients` rejects a double-install (a second install with different
  clients raises `RuntimeError`; re-installing the identical set is a no-op).

### Credential neutrality

The pooled clients are constructed with **no default headers, auth, cookies, or
params**. Credentials (`apikey`, bearer token, service-role key) are passed
**per request only** — see `SupabaseAuthServerVerifier` (`auth.py`) and
`SupabaseServiceRoleMembershipLookup` (`membership_directory.py`). Services must
never mutate, close, or `async with` the pooled client; `send_pooled_request`
(`pooled_requests.py`) is the single entry point for sync services.

Each pooled client also has cookie persistence disabled at construction
(`disable_cookie_persistence` in `http_pool.py` installs a no-op cookie jar), so
an upstream `Set-Cookie` is never stored and replayed as a `Cookie:` header on a
later request from a different credential — the shared clients stay strictly
credential-neutral.

### Test seam

Every service constructor accepts a `transport=` override. When supplied, the
service builds a short-lived client bound to that transport and bypasses the pool
entirely — so tests never touch (or need to install) the shared clients.

### Error semantics

- `httpx.TransportError` (including `PoolTimeout`) from the auth verifier → HTTP
  503 "Authentication service unavailable"; from the membership lookup →
  `MembershipLookupUnavailable` → 503.
- **Upstream 429/5xx responses** are treated the same way: an HTTP 429 or any 5xx
  status from the Supabase auth verifier or the membership lookup is an
  infrastructure failure (rate limiting, upstream outage), so it surfaces as 503,
  not 401.
- Invalid tokens, malformed payloads, bad claims, and other client-side rejections
  (e.g. upstream 400/401/403/404) stay **401** — infrastructure failure is never
  masked as an auth error, and vice versa.
- **Timeout policy:** each request uses a per-request timeout with pool
  acquisition capped at `POOL_TIMEOUT_SECONDS` (1s), via `request_timeout`.

### Explicitly deferred

Caching the authenticated round-trips is **out of scope** for this work and left
to a separate, security-sensitive issue. Today every authenticated request still
makes one Supabase verifier call plus one live membership lookup. Membership is
read live on every request by design (see `docs/security-model.md`), so any cache
here needs its own invalidation/revocation story.
