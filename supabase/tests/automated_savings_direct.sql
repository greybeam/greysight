\set ON_ERROR_STOP on

begin;

insert into auth.users (id, email)
values
    (
        '20000000-0000-0000-0000-000000000001',
        'automated-savings-member@example.test'
    ),
    (
        '20000000-0000-0000-0000-000000000002',
        'automated-savings-admin@example.test'
    );

insert into public.organizations (id, name, slug, created_by_user_id)
values
    (
        '10000000-0000-0000-0000-000000000001',
        'Automated Savings Direct Test A',
        'automated-savings-direct-test-a',
        null
    ),
    (
        '10000000-0000-0000-0000-000000000002',
        'Automated Savings Direct Test B',
        'automated-savings-direct-test-b',
        null
    );

insert into public.organization_memberships (organization_id, user_id, role)
values
    (
        '10000000-0000-0000-0000-000000000001',
        '20000000-0000-0000-0000-000000000001',
        'member'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        '20000000-0000-0000-0000-000000000002',
        'admin'
    );

insert into public.automated_savings_settings (
    organization_id,
    agreed_at,
    global_enabled,
    grant_present,
    updated_at
)
values (
    '10000000-0000-0000-0000-000000000001',
    '2026-01-01 00:00:00+00',
    true,
    true,
    '2000-01-01 00:00:00+00'
), (
    '10000000-0000-0000-0000-000000000002',
    '2026-01-01 00:00:00+00',
    true,
    true,
    '2000-01-01 00:00:00+00'
);

-- Seed old versions because now() is transaction-stable. The update triggers
-- can then be proven to advance both versions inside this transaction.
insert into public.automated_savings_warehouses (
    organization_id,
    warehouse_name,
    enabled,
    warehouse_created_on,
    updated_at
)
values (
    '10000000-0000-0000-0000-000000000001',
    'DIRECT_TEST_WH',
    true,
    '2026-01-01 00:00:00+00',
    '2000-01-01 00:00:00+00'
), (
    '10000000-0000-0000-0000-000000000002',
    'DIRECT_TEST_WH_B',
    true,
    '2026-01-01 00:00:00+00',
    '2000-01-01 00:00:00+00'
);

insert into public.automated_savings_events (
    organization_id, warehouse_name, action, reason, observed_state,
    observed_running, observed_queued, observed_quiescing,
    observed_resumed_on, observed_at
) values (
    '10000000-0000-0000-0000-000000000002', 'DIRECT_TEST_WH_B',
    'suspend', 'idle', 'STARTED', 0, 0, 0,
    '2026-01-01 00:02:00+00', '2026-01-01 00:03:00+00'
);

select
    organization_id as organization_id,
    warehouse_created_on as warehouse_created_on,
    updated_at as original_updated_at
from public.automated_savings_warehouses
where organization_id = '10000000-0000-0000-0000-000000000001'
  and warehouse_name = 'DIRECT_TEST_WH'
\gset fixture_

create temporary table automated_savings_direct_fixture_state (
    organization_id uuid not null,
    warehouse_created_on timestamptz not null,
    original_updated_at timestamptz not null
) on commit drop;

insert into automated_savings_direct_fixture_state
values (
    :'fixture_organization_id',
    :'fixture_warehouse_created_on',
    :'fixture_original_updated_at'
);

grant select on automated_savings_direct_fixture_state to service_role;
set local role service_role;

do $$
declare
    v_rejected boolean := false;
begin
    begin
        perform public.automated_savings_upsert_enrollment(
            '10000000-0000-0000-0000-000000000001',
            'NULL_IDENTITY_WH',
            true,
            null
        );
    exception when raise_exception then
        if sqlerrm <> 'warehouse identity is required' then
            raise;
        end if;
        v_rejected := true;
    end;

    if not v_rejected then
        raise exception 'null enrollment identity was accepted';
    end if;
end;
$$;

reset role;

set local role service_role;
do $$
declare
    v_authorized boolean;
begin
    select public.automated_savings_authorize_suspend(
        w.organization_id,
        w.warehouse_name,
        w.warehouse_created_on,
        w.updated_at
    )
    into v_authorized
    from public.automated_savings_warehouses w
    where w.organization_id = '10000000-0000-0000-0000-000000000001'
      and w.warehouse_name = 'DIRECT_TEST_WH';

    if not v_authorized then
        raise exception 'current enabled enrollment was not authorized';
    end if;
end;
$$;

update public.automated_savings_settings
set global_enabled = false
where organization_id = '10000000-0000-0000-0000-000000000001';

do $$
declare
    v_authorized boolean;
    v_settings_updated_at timestamptz;
