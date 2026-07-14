# Direct Warehouse Suspension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unreleased `AUTO_SUSPEND=1` prototype with a greenfield direct-only Automated Savings worker that safely issues `ALTER WAREHOUSE … SUSPEND`.

**Architecture:** Each tenant cycle parses one `SHOW WAREHOUSES` snapshot including quiescing and mandatory identity, invalidates stale enrollments, evaluates fail-closed eligibility, re-authorizes against current settings/enrollment, and issues one direct suspend. The 62-second resume floor is the only time gate; errors use retry backoff and no cooldown is persisted. The final schema stores only settings, enrollment identity, and direct events.

**Tech Stack:** Python 3.12, Snowflake Connector for Python, pytest/pytest-asyncio, FastAPI/Pydantic, Supabase/Postgres/PostgREST, Next.js 16/React 18/TypeScript/Vitest/Testing Library.

---

## Final file map

- Create `docs/superpowers/plans/2026-07-13-direct-suspend-spike-notes.md` for the isolated availability/error evidence.
- Rewrite `supabase/migrations/202607120001_automated_savings.sql` as the final direct-only schema.
- Delete six unreleased sentinel follow-up migrations listed in Task 2.
- Delete `apps/auto-savings/src/auto_savings/reconcile.py` and `apps/auto-savings/tests/test_reconcile.py`.
- Modify worker decision, snapshot, session, engine, store, config, tenant loop, and their focused tests.
- Modify API route/store/warehouse view and tests to enroll identity only and expose live `AUTO_SUSPEND` read-only.
- Modify the web API contract/table/opt-in copy and tests to remove managed-default/drift/reconcile behavior.
- Modify `AGENTS.md` and both Automated Savings guides to describe only direct suspension.

### Task 1: Complete the isolated Snowflake go/no-go spike

**Files:**
- Create: `docs/superpowers/plans/2026-07-13-direct-suspend-spike-notes.md`
- Reference: `docs/superpowers/specs/2026-07-13-direct-warehouse-suspend-design.md`

- [x] **Step 1: Prepare an authorized development warehouse**

The spike used authorized `DEV_WH` (`STANDARD`, `X-Small`,
`AUTO_SUSPEND=60`, `AUTO_RESUME=true`), connector 3.12.4, and three independent
sessions. No production tenant discovery was invoked. The user authorized
leaving the warehouse in its final observed state; the harness closed all
sessions and left no owned query running.

- [x] **Step 2: Run the availability cases from independent sessions**

```sql
alter warehouse "DIRECT_SUSPEND_SPIKE" resume;
select system$wait(30);
alter warehouse "DIRECT_SUSPEND_SPIKE" suspend;
```

The authoritative report records Cases A–E using a real uncached compute query.
`SYSTEM$WAIT` did not expose the real `SUSPENDING` transition, and `SELECT 1`
did not force auto-resume. Use a real compute workload for future transition or
resume smoke tests.

- [x] **Step 3: Capture real `90064` connector data**

```python
except snowflake.connector.errors.Error as exc:
    observation = {
        "type": type(exc).__name__,
        "errno": exc.errno,
        "sqlstate": exc.sqlstate,
        "message": exc.msg,
        "warehouse_state": show_warehouse_state(),
    }
```

Reissued suspension during transition and after observed suspension both
returned `errno=90064` with Snowflake's sanitized
`Invalid state … cannot be suspended` message. The supplied sanitized evidence
did not retain connector exception class or SQLSTATE; do not invent them or
label the outcome “already suspended.” The implementation contract remains
`UNKNOWN_IDEMPOTENT`: no event, healthy session retained, retry backoff.

- [x] **Step 4: Write the explicit decision**

The report records `**Decision: GO.**` Every measured running/race-window query
completed without error or abort. The observed idle `quiescing=''` encoding is
normalized to zero, its dedicated regression test passes, and full verification
passes.

- [ ] **Step 5: Commit the spike evidence**

```bash
rtk git add docs/superpowers/plans/2026-07-13-direct-suspend-spike-notes.md
rtk git commit -m "docs: record direct suspend race spike"
```

### Task 2: Consolidate the unreleased schema to direct-only state

**Files:**
- Rewrite: `supabase/migrations/202607120001_automated_savings.sql`
- Delete: `supabase/migrations/202607130001_automated_savings_upsert_enrollment_fn.sql`
- Delete: `supabase/migrations/202607130002_automated_savings_baseline_resumed_on.sql`
- Delete: `supabase/migrations/20260713223505_automated_savings_sentinel_confirmed.sql`
- Delete: `supabase/migrations/20260714002106_automated_savings_intent_safety.sql`
- Delete: `supabase/migrations/20260714005623_automated_savings_atomic_guards.sql`
- Delete: `supabase/migrations/20260714010713_automated_savings_activity_unknown_reason.sql`
- Rewrite: `apps/api/tests/test_automated_savings_migration.py`
- Create: `supabase/tests/automated_savings_direct.sql`

- [ ] **Step 1: Write failing final-shape tests**

Replace sentinel migration tests with:

