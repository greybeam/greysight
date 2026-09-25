# Warehouse waste estimator

The dashboard already computes per-warehouse idle share as
`credits_used_compute − credits_attributed_queries`. The waste estimator
dollarizes that idle compute, projects it to a 30-day month, and links the
result to Auto Savings enrollment. It reuses `warehouse_spend_daily`; there is
no new Snowflake SQL source and no new writer.

## Idle credits

Idle credits for a warehouse over the selected window are:

```
idle = credits_used_compute − credits_attributed_queries
```

That matches `_warehouse_idle_pct` on the warehouse ranked bars. Dollar amounts
are produced in `dashboard_view_builder._build_warehouse_waste` by converting
each day's idle credits through the same rate index that prices
`warehouse_spend`, so the two sections stay consistent to the cent.

If any day in the window is missing `credits_attributed_queries` (adaptive
warehouses on older Account Usage schemas), the whole warehouse's `idle_pct`
collapses to `None` and its idle dollars are treated as zero. A partial
attribution number would look precise and be wrong; the UI renders an em dash
instead.

`attributed > compute` (beyond float epsilon) is an impossible state and
raises. Warehouses with zero compute in the window are dropped.

## Monthly projection

Projected monthly idle spend is:

```
period_idle_spend × (30 / window_days)
```

where `window_days` is the inclusive length of the selected view range. This is
linear extrapolation of the observed idle rate, not a forecast: it does not
model seasonality, AUTO_SUSPEND changes, or enrollment. A 7-day window with
high idle will project a high monthly number even if that week was unusual.

The table shows up to five warehouses by period idle spend. Fully attributed
warehouses (no idle compute) are omitted from the table; warehouses with
missing attribution are shown with an em dash. KPI totals cover every
warehouse in the window, not only the displayed rows.

## Empty state

When total period idle spend across all warehouses is `<= 0`, the prepared view
sets `is_empty=True` and the card renders nothing. That includes demo windows
whose warehouses have no idle compute, fully attributed compute, or only
null-attribution warehouses. Hiding the card avoids a CTA that would imply
recoverable dollars that are not there.

## Auto Savings CTA

The card links to `/automated-savings`. Idle compute is the observation; Auto
Savings is the in-product control loop that can suspend idle warehouses. The
estimator does not mutate Snowflake and does not recommend an `AUTO_SUSPEND`
value.