begin
    select public.automated_savings_authorize_suspend(
        w.organization_id,
        w.warehouse_name,
        w.warehouse_created_on,
        w.updated_at
    )
    into v_authorized
    from public.automated_savings_warehouses w
    where w.organization_id = '10000000-0000-0000-0000-000000000001'
      and w.warehouse_name = 'DIRECT_TEST_WH';

    if v_authorized then
        raise exception 'disabled global switch was authorized';
    end if;

    select updated_at into strict v_settings_updated_at
    from public.automated_savings_settings
    where organization_id = '10000000-0000-0000-0000-000000000001';
    if v_settings_updated_at <= '2000-01-01 00:00:00+00'::timestamptz then
        raise exception 'settings update did not advance updated_at';
    end if;
end;
$$;

update public.automated_savings_settings
set global_enabled = true
where organization_id = '10000000-0000-0000-0000-000000000001';

do $$
declare
    v_identity timestamptz;
begin
    select warehouse_created_on into strict v_identity
    from public.automated_savings_warehouses
    where organization_id = '10000000-0000-0000-0000-000000000001'
      and warehouse_name = 'DIRECT_TEST_WH';

    if public.automated_savings_disable_enrollment(
        '10000000-0000-0000-0000-000000000001',
        'direct_test_wh'
    ) then
        raise exception 'alternate-case name disabled enrollment';
    end if;

    if public.automated_savings_disable_enrollment(
        '10000000-0000-0000-0000-000000000001',
        'MISSING_WH'
    ) then
        raise exception 'missing enrollment reported disabled';
    end if;

    if exists (
        select 1 from public.automated_savings_warehouses
        where organization_id = '10000000-0000-0000-0000-000000000001'
          and warehouse_name = 'MISSING_WH'
    ) then
        raise exception 'disable inserted a missing enrollment';
    end if;

    if not public.automated_savings_disable_enrollment(
        '10000000-0000-0000-0000-000000000001',
        'DIRECT_TEST_WH'
    ) then
        raise exception 'existing enrollment was not disabled';
    end if;

    if not exists (
        select 1 from public.automated_savings_warehouses
        where organization_id = '10000000-0000-0000-0000-000000000001'
          and warehouse_name = 'DIRECT_TEST_WH'
          and not enabled
          and warehouse_created_on = v_identity
          and warehouse_created_on is not null
    ) then
        raise exception 'disable changed or nulled enrollment identity';
    end if;
end;
$$;

do $$
declare
    v_row public.automated_savings_warehouses%rowtype;
    v_original_updated_at timestamptz;
begin
    select * into strict v_row
    from public.automated_savings_warehouses
    where organization_id = '10000000-0000-0000-0000-000000000001'
      and warehouse_name = 'DIRECT_TEST_WH';

    select original_updated_at into strict v_original_updated_at
    from automated_savings_direct_fixture_state;

    if public.automated_savings_authorize_suspend(
        v_row.organization_id,
        v_row.warehouse_name,
        v_row.warehouse_created_on,
        v_row.updated_at
    ) then
        raise exception 'disabled enrollment was authorized';
    end if;

    if v_row.updated_at <= v_original_updated_at then
        raise exception 'enrollment update did not advance updated_at';
    end if;
end;
$$;

update public.automated_savings_warehouses
set enabled = true
where organization_id = '10000000-0000-0000-0000-000000000001'
  and warehouse_name = 'DIRECT_TEST_WH';

do $$
declare
    v_row public.automated_savings_warehouses%rowtype;
begin
    select * into strict v_row
    from public.automated_savings_warehouses
    where organization_id = '10000000-0000-0000-0000-000000000001'
      and warehouse_name = 'DIRECT_TEST_WH';

    if public.automated_savings_authorize_suspend(
        v_row.organization_id,
        v_row.warehouse_name,
        '2025-01-01 00:00:00+00',
        v_row.updated_at
    ) then
        raise exception 'stale warehouse identity was authorized';
    end if;

    if public.automated_savings_authorize_suspend(
        v_row.organization_id,
        v_row.warehouse_name,
        v_row.warehouse_created_on,
        '2000-01-01 00:00:00+00'
    ) then
        raise exception 'stale enrollment version was authorized';
    end if;
end;
$$;

-- Table-driven constraint checks: exercise the three custom CHECK (>= 0)
-- constraints and one representative NOT NULL case. Each case overrides one
-- column of an otherwise valid event row with a bad value and asserts the
-- insert fails with the expected SQLSTATE (23514 check, 23502 not-null).
do $$
declare
    v_case record;
    v_defaults jsonb := jsonb_build_object(
        'organization_id', '10000000-0000-0000-0000-000000000001',
        'warehouse_name', 'DIRECT_TEST_WH',
        'action', 'suspend',
        'reason', 'idle',
        'observed_state', 'STARTED',
        'observed_running', 0,
        'observed_queued', 0,
        'observed_quiescing', 0,
        'observed_resumed_on', '2026-01-01 00:02:00+00',
        'observed_at', '2026-01-01 00:03:00+00'
    );
    v_row jsonb;
