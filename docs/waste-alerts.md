# Waste alert digest

The waste estimator (see [warehouse-waste-estimator.md](warehouse-waste-estimator.md))
surfaces per-warehouse projected monthly idle spend on the dashboard. The
digest turns that observation into an action: on a schedule, it posts a
Slack-compatible webhook when a warehouse's projected monthly idle spend
crosses a configurable threshold.

## Consistency

The digest reuses `_build_warehouse_waste` verbatim. Alerts and the
dashboard card can never disagree on projected monthly waste — if the
number in Slack differs from the number in the UI, it's a bug in the
shared function, not in either surface.

Alerts pass `row_limit=None` so every warehouse over threshold is
eligible, not only the dashboard's displayed top five.

## Dedup model

One row per warehouse in `waste_alert_state`, keyed by warehouse name.
On each run, a warehouse is only included in the digest if its last
alert is older than `WASTE_ALERT_COOLDOWN_HOURS` (default: 168h = one
week). Warehouses that stay above threshold across many runs will
re-alert once per cooldown window — not continuously, not silently.

The record is only written for warehouses that actually shipped in a
delivered digest. Filtered-out warehouses (below threshold, or within
cooldown) are never written.

## Threshold semantics

The threshold is compared against `projected_monthly_idle_spend`, not
against `period_idle_spend`. The projection is what makes an alert
actionable — "this warehouse will cost you $X/month if nothing changes".

## Empty state

If no warehouse meets the threshold-and-cooldown filter, the digest is
empty and the delivery function short-circuits before making any HTTP
call. Silence is the correct signal — a "no waste today" message would
be noise.

## Scheduling

Greysight does not embed a scheduler. `run_waste_alert_job` is an async
function invoked externally (cron, Cloud Scheduler, GitHub Actions,
Kubernetes CronJob, etc.). The recommended cadence is once per week,
matching the default cooldown.

Example cron:
```
0 14 * * 1  cd /app && python -m app.jobs.waste_alert_job
```

Run that from `apps/api` so the `app` package resolves.

## What this does not do

- Does not recommend an `AUTO_SUSPEND` value. Auto Savings owns that
  control loop. The Slack message links to `/automated-savings` — the
  digest is an observation, not a prescription.
- Does not mutate Snowflake.
- Does not query Snowflake directly — reads the same `warehouse_spend_daily`
  dataset the dashboard already loads (`build_demo_dashboard_dataset` in
  demo mode, `build_snowflake_dashboard_data` in live mode).

## Persistence

There is no pre-existing API `Store` protocol. Waste-alert state uses a
small protocol in `app.services.store` with three backends:

- `memory` (default): `InMemoryStore`, used by tests.
- `supabase`: service-role REST against `waste_alert_state`.
- `duckdb`: optional local file; imports `duckdb` lazily so it is not an
  API dependency unless `WASTE_ALERT_BACKEND=duckdb`.

## How to enable

```
WASTE_ALERT_ENABLED=true
WASTE_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
WASTE_ALERT_MONTHLY_THRESHOLD_USD=250
WASTE_ALERT_WINDOW_DAYS=30
WASTE_ALERT_COOLDOWN_HOURS=168
WASTE_ALERT_CURRENCY=USD
```

`WASTE_ALERT_WINDOW_DAYS` defaults to 30 to match
`DEFAULT_VIEW_WINDOW_DAYS` on the dashboard.

Ships dormant — existing deployments see no behavior change until
`WASTE_ALERT_ENABLED` is set.