```python
MIGRATION = (MIGRATIONS_DIR / "202607120001_automated_savings.sql").read_text()


def test_direct_schema_has_only_final_tables():
    assert "create table automated_savings_settings" in MIGRATION
    assert "create table automated_savings_warehouses" in MIGRATION
    assert "create table automated_savings_events" in MIGRATION
    assert "automated_savings_restore_intents" not in MIGRATION


def test_direct_enrollment_has_mandatory_identity_without_legacy_columns():
    assert "warehouse_created_on timestamptz not null" in MIGRATION
    for legacy in (
        "managed_auto_suspend", "stored_default_auto_suspend",
        "drift_state", "drifted_value", "sentinel_confirmed", "cooldown_ts",
    ):
        assert legacy not in MIGRATION


def test_direct_event_shape_is_suspend_only():
    assert "action text not null check (action = 'suspend')" in MIGRATION
    assert "reason text not null check (reason = 'idle')" in MIGRATION
    assert "observed_started_clusters integer" in MIGRATION
    assert "observed_min_cluster_count integer" in MIGRATION
    assert "observed_max_cluster_count integer" in MIGRATION
    assert "observed_quiescing integer not null check (observed_quiescing >= 0)" in MIGRATION
    assert "observed_state text not null" in MIGRATION
    assert "observed_running integer not null check (observed_running >= 0)" in MIGRATION
    assert "observed_queued integer not null check (observed_queued >= 0)" in MIGRATION
    assert "observed_resumed_on timestamptz not null" in MIGRATION
    for legacy in ("cycle_id", "from_value", "to_value", "set_sentinel", "restore"):
        assert legacy not in MIGRATION


def test_direct_schema_has_guarded_worker_rpcs():
    assert "function automated_savings_upsert_enrollment(" in MIGRATION
    assert "function automated_savings_authorize_suspend(" in MIGRATION
    assert "function automated_savings_delete_stale_enrollment(" in MIGRATION
    assert "function automated_savings_worker_tenants()" in MIGRATION
    assert "s.global_enabled" in MIGRATION
    assert "security invoker" in MIGRATION
    assert "set search_path = ''" in MIGRATION


def test_direct_schema_keeps_explicit_rls_and_version_trigger():
    assert "set_automated_savings_settings_updated_at" in MIGRATION
    assert "create trigger set_automated_savings_warehouses_updated_at" in MIGRATION
    assert "automated_savings_warehouses_read" in MIGRATION
    assert "automated_savings_warehouses_insert" in MIGRATION
    assert "automated_savings_warehouses_update" in MIGRATION
    assert "for delete to authenticated" not in MIGRATION
    assert "automated_savings_events_write" not in MIGRATION
```

- [ ] **Step 2: Run the migration tests and verify RED**

Run: `rtk uv run --directory apps/api pytest tests/test_automated_savings_migration.py -q`

Expected: FAIL because the base migration still contains restore/sentinel/default/drift state.

- [ ] **Step 3: Rewrite the base migration**

Keep settings columns and existing RLS intent. Define the final warehouse/event tables as:

```sql
create table automated_savings_warehouses (
    organization_id uuid not null references organizations(id) on delete cascade,
    warehouse_name text not null,
    enabled boolean not null default false,
    warehouse_created_on timestamptz not null,
    updated_at timestamptz not null default now(),
    primary key (organization_id, warehouse_name)
);

create table automated_savings_events (
    id bigint generated always as identity primary key,
    organization_id uuid not null references organizations(id) on delete cascade,
    warehouse_name text not null,
    action text not null check (action = 'suspend'),
    reason text not null check (reason = 'idle'),
    observed_state text not null,
    observed_running integer not null check (observed_running >= 0),
    observed_queued integer not null check (observed_queued >= 0),
    observed_quiescing integer not null check (observed_quiescing >= 0),
    observed_resumed_on timestamptz not null,
    observed_started_clusters integer,
    observed_min_cluster_count integer,
    observed_max_cluster_count integer,
    observed_at timestamptz not null,
    created_at timestamptz not null default now()
);
```

Explicitly install member-select plus admin insert/update policies; do not use `FOR ALL`. Keep the `set_automated_savings_warehouses_updated_at` trigger. Add no authenticated enrollment-delete or event-mutation policy.

```sql
create policy automated_savings_settings_read
    on automated_savings_settings for select to authenticated
    using (is_organization_member(organization_id));
create policy automated_savings_settings_insert
    on automated_savings_settings for insert to authenticated
    with check (is_organization_admin(organization_id));
create policy automated_savings_settings_update
    on automated_savings_settings for update to authenticated
    using (is_organization_admin(organization_id))
    with check (is_organization_admin(organization_id));
create policy automated_savings_warehouses_read
    on automated_savings_warehouses for select to authenticated
    using (is_organization_member(organization_id));
create policy automated_savings_warehouses_insert
    on automated_savings_warehouses for insert to authenticated
    with check (is_organization_admin(organization_id));
create policy automated_savings_warehouses_update
    on automated_savings_warehouses for update to authenticated
    using (is_organization_admin(organization_id))
    with check (is_organization_admin(organization_id));
create policy automated_savings_events_read
    on automated_savings_events for select to authenticated
    using (is_organization_member(organization_id));
```

- [ ] **Step 4: Add the final RPCs**

`automated_savings_upsert_enrollment(uuid, text, boolean, timestamptz)` rejects null identity and inserts/updates enabled plus `warehouse_created_on`. `automated_savings_worker_tenants()` returns organizations whose settings are globally enabled and have an enabled enrollment—no union.