begin
    for v_case in
        select *
        from (values
            ('observed_running', to_jsonb(-1), '23514'),
            ('observed_queued', to_jsonb(-1), '23514'),
            ('observed_quiescing', to_jsonb(-1), '23514'),
            ('observed_state', 'null'::jsonb, '23502')
        ) as c(column_name, bad_value, expected_sqlstate)
    loop
        v_row := jsonb_set(
            v_defaults, array[v_case.column_name], v_case.bad_value
        );
        begin
            insert into public.automated_savings_events (
                organization_id, warehouse_name, action, reason, observed_state,
                observed_running, observed_queued, observed_quiescing,
                observed_resumed_on, observed_at
            )
            select
                r.organization_id, r.warehouse_name, r.action, r.reason,
                r.observed_state, r.observed_running, r.observed_queued,
                r.observed_quiescing, r.observed_resumed_on, r.observed_at
            from jsonb_populate_record(
                null::public.automated_savings_events, v_row
            ) r;
            raise exception
                'bad % value was accepted', v_case.column_name;
        exception when others then
            if sqlstate <> v_case.expected_sqlstate then
                raise;
            end if;
        end;
    end loop;
end;
$$;

insert into public.automated_savings_events (
    organization_id, warehouse_name, action, reason, observed_state,
    observed_running, observed_queued, observed_quiescing,
    observed_resumed_on, observed_at
) values (
    '10000000-0000-0000-0000-000000000001', 'DIRECT_TEST_WH',
    'suspend', 'idle', 'STARTED', 0, 0, 0,
    '2026-01-01 00:02:00+00', '2026-01-01 00:03:00+00'
);

do $$
begin
    begin
        update public.automated_savings_events
        set reason = 'idle'
        where organization_id = '10000000-0000-0000-0000-000000000001';
        raise exception 'service_role event update was permitted';
    exception when insufficient_privilege then
        null;
    end;

    begin
        delete from public.automated_savings_events
        where organization_id = '10000000-0000-0000-0000-000000000001';
        raise exception 'service_role event delete was permitted';
    exception when insufficient_privilege then
        null;
    end;
end;
$$;

reset role;
do $$
declare
    v_columns text[];
    v_auth_grants text[];
    v_service_grants text[];
    v_rpc_signature_count integer;
    v_table_count integer;
