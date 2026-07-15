# Dashboard Attribution Availability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep dashboards loadable when Snowflake attribution is null while rejecting legacy cached datasets that omit the attribution field.

**Architecture:** The prepared-view builder will distinguish a required key with a nullable value from an absent required key and propagate unavailable attribution as `idle_pct: null`. The cached-run route will validate only the required base datasets against the existing request contract before creating an in-memory snapshot; incompatible cache entries become 204 misses and are deleted best-effort.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, Supabase/PostgREST cache adapter

---

### Task 1: Preserve unavailable warehouse attribution

**Files:**
- Modify: `apps/api/app/services/dashboard_view_builder.py:1163-1268`
- Test: `apps/api/tests/test_dashboard_view_builder.py:2070-2225`

- [ ] **Step 1: Write failing prepared-view tests**

Add one integration test with a `warehouse_spend_daily` row whose
`credits_attributed_queries` key is present with `None`. Build the view and
assert the warehouse remains ranked while its bar has `idle_pct is None`.
Add a second regression test that removes the key entirely and asserts the
existing missing-required-field `ValueError` is retained.

```python
def test_warehouse_bars_idle_pct_none_when_attribution_unavailable() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["warehouse_spend_daily"] = [
        {
            "usage_date": "2026-06-08",
            "warehouse_name": "ADAPTIVE_WH",
            "credits_used": 10.0,
            "credits_used_compute": 10.0,
            "credits_attributed_queries": None,
        }
    ]
    datasets["query_compute_by_user_daily"] = []

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 8),
    )

    assert [bar.name for bar in view.warehouse_spend.warehouse_bars] == [
        "ADAPTIVE_WH"
    ]
    assert view.warehouse_spend.warehouse_bars[0].idle_pct is None


def test_warehouse_bars_reject_missing_attribution_field() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["warehouse_spend_daily"][0].pop("credits_attributed_queries")

    with pytest.raises(
        ValueError,
        match=(
            "missing required numeric field "
            "warehouse_spend_daily.credits_attributed_queries"
        ),
    ):
        build_dashboard_view(
            run=_demo_run(),
            datasets=datasets,
            metadata=_demo_metadata(),
            source_start_date=source_start,
            source_end_date=source_end,
        )
```

- [ ] **Step 2: Run the tests and verify RED**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_dashboard_view_builder.py \
  -k "attribution_unavailable or reject_missing_attribution_field" -q
```

Expected: the null-attribution test fails with the production `ValueError`; the
absent-key test passes, proving the test distinguishes the two states.

- [ ] **Step 3: Implement nullable required-field handling**

Add a helper that returns `None` only when the key exists with a null value, and
otherwise delegates numeric validation to `_required_float_field`:

```python
def _required_nullable_float_field(
    row: DatasetRow, dataset_key: str, field_name: str
) -> float | None:
    if field_name not in row:
        return _required_float_field(row, dataset_key, field_name)
    if row[field_name] is None:
        return None
    return _required_float_field(row, dataset_key, field_name)
```

Change `_warehouse_idle_pct` to accept `attributed_credits: float | None`, keep
the negative-compute guard, and return `None` before arithmetic when attribution
is unavailable. Aggregate attribution as `dict[str, float | None]`; once any row
for a warehouse is null, keep that warehouse's aggregate null. Continue summing
and validating all non-null values.

- [ ] **Step 4: Run the targeted builder tests and verify GREEN**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_dashboard_view_builder.py -q
```

Expected: all dashboard view builder tests pass.

### Task 2: Reject incompatible durable cache datasets

**Files:**
- Modify: `apps/api/app/routes/dashboard_runs.py:10-18,931-975`
- Test: `apps/api/tests/test_dashboard_run_cache_route.py:1-150`

- [ ] **Step 1: Write the failing cache-route test**

Import `deepcopy`, clone an active demo cache entry, remove
`credits_attributed_queries` from its first warehouse row, and store the stale
entry. Assert the cached-run endpoint returns 204 and deletes the entry.

```python
def test_cached_rejects_and_deletes_incompatible_dataset(_stores) -> None:
    _settings_store, run_store = _stores
    _seed_active_cached_run(run_store)
    cached = run_store.get_active(ORG_ID)
    assert cached is not None
    datasets = deepcopy(cached.datasets)
    datasets["warehouse_spend_daily"][0].pop("credits_attributed_queries")
    run_store.upsert(replace(cached, datasets=datasets))

    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        response = TestClient(app).get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert run_store.get_active(ORG_ID) is None
```

- [ ] **Step 2: Run the cache test and verify RED**

Run from `apps/api`:

```bash
rtk uv run pytest \
  tests/test_dashboard_run_cache_route.py::test_cached_rejects_and_deletes_incompatible_dataset \
  -q
```

Expected: FAIL because the route currently returns 200 and leaves the stale
entry in the cache store.

- [ ] **Step 3: Implement current-contract validation at the cache boundary**

Import `REQUIRED_DATASET_KEYS` from `app.models`. Add a focused helper that
subsets deferred datasets away and reuses `DashboardRunCreateRequest` validation:

```python
def _cached_datasets_match_current_contract(cached: CachedDashboardRun) -> bool:
    try:
        base_datasets = {
            key: cached.datasets[key] for key in REQUIRED_DATASET_KEYS
        }
        DashboardRunCreateRequest(
            window_days=cached.window_days,
            datasets=base_datasets,
        )
    except (KeyError, ValueError):
        return False
    return True
```

After `get_active` returns a cache entry and before creating a snapshot, reject
incompatible entries. Log only the organization ID, delete through the existing
store, catch `RunCacheStoreError` from deletion, and return an empty 204 response
even if cleanup fails.

- [ ] **Step 4: Run the cache-route tests and verify GREEN**

Run from `apps/api`:

```bash
rtk uv run pytest tests/test_dashboard_run_cache_route.py -q
```

Expected: all cache-route tests pass, including valid caches with deferred
datasets and null attribution values.

### Task 3: Broad verification

**Files:**
- Verify only; no additional production changes are expected.

- [ ] **Step 1: Run the complete API suite**

Run from the repository root:

```bash
rtk npm run test:api
```

Expected: all API tests pass.

- [ ] **Step 2: Run repository lint**

Run from the repository root:

```bash
rtk npm run lint
```

Expected: ESLint and Ruff checks pass without errors.

- [ ] **Step 3: Review the final diff**

```bash
rtk git diff origin/main...HEAD
rtk git status --short
```

Expected: only the approved docs, prepared-view behavior, cache validation, and
their regression tests differ from `origin/main`; the worktree is otherwise
clean.