The final stateless authorization is:

```sql
create function automated_savings_authorize_suspend(
    p_organization_id uuid,
    p_warehouse_name text,
    p_warehouse_created_on timestamptz,
    p_enrollment_updated_at timestamptz
)
returns boolean
language sql
security invoker
set search_path = ''
as $$
    select exists (
        select 1
        from public.automated_savings_warehouses w
        join public.automated_savings_settings s
          on s.organization_id = w.organization_id
        where w.organization_id = p_organization_id
          and w.warehouse_name = p_warehouse_name
          and s.global_enabled
          and w.enabled
          and w.warehouse_created_on = p_warehouse_created_on
          and w.updated_at = p_enrollment_updated_at
    );
$$;
```

Add `automated_savings_delete_stale_enrollment(uuid, text, timestamptz, timestamptz)` that deletes only when non-null identity and `updated_at` match. Every RPC uses `SECURITY INVOKER`, `SET search_path = ''`, and schema-qualified relations. Revoke each exact signature from `PUBLIC`; grant only to `service_role`.

- [ ] **Step 5: Delete sentinel follow-up migrations and verify GREEN**

Delete the six files named above, then run: `rtk uv run --directory apps/api pytest tests/test_automated_savings_migration.py -q`

Expected: PASS.

- [ ] **Step 6: Add authoritative functional database verification**

Create a transaction-wrapped SQL script that inserts a fixed test organization, enabled settings, and a warehouse with non-null identity. It must use `\gset` plus `DO` assertions to prove: authorization returns true initially; global disable returns false; enrollment disable returns false; stale identity/version return false; updating enrollment advances `updated_at`; stale-enrollment deletion requires exact identity/version; negative activity/quiescing and null decision fields violate event constraints; and `SET LOCAL ROLE authenticated` cannot delete enrollment or insert an event. Query `pg_policies`, `information_schema.role_table_grants`, and `information_schema.routine_privileges` inside the script and raise when policies/grants differ from the final contract. Roll back at the end.

Run after reset:

```bash
rtk proxy psql postgresql://postgres:postgres@127.0.0.1:54322/postgres -v ON_ERROR_STOP=1 -f supabase/tests/automated_savings_direct.sql
```

Expected: `AUTOMATED SAVINGS DIRECT SCHEMA OK`, exit 0, and no persisted fixture rows.

- [ ] **Step 7: Reset and verify every development database**

Inventory every local/shared development Supabase project that applied the old chain. Reset each after checking `rtk npx supabase db reset --help`; if any cannot be reset, stop and create one immediate cleanup migration before implementation continues. Verify applied migration versions, exact tables/columns, `pg_trigger`, `pg_policies`, table grants, exact function signatures/execute grants, and absence of restore/sentinel objects. Functionally call authorization with enabled/disabled/stale-version rows, call guarded stale deletion, attempt an invalid event, and prove authenticated roles cannot delete enrollments or mutate events. Run database advisors when supported. These live checks are authoritative; substring tests are supplemental.

- [ ] **Step 8: Commit**

```bash
rtk git add -A supabase/migrations supabase/tests/automated_savings_direct.sql apps/api/tests/test_automated_savings_migration.py
rtk git commit -m "refactor: consolidate direct suspend schema"
```

### Task 3: Define direct eligibility and nullable cluster audit fields

**Files:**
- Modify: `apps/auto-savings/src/auto_savings/decision.py`
- Modify: `apps/auto-savings/src/auto_savings/warehouse_snapshot.py`
- Modify: `apps/auto-savings/tests/test_decision.py`
- Modify: `apps/auto-savings/tests/test_warehouse_snapshot.py`

- [ ] **Step 1: Write failing truth-table tests**

```python
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"type": "SNOWPARK-OPTIMIZED"}, False),
        ({"state": "SUSPENDING"}, False),
        ({"running": 1}, False),
        ({"queued": 1}, False),
        ({"quiescing": 1}, False),
        ({"auto_resume": False}, False),
        ({"resumed_on": None}, False),
        ({"resumed_on": NOW - timedelta(seconds=61)}, False),
    ],
)
def test_direct_suspend_truth_table(overrides, expected):
    assert _decide(_wh(**overrides)) is expected


@pytest.mark.parametrize(
    "cluster_values",
    [
        {"started_clusters": 3, "min_cluster_count": 1, "max_cluster_count": 4},
        {"started_clusters": None, "min_cluster_count": None, "max_cluster_count": None},
    ],
)
def test_multi_cluster_and_unknown_counts_are_eligible(cluster_values):
    assert _decide(_wh(**cluster_values)) is True
```

Keep malformed/missing running, queued, quiescing, identity, and resumed timestamp tests failing closed. Replace malformed cluster tests so they assert nullable audit values without blocking eligibility. Add cases proving `auto_suspend` values `None`, `0`, `1`, and `30` do not affect the decision.

- [ ] **Step 2: Run tests and verify RED**

Run: `rtk uv run --directory apps/auto-savings pytest tests/test_decision.py tests/test_warehouse_snapshot.py -q`