begin
    select count(*) into v_table_count
    from information_schema.tables
    where table_schema = 'public'
      and table_name like 'automated_savings_%';
    if v_table_count <> 3 then
        raise exception 'expected 3 automated savings tables, got %', v_table_count;
    end if;

    if exists (
        select 1
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public'
          and c.relname in (
              'automated_savings_settings',
              'automated_savings_warehouses',
              'automated_savings_events'
          )
          and not c.relrowsecurity
    ) then
        raise exception 'RLS is not enabled on every automated savings table';
    end if;

    select array_agg(column_name::text order by ordinal_position)
    into v_columns
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'automated_savings_warehouses';
    if v_columns <> array[
        'organization_id', 'warehouse_name', 'enabled',
        'warehouse_created_on', 'updated_at'
    ]::text[] then
        raise exception 'unexpected enrollment columns: %', v_columns;
    end if;

    select array_agg(column_name::text order by ordinal_position)
    into v_columns
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'automated_savings_settings';
    if v_columns <> array[
        'organization_id', 'agreed_at', 'global_enabled', 'grant_present',
        'grant_checked_at', 'created_at', 'updated_at'
    ]::text[] then
        raise exception 'unexpected settings columns: %', v_columns;
    end if;

    select array_agg(column_name::text order by ordinal_position)
    into v_columns
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'automated_savings_events';
    if v_columns <> array[
        'id', 'organization_id', 'warehouse_name', 'action', 'reason',
        'observed_state', 'observed_running', 'observed_queued',
        'observed_quiescing', 'observed_resumed_on',
        'observed_started_clusters', 'observed_min_cluster_count',
        'observed_max_cluster_count', 'observed_at', 'created_at'
    ]::text[] then
        raise exception 'unexpected event columns: %', v_columns;
    end if;

    if exists (
        select 1
        from (
            values
                (
                    'automated_savings_settings',
                    'set_automated_savings_settings_updated_at'
                ),
                (
                    'automated_savings_warehouses',
                    'set_automated_savings_warehouses_updated_at'
                )
        ) expected(tablename, trigger_name)
        left join (
            select
                c.relname as tablename,
                t.tgname as trigger_name,
                t.tgtype,
                t.tgenabled,
                p.proname as function_name,
                pn.nspname as function_schema
            from pg_catalog.pg_trigger t
            join pg_catalog.pg_class c on c.oid = t.tgrelid
            join pg_catalog.pg_namespace n on n.oid = c.relnamespace
            join pg_catalog.pg_proc p on p.oid = t.tgfoid
            join pg_catalog.pg_namespace pn on pn.oid = p.pronamespace
            where n.nspname = 'public'
              and not t.tgisinternal
              and c.relname in (
                  'automated_savings_settings',
                  'automated_savings_warehouses'
              )
        ) actual
          on actual.tablename = expected.tablename
         and actual.trigger_name = expected.trigger_name
         and actual.tgtype = 19
         and actual.tgenabled = 'O'
         and actual.function_name = 'set_updated_at'
         and actual.function_schema = 'public'
        where actual.trigger_name is null
    ) then
        raise exception 'automated savings version trigger differs from contract';
    end if;

    if exists (
        select 1
        from (
            values
                (
                    'automated_savings_settings',
                    'automated_savings_settings_read',
                    'SELECT',
                    'is_organization_member(organization_id)',
                    null
                ),
                (
                    'automated_savings_settings',
                    'automated_savings_settings_insert',
                    'INSERT',
                    null,
                    'is_organization_admin(organization_id)'
                ),
                (
                    'automated_savings_settings',
                    'automated_savings_settings_update',
                    'UPDATE',
                    'is_organization_admin(organization_id)',
                    'is_organization_admin(organization_id)'
                ),
                (
                    'automated_savings_warehouses',
                    'automated_savings_warehouses_read',
                    'SELECT',
                    'is_organization_member(organization_id)',
                    null
                ),
                (
                    'automated_savings_warehouses',
                    'automated_savings_warehouses_insert',
                    'INSERT',
                    null,
                    'is_organization_admin(organization_id)'
                ),
                (
                    'automated_savings_warehouses',
                    'automated_savings_warehouses_update',
                    'UPDATE',
                    'is_organization_admin(organization_id)',
                    'is_organization_admin(organization_id)'
                ),
                (
                    'automated_savings_events',
                    'automated_savings_events_read',
                    'SELECT',
                    'is_organization_member(organization_id)',
                    null
                )
        ) expected(tablename, policyname, cmd, expected_qual, expected_with_check)
        left join (
            select
                p.tablename,
                p.policyname,
                p.cmd,
                p.roles,
                case
                    when p.qual is null then null
                    else regexp_replace(
                        regexp_replace(lower(p.qual), '[[:space:]]+', '', 'g'),
                        '^\((.*)\)$',
                        '\1'
                    )
                end as normalized_qual,
                case
                    when p.with_check is null then null
                    else regexp_replace(
                        regexp_replace(
                            lower(p.with_check), '[[:space:]]+', '', 'g'
                        ),
                        '^\((.*)\)$',
                        '\1'
                    )
                end as normalized_with_check
            from pg_catalog.pg_policies p
            where p.schemaname = 'public'
              and p.tablename in (
                  'automated_savings_settings',
                  'automated_savings_warehouses',
                  'automated_savings_events'
              )
        ) actual
          on actual.tablename = expected.tablename
         and actual.policyname = expected.policyname
         and actual.cmd = expected.cmd
         and actual.roles = array['authenticated']::name[]
        where actual.policyname is null
           or normalized_qual is distinct from expected.expected_qual
           or normalized_with_check is distinct from expected.expected_with_check
    ) then
        raise exception 'an automated savings policy predicate differs from contract';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_policies
        where schemaname = 'public'
          and tablename in (
              'automated_savings_settings',
              'automated_savings_warehouses',
              'automated_savings_events'
          )
          and (
              roles <> array['authenticated']::name[]
              or cmd not in ('SELECT', 'INSERT', 'UPDATE')
              or cmd = 'DELETE'
          )
    ) then
        raise exception 'unexpected automated savings policy role or command';
    end if;

    -- Reverse anti-join: every actual policy on the three tables must appear
    -- in the expected set by (tablename, policyname, cmd). This closes the gap
    -- where an EXTRA permissive policy (e.g. a second authenticated SELECT with
    -- qual `true`) would slip past both the forward predicate check and the
    -- role/cmd-only reverse check above.
    if exists (
        select 1
        from pg_catalog.pg_policies actual
        left join (
            values
                ('automated_savings_settings',
                 'automated_savings_settings_read', 'SELECT'),
                ('automated_savings_settings',
                 'automated_savings_settings_insert', 'INSERT'),
                ('automated_savings_settings',
                 'automated_savings_settings_update', 'UPDATE'),
                ('automated_savings_warehouses',
                 'automated_savings_warehouses_read', 'SELECT'),
                ('automated_savings_warehouses',
                 'automated_savings_warehouses_insert', 'INSERT'),
                ('automated_savings_warehouses',
                 'automated_savings_warehouses_update', 'UPDATE'),
                ('automated_savings_events',
                 'automated_savings_events_read', 'SELECT')
        ) expected(tablename, policyname, cmd)
          on expected.tablename = actual.tablename
         and expected.policyname = actual.policyname
         and expected.cmd = actual.cmd
        where actual.schemaname = 'public'
          and actual.tablename in (
              'automated_savings_settings',
              'automated_savings_warehouses',
              'automated_savings_events'
          )
          and expected.policyname is null
    ) then
        raise exception 'unexpected automated savings policy present';
    end if;

    select array_agg(
        table_name::text || ':' || privilege_type::text
        order by table_name, privilege_type
    )
    into v_auth_grants
    from information_schema.role_table_grants
    where table_schema = 'public'
      and grantee = 'authenticated'
      and table_name in (
          'automated_savings_settings',
          'automated_savings_warehouses',
          'automated_savings_events'
      );
    if v_auth_grants <> array[
        'automated_savings_events:SELECT',
        'automated_savings_settings:INSERT',
        'automated_savings_settings:SELECT',
        'automated_savings_settings:UPDATE',
        'automated_savings_warehouses:INSERT',
        'automated_savings_warehouses:SELECT',
        'automated_savings_warehouses:UPDATE'
    ]::text[] then
        raise exception 'unexpected authenticated table grants: %', v_auth_grants;
    end if;

    select array_agg(
        table_name::text || ':' || privilege_type::text
        order by table_name, privilege_type
    )
    into v_service_grants
    from information_schema.role_table_grants
    where table_schema = 'public'
      and grantee = 'service_role'
      and table_name in (
          'automated_savings_settings',
          'automated_savings_warehouses',
          'automated_savings_events'
      );
    if v_service_grants <> array[
        'automated_savings_events:INSERT',
        'automated_savings_events:SELECT',
        'automated_savings_settings:INSERT',
        'automated_savings_settings:SELECT',
        'automated_savings_settings:UPDATE',
        'automated_savings_warehouses:DELETE',
        'automated_savings_warehouses:INSERT',
        'automated_savings_warehouses:SELECT',
        'automated_savings_warehouses:UPDATE'
    ]::text[] then
        raise exception 'unexpected service_role table grants: %', v_service_grants;
    end if;

    if not has_sequence_privilege(
        'service_role',
        'public.automated_savings_events_id_seq',
        'USAGE'
    ) or not has_sequence_privilege(
        'service_role',
        'public.automated_savings_events_id_seq',
        'SELECT'
    ) or has_sequence_privilege(
        'service_role',
        'public.automated_savings_events_id_seq',
        'UPDATE'
    ) or has_sequence_privilege(
        'anon',
        'public.automated_savings_events_id_seq',
        'USAGE, SELECT, UPDATE'
    ) or has_sequence_privilege(
        'authenticated',
        'public.automated_savings_events_id_seq',
        'USAGE, SELECT, UPDATE'
    ) then
        raise exception 'unexpected automated savings event sequence grants';
    end if;

    if exists (
        select 1
        from information_schema.role_table_grants
        where table_schema = 'public'
          and grantee = 'anon'
          and table_name in (
              'automated_savings_settings',
              'automated_savings_warehouses',
              'automated_savings_events'
          )
    ) then
        raise exception 'anonymous role has automated savings table grants';
    end if;

    if exists (
        select 1
        from information_schema.routine_privileges
        where routine_schema = 'public'
          and routine_name in (
              'automated_savings_upsert_enrollment',
              'automated_savings_disable_enrollment',
              'automated_savings_authorize_suspend',
              'automated_savings_delete_stale_enrollment',
              'automated_savings_worker_tenants'
          )
          and grantee in ('PUBLIC', 'anon', 'authenticated')
    ) then
        raise exception 'worker RPC is executable by a non-worker role';
    end if;

    if exists (
        select 1
        from information_schema.routine_privileges
        where routine_schema = 'public'
          and routine_name in (
              'automated_savings_upsert_enrollment',
              'automated_savings_disable_enrollment',
              'automated_savings_authorize_suspend',
              'automated_savings_delete_stale_enrollment',
              'automated_savings_worker_tenants'
          )
          and grantee not in ('postgres', 'service_role')
    ) then
        raise exception 'worker RPC has an unexpected execute grantee';
    end if;

    if exists (
        select 1
        from pg_catalog.pg_proc p
        join pg_catalog.pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
              'automated_savings_upsert_enrollment',
              'automated_savings_disable_enrollment',
              'automated_savings_authorize_suspend',
              'automated_savings_delete_stale_enrollment',
              'automated_savings_worker_tenants'
          )
          and (
              p.prosecdef
              or p.proconfig is null
              or not exists (
                  select 1
                  from unnest(p.proconfig) setting
                  where setting in ('search_path=', 'search_path=""')
              )
          )
    ) then
        raise exception 'worker RPC is not SECURITY INVOKER with empty search_path';
    end if;

    -- Total-count guard: catches any extra automated_savings_ overload the
    -- exact-signature check below would otherwise miss.
    select count(*) into v_rpc_signature_count
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and left(p.proname, length('automated_savings_')) = 'automated_savings_';
    if v_rpc_signature_count <> 5 then
        raise exception 'unexpected automated savings RPC signature or overload';
    end if;

    if exists (
        select 1
        from (
            values
                ('automated_savings_upsert_enrollment',
                 'uuid, text, boolean, timestamp with time zone'),
                ('automated_savings_disable_enrollment', 'uuid, text'),
                ('automated_savings_authorize_suspend',
                 'uuid, text, timestamp with time zone, timestamp with time zone'),
                ('automated_savings_delete_stale_enrollment',
                 'uuid, text, timestamp with time zone, timestamp with time zone'),
                ('automated_savings_worker_tenants', '')
        ) expected(name, arguments)
        where not exists (
            select 1
            from pg_catalog.pg_proc p
            join pg_catalog.pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public'
              and p.proname = expected.name
              and pg_catalog.oidvectortypes(p.proargtypes) = expected.arguments
        )
    ) then
        raise exception 'unexpected automated savings RPC signature or overload';
    end if;

    if to_regclass('public.automated_savings_restore_intents') is not null then
        raise exception 'legacy restore-intent table still exists';
    end if;

    if exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name in ('automated_savings_warehouses', 'automated_savings_events')
          and column_name in (
              'managed_auto_suspend', 'stored_default_auto_suspend', 'drift_state',
              'drifted_value', 'sentinel_confirmed', 'cooldown_ts', 'cycle_id',
              'from_value', 'to_value'
          )
    ) then
        raise exception 'legacy sentinel column still exists';
    end if;
