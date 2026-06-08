# Standalone Snowflake Cost Observability App Briefing

Date: 2026-06-08
Status: Draft handoff

## Goal

Build a standalone, open-source Snowflake cost observability app for `obs.greybeam.ai`.

The app should be a free lead magnet for Snowflake users who want useful cost visibility without paying for a heavier product like Select.dev. It should let a user sign in with email, connect read-only Snowflake metadata access, live-query their usage data, view polished dashboards, and receive a credible savings estimate.

The current Snowflake Native App is useful for marketplace visibility, but it does not serve this goal well:

- Greybeam does not get direct usage data.
- Greybeam does not get the user's savings estimate.
- Native App development and packaging are slow and painful.
- Iterating a hosted web app should be much faster.

## Locked V1 Stack

Use this stack unless a concrete implementation blocker appears:

- **Frontend:** Next.js on Vercel.
- **Backend:** FastAPI on Vercel.
- **Dashboard UI:** Tremor React components.
- **Auth:** Supabase Auth with passwordless email login.
- **Database:** Supabase Postgres for operational app state.
- **Email:** Supabase Auth email flow; add custom SMTP via Resend, Postmark, or similar before public launch if deliverability requires it.
- **Snowflake access:** Live read-only queries against customer Snowflake metadata.
- **Dashboard query reuse:** Use embedded DuckDB in the Next.js app for ephemeral downstream filtering, grouping, and chart shaping over shared query results.
- **File storage:** Vercel Blob only if export/report artifacts are needed.
- **BI tools:** Do not use Evidence, Superset, or Metabase in v1.
- **Analytics database:** Do not use DuckDB, Quack, MotherDuck, or Neon as a persisted backend in v1.

## Product Shape

The first product experience should be direct:

1. User lands on a page offering a free Snowflake cost observability
2. User signs in with email magic link or one-time code.
3. User creates or joins an organization.
4. User follows a clear Snowflake setup flow for read-only metadata access.
5. App validates the Snowflake connection and required privileges.
6. User runs a live cost analysis.
7. App shows savings estimate first, then supporting dashboards.
8. App offers export/share and "Talk to Greybeam" actions.

The app should feel like a lightweight product, not a BI embedding surface. The dashboard is curated and opinionated, not ad-hoc self-service BI.

## Architecture

One Vercel project should contain both the Next.js frontend and the FastAPI backend.

FastAPI owns:

- Supabase session/JWT validation.
- Snowflake connection validation.
- Live Snowflake source query execution.
- Metric calculations.
- Insight generation.
- Savings estimate generation.
- Operational database writes.

Next.js owns:

- Landing page.
- Auth entry points.
- Connection setup UI.
- Dashboard pages.
- Tremor charts, tables, filters, and metric cards.
- Ephemeral DuckDB transformations for dashboard filtering and chart-specific rollups.
- Polling run status.
- Export/share calls.

## Dashboarding

Use Tremor for v1 dashboard UI.

Tremor should cover:

- Metric cards.
- Line charts.
- Bar charts.
- Ranked tables.
- Badges and callouts.
- Date range filters.
- Select/multiselect filters.
- Dashboard layout primitives.

Use FastAPI endpoints to return shared dashboard datasets. Do not introduce a BI server for v1.

Dashboard performance should be fast and DRY:

- Define reusable Snowflake source queries as SQL assets, not inline in chart code.
- Maintain a source-to-dashboard registry, such as `sql/dashboard_sources.yml`, that documents which charts and tables depend on each source query.
- Each chart/table definition should declare its upstream source query key and its local DuckDB projection/filtering logic.
- Prefer one reusable upstream SQL query per dashboard area or data grain, not one Snowflake query per chart.
- If multiple charts can be built from one Snowflake result set, run that Snowflake query once for the selected connection, time window, and filter scope.
- Return a compact shared result set to the Next.js app.
- Load that result set into embedded DuckDB in the Next.js app.
- Use DuckDB for downstream filtering, grouping, slicing, and chart-specific projections.
- Render the resulting chart/table datasets with Tremor.
- Never expose Snowflake credentials to the browser.
- Do not persist detailed customer usage data in DuckDB or Supabase by default; DuckDB is an ephemeral query engine for the current dashboard session.

If a shared result set is too large for browser-side DuckDB, push that transformation back to FastAPI and keep the same source-query reuse rule on the backend.

Example source registry shape:

```yaml
sources:
  warehouse_spend_daily:
    sql: snowflake/warehouse_spend_daily.sql
    grain: warehouse_name, usage_date
    required_columns:
      - usage_date
      - warehouse_name
      - credits_used
    feeds:
      - top_warehouses_table
      - warehouse_spend_trend_chart
      - warehouse_share_bar_chart
      - warehouse_movers_table
  account_spend_daily:
    sql: snowflake/account_spend_daily.sql
    grain: usage_date
    required_columns:
      - usage_date
      - credits_used
    feeds:
      - spend_trend_chart
      - average_daily_burn_card
      - month_to_date_spend_card
```

Example chart contract shape:

```ts
{
  key: "warehouse_share_bar_chart",
  source: "warehouse_spend_daily",
  duckdbSql: `
    select
      warehouse_name,
      sum(credits_used) as credits_used
    from warehouse_spend_daily
    group by warehouse_name
    order by credits_used desc
    limit 10
  `
}
```

Initial dashboard will just be the home page which will show:
- Total spend in period
- Annualized spend in period
- Daily spend
- Daily spend by service
- Daily spend by warehouse
- Daily compute spend by user
- Daily storage spend by database

