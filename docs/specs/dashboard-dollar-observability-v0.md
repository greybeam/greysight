# Dashboard Dollar Observability V0

## Status

Draft for review.

## Purpose

Greysight Dashboard V0 should be a dense Snowflake cost observability console. It should show dollar-denominated spend, clear freshness, and fast local breakdowns that are more usable than Snowflake's native cost dashboard.

This is not an optimization or recommendations release. V0 should help users understand where spend is going, not claim savings or prescribe actions.

## Product Principles

- Dollars are always the primary user-facing unit.
- Credits can appear only as secondary reconciliation metadata in detail rows or tooltips.
- The word `billed` is reserved for figures sourced from Snowflake Organization Usage billing data.
- Estimated dollar figures must be labeled as estimated.
- Missing billing access should not make the whole product unusable when Account Usage data is available.
- Demo, billed, and estimated modes should share one dashboard layout and one transform path.

## Goals

- Show dollars as the primary unit across the dashboard.
- Use Snowflake Organization Usage billing data as the authoritative source for billed spend when available.
- Degrade from billed dollars to estimated dollars when Organization Usage billing data is unavailable.
- Convert credit-native Account Usage drilldowns to estimated dollars through the best available source tier.
- Make Total, Compute, Storage, and Service spend readable on the first dashboard screen.
- Support local 7, 30, and 90 day filtering without another Snowflake round trip.
- Make run status, billing freshness, selected mode, and selected account obvious.
- Provide loading, empty, and error states that are useful with real Snowflake data.
- Preserve a dense cost-ops feel: compact charts, ranked bars, scrollable tables, restrained styling.

## Non-Goals

- Previous-period comparison deltas.
- Savings estimates.
- Recommendations or anomaly detection.
- Multi-account organization dashboards.
- AI-specific dashboard tab.
- Persistent browser cache across sessions.
- Browser DuckDB.
- Full login and organization provisioning flow.
- Replacing the existing registry-approved SQL safety model.
- Billing reconciliation for rebates, adjustments, support credits, or non-consumption rows.

## Source Tiers

V0 should use a clear source hierarchy for dollar figures.

### Tier 1: Billed Dollars

Use `SNOWFLAKE.ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY` as the source of truth for billed spend.

This data powers:

- Total spend.
- Daily spend.
- Spend by service.
- Spend by rating type, including compute and storage.
- Currency display.
- Billing-through date.

Only Tier 1 figures may be labeled `billed`.

### Tier 2: Rate-Sheet Estimated Dollars

Use `SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY` for effective rates when converting credit-native Account Usage datasets to estimated dollars.

This data powers:

- Estimated warehouse spend where only Account Usage credits are available.
- Estimated user compute attribution where only Account Usage credits are available.

Tier 2 figures must be labeled estimated and should not be expected to reconcile with Tier 1 billed totals.

### Tier 3: Configured-Price Estimated Dollars

When Organization Usage billing or rate-sheet data is unavailable, convert credit-native Account Usage datasets with configured prices.

Add a compute estimate setting:

- `ESTIMATED_CREDIT_PRICE_USD`
- Default: `3.00`

Continue using the existing storage estimate setting for storage bytes:

- `STORAGE_PRICE_USD_PER_TB_MONTH` (the existing env var name; do not rename it in this slice)

When Tier 3 is used, the dashboard must show the assumption visibly, for example:

`Estimated spend at $3.00/credit - billed data unavailable`

Tier 3 figures must be labeled estimated. They will not match invoices because one configured credit price cannot model edition differences, serverless multipliers, cloud-services waivers, or contract-specific billing effects.

## Base Datasets

The API should return bounded base datasets, not unbounded row-level extracts.

Base datasets are daily-grain dimensional aggregates over the V0 fetch window. Snowflake performs the heavy reduction with registry-approved SQL. The browser only windows, joins, ranks, and formats these base datasets.

Required base dataset shape:

- Daily grain.
- Explicit dimensions.
- Bounded 100-day fetch window.
- No arbitrary user-provided SQL.
- Schema-validated before entering frontend transforms.

## Dataset Keys

Existing Account Usage dataset keys remain:

- `account_spend_daily`
- `service_spend_daily`
- `warehouse_spend_daily`
- `query_compute_by_user_daily`
- `database_storage_daily`
- `top_warehouses_table`

`top_warehouses_table` is a legacy server-side ranking over the full fetch window. V0 ranked tables must be built in the frontend transform layer from daily base datasets so rankings respect the selected local window. Keep the key for contract compatibility, but do not render selected-window rankings from it.

V0 adds these dataset keys:

- `org_spend_daily`
- `rate_sheet_daily`
- `current_account`