end;
$$;

set local role authenticated;
select set_config(
    'request.jwt.claims',
    '{"sub":"20000000-0000-0000-0000-000000000001","role":"authenticated"}',
    true
);
select set_config(
    'request.jwt.claim.sub',
    '20000000-0000-0000-0000-000000000001',
    true
);
do $$
declare
    v_count integer;
    v_affected integer;
begin
    if auth.uid() is distinct from
       '20000000-0000-0000-0000-000000000001'::uuid then
        raise exception 'member JWT did not establish auth.uid()';
    end if;

    select count(*) into v_count from public.automated_savings_settings;
    if v_count <> 1 then
        raise exception 'org A member did not see exactly its settings row';
    end if;

    select count(*) into v_count from public.automated_savings_warehouses;
    if v_count <> 1 then
        raise exception 'org A member did not see exactly its enrollment row';
    end if;

    select count(*) into v_count from public.automated_savings_events;
    if v_count <> 1 then
        raise exception 'org A member did not see exactly its event row';
    end if;

    select count(*) into v_count
    from public.automated_savings_settings
    where organization_id = '10000000-0000-0000-0000-000000000002';
    if v_count <> 0 then
        raise exception 'org A member could read org B settings';
    end if;

    select count(*) into v_count
    from public.automated_savings_warehouses
    where organization_id = '10000000-0000-0000-0000-000000000002';
    if v_count <> 0 then
        raise exception 'org A member could read org B enrollment';
    end if;

    select count(*) into v_count
    from public.automated_savings_events
    where organization_id = '10000000-0000-0000-0000-000000000002';
    if v_count <> 0 then
        raise exception 'org A member could read org B events';
    end if;

    update public.automated_savings_settings
    set grant_present = false
    where organization_id = '10000000-0000-0000-0000-000000000001';
    get diagnostics v_affected = row_count;
    if v_affected <> 0 then
        raise exception 'ordinary member updated org A settings';
    end if;

    update public.automated_savings_warehouses
    set enabled = false
    where organization_id = '10000000-0000-0000-0000-000000000001'
      and warehouse_name = 'DIRECT_TEST_WH';
    get diagnostics v_affected = row_count;
    if v_affected <> 0 then
        raise exception 'ordinary member updated org A enrollment';
    end if;

    update public.automated_savings_settings
    set grant_present = false
    where organization_id = '10000000-0000-0000-0000-000000000002';
    get diagnostics v_affected = row_count;
    if v_affected <> 0 then
        raise exception 'org A member updated org B settings';
    end if;

    update public.automated_savings_warehouses
    set enabled = false
    where organization_id = '10000000-0000-0000-0000-000000000002'
      and warehouse_name = 'DIRECT_TEST_WH_B';
    get diagnostics v_affected = row_count;
    if v_affected <> 0 then
        raise exception 'org A member updated org B enrollment';
    end if;

    begin
        insert into public.automated_savings_warehouses (
            organization_id, warehouse_name, enabled, warehouse_created_on
        ) values (
            '10000000-0000-0000-0000-000000000001',
            'MEMBER_DENIED_WH',
            true,
            '2026-01-01 00:00:00+00'
        );
        raise exception 'ordinary member inserted org A enrollment';
    exception when insufficient_privilege then
        null;
    end;

    begin
        insert into public.automated_savings_warehouses (
            organization_id, warehouse_name, enabled, warehouse_created_on
        ) values (
            '10000000-0000-0000-0000-000000000002',
            'MEMBER_CROSS_ORG_WH',
            true,
            '2026-01-01 00:00:00+00'
        );
        raise exception 'org A member inserted org B enrollment';
    exception when insufficient_privilege then
        null;
    end;

    begin
        perform public.automated_savings_disable_enrollment(
            '10000000-0000-0000-0000-000000000001',
            'DIRECT_TEST_WH'
        );
        raise exception 'authenticated disable RPC call was permitted';
    exception when insufficient_privilege then
        null;
    end;

    begin
        delete from public.automated_savings_warehouses
        where organization_id = '10000000-0000-0000-0000-000000000001'
          and warehouse_name = 'DIRECT_TEST_WH';
        raise exception 'authenticated enrollment delete was permitted';
    exception when insufficient_privilege then
        null;
    end;

    begin
        insert into public.automated_savings_events (
            organization_id, warehouse_name, action, reason, observed_state,
            observed_running, observed_queued, observed_quiescing,
            observed_resumed_on, observed_at
        ) values (
            '10000000-0000-0000-0000-000000000001', 'DIRECT_TEST_WH',
            'suspend', 'idle', 'STARTED', 0, 0, 0,
            '2026-01-01 00:02:00+00', '2026-01-01 00:03:00+00'
        );
        raise exception 'authenticated event insert was permitted';
    exception when insufficient_privilege then
        null;
    end;