Out of scope includes other dashboard pages like: warehouse usage, query insights, AI overview, etc.

## Auth And App State

Use Supabase Auth for passwordless email login. Support magic links or one-time codes; decide which one feels better during implementation.

Use Supabase Postgres for operational app state only:

- Users and organizations.
- Organization memberships.
- Snowflake connection records.
- Encrypted credential metadata or secret references.
- Saved dashboard filters/preferences.
- Live analysis run records.
- Connection validation results.
- Audit events.
- Minimal lead analytics.
- Optional high-level savings estimate summaries.

Do not store Snowflake credentials in Supabase Auth user metadata.

Do not store detailed customer Snowflake usage data by default. Dashboards should live-query Snowflake metadata and return ephemeral shared dashboard datasets. The Next.js app can use embedded DuckDB to derive chart-ready responses locally. If caching is later required, make it explicit, short-lived, and documented.

## Snowflake Access

The app should guide the user through creating a dedicated read-only Snowflake integration.

Required properties:

- Read access to Snowflake account usage metadata.
- No write access.
- No access to customer table data.
- Clear SQL setup script the user can inspect.
- Actionable validation errors for missing privileges.

Connection methods to evaluate during implementation:

- Key-pair auth with a dedicated Snowflake user.

## Live Analysis Runs

A live analysis run is one execution of the app's Snowflake cost analysis for an organization and connection.

Example:

```text
Run #123
Org: Acme
Snowflake connection: acme-prod
Window: last 30 days
Status: completed
Started: 2026-06-08 10:14
Finished: 2026-06-08 10:16
```

For v1:

- User starts a run from the UI.
- FastAPI creates a run row with status `pending`.
- FastAPI executes bounded Snowflake metadata queries.
- FastAPI calculates metrics, findings, and savings estimates.
- Detailed query results remain ephemeral.
- Shared dashboard result sets may be loaded into embedded DuckDB in the Next.js app for local filtering and chart shaping.
- FastAPI may persist run status, errors, and optional high-level estimate summary.
- Next.js polls run status and renders results when ready.

If live analysis runs exceed Vercel function duration limits, split run execution into a separate worker later. Do not introduce a worker or queue before it is needed.

## Initial Data Sources

The first live analysis should query only enough data to support the core value proposition:

- Account-level daily spend or credit usage.
- Remaining balance, if available.
- Warehouse spend by day and warehouse.
- Query attribution or query history summaries, if available.
- Spend by user, role, database, and query tag.
- Storage costs, if available.

Reuse the existing repo's Snowflake SQL assets where possible, but remove Snowflake Native App assumptions.

## Privacy And Security

Minimum requirements:

- Encrypt Snowflake credentials or store only secure secret references.
- Use least-privilege Snowflake setup.
- Require no write privileges in customer Snowflake accounts.
- Keep tenant data separated by organization.
- Enforce authorization on every organization-scoped request.
- Keep an audit log for connection creation, validation, analysis runs, and exports.
- Do not log secrets.
- Do not leak credentials or full SQL responses in errors.
- Avoid storing full query text by default.
- Make data deletion/export possible, even if the first UI is simple.

The public messaging should be clear: the app live-queries Snowflake metadata to produce dashboards and estimates, but does not store detailed usage data by default.

## Milestones

### 1. Skeleton App

- Next.js frontend deploys on Vercel.
- FastAPI backend deploys on Vercel.
- Supabase Auth passwordless login works.
- Supabase Postgres connection works.
- Migrations run.
- Organization model exists.
- Authenticated Tremor dashboard shell renders.

### 2. Snowflake Connection Validation

- User can create a Snowflake connection.
- App validates credentials.
- App validates required metadata privileges.
- Validation errors are actionable.
- Secrets are encrypted or stored through a secure secret path.

### 3. First Live Analysis

- App runs account spend, warehouse spend, and balance queries.
- Run status is persisted.
- Detailed Snowflake usage results remain ephemeral.
- FastAPI returns chart-ready JSON.
- Tremor executive summary renders.

### 4. Findings And Savings Estimate

- Deterministic findings are generated.
- Savings estimate is calculated with visible assumptions.
- User can see prioritized opportunities.
- Greybeam can capture minimal aggregate lead analytics.

### 5. Dashboard Depth

- Query spend dashboard is added.
- Attribution dashboard is added.
- Storage overview is added.
- Export/share flow is added.
- "Talk to Greybeam" conversion event is captured.

### 6. Open Source Readiness

- README explains self-hosting.
- Snowflake privileges are documented.
- Security model is documented.
- Example environment file exists without secrets.
- Tests cover metric and savings calculations.

## Open Questions

1. Should passwordless auth use magic links, email one-time codes, or support both?
2. Should high-level savings estimate summaries be stored for lead follow-up, or only shown ephemerally?
3. What Snowflake connection method creates the best balance of trust and low-friction onboarding?
4. What is the minimum credible savings estimate methodology for launch?
5. Is v1 intended to support multiple Snowflake accounts per organization?
6. What time window keeps live analysis comfortably within Vercel function limits?

## References

- Vercel FastAPI documentation: https://vercel.com/docs/frameworks/backend/fastapi
- Vercel Python runtime documentation: https://vercel.com/docs/functions/runtimes/python
- Vercel function limits: https://vercel.com/docs/functions/limitations
- Supabase passwordless auth documentation: https://supabase.com/docs/guides/auth/auth-email-passwordless
- Supabase signInWithOtp reference: https://supabase.com/docs/reference/javascript/auth-signinwithotp
- Tremor React dashboard components: https://npm.tremor.so/