`org_spend_daily` should come from `USAGE_IN_CURRENCY_DAILY`.

`rate_sheet_daily` should come from `RATE_SHEET_DAILY`.

`current_account` should come from a registry-approved metadata query:

```sql
select current_account() as account_locator
```

The registry should distinguish source kinds:

- `snowflake_account_usage`
- `snowflake_organization_usage`
- `snowflake_metadata`

## Contract Versioning

The dashboard dataset response should include `schema_version`.

V0 should use an explicit value such as:

```json
{ "schema_version": 1 }
```

Frontend contract parsing must validate `schema_version` before passing data into the view-model layer. Future persistent browser cache keys must include this version.

## Metadata Contract

The dashboard dataset response should include metadata needed by the header and transforms:

- `schema_version`
- `data_mode`: `demo`, `billed`, or `estimated`
- `account_locator`
- `currency`
- `billing_through_date`
- `account_usage_through_date`
- `estimated_credit_price_usd`
- `storage_price_usd_per_tb_month`
- source availability details for Organization Usage and Account Usage

`DashboardSummary` should not be the selected-window source of truth in V0. Selected-window summaries should be produced by the frontend view-model transform. Existing summary fields can remain for compatibility during migration, but the V0 dashboard should not render selected-window totals from server-side `DashboardSummary`.

## Account Locator

Organization Usage queries must filter by account locator.

V0 should derive the account locator from Snowflake with the `current_account` metadata dataset. Do not require a separate `SNOWFLAKE_ACCOUNT_LOCATOR` environment variable.

The derived account locator should be bound into Organization Usage SQL filters. Do not interpolate it into SQL strings.

## Organization Usage Availability

Organization Usage access is expected to fail for some valid deployments. Billing views may be denied, unavailable, or empty in accounts without the right Organization Usage access.

V0 behavior:

- If Organization Usage access fails but Account Usage succeeds, render estimated dollars mode.
- If Organization Usage is accessible and returns zero spend rows, render a dollar empty state, not estimated-mode copy.
- If both Organization Usage and Account Usage fail, fail the dashboard run with a user-safe error.

Organization Usage probes should be separate and tolerant. Do not add Organization Usage probes to validation paths that would make Account Usage-only deployments fail setup validation.

## Base/Transform Boundary

All windowing, summary-building, ranking, dollar-mode selection, and chart/table view-model construction should live in one shared pure TypeScript transform module.

Do not scatter this logic inside React components.

React components should receive already-built view models and render them.

This transform module should be used identically by:

- Demo mode.
- Live billed mode.
- Live estimated mode.

Demo/live parity should be enforced at the base-dataset contract layer. Transforms are one code path.

This separation keeps a later TypeScript-to-DuckDB swap contained to the transform/query layer rather than requiring a dashboard rewrite.

## Frontend Aggregation

V0 aggregation and local windowing should happen in the frontend TypeScript view-model layer after the API returns parsed base datasets.

The API is responsible for:

- Executing registry-approved Snowflake queries.
- Returning bounded base datasets for the raw fetch window.
- Returning run metadata.
- Returning source availability and freshness metadata.
- Detecting mixed-currency Organization Usage results.

The frontend is responsible for:

- Applying 7, 30, and 90 day local filters.
- Building selected-window section summaries.
- Building chart series and ranked tables.
- Choosing billed or estimated view models from availability metadata.
- Rendering unsupported states exposed by the API.

## Currency Handling

V0 supports one currency per dashboard dataset.

The API should detect mixed currencies before shipping dashboard data. If multiple currencies are present:

- Do not sum them together.
- Mark the run or dataset response as unsupported mixed-currency.
- Preserve enough metadata for a user-safe unsupported state.

The frontend should render the unsupported state from metadata rather than independently discovering mixed currencies after render begins.

For local Snowflake mode, USD is expected but must not be hardcoded.

## Billing Row Inclusion

Default billed spend charts and summaries should include:

- `billing_type = 'consumption'`
- `is_adjustment = false`

Verify on live data that the negative included-cloud-services adjustment row carries `billing_type = 'consumption'` and `is_adjustment = false` so this filter keeps it. If it is flagged differently, adjust the filter so invoice-matching negative rows stay in billed totals.

Negative consumption rows are valid and must be preserved in billed dollar totals. Charts, ranked tables, and driver cards must handle negative spend without breaking or hiding the row.

Non-consumption rows can remain in base datasets but should not be included in primary V0 spend summaries unless a later reconciliation spec adds them.

Manual live-mode verification should include one known invoice month where the V0 Organization Usage window total matches Snowflake billing totals after applying the same V0 filters. This is not a hermetic CI test.