end;
$$;
reset role;
select set_config('request.jwt.claims', '{}', true);
select set_config('request.jwt.claim.sub', '', true);

delete from public.automated_savings_settings
where organization_id = '10000000-0000-0000-0000-000000000001';

set local role authenticated;
select set_config(
    'request.jwt.claims',
    '{"sub":"20000000-0000-0000-0000-000000000001","role":"authenticated"}',
    true
);
select set_config(
    'request.jwt.claim.sub',
    '20000000-0000-0000-0000-000000000001',
    true
);
do $$
begin
    if auth.uid() is distinct from
       '20000000-0000-0000-0000-000000000001'::uuid then
        raise exception 'member JWT did not establish auth.uid()';
    end if;

    begin
        insert into public.automated_savings_settings (
            organization_id, agreed_at, global_enabled, grant_present
        ) values (
            '10000000-0000-0000-0000-000000000001',
            '2026-01-01 00:00:00+00',
            true,
            true
        );
        raise exception 'ordinary member inserted org A settings';
    exception when insufficient_privilege then
        null;
    end;
end;
$$;
reset role;
select set_config('request.jwt.claims', '{}', true);
select set_config('request.jwt.claim.sub', '', true);

set local role authenticated;
select set_config(
    'request.jwt.claims',
    '{"sub":"20000000-0000-0000-0000-000000000002","role":"authenticated"}',
    true
);
select set_config(
    'request.jwt.claim.sub',
    '20000000-0000-0000-0000-000000000002',
    true
);
do $$
declare
    v_affected integer;
    v_count integer;
