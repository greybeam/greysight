-- Waste alert digest: per-warehouse cooldown state.
-- Written only by the scheduled job via the service role. Members do not
-- read this table in the product UI.

create table if not exists waste_alert_state (
    warehouse_name text primary key,
    last_alerted_at timestamptz not null,
    last_projected_monthly_idle_spend double precision not null
);

alter table waste_alert_state enable row level security;

revoke all on waste_alert_state from anon, authenticated, public;
grant select, insert, update on waste_alert_state to service_role;