## Estimated Dollar Conversion

Account Usage credits should be converted to dollars using the best available tier:

1. Matching rate-sheet row.
2. Configured estimated credit price.

The configured price is USD. Apply the per-row configured-price fallback only when the dashboard currency is USD; for non-USD billing currencies, mark dollar spend unavailable for rows without a matching rate instead of mixing currencies. Full estimated mode output is always labeled USD.

Never render credits as the primary value when a dollar column is expected.

Credits may appear as secondary metadata in detail tables for users reconciling against Snowflake's Account Usage views.

Warehouse spend conversion:

- Extend `warehouse_spend_daily` to include both `credits_used` and `credits_used_compute`.
- Use `credits_used_compute` for estimated compute spend.
- Keep `credits_used` as secondary metadata.

User spend conversion:

- Use `credits_attributed_compute` from `QUERY_ATTRIBUTION_HISTORY`.
- Preserve the output field as a clearly named base field rather than hiding the source semantics.

Rate-sheet joins must use only columns verified to exist in the live Snowflake view. Before writing the implementation plan, run a live schema/row inspection for `RATE_SHEET_DAILY` and document the exact join mapping.

The planned join should start from:

- usage date
- account locator
- currency
- service or usage type mapping
- rating type, billing type, and adjustment flags only if verified in the live view

Estimated warehouse/user dollars will usually not reconcile to billed compute totals. The UI must not imply that they do.

## Storage Spend

Daily storage spend should use one canonical Organization Usage predicate:

- `rating_type = 'storage'`

Do not use an OR condition across rating type and service type for V0 storage spend.

When billed storage data is unavailable, storage estimates may use `STORAGE_PRICE_USD_PER_TB_MONTH` and Account Usage storage bytes.

Storage byte values should be secondary to dollar values.

## Freshness Policy

Billing data is not real time. The dashboard must display a clear billing-through date based on the latest `USAGE_IN_CURRENCY_DAILY` usage date included in the current usable billing dataset.

Copy should use language like:

- `Billing data through Jun 8, 2026`
- `Local Snowflake`
- `Last analysis completed Jun 10, 2026, 2:14 PM`

Avoid copy that implies current-day billing is complete or that recent billing rows are final.

Because Organization Usage billing data can lag Account Usage attribution data, V0 should clamp all dollar sections to the billing-through date in billed mode.

Account Usage rows after the billing-through date should be hidden everywhere in billed mode, including detail tables. One rule is preferable for V0.

In estimated mode, freshness should be based on Account Usage availability and should be labeled separately.

The Snowflake run should fetch 100 days of base data so the UI can still present a complete 90-day local filter after billing lag and partial tail days are removed.

## Windowing And Filters

The UI should default to 30 days and provide local filter controls for:

- 7 days
- 30 days
- 90 days

Changing this filter should update dashboard charts and tables from local browser data. It should not start a new Snowflake analysis.

Projected monthly spend should be labeled by basis. For V0, always base projected monthly spend on the latest available 30-day window when at least 30 days are available, even if the visible filter is 7 days. If fewer than 30 days are available, label the projection as based on the available day count.

## Payload Bounds

The API must bound high-cardinality attribution datasets before returning JSON.

For `query_compute_by_user_daily`:

- Compute top 100 users by total compute credits over the 100-day fetch window.
- Return user-by-warehouse-by-day rows for those top 100 users.
- Roll all remaining users into per-day `Other` rows.
- Preserve local 7, 30, and 90 day fidelity for the head users and the `Other` aggregate.

If warehouse cardinality becomes a payload issue, apply the same top-N plus `Other` pattern in a later slice.

## Browser Analytics

For V0:

- Use pure TypeScript view-model aggregation.
- Keep all browser billing data in memory for the current page/session.
- Do not persist authenticated billing data across browser sessions.

DuckDB is deferred to a later persistent-cache slice. That slice should include browser-level verification for the real WASM/Worker path.

## Dashboard Information Architecture

### Header

The header should show:

- Product name: `Greysight`.
- Mode: `Demo`, `Local Snowflake`, or `Authenticated Snowflake`.
- Data mode: `Billed` or `Estimated`.
- Selected organization/account context.
- Account locator.
- Run status.
- Billing-through date when available.
- Estimated-rate assumption when estimated mode is active.
- `Run analysis` action.

The current unauthenticated Snowflake path should not be labeled `Demo mode`.

### Filter Bar

The filter bar should show:

- 7 / 30 / 90 day segmented control.
- Active currency.
- Optional row count or source freshness metadata if useful.

The selected local window controls frontend aggregation only. It does not trigger a Snowflake run.

### Total Spend