begin
    if auth.uid() is distinct from
       '20000000-0000-0000-0000-000000000002'::uuid then
        raise exception 'admin JWT did not establish auth.uid()';
    end if;

    select count(*) into v_count
    from public.automated_savings_settings
    where organization_id = '10000000-0000-0000-0000-000000000002';
    if v_count <> 0 then
        raise exception 'org A admin could read org B settings';
    end if;

    select count(*) into v_count
    from public.automated_savings_warehouses
    where organization_id = '10000000-0000-0000-0000-000000000002';
    if v_count <> 0 then
        raise exception 'org A admin could read org B enrollment';
    end if;

    select count(*) into v_count
    from public.automated_savings_events
    where organization_id = '10000000-0000-0000-0000-000000000002';
    if v_count <> 0 then
        raise exception 'org A admin could read org B events';
    end if;

    insert into public.automated_savings_settings (
        organization_id, agreed_at, global_enabled, grant_present
    ) values (
        '10000000-0000-0000-0000-000000000001',
        '2026-01-01 00:00:00+00',
        true,
        true
    );

    update public.automated_savings_settings
    set grant_present = false
    where organization_id = '10000000-0000-0000-0000-000000000001';
    get diagnostics v_affected = row_count;
    if v_affected <> 1 then
        raise exception 'org A admin could not update org A settings';
    end if;

    insert into public.automated_savings_warehouses (
        organization_id, warehouse_name, enabled, warehouse_created_on
    ) values (
        '10000000-0000-0000-0000-000000000001',
        'ADMIN_INSERT_WH',
        true,
        '2026-01-01 00:00:00+00'
    );

    update public.automated_savings_warehouses
    set enabled = false
    where organization_id = '10000000-0000-0000-0000-000000000001'
      and warehouse_name = 'ADMIN_INSERT_WH';
    get diagnostics v_affected = row_count;
    if v_affected <> 1 then
        raise exception 'org A admin could not update org A enrollment';
    end if;

    update public.automated_savings_settings
    set grant_present = false
    where organization_id = '10000000-0000-0000-0000-000000000002';
    get diagnostics v_affected = row_count;
    if v_affected <> 0 then
        raise exception 'org A admin updated org B settings';
    end if;

    update public.automated_savings_warehouses
    set enabled = false
    where organization_id = '10000000-0000-0000-0000-000000000002'
      and warehouse_name = 'DIRECT_TEST_WH_B';
    get diagnostics v_affected = row_count;
    if v_affected <> 0 then
        raise exception 'org A admin updated org B enrollment';
    end if;

    begin
        insert into public.automated_savings_warehouses (
            organization_id, warehouse_name, enabled, warehouse_created_on
        ) values (
            '10000000-0000-0000-0000-000000000002',
            'ADMIN_CROSS_ORG_WH',
            true,
            '2026-01-01 00:00:00+00'
        );
        raise exception 'org A admin inserted org B enrollment';
    exception when insufficient_privilege then
        null;
    end;

    begin
        delete from public.automated_savings_warehouses
        where organization_id = '10000000-0000-0000-0000-000000000001'
          and warehouse_name = 'ADMIN_INSERT_WH';
        raise exception 'org A admin deleted enrollment';
    exception when insufficient_privilege then
        null;
    end;

    begin
        insert into public.automated_savings_events (
            organization_id, warehouse_name, action, reason, observed_state,
            observed_running, observed_queued, observed_quiescing,
            observed_resumed_on, observed_at
        ) values (
            '10000000-0000-0000-0000-000000000001', 'DIRECT_TEST_WH',
            'suspend', 'idle', 'STARTED', 0, 0, 0,
            '2026-01-01 00:02:00+00', '2026-01-01 00:03:00+00'
        );
        raise exception 'org A admin inserted event';
    exception when insufficient_privilege then
        null;
    end;