Expected: FAIL because quiescing/mandatory identity are not modeled and cluster counts, drift, intents, and `AUTO_SUSPEND` still influence eligibility.

- [ ] **Step 3: Implement the final decision**

```python
def should_suspend(
    snapshot: WarehouseSnapshot,
    *,
    now: datetime,
    uptime_floor_seconds: int,
    enrolled_created_on: datetime,
) -> bool:
    if snapshot.type != "STANDARD" or snapshot.state != "STARTED":
        return False
    if not snapshot.activity_valid or not snapshot.quiescing_valid:
        return False
    if snapshot.created_on is None or snapshot.created_on != enrolled_created_on:
        return False
    uptime = uptime_seconds(snapshot, now=now)
    if uptime is None or uptime < uptime_floor_seconds:
        return False
    if snapshot.running != 0 or snapshot.queued != 0:
        return False
    if snapshot.quiescing != 0:
        return False
    if not snapshot.auto_resume:
        return False
    return True
```

Add `quiescing: int` plus `quiescing_valid: bool`. Accept finite nonnegative
integers and normalize Snowflake's observed idle empty string (`''`) to zero.
Add a regression case proving an otherwise eligible empty-string row remains
eligible. Missing (`None`/absent), malformed, negative, fractional, and
non-finite quiescing values still fail closed. Make all three cluster fields
`int | None`. Missing/malformed counts become `None`; they are never read by
`should_suspend`. Keep live `auto_suspend: int | None` for the read-only API
view, not worker eligibility. Enrollment identity is non-null; missing snapshot
identity fails closed.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same targeted command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add apps/auto-savings/src/auto_savings/decision.py apps/auto-savings/src/auto_savings/warehouse_snapshot.py apps/auto-savings/tests/test_decision.py apps/auto-savings/tests/test_warehouse_snapshot.py
rtk git commit -m "feat: define direct suspend eligibility"
```

### Task 4: Implement explicit direct command outcomes

**Files:**
- Modify: `apps/auto-savings/src/auto_savings/snowflake_session.py`
- Modify: `apps/auto-savings/tests/test_snowflake_session.py`

- [ ] **Step 1: Write failing session tests**

```python
def test_suspend_quotes_identifier_and_returns_accepted():
    session, cursor = _session_with_cursor()
    result = session.suspend_warehouse('weird"name')
    assert result == SuspendResult(SuspendOutcome.ACCEPTED)
    cursor.execute.assert_called_once_with('ALTER WAREHOUSE "weird""name" SUSPEND')


def test_90064_is_unknown_without_claiming_success():
    session, cursor = _session_with_cursor()
    cursor.execute.side_effect = snowflake.connector.errors.ProgrammingError(
        msg="observed race", errno=90064, sqlstate="57014"
    )
    result = session.suspend_warehouse("WH1")
    assert result.outcome is SuspendOutcome.UNKNOWN_IDEMPOTENT
    assert result.connector_error == ConnectorErrorMetadata(
        error_type="ProgrammingError", errno=90064,
        sqlstate="57014", message="observed race",
    )


def test_connection_failure_propagates_as_ambiguous():
    session, cursor = _session_with_cursor()
    cursor.execute.side_effect = snowflake.connector.errors.OperationalError("lost")
    with pytest.raises(snowflake.connector.errors.OperationalError):
        session.suspend_warehouse("WH1")
```

Retain cursor-close failure and reconnect lifecycle tests.
Add invalid-construction tests proving an accepted result rejects connector metadata and an unknown result requires it.

- [ ] **Step 2: Run tests and verify RED**

Run: `rtk uv run --directory apps/auto-savings pytest tests/test_snowflake_session.py -q`

Expected: FAIL because only `alter_auto_suspend` exists.

- [ ] **Step 3: Implement `SuspendOutcome` and `suspend_warehouse`**

Use immutable enum values `ACCEPTED` and `UNKNOWN_IDEMPOTENT`, plus a frozen `SuspendResult` carrying the outcome and optional frozen `ConnectorErrorMetadata`. Execute the quoted direct SQL. Catch Snowflake connector errors only to convert `errno == 90064`; sanitize and preserve the actual connector type/errno/SQLSTATE/message on the returned result as well as the session log. Re-raise all other errors. Ignore cursor-close failure after an accepted command. Delete `alter_auto_suspend` entirely. Do not attach mutable metadata to enum members.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add apps/auto-savings/src/auto_savings/snowflake_session.py apps/auto-savings/tests/test_snowflake_session.py
rtk git commit -m "feat: add direct suspend command outcomes"
```

### Task 5: Replace the worker store with final direct state

**Files:**
- Modify: `apps/auto-savings/src/auto_savings/store.py`
- Modify: `apps/auto-savings/tests/test_store.py`
- Delete: `apps/auto-savings/src/auto_savings/reconcile.py`
- Delete: `apps/auto-savings/tests/test_reconcile.py`

- [ ] **Step 1: Write failing direct-store tests**

