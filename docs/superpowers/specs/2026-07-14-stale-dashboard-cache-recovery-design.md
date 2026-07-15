# Dashboard Attribution Availability Recovery

## Problem

Two valid production paths currently produce the same 500 response:

- The durable `dashboard_run_cache` stores dataset JSON without a
  cache-contract version or validation step. A cached `warehouse_spend_daily`
  row created before the idle-metrics dataset change can omit
  `credits_attributed_queries`.
- Snowflake returns `CREDITS_ATTRIBUTED_COMPUTE_QUERIES` as `NULL` for adaptive
  warehouses. The value can also be unavailable while attribution data lags
  other warehouse metering fields.

The prepared-view builder currently uses `row.get`, so an absent field and a
present-but-null value raise the same "missing required numeric field" error.

Defaulting either case to zero would fabricate a 100% idle result. An absent
field is an incompatible dataset contract, while a present null field means the
idle percentage is unavailable; the two cases require different handling.

## Design

### Cached dataset compatibility

Validate the required base datasets from a cached run against the current
`DashboardRunCreateRequest` dataset contract before loading the snapshot into
the in-memory repository. Ignore deferred dataset keys during this validation;
they have separate contracts and may be appended to a valid cached run. A null
field value remains contract-compatible because the key exists.

When the required base datasets are absent or incompatible:

1. Treat the cached run as a cache miss and return HTTP 204.
2. Delete the incompatible cache row on a best-effort basis only if its run ID
   and completion timestamp still match the row that was validated, so cleanup
   cannot remove a concurrently written fresh cache.
3. Log deletion failures server-side without exposing cached data or turning the
   recovery response into a 500.

The existing frontend already starts a fresh Snowflake dashboard run after a
204 cache response. That fresh run uses the current SQL and overwrites the cache
with rows containing `credits_attributed_queries`.

### Nullable attribution

Keep the Snowflake SQL value nullable rather than applying `COALESCE`. In the
view builder, distinguish an absent `credits_attributed_queries` key from a key
whose value is null:

- Continue raising for an absent key so producer/consumer contract regressions
  fail loudly outside the validated cache path.
- Mark a warehouse's idle percentage unavailable when any selected row for that
  warehouse has null attribution. Preserve its spend, ranking, and chart data.
- Emit `idle_pct: null`; the existing frontend contract and component already
  render this as an unavailable value.

For non-null values, retain all existing numeric validation and idle-percentage
invariants.

## Scope

The changes belong in the cached-run API path and prepared-view builder. They do
not alter Snowflake SQL, RLS, database schema, or the frontend.

## Testing

Add a route regression test that stores an otherwise valid cached run whose
`warehouse_spend_daily` row lacks `credits_attributed_queries`. Assert that
`GET /api/dashboard-runs/cached` returns 204 and removes the stale cache entry.
Existing valid-cache route tests must continue returning a completed cached run.
Add a race regression test proving cleanup preserves a newer cache row written
after the incompatible row was read.

Add a prepared-view regression test containing one warehouse with null
`credits_attributed_queries`. Assert that the dashboard view still builds, the
warehouse remains in spend output, and its bar has `idle_pct is None`. Retain a
test proving that an absent key still raises the contract error.

Run the targeted API test module first, then the complete API test suite and
API lint checks.
