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

Edge cases:

- `sum(credits_used_compute) == 0` → `idle_pct = null` → rendered as `–`
  (matches SELECT's dash rows for warehouses with no compute).
- Tiny negative float noise in `idle_credits` is clamped to `0`.
- Result is clamped to `[0, 1]` defensively.

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

Compute `idle_pct` per warehouse (with the edge-case handling above) and attach
it to the warehouse bar rows only. The user bar rows are built exactly as
before.

### 3. Models — `apps/api/app/services/dashboard_view_models.py`

Add a warehouse-specific bar row type carrying `idle_pct: float | None`, leaving
the shared `RankedBarRow` (used by the user panel and other sections) untouched.
`WarehouseSpendViewModel.warehouse_bars` uses the new type.

### 4. TS contracts — `apps/web/src/lib/dashboard-contracts.ts`

Mirror the new warehouse bar row type with `idlePct: number | null`. The shared
`RankedBarRow` type is unchanged.

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

## Testing

Backend only (frontend tests intentionally skipped per request):

- `idle_pct` math for representative warehouses.
- Zero compute credits → `idle_pct` is `null`.
- Negative-noise clamp → `0`.
- Clamp to `[0, 1]`.
- Warehouse row ordering preserved (by spend descending).
- User bar rows / shared `RankedBarRow` shape unchanged (regression guard).

## Non-goals

- No change to the stacked time-series chart on the left.
- No per-user idle metric.
- No change to credits→dollars pricing logic.