```python
def test_authorize_suspend_accepts_only_current_enabled_state():
    store = _seed_direct_store()
    authorized = store.authorize_suspend(
        "org-1", "WH1", warehouse_created_on=CREATED_ON,
        enrollment_updated_at=NOW,
    )
    assert authorized is True


@pytest.mark.parametrize("change", ["kill_switch", "disabled", "identity", "version"])
def test_authorize_suspend_rejects_stale_or_disabled_state(change):
    store = _seed_direct_store(change=change)
    assert store.authorize_suspend(
        "org-1", "WH1", warehouse_created_on=CREATED_ON,
        enrollment_updated_at=NOW,
    ) is False


def test_direct_event_payload_has_no_setting_or_cycle_fields():
    store = _recording_supabase_store()
    store.record_event(_direct_event())
    payload = store.last_json
    assert payload["action"] == "suspend" and payload["reason"] == "idle"
    assert "cycle_id" not in payload
    assert "from_value" not in payload
    assert "to_value" not in payload
```

Add transport/error-shape tests for authorization and conditional stale-enrollment deletion. Add a functional Supabase test proving the `updated_at` trigger changes the version on enrollment update.

- [ ] **Step 2: Run tests and verify RED**

Run: `rtk uv run --directory apps/auto-savings pytest tests/test_store.py -q`

Expected: FAIL because store types/methods still model restore intents and sentinel events.

- [ ] **Step 3: Delete sentinel store machinery**

Delete `RestoreIntent`; managed/stored default, cooldown, and drift fields; `enqueue_sentinel`; `confirm_sentinel`; `list_intents`; `delete_intent`; both cleanup-intent methods; `set_cooldown`; `mark_drifted`; `mark_unsupported`; sentinel/reapply parsers; and their Supabase URLs. Delete `reconcile.py` and its test file.

Keep `EnrollmentRow` with organization, warehouse, enabled, mandatory `warehouse_created_on`, and `updated_at`. Keep settings. Define `SavingsEvent` with non-null state/activity/quiescing/resume observations and nullable cluster fields. Add `authorize_suspend` and `delete_stale_enrollment` backed by the final RPCs. Keep `worker_tenants` and direct event insert.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `rtk uv run --directory apps/auto-savings pytest tests/test_store.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add -A apps/auto-savings/src/auto_savings apps/auto-savings/tests
rtk git commit -m "refactor: remove sentinel worker state"
```

### Task 6: Build the direct-only engine

**Files:**
- Rewrite: `apps/auto-savings/src/auto_savings/engine.py`
- Rewrite: `apps/auto-savings/tests/test_engine.py`

- [ ] **Step 1: Write failing authorization, outcome, and restart tests**

```python
def test_authorizes_immediately_before_suspend_and_audits_acceptance():
    store = _tracking_direct_store()

    def suspend(name):
        assert store.operations == ["authorize_suspend"]
        store.operations.append("suspend")
        return SuspendResult(SuspendOutcome.ACCEPTED)

    run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW, suspend=suspend)
    assert store.operations == ["authorize_suspend", "suspend", "record_event"]


def test_failed_or_stale_authorization_prevents_command():
    store = _tracking_direct_store(authorized=False)
    suspend = Mock()
    run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW, suspend=suspend)
    suspend.assert_not_called()


def test_unknown_90064_requests_backoff_without_mutation_event():
    store = _tracking_direct_store()
    result = run_cycle(
        "org-1", rows=_rows(), store=store, config=CONFIG, now=NOW,
        suspend=lambda name: SuspendResult(
            SuspendOutcome.UNKNOWN_IDEMPOTENT,
            ConnectorErrorMetadata(
                error_type="ProgrammingError", errno=90064,
                sqlstate="57014", message="observed race",
            ),
        ),
    )
    assert result is CycleResult.RETRY_BACKOFF
    assert store.events == []


def test_ambiguous_connection_error_writes_no_event_and_propagates():
    store = _tracking_direct_store()
    with pytest.raises(OperationalError):
        run_cycle(
            "org-1", rows=_rows(), store=store, config=CONFIG, now=NOW,
            suspend=lambda name: (_ for _ in ()).throw(OperationalError("lost")),
        )
    assert store.events == []


def test_disable_after_authorization_does_not_cancel_in_flight_command():
    store = _tracking_direct_store(disable_immediately_after_authorize=True)
    suspend = Mock(return_value=SuspendResult(SuspendOutcome.ACCEPTED))
    run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW, suspend=suspend)
    suspend.assert_called_once_with("WH1")


def test_disable_before_authorization_prevents_command():
    store = _tracking_direct_store(authorized=False)
    suspend = Mock()
    run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW, suspend=suspend)
    suspend.assert_not_called()
```

Add these behavioral tests:

- audit failure after acceptance does not reissue within the cycle;
- after restart, transitional/suspended/nonzero-quiescing snapshots skip, while a still-eligible `STARTED` snapshot may idempotently reissue;
- disabling before authorization returns false and prevents the command;
- disabling after authorization does not cancel the already authorized command, documenting the exact kill-switch contract;
- busy/malformed/unsupported/missing-identity snapshots never authorize;
- multi-cluster eligible snapshots authorize;
- identity mismatch deletes the stale enrollment, while missing identity fails closed without deletion or suspend;
- every request/outcome log shares an attempt UUID;
- an unknown attempt UUID appears on the next matching `SHOW WAREHOUSES` snapshot log and is then removed from the in-memory observability map, even if its enrollment disappeared.
- contradictory duplicate same-name snapshots fail closed in either row order without authorization, stale deletion, or suspension, while other unique names proceed;
- prior unknown names missing from a later `SHOW WAREHOUSES` result emit a correlated safe retirement record and are removed so churn cannot grow the map without bound;
- accepted, unknown, and error outcome records have the same exact connector metadata keys, with explicit nulls when absent, and metric records have only their exact named label schema plus attempt ID.