end;
$$;
reset role;
select set_config('request.jwt.claims', '{}', true);
select set_config('request.jwt.claim.sub', '', true);

set local role anon;
do $$
begin
    begin
        perform public.automated_savings_disable_enrollment(
            '10000000-0000-0000-0000-000000000001',
            'DIRECT_TEST_WH'
        );
        raise exception 'anonymous disable RPC call was permitted';
    exception when insufficient_privilege then
        null;
    end;
end;
$$;
reset role;

set local role service_role;
do $$
declare
    v_row public.automated_savings_warehouses%rowtype;
begin
    select * into strict v_row
    from public.automated_savings_warehouses
    where organization_id = '10000000-0000-0000-0000-000000000001'
      and warehouse_name = 'DIRECT_TEST_WH';

    if public.automated_savings_delete_stale_enrollment(
        v_row.organization_id,
        v_row.warehouse_name,
        null,
        v_row.updated_at
    ) then
        raise exception 'null identity deleted enrollment';
    end if;

    if public.automated_savings_delete_stale_enrollment(
        v_row.organization_id,
        v_row.warehouse_name,
        v_row.warehouse_created_on,
        '2000-01-01 00:00:00+00'
    ) then
        raise exception 'stale version deleted enrollment';
    end if;

    if not public.automated_savings_delete_stale_enrollment(
        v_row.organization_id,
        v_row.warehouse_name,
        v_row.warehouse_created_on,
        v_row.updated_at
    ) then
        raise exception 'exact identity/version did not delete enrollment';
    end if;
end;
$$;
reset role;

select 'AUTOMATED SAVINGS DIRECT SCHEMA OK';

rollback;
