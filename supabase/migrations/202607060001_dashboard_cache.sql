-- Cached dashboard runs, per organization.
--
-- A completed Snowflake dashboard run is cached once per org so a page reload
-- (or another org member) can re-derive any time window from the cached
-- datasets instead of re-querying Snowflake. Two tables:
--
--   * organization_dashboard_cache_settings — the per-org on/off toggle and TTL.
--     Members read; owners/admins mutate (mirrors the org membership RLS style).
--   * dashboard_run_cache — the latest cached run for an org. Service-role only:
--     the API is the SOLE reader and writer (mirrors
--     organization_snowflake_connections, whose access bypasses RLS). Members do
--     NOT read this table directly — the API gates every read behind its
--     TTL/cache_enabled/connection-fingerprint checks, so a direct member SELECT
--     would bypass those guards (e.g. serving a stale disconnected account's
--     payload). RLS stays enabled with NO policies → all authenticated access is
--     denied and only the service role can touch the raw rows.

create table organization_dashboard_cache_settings (
  organization_id uuid primary key references organizations(id) on delete cascade,
  cache_enabled boolean not null default true,
  cache_ttl_seconds integer not null default 86400
    check (cache_ttl_seconds between 3600 and 604800),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table dashboard_run_cache (
  organization_id uuid primary key references organizations(id) on delete cascade,
  run_id uuid not null,
  source text not null,
  window_days integer not null,
  -- Snowflake account fingerprint for the cached run. If the org disconnects or
  -- swaps its Snowflake connection, the current account_locator no longer
  -- matches this cached row, so /cached treats it as a miss instead of serving
  -- the old connection's cost data until TTL expiry. Nullable: pre-fingerprint
  -- rows and runs without a resolved locator compare as None.
  account_locator text,
  summary jsonb not null,
  metadata jsonb,
  datasets jsonb not null,
  source_start_date date not null,
  source_end_date date not null,
  completed_at timestamptz not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger organization_dashboard_cache_settings_set_updated_at
  before update on organization_dashboard_cache_settings
  for each row execute function set_updated_at();
create trigger dashboard_run_cache_set_updated_at
  before update on dashboard_run_cache
  for each row execute function set_updated_at();

alter table organization_dashboard_cache_settings enable row level security;
alter table dashboard_run_cache enable row level security;

-- Cache settings: members read, owners/admins write. No delete policy.
create policy organization_dashboard_cache_settings_select_for_members
  on organization_dashboard_cache_settings for select
  to authenticated
  using (is_organization_member(organization_id));

create policy organization_dashboard_cache_settings_insert_for_admins
  on organization_dashboard_cache_settings for insert
  to authenticated
  with check (is_organization_admin(organization_id));

create policy organization_dashboard_cache_settings_update_for_admins
  on organization_dashboard_cache_settings for update
  to authenticated
  using (is_organization_admin(organization_id))
  with check (is_organization_admin(organization_id));

-- Cached run payloads: NO policies at all. RLS is enabled (above), so every
-- authenticated read/write is denied; the API accesses this table exclusively
-- via the service role, which bypasses RLS. A member SELECT policy would let
-- clients read raw cached payloads directly, bypassing the API's
-- TTL/cache_enabled/fingerprint checks — so it is intentionally omitted.