The Total Spend section should show:

- Total spend in selected window.
- Average daily spend.
- Projected monthly spend based on the projection basis rule.
- Daily spend chart.
- Top service or top rating type as a compact driver card.

Previous-period deltas are intentionally excluded from V0.

### Compute Spend

The Compute Spend section should show:

- Daily compute spend, from Organization Usage filtered to `rating_type = 'compute'` in billed mode.
- Daily warehouse spend, estimated from Account Usage when necessary.
- Ranked warehouses by estimated spend.
- Ranked users by estimated spend from query attribution.

Estimated labels must be visible for warehouse and user attribution when those figures are not billed Organization Usage totals.

### Storage Spend

The Storage Spend section should show:

- Daily storage spend.
- Latest storage by database from Account Usage storage bytes.
- Storage byte values as secondary context.

Do not use the existing local storage price estimate as the primary spend source when Organization Usage billing data is available.

### Service Spend

The Service Spend section should show:

- Daily spend by Snowflake service type.
- Ranked services by selected-window spend.
- Cortex/AI-like services if they appear in billing data, but no dedicated AI tab yet.

### Detail Tables

Below the main sections, provide dense detail tables for:

- Service spend.
- Warehouse spend.
- User compute spend.
- Storage by database.

Tables should be compact, capped or scrollable, and useful with real Snowflake data volume.

Credits may appear in detail tables as secondary metadata, not as the primary value.

## Loading, Empty, And Error States

### Loading

Loading states should show:

- Current run status.
- Disabled run action with visible state.
- Skeletons or placeholders for chart/table regions.

### Empty

Each major section should have its own empty state:

- No total spend data.
- No compute spend data.
- No storage spend data.
- No service spend data.

Accessible-but-zero Organization Usage data should render empty dollar states, not estimated-mode warning copy.

### Errors

Errors should be user-safe and specific where possible:

- Missing Organization Usage permissions.
- Snowflake query failure.
- Unsupported mixed-currency result.
- Account Usage unavailable.

Organization Usage permission or availability errors should lead to estimated dollars mode when Account Usage data is available. They should hard-fail the dashboard only when both billing and Account Usage sources are unavailable.

Avoid hardcoded `Account Usage` error copy for failures that may come from Organization Usage or metadata sources.

## Demo Mode

Demo mode should include deterministic dollar-denominated fixture data for the full 100-day fetch window:

- Total spend.
- Compute spend.
- Storage spend.
- Service spend.
- Warehouse/user attribution with estimated conversion.
- Billing-through date.
- Account Usage freshness date.

Demo data should preserve the same base dataset keys and shapes as Snowflake mode.

The 90-day filter must show meaningful demo data.

## Security And Privacy

Organization Usage exposes financial billing data. This should be treated as sensitive customer data.

V0 should:

- Keep authenticated browser data in memory only.
- Avoid storing access tokens in cache keys or client-side tables.
- Continue executing only registry-approved SQL assets.
- Require bounded scans and explicit derived account locator filters for Organization Usage queries.
- Preserve existing auth/org guard behavior.

## UX Direction

The dashboard should feel like a cost operations console:

- Compact chart heights.
- Dense tables.
- Ranked horizontal bars where they scan better than large charts.
- Restrained color palette.
- Clear hierarchy.
- No marketing hero treatment.
- No decorative background effects.

The first screen should prioritize information density and operational clarity over visual flourish.

## Plan Prerequisites

Before writing the implementation plan:

- Run a live schema/row inspection for `RATE_SHEET_DAILY`.
- Pin the exact rate-sheet join mapping from verified columns.
- Confirm whether `WAREHOUSE_METERING_HISTORY.CREDITS_USED_COMPUTE` is available in the target account and update the base SQL accordingly.
- Decide the exact API shape for `schema_version`, metadata, source availability, and unsupported mixed-currency responses.
- Confirm which development account has Organization Usage views populated and which `SNOWFLAKE` database role grant (for example `ORGANIZATION_BILLING_VIEWER`) the Greysight role needs; document the new grants in `docs/snowflake-setup.md`.
- Verify how the included-cloud-services adjustment row is flagged in `USAGE_IN_CURRENCY_DAILY` (`billing_type`, `is_adjustment`) so the billing row filter keeps invoice-matching negative rows.

## Follow-Up Slices

- Previous-period comparison cards and percent deltas.
- Persistent browser DuckDB/IndexedDB cache with Playwright coverage.
- Multi-account Organization Usage views.
- AI/Cortex-specific spend breakdown.
- Billing reconciliation view for adjustments, rebates, and non-consumption billing rows.
- Interactive warehouse/user/service focus filters.