- [ ] **Step 2: Run tests and verify RED**

Run: `rtk uv run --directory apps/auto-savings pytest tests/test_engine.py -q`

Expected: FAIL because the engine still reconciles and creates a sentinel intent.

- [ ] **Step 3: Implement the direct cycle**

Define `run_cycle(org_id: str, *, rows: list[dict], store: Store, config: WorkerConfig, now: datetime, suspend: SuspendWarehouse, unknown_attempts: dict[str, str]) -> CycleResult`. At cycle start, retire and correlate prior unknown names absent from the new parsed snapshot set. Group snapshots by name before enrollment iteration: duplicates are ambiguous and must log then fail closed for that name without authorization, stale deletion, or suspension, independent of row order; unique names still proceed. Log each unique snapshot and consume a matching unknown-attempt entry so vanished enrollments cannot leak observability state. Then, for each top-of-cycle enrollment: find the unique snapshot; fail closed if either identity is missing; on mismatch call guarded `delete_stale_enrollment` and continue; skip disabled/missing/ineligible snapshots; call `authorize_suspend` with exact identity/version immediately before `suspend`; call Snowflake only when authorized. Do not add a second `SHOW`: it cannot close the name-based TOCTOU and contradicts the one-snapshot design. Define `SuspendWarehouse = Callable[[str], SuspendResult]`, `CycleResult.NORMAL`, and `CycleResult.RETRY_BACKOFF`.

On `ACCEPTED`, best-effort insert:

```python
SavingsEvent(
    organization_id=org_id,
    warehouse_name=name,
    action="suspend",
    reason="idle",
    observed_state=snapshot.state,
    observed_running=snapshot.running,
    observed_queued=snapshot.queued,
    observed_quiescing=snapshot.quiescing,
    observed_resumed_on=snapshot.resumed_on,
    observed_started_clusters=snapshot.started_clusters,
    observed_min_cluster_count=snapshot.min_cluster_count,
    observed_max_cluster_count=snapshot.max_cluster_count,
    observed_at=now,
)
```

Catch only `StoreError` around the post-command event insert. On unknown `90064`, put the actual sanitized connector metadata from `SuspendResult` on the correlated outcome/error records, retain the attempt UUID in the supplied in-memory observation map, and return `RETRY_BACKOFF` with no event. On other/ambiguous exceptions, log sanitized metadata and re-raise so the tenant loop closes/reconnects and backs off.

The worker has no metrics exporter. Emit structured `event="metric"` log records as the operational counter source—never fake counters—with exact schemas `auto_savings_suspend_attempt_total{outcome}`, `auto_savings_suspend_error_total{errno,sqlstate}`, and `auto_savings_authorization_total{result}`, each also carrying `attempt_id` and no incidental labels. Emit structured snapshot/request/outcome logs with attempt ID, org, warehouse, state, uptime, running, queued, quiescing, cluster counts, resumed time, outcome, and connector metadata. Accepted, unknown, and error outcomes all include `connector_error_type`, `connector_errno`, `connector_sqlstate`, and `connector_message`, explicitly null when absent. Tests assert exact custom-field schemas and exact metric name/label schemas, not formatted message text. A matching snapshot log consumes the prior unknown attempt ID before enrollment iteration; a missing snapshot emits a correlated retirement record and removes it. This observability-only map never changes eligibility and may be lost on restart. Task 10 documents deployment log aggregation.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add apps/auto-savings/src/auto_savings/engine.py apps/auto-savings/tests/test_engine.py
rtk git commit -m "feat: suspend eligible warehouses directly"
```

### Task 7: Remove sentinel cadence and preserve tenant recovery

**Files:**
- Modify: `apps/auto-savings/src/auto_savings/config.py`
- Modify: `apps/auto-savings/src/auto_savings/tenant_loop.py`
- Modify: `apps/auto-savings/tests/test_config.py`
- Modify: `apps/auto-savings/tests/test_tenant_loop.py`

- [ ] **Step 1: Write failing final-cadence tests**

Update `FakeSession` to expose `suspend_warehouse`. Add a success test asserting the direct command is called and no AUTO_SUSPEND value is passed. Replace fast-poll tests with: `CycleResult.NORMAL` sleeps at `poll_interval_seconds * (0.85 + 0.30 * jitter())`; `CycleResult.RETRY_BACKOFF` sleeps using `next_backoff`, increments the attempt, and does not close the healthy Snowflake session. Verify the tenant loop owns one in-memory unknown-attempt map and passes it into consecutive cycles.

Retain watchdog tests proving: a queued tick cancels; a started failed/timeout tick hard-closes and drains before return; the next tick reconnects; supervisor isolates/restarts tenants; sharding ownership is unchanged.

- [ ] **Step 2: Run tests and verify RED**

Run: `rtk uv run --directory apps/auto-savings pytest tests/test_config.py tests/test_tenant_loop.py -q`

Expected: FAIL because the loop wires `alter_auto_suspend`, expects an intent boolean, and selects fast cadence.

- [ ] **Step 3: Simplify config and loop**

Delete `cooldown_seconds`, `intent_poll_interval_seconds`, `max_intent_hold_ticks`, `orphan_grace_seconds`, and their env parsing/validation. Make `run_cycle`/`run_tenant_once` return `CycleResult`. Wire only `session.suspend_warehouse`. `NORMAL` uses normal jittered cadence. `RETRY_BACKOFF` uses exponential delay without raising or hard-closing. Actual exceptions keep the existing hard-close, guaranteed drain, reconnect, and exponential backoff behavior.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add apps/auto-savings/src/auto_savings/config.py apps/auto-savings/src/auto_savings/tenant_loop.py apps/auto-savings/tests/test_config.py apps/auto-savings/tests/test_tenant_loop.py
rtk git commit -m "refactor: remove sentinel polling cadence"
```

