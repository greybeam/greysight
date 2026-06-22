# Warehouse Spend: Idle % bar — Design

**Date:** 2026-06-20
**Branch:** `feat/idle_pct`

## Problem

The "Total spend by warehouse" panel (right column of the Warehouse Spend
section) currently shows the same horizontal spend bar used by every other
ranked panel. For warehouses specifically that bar is low-signal — the dollar
value already carries the spend information.

SELECT surfaces a more useful per-warehouse metric: **Utilization Efficiency**
(the share of compute credits actually attributed to running queries). We want
the inverse — **Idle %** — because for us a *high* idle percentage is the bad
state worth surfacing.

## Scope

- **In scope:** the "Total spend by warehouse" panel only.
- **Out of scope:** the "Total spend by user" panel, Storage Spend, and AI
  Spend all keep using the existing shared `RankedSpendBars` component
  unchanged.

## Idle % definition

Aggregated per warehouse over the dashboard window:

- `idle_credits = sum(credits_used_compute) − sum(credits_attributed_queries)`
- `idle_pct = idle_credits / sum(credits_used_compute)` — a fraction in `[0, 1]`

Edge cases (mirroring the fail-loud stance of `_warehouse_row_dollars`):

- `sum(credits_used_compute) == 0` → `idle_pct = null` → rendered as `–`
  (matches SELECT's dash rows for warehouses with no compute).
- `idle_credits` in the band `[-_FLOAT_EPSILON, 0)` is clamped to `0` (float
  noise only).
- If `sum(credits_attributed_queries)` materially exceeds
  `sum(credits_used_compute)` (i.e. `idle_credits < -_FLOAT_EPSILON`), **raise**
  rather than clamp — this is an impossible state and should fail loud, exactly
  like the existing cloud-services-credits invariant check.
- No blanket `[0, 1]` clamp: once the epsilon band is handled and the
  impossible-negative case raises, `idle_pct` is naturally in `[0, 1]`.

## Changes

### 1. SQL — `sql/snowflake/warehouse_spend_daily.sql`

Add one aggregate to the existing select:

```sql
sum(credits_attributed_compute_queries) as credits_attributed_queries
```

`credits_attributed_compute_queries` is a column of
`snowflake.account_usage.warehouse_metering_history`.

### 2. Python view builder — `apps/api/app/services/dashboard_view_builder.py`

In `_build_warehouse_spend`, aggregate per warehouse alongside the existing
spend totals:

- `sum(credits_used_compute)`
- `sum(credits_attributed_queries)`

Compute `idle_pct` per warehouse (with the edge-case handling above) and build
the warehouse idle bar rows **inline in `_build_warehouse_spend`** from the
ranked `RankedSpendRow` values — do **not** route them through
`_build_ranked_bar_rows`. That helper sets `bar_width_percent = spend / top_spend`
(spend-relative), whereas the idle bar width is `idle_pct × 100` (absolute,
0–100). The bar width therefore lives on the frontend (derived from `idlePct`);
the warehouse row itself carries only `idle_pct`. The user bar rows are built
exactly as before via `_build_ranked_bar_rows`, and that helper is left
untouched for the spend-scaled panels.

### 3. Models — `apps/api/app/services/dashboard_view_models.py`

Add a warehouse-specific bar row type derived from `RankedSpendRow` (name,
spend, spend_label, credits) plus `idle_pct: float | None` — **not** from
`RankedBarRow` (no `bar_width_percent`, since the idle width is derived on the
frontend). The shared `RankedBarRow` (used by the user panel and other sections)
is untouched. `WarehouseSpendViewModel.warehouse_bars` uses the new type.

### 4. TS contracts — `apps/web/src/lib/dashboard-contracts.ts`

Mirror the new warehouse bar row type with `idlePct: number | null`. The shared
`RankedBarRow` type is unchanged.

**Raw dataset contract (`WarehouseSpendDaily`):** intentionally **not** changed.
`credits_attributed_queries` is consumed only inside the Python view builder to
compute `idle_pct`; it is never surfaced through the raw dataset response, so
adding it to the TS `WarehouseSpendDaily` type would create an unused field.

### 5. Frontend component — `apps/web/src/components/dashboard/dashboard-design-system.tsx`

New `WarehouseIdleBars` component (sibling to `RankedSpendBars`), used only by
the "Total spend by warehouse" panel in
`apps/web/src/components/dashboard/spend-sections.tsx`.

Four-column grid, aligned with `tabular-nums`:

```
warehouse_name        idle%   [====bar 0–100%====]   $spend
dbt_large               5%    ██░░░░░░░░  (green)      $12.3K
etl_xl                 47%    █████░░░░░  (amber)       $9.8K
bi_pool                52%    ██████░░░░  (red)         $7.7K
unused_wh               –     ░░░░░░░░░░               $0.40
```

- Bar width = `idlePct × 100`, capped at 100%.
- Color buckets: `≤25%` green, `>25% and ≤50%` amber, `>50%` red — using the
  design system's Tailwind/Tremor color tokens.
- `idlePct === null` → render `–` in the percent column and no bar.
- Row ordering unchanged: sorted by total spend descending (highest spenders
  first, so their idle is visible at the top).
- The spend value on the right keeps the existing `compactSpendLabel`
  formatting.
- **Layout shell:** reuse the same absolute-fill scroll wrapper + grid `<ul>`
  list shell that `RankedSpendBars` uses (so it sits correctly inside the flex
  half-height panel in `WarehouseSpendSection`). Extract a tiny shared shell if
  clean; otherwise duplicate just those ~5 layout lines. The grid template
  changes from 3 columns to 4 (`name | idle% | bar | spend`).
- **Visible-row filter:** apply the same sub-cent filter as `RankedSpendBars`
  (`Math.round(row.spend * 100) !== 0`). Note this keys off **spend**, so a
  near-zero-spend warehouse is hidden even if its idle % is high — correct for a
  spend panel.

## Testing

Backend only (frontend tests intentionally skipped per request):

- `idle_pct` math for representative warehouses.
- Zero compute credits → `idle_pct` is `null`.
- Epsilon-band negative noise → clamped to `0`.
- Material invariant violation (attributed > compute beyond epsilon) → raises.
- Warehouse row ordering preserved (by spend descending).
- User bar rows / shared `RankedBarRow` shape unchanged (regression guard).

## Non-goals

- No change to the stacked time-series chart on the left.
- No per-user idle metric.
- No change to credits→dollars pricing logic.
