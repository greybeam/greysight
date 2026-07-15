# Stale Dashboard Cache Recovery

## Problem

The durable `dashboard_run_cache` stores dataset JSON without a cache-contract
version or validation step. A cached `warehouse_spend_daily` row created before
the idle-metrics dataset change can omit `credits_attributed_queries`. The
current prepared-view builder correctly requires that field, so loading the
legacy cache produces a 500 instead of starting a fresh Snowflake run.

Defaulting the missing value to zero would fabricate an idle percentage and
hide an incompatible dataset. The stale snapshot must not reach the view
builder.

## Design

Validate the required base datasets from a cached run against the current
`DashboardRunCreateRequest` dataset contract before loading the snapshot into
the in-memory repository. Ignore deferred dataset keys during this validation;
they have separate contracts and may be appended to a valid cached run.

When the required base datasets are absent or incompatible:

1. Treat the cached run as a cache miss and return HTTP 204.
2. Delete the incompatible cache row on a best-effort basis so later page loads
   do not repeatedly inspect it.
3. Log deletion failures server-side without exposing cached data or turning the
   recovery response into a 500.

The existing frontend already starts a fresh Snowflake dashboard run after a
204 cache response. That fresh run uses the current SQL and overwrites the cache
with rows containing `credits_attributed_queries`.

## Scope

The change belongs in the cached-run API path. It does not relax the prepared
view's required-field invariant, alter Snowflake SQL, change RLS, add a database
migration, or modify the frontend.

## Testing

Add a route regression test that stores an otherwise valid cached run whose
`warehouse_spend_daily` row lacks `credits_attributed_queries`. Assert that
`GET /api/dashboard-runs/cached` returns 204 and removes the stale cache entry.
Existing valid-cache route tests must continue returning a completed cached run.

Run the targeted API test module first, then the complete API test suite and
API lint checks.