### Task 8: Simplify the API to identity-only enrollment

**Files:**
- Modify: `apps/api/app/routes/automated_savings.py`
- Modify: `apps/api/app/services/automated_savings_store.py`
- Modify: `apps/api/app/services/warehouse_directory.py`
- Modify: `apps/api/tests/test_automated_savings_route.py`
- Modify: `apps/api/tests/test_automated_savings_store.py`
- Modify: `apps/api/tests/test_warehouse_directory.py`

- [ ] **Step 1: Write failing final API tests**

```python
class _CaptureStore:
    def __init__(self, captured):
        self.captured = captured

    def upsert_enrollment(self, organization_id, warehouse_name, **fields):
        self.captured.update(
            organization_id=organization_id,
            warehouse_name=warehouse_name,
            **fields,
        )


def test_enrollment_persists_identity_without_defaults(monkeypatch):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    monkeypatch.setattr(
        automated_savings,
        "capture_warehouse_identity",
        lambda **kwargs: automated_savings.CapturedWarehouseIdentity(
            warehouse_created_on="2026-01-01T00:00:00Z"
        ),
    )
    captured = {}
    monkeypatch.setattr(automated_savings, "_require_store", lambda: _CaptureStore(captured))
    response = TestClient(app).post(
        "/api/automated-savings/org-1/warehouses/WH1/toggle",
        json={"enabled": True},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert set(captured) == {
        "organization_id", "warehouse_name", "enabled", "warehouse_created_on"
    }


def test_management_routes_are_absent():
    paths = {route.path for route in app.routes}
    assert not any(path.endswith("/managed-default") for path in paths)
    assert not any(path.endswith("/reconcile") for path in paths)


def test_enrollment_rejects_missing_warehouse_identity(monkeypatch):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    monkeypatch.setattr(
        automated_savings,
        "capture_warehouse_identity",
        lambda **kwargs: automated_savings.CapturedWarehouseIdentity(
            warehouse_created_on=None
        ),
    )
    response = TestClient(app).post(
        "/api/automated-savings/org-1/warehouses/WH1/toggle",
        json={"enabled": True},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 422
```

In warehouse-directory tests, assert `auto_suspend` and parsed nonnegative `quiescing` are copied from the live row and the response has no managed/stored/drift/cooldown fields. Add one response fixture for each exact status: `unsupported`, `transitioning` (state transition or quiescing greater than zero), and `idle`.

- [ ] **Step 2: Run tests and verify RED**

Run: `rtk uv run --directory apps/api pytest tests/test_automated_savings_route.py tests/test_automated_savings_store.py tests/test_warehouse_directory.py -q`

Expected: FAIL because enrollment captures/rejects defaults and management routes remain.

- [ ] **Step 3: Implement the final API contract**

Rename capture to `capture_warehouse_identity`; read only `created_on` and reject missing warehouse/timestamp with 422. Change store `EnrollmentRow`, selects, parser, and `upsert_enrollment` RPC payload to mandatory identity-only fields. Remove `_SENTINEL_DEFAULTS`, `MANAGED_DEFAULT_FLOOR_SECONDS`, managed/reconcile request models and routes, set-managed/reconcile store methods, reapply URL, and all default/drift/cooldown response fields.

Add `auto_suspend: int | None` and `quiescing: int | None` to `WarehouseView` and `WarehouseResponse`, sourced from current `SHOW WAREHOUSES`. Define the only status values as `unsupported`, `transitioning`, and `idle`. Keep auth/admin enforcement, membership reads, global switch, grant check, AUTO_RESUME status, supported type, enabled, and identity.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add apps/api/app/routes/automated_savings.py apps/api/app/services/automated_savings_store.py apps/api/app/services/warehouse_directory.py apps/api/tests/test_automated_savings_route.py apps/api/tests/test_automated_savings_store.py apps/api/tests/test_warehouse_directory.py
rtk git commit -m "refactor: simplify automated savings enrollment"
```

### Task 9: Remove managed-default and reconcile UI behavior

**Files:**
- Modify: `apps/web/src/lib/automated-savings-api.ts`
- Modify: `apps/web/src/lib/automated-savings-api.test.ts`
- Modify: `apps/web/src/components/automated-savings/warehouse-table.tsx`
- Modify: `apps/web/src/components/automated-savings/warehouse-table.test.tsx`
- Modify: `apps/web/src/components/automated-savings/opt-in-gate.tsx`
- Modify: `apps/web/src/components/automated-savings/opt-in-gate.test.tsx`

- [ ] **Step 1: Write failing parser and behavior tests**

Update raw API fixtures to include `auto_suspend: 300`, `quiescing: 0`, and each backend status (`unsupported`, `transitioning`, `idle`). Assert the parsed public contract equals the complete final `WarehouseRow` object, which necessarily excludes managed/stored/drift/cooldown fields. Delete mutation-contract cases for managed-default and reconcile.

Add behavior assertions that the table displays `300s` as plain text, contains no spinbutton, and contains no reconcile button. Retain meaningful tests for AUTO_RESUME/unsupported/admin toggle guards, mutation failures, enrollment refresh, and double-action protection.

- [ ] **Step 2: Run tests and verify RED**

Run from `apps/web`:

```bash
rtk npx vitest run src/lib/automated-savings-api.test.ts src/components/automated-savings/warehouse-table.test.tsx src/components/automated-savings/opt-in-gate.test.tsx
```

Expected: FAIL because the client/table still model editable managed defaults and drift reconciliation.

- [ ] **Step 3: Implement the read-only direct UI**

Define `SavingsStatus = "idle" | "transitioning" | "unsupported"`. Define `WarehouseRow` with live/display fields, `quiescing`, `autoSuspend`, enabled, and status only. Remove `DriftState`, `ManagedDefaultFloorError`, `setManagedDefault`, and `reconcileWarehouse`. Delete input draft/floor/reconcile state and handlers. Render current `AUTO_SUSPEND` as `—` or `${warehouse.autoSuspend}s`; render `transitioning` consistently for state/quiescing transitions.

Update tooltip/opt-in text to say Greysight requests safe Snowflake suspension after the billing floor. Remove every claim that Greysight lowers or restores `AUTO_SUSPEND`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add apps/web/src/lib/automated-savings-api.ts apps/web/src/lib/automated-savings-api.test.ts apps/web/src/components/automated-savings
rtk git commit -m "feat: present direct suspension controls"
```

### Task 10: Update documentation and verify the greenfield replacement

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/automated-savings.md`
- Modify: `docs/automated-savings-how-it-works.md`

- [ ] **Step 1: Rewrite active documentation**

Document one direct-only path, first eligible snapshot, zero quiescing, mandatory identity, the 62-second uptime floor as the only time gate, multi-cluster caveat, final authorization, explicit disable-after-authorization semantics, error/backoff outcomes, residual name-reuse TOCTOU, operational single-writer deployment, and both supported privilege paths: account-level `MANAGE WAREHOUSES`, or per-warehouse `OPERATE` with required `USAGE` context. Do not claim the smoke run's combined `MODIFY, OPERATE` grant proves `MODIFY` is universally required. Delete active guidance for cooldown, restore intents, sentinels, managed/stored defaults, drift/reapply, hold/resume-aware logic, fast polling, rollout drains, and old-image rollback.

- [ ] **Step 2: Prove sentinel machinery is absent**

```bash
rtk proxy rg -n "enqueue_sentinel|sentinel_confirmed|set_sentinel|restore_intent|managed_auto_suspend|stored_default_auto_suspend|mark_drifted|mark_unsupported|enqueue_reapply|MAX_INTENT_HOLD|INTENT_POLL|COOLDOWN|cooldown_ts|alter_auto_suspend" apps/auto-savings/src apps/api/app/routes/automated_savings.py apps/api/app/services/automated_savings_store.py apps/api/app/services/warehouse_directory.py apps/web/src/lib/automated-savings-api.ts apps/web/src/components/automated-savings AGENTS.md docs/automated-savings.md docs/automated-savings-how-it-works.md supabase/migrations --glob '!**/*.test.*'
```

Expected: no matches in Automated Savings runtime implementation, active guides,
or current migrations. Tests are excluded because intentional negative assertions
name forbidden legacy concepts. Unrelated product features are out of scope. Do
not split, concatenate, or rename forbidden tokens merely to satisfy the scan.

- [ ] **Step 3: Run the full verification matrix**

```bash
rtk uv run --directory apps/auto-savings pytest
rtk uv run --directory apps/auto-savings ruff check src tests
rtk uv run --directory apps/api pytest
rtk uv run --directory shared/connect pytest
rtk npm run test:web
rtk npm run typecheck
rtk npm run lint
rtk git diff --check
```

Expected: all commands PASS.

- [ ] **Step 4: Review invariants before deployment**

Confirm the spike says GO; one direct decision path exists; no mode flag, cooldown, or compatibility state exists; no code issues `SET AUTO_SUSPEND`; zero quiescing and matching mandatory identity gate eligibility; cluster fields are audit-only; authorization rechecks current state; disable-after-authorization semantics are documented; `90064` backs off without closing a healthy session; ambiguous connection outcomes reconnect/back off; attempt logs correlate the next observation; RLS/grants/triggers were functionally verified; name reuse invalidates enrollment; and the final schema has only complete direct events.

- [ ] **Step 5: Commit documentation**

```bash
rtk git add AGENTS.md docs/automated-savings.md docs/automated-savings-how-it-works.md
rtk git commit -m "docs: describe direct warehouse suspension"
```
