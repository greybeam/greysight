# Warehouse Idle % Bar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the spend bar in the "Total spend by warehouse" panel with a per-warehouse **Idle %** bar (idle compute credits ÷ compute credits), colored green→amber→red, while keeping the dollar value on the right.

**Architecture:** A new `credits_attributed_queries` column flows from SQL → dataset allowlist → demo generator. The Python view builder computes `idle_pct` per warehouse and emits a warehouse-specific bar row (`WarehouseIdleBarRow`). The frontend parses that row and renders a dedicated `WarehouseIdleBars` component for the warehouse panel only. Demo and real data share the same Python build path, so idle % is computed once, server-side. The user panel, storage, and AI spend keep the shared `RankedSpendBars`/`RankedBarRow` untouched.

**Tech Stack:** Python (FastAPI, Pydantic, pytest), TypeScript/React (Next.js, Tremor, Tailwind, Vitest).

## Global Constraints

- Idle % = `(sum(credits_used_compute) − sum(credits_attributed_queries)) / sum(credits_used_compute)`, aggregated per warehouse over the window; a fraction in `[0, 1]`.
- `sum(credits_used_compute) == 0` → `idle_pct = None`/`null` → render `–`, no bar.
- `idle_credits` in `[-_FLOAT_EPSILON, 0)` clamps to `0`; a material excess (`idle_credits < -_FLOAT_EPSILON`) **raises** (fail-loud, mirroring `_warehouse_row_dollars`). No blanket `[0,1]` clamp.
- `_FLOAT_EPSILON = 1e-9` (already defined at `dashboard_view_builder.py:87`).
- Color buckets (inclusive at top): `idle ≤ 0.25` green (`bg-emerald-500`), `0.25 < idle ≤ 0.50` amber (`bg-amber-500`), `idle > 0.50` red (`bg-rose-500`). Color classes MUST be written as full literal strings so Tailwind's content scanner keeps them.
- Bar width = `idlePct × 100`, capped at 100%.
- Row order unchanged: warehouses sorted by total spend descending.
- Backend tests only (frontend tests intentionally skipped). Existing TS tests must still pass (`tsc` + Vitest).
- Backend test command (run from `apps/api`): `uv run pytest`. Frontend checks (run from `apps/web`): `npm run lint` / `npx tsc --noEmit` / `npm test`.
- Spec: `docs/superpowers/specs/2026-06-20-warehouse-idle-pct-design.md`.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `sql/snowflake/warehouse_spend_daily.sql` | Source query | Add `sum(credits_attributed_compute_queries) as credits_attributed_queries` |
| `apps/api/app/models.py` | Dataset field allowlist (exact-match validation) | Add `credits_attributed_queries` to `warehouse_spend_daily` |
| `apps/api/app/services/demo_data.py` | Demo dataset generator | Emit `credits_attributed_queries` per warehouse row |
| `apps/api/app/services/dashboard_view_models.py` | Pydantic view models | Add `WarehouseIdleBarRow`; retype `warehouse_bars` |
| `apps/api/app/services/dashboard_view_builder.py` | View builder | `_warehouse_idle_pct` helper + build warehouse idle bars inline |
| `apps/web/src/lib/dashboard-contracts.ts` | TS types + parser | Add `WarehouseIdleBarRow` type + `parseWarehouseIdleBarRow`; retype `warehouseBars` |
| `apps/web/src/components/dashboard/dashboard-design-system.tsx` | Shared dashboard UI | Add `WarehouseIdleBars` + idle color/label helpers |
| `apps/web/src/components/dashboard/spend-sections.tsx` | Warehouse spend section | Use `WarehouseIdleBars` for the warehouse panel |
| Test fixtures (several) | — | Add `credits_attributed_queries` to inline/validated `warehouse_spend_daily` rows |

---

## Task 1: Plumb `credits_attributed_queries` through SQL, allowlist, and demo data

**Files:**
- Modify: `sql/snowflake/warehouse_spend_daily.sql`
- Modify: `apps/api/app/models.py:21-23`
- Modify: `apps/api/app/services/demo_data.py:39-43` and `:157-171`
- Test: `apps/api/tests/test_demo_data.py` (existing tests validate exact field set; add one assertion)

**Interfaces:**
- Produces: every `warehouse_spend_daily` row now carries `credits_attributed_queries: float` with `0 ≤ credits_attributed_queries ≤ credits_used_compute`. Consumed by Task 2.

- [ ] **Step 1: Add a failing assertion to the demo-data contract test**

In `apps/api/tests/test_demo_data.py`, inside `test_demo_dashboard_dataset_matches_v0_contract`, add after the existing `credits_used_compute` block (around line 66):

```python
    assert all(
        "credits_attributed_queries" in row
        and row["credits_attributed_queries"] <= row["credits_used_compute"]
        for row in payload.datasets["warehouse_spend_daily"]
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_demo_data.py::test_demo_dashboard_dataset_matches_v0_contract -v`
Expected: FAIL — `credits_attributed_queries` missing from demo rows (and the exact-field-set assertion at line 52-54 also fails once the allowlist is updated; that is expected to pass only after Step 4).

- [ ] **Step 3: Add the SQL aggregate**

In `sql/snowflake/warehouse_spend_daily.sql`, add the new aggregate after `credits_used_compute` (keep the trailing comma correct):

```sql
select
    convert_timezone('UTC', start_time)::date as usage_date,
    warehouse_name,
    sum(credits_used) as credits_used,
    sum(credits_used_compute) as credits_used_compute,
    sum(credits_attributed_compute_queries) as credits_attributed_queries
from snowflake.account_usage.warehouse_metering_history
```

(Leave the `where`/`group by`/`order by` clauses unchanged.)

- [ ] **Step 4: Add the field to the dataset allowlist**

In `apps/api/app/models.py`, update the `warehouse_spend_daily` entry (lines 21-23):

```python
    "warehouse_spend_daily": frozenset(
        {
            "usage_date",
            "warehouse_name",
            "credits_used",
            "credits_used_compute",
            "credits_attributed_queries",
        }
    ),
```

- [ ] **Step 5: Emit the field from the demo generator**

In `apps/api/app/services/demo_data.py`, extend `_DEMO_WAREHOUSES` (lines 39-43) to carry a per-warehouse attributed share that yields a green/amber/red spread:

```python
# (name, spend share, attributed-compute share). The attributed share sets the
# demo idle %: idle = 1 - attributed_share. Chosen to land one warehouse in each
# color band (green <=25%, amber <=50%, red >50%).
_DEMO_WAREHOUSES = (
    ("BI_WH", 0.50, 0.78),
    ("ETL_WH", 0.35, 0.55),
    ("ADHOC_WH", 0.15, 0.36),
)
```

Then update `_build_warehouse_spend_daily` (lines 157-171) to unpack the new field and emit `credits_attributed_queries`:

```python
def _build_warehouse_spend_daily(usage_dates: list[date]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, usage_date in enumerate(usage_dates):
        total_compute = 38.0 * _daily_multiplier(index)
        for warehouse_name, share, attributed_share in _DEMO_WAREHOUSES:
            compute_credits = round(total_compute * share, 3)
            rows.append(
                {
                    "usage_date": usage_date,
                    "warehouse_name": warehouse_name,
                    "credits_used": round(compute_credits * 1.08, 3),
                    "credits_used_compute": compute_credits,
                    "credits_attributed_queries": round(
                        compute_credits * attributed_share, 3
                    ),
                }
            )
    return rows
```

- [ ] **Step 6: Run the demo-data tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_demo_data.py -v`
Expected: PASS (all three tests, including the exact-field-set check at lines 50-54 and the new assertion).

- [ ] **Step 7: Update validated-payload fixtures that pin the exact field set**

These fixtures build `warehouse_spend_daily` rows that pass through `DashboardDatasetPayload` validation (exact field match) and will now fail with "missing fields: credits_attributed_queries". Add the field to each row (value ≤ its `credits_used_compute`):

- `apps/api/tests/test_dashboard_datasets.py:30` — row has `credits_used_compute: 7.5`; add `"credits_attributed_queries": 4.5,`.
- `apps/api/tests/test_snowflake_dashboard_run.py:488` — row has `credits_used_compute: 7.5`; add `"credits_attributed_queries": 4.5,`.
- `apps/api/tests/test_demo_dashboard_run.py:42` — row has `credits_used_compute: 1.5`; add `"credits_attributed_queries": 0.9,`.
- `apps/api/tests/test_dashboard_runs_async.py:123` — row has `credits_used_compute: 1.0`; add `"credits_attributed_queries": 0.6,`.

For each, insert the new key immediately after the `credits_used_compute` line, matching the surrounding indentation. Example for `test_dashboard_runs_async.py:123`:

```python
                "credits_used_compute": 1.0,
                "credits_attributed_queries": 0.6,
```

- [ ] **Step 8: Run the affected suites to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_dashboard_datasets.py tests/test_snowflake_dashboard_run.py tests/test_demo_dashboard_run.py tests/test_dashboard_runs_async.py -q`
Expected: PASS. (If any other test reports "missing fields: credits_attributed_queries", add the field to that fixture the same way.)

- [ ] **Step 9: Commit**

```bash
git add sql/snowflake/warehouse_spend_daily.sql apps/api/app/models.py apps/api/app/services/demo_data.py apps/api/tests/test_demo_data.py apps/api/tests/test_dashboard_datasets.py apps/api/tests/test_snowflake_dashboard_run.py apps/api/tests/test_demo_dashboard_run.py apps/api/tests/test_dashboard_runs_async.py
git commit -m "feat: plumb credits_attributed_queries through warehouse spend dataset"
```

---

## Task 2: Compute idle % and emit `WarehouseIdleBarRow` from the view builder

**Files:**
- Modify: `apps/api/app/services/dashboard_view_models.py:56-57` and `:120-130`
- Modify: `apps/api/app/services/dashboard_view_builder.py` (add helper near `_warehouse_row_dollars` ~line 1212; edit `_build_warehouse_spend` ~lines 1214-1311)
- Test: `apps/api/tests/test_dashboard_view_builder.py`

**Interfaces:**
- Consumes: `credits_attributed_queries` on each warehouse row (Task 1).
- Produces:
  - `WarehouseIdleBarRow(RankedSpendRow)` with `idle_pct: float | None`.
  - `WarehouseSpendViewModel.warehouse_bars: list[WarehouseIdleBarRow]` (serialized key `warehouse_bars`, each row: `name`, `spend`, `spend_label`, `credits`, `idle_pct`).
  - `_warehouse_idle_pct(*, compute_credits: float, attributed_credits: float) -> float | None`.

- [ ] **Step 1: Add the model (no test yet — supports the tests below)**

In `apps/api/app/services/dashboard_view_models.py`, add after `RankedBarRow` (lines 56-57):

```python
class WarehouseIdleBarRow(RankedSpendRow):
    idle_pct: float | None
```

Then retype `warehouse_bars` in `WarehouseSpendViewModel` (line 128):

```python
    warehouse_bars: list[WarehouseIdleBarRow]
```

(Leave `user_bars: list[RankedBarRow]` unchanged.)

- [ ] **Step 2: Write the failing tests**

In `apps/api/tests/test_dashboard_view_builder.py`, add these tests. Import the helper at the top alongside the existing `_warehouse_row_dollars` import (find the existing `from app.services.dashboard_view_builder import (...)` block and add `_warehouse_idle_pct`).

```python
def test_warehouse_idle_pct_basic_fraction() -> None:
    # 10 compute credits, 6 attributed -> 4 idle -> 0.4 idle pct.
    assert _warehouse_idle_pct(
        compute_credits=10.0, attributed_credits=6.0
    ) == pytest.approx(0.4)


def test_warehouse_idle_pct_zero_compute_is_none() -> None:
    assert (
        _warehouse_idle_pct(compute_credits=0.0, attributed_credits=0.0) is None
    )


def test_warehouse_idle_pct_clamps_epsilon_noise_to_zero() -> None:
    # attributed marginally above compute (float noise) -> 0.0, not negative.
    idle = _warehouse_idle_pct(
        compute_credits=10.0, attributed_credits=10.0 + 1e-12
    )
    assert idle == 0.0


def test_warehouse_idle_pct_raises_on_material_excess() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "warehouse_spend_daily credits_used_compute must be "
            ">= credits_attributed_queries"
        ),
    ):
        _warehouse_idle_pct(compute_credits=10.0, attributed_credits=12.0)


def test_warehouse_bars_carry_idle_pct_in_spend_order() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )
    datasets["org_spend_daily"] = []
    # Two warehouses on one day. BIG_WH: 20 compute, 5 attributed -> idle 0.75.
    # SMALL_WH: 10 compute, 9 attributed -> idle 0.10. BIG_WH has more spend so
    # it must rank first.
    datasets["warehouse_spend_daily"] = [
        {
            "usage_date": "2026-06-08",
            "warehouse_name": "BIG_WH",
            "credits_used": 20.0,
            "credits_used_compute": 20.0,
            "credits_attributed_queries": 5.0,
        },
        {
            "usage_date": "2026-06-08",
            "warehouse_name": "SMALL_WH",
            "credits_used": 10.0,
            "credits_used_compute": 10.0,
            "credits_attributed_queries": 9.0,
        },
    ]
    datasets["query_compute_by_user_daily"] = []
    datasets["rate_sheet_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "WAREHOUSE_METERING",
            "rating_type": "COMPUTE",
            "currency": "USD",
            "effective_rate": 2.0,
        }
    ]

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 8),
    )

    bars = view.warehouse_spend.warehouse_bars
    assert [bar.name for bar in bars] == ["BIG_WH", "SMALL_WH"]
    assert bars[0].idle_pct == pytest.approx(0.75)
    assert bars[1].idle_pct == pytest.approx(0.10)
    # The shared spend fields still ride along on the warehouse bar row.
    assert bars[0].spend == pytest.approx(40.0)


def test_warehouse_bars_idle_pct_none_when_no_compute() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )
    datasets["org_spend_daily"] = []
    # Pure cloud-services usage: credits_used > 0 but compute == 0.
    datasets["warehouse_spend_daily"] = [
        {
            "usage_date": "2026-06-08",
            "warehouse_name": "IDLE_WH",
            "credits_used": 4.0,
            "credits_used_compute": 0.0,
            "credits_attributed_queries": 0.0,
        }
    ]
    datasets["query_compute_by_user_daily"] = []
    datasets["rate_sheet_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "CLOUD_SERVICES",
            "rating_type": "COMPUTE",
            "currency": "USD",
            "effective_rate": 0.5,
        }
    ]

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 8),
    )

    bars = view.warehouse_spend.warehouse_bars
    assert len(bars) == 1
    assert bars[0].idle_pct is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_dashboard_view_builder.py -k "idle" -v`
Expected: FAIL — `_warehouse_idle_pct` not importable / `warehouse_bars` rows have no `idle_pct`.

- [ ] **Step 4: Add the `_warehouse_idle_pct` helper**

In `apps/api/app/services/dashboard_view_builder.py`, add immediately after `_warehouse_row_dollars` (after line 1211). Also add `WarehouseIdleBarRow` to the existing import of view models from `app.services.dashboard_view_models`.

```python
def _warehouse_idle_pct(
    *,
    compute_credits: float,
    attributed_credits: float,
) -> float | None:
    """Idle share of a warehouse's compute credits over the window.

    ``idle = compute - attributed``; the fraction returned is ``idle / compute``.
    Mirrors ``_warehouse_row_dollars``'s fail-loud stance: query-attributed
    credits can never exceed compute credits, so a material excess raises rather
    than being silently clamped. ``assert`` is avoided so ``python -O`` cannot
    strip the guard. Tiny negative float noise inside the epsilon band clamps to
    zero. A warehouse with no compute credits returns ``None`` (rendered "–").
    """
    if compute_credits <= 0.0:
        return None
    idle_credits = compute_credits - attributed_credits
    if idle_credits < -_FLOAT_EPSILON:
        raise ValueError(
            "warehouse_spend_daily credits_used_compute must be "
            ">= credits_attributed_queries"
        )
    return max(idle_credits, 0.0) / compute_credits
```

- [ ] **Step 5: Build the idle bars inline in `_build_warehouse_spend`**

In `apps/api/app/services/dashboard_view_builder.py`, inside `_build_warehouse_spend`, add per-warehouse aggregation of compute and attributed credits. Insert this block after the `warehouse_names = sorted(...)` block (after line 1266), before `spend_by_date_and_warehouse`:

```python
    compute_by_warehouse: dict[str, float] = {}
    attributed_by_warehouse: dict[str, float] = {}
    for row in warehouse_rows:
        warehouse_name = _string_field(row, "warehouse_name", "Unknown warehouse")
        compute_by_warehouse[warehouse_name] = compute_by_warehouse.get(
            warehouse_name, 0.0
        ) + _required_float_field(
            row, "warehouse_spend_daily", "credits_used_compute"
        )
        attributed_by_warehouse[warehouse_name] = attributed_by_warehouse.get(
            warehouse_name, 0.0
        ) + _required_float_field(
            row, "warehouse_spend_daily", "credits_attributed_queries"
        )

    # Idle bars are built from the ranked warehouses (preserving spend-desc order)
    # rather than _build_ranked_bar_rows: the bar width is idle-based (0-100,
    # derived on the frontend), not spend-relative, so the RankedBarRow path does
    # not apply here.
    warehouse_bars = [
        WarehouseIdleBarRow(
            **ranked.model_dump(),
            idle_pct=_warehouse_idle_pct(
                compute_credits=compute_by_warehouse.get(ranked.name, 0.0),
                attributed_credits=attributed_by_warehouse.get(ranked.name, 0.0),
            ),
        )
        for ranked in ranked_warehouses
    ]
```

Then change the `return WarehouseSpendViewModel(...)` so `warehouse_bars` uses the new list (line 1308):

```python
        warehouse_bars=warehouse_bars,
        user_bars=_build_ranked_bar_rows(ranked_users),
```

- [ ] **Step 6: Run the idle tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_dashboard_view_builder.py -k "idle" -v`
Expected: PASS (all six tests).

- [ ] **Step 7: Run the full backend suite (regression guard)**

Run: `cd apps/api && uv run pytest -q`
Expected: PASS. If any test fails with "missing fields: credits_attributed_queries" or a missing-field `KeyError`, add `credits_attributed_queries` to that `warehouse_spend_daily` fixture (value ≤ its `credits_used_compute`) and re-run.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/dashboard_view_models.py apps/api/app/services/dashboard_view_builder.py apps/api/tests/test_dashboard_view_builder.py
git commit -m "feat: compute warehouse idle pct in dashboard view builder"
```

---

## Task 3: Add the `WarehouseIdleBarRow` contract and parser (frontend)

**Files:**
- Modify: `apps/web/src/lib/dashboard-contracts.ts` (type ~line 176; `WarehouseSpendViewModel` ~line 225; parser ~line 824 and ~line 996)

**Interfaces:**
- Consumes: serialized `warehouse_bars` rows with `idle_pct` (Task 2).
- Produces: `WarehouseIdleBarRow = RankedSpendRow & { idlePct: number | null }`; `WarehouseSpendViewModel.warehouseBars: WarehouseIdleBarRow[]`; `parseWarehouseIdleBarRow`.

- [ ] **Step 1: Add the type**

In `apps/web/src/lib/dashboard-contracts.ts`, after `RankedBarRow` (lines 174-176):

```typescript
export type WarehouseIdleBarRow = RankedSpendRow & {
  idlePct: number | null;
};
```

- [ ] **Step 2: Retype `warehouseBars` on the view model**

Change line 225 in `WarehouseSpendViewModel`:

```typescript
  warehouseBars: WarehouseIdleBarRow[];
```

(Leave `userBars: RankedBarRow[]` unchanged.)

- [ ] **Step 3: Add the parser**

After `parseRankedBarRow` (lines 986-996), add:

```typescript
function parseWarehouseIdleBarRow(payload: unknown): WarehouseIdleBarRow {
  const record = asViewRecord(payload);
  return {
    ...parseRankedSpendRow(record),
    idlePct: readViewNullableNumber(record, "idle_pct", "idlePct"),
  };
}
```

- [ ] **Step 4: Use the new parser for warehouse bars**

In `parseWarehouseSpendViewModel`, change the `warehouseBars` mapping (lines 824-828) from `.map(parseRankedBarRow)` to:

```typescript
    warehouseBars: readViewArray(
      payload,
      "warehouse_bars",
      "warehouseBars",
    ).map(parseWarehouseIdleBarRow),
```

(Leave `userBars: ....map(parseRankedBarRow)` unchanged.)

- [ ] **Step 5: Verify type-check and existing tests pass**

Run: `cd apps/web && npx tsc --noEmit && npm test`
Expected: PASS. The existing `dashboard-contracts.test.ts` fixture uses `warehouse_bars: []`, so the new parser is never exercised there and no fixture change is needed. (If `tsc` flags `WarehouseIdleBars` as unused, ignore — it is added in Task 4. If it flags a missing import elsewhere, fix the import.)

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/dashboard-contracts.ts
git commit -m "feat: add WarehouseIdleBarRow contract and parser"
```

---

## Task 4: Render the `WarehouseIdleBars` component and wire it into the warehouse panel

**Files:**
- Modify: `apps/web/src/components/dashboard/dashboard-design-system.tsx` (add after `RankedSpendBars`, ~line 208)
- Modify: `apps/web/src/components/dashboard/spend-sections.tsx` (warehouse ranking panel, ~line 246)

**Interfaces:**
- Consumes: `WarehouseIdleBarRow[]` (Task 3) via `viewModel.warehouseBars`.
- Produces: `WarehouseIdleBars` React component, exported from `dashboard-design-system.tsx`.

- [ ] **Step 1: Add the idle helpers + component**

In `apps/web/src/components/dashboard/dashboard-design-system.tsx`, ensure `WarehouseIdleBarRow` is imported from `@/lib/dashboard-contracts` (add it to the existing import of `RankedBarRow`). Then add after `RankedSpendBars` (after line 208):

```tsx
// Color bands for the idle % bar. High idle is the bad state, so the scale runs
// green -> amber -> red as idle climbs. Full literal class strings (no template
// construction) so Tailwind's content scanner keeps them in the build.
function idleBarColorClass(idlePct: number): string {
  if (idlePct <= 0.25) {
    return "bg-emerald-500";
  }
  if (idlePct <= 0.5) {
    return "bg-amber-500";
  }
  return "bg-rose-500";
}

// Whole-percent label for the idle column; null compute renders an em dash.
function idlePctLabel(idlePct: number | null): string {
  if (idlePct === null) {
    return "–";
  }
  return `${Math.round(idlePct * 100)}%`;
}

// Warehouse-only ranked panel: idle % bar (out of 100, colored by band) with the
// percentage on the left and total spend on the right. Reuses RankedSpendBars'
// absolute-fill scroll shell so it sits correctly inside the flex half-height
// panel; the grid carries a fourth column for the idle % value. Sub-cent-spend
// rows are filtered like RankedSpendBars (keyed off spend — this is a spend
// panel — so a near-zero-spend warehouse is hidden even at high idle).
export function WarehouseIdleBars({ rows }: { rows: WarehouseIdleBarRow[] }) {
  const visibleRows = rows.filter((row) => Math.round(row.spend * 100) !== 0);

  if (visibleRows.length === 0) {
    return <p className="mt-4 text-xs text-slate-400">No warehouse spend data</p>;
  }

  return (
    <div className="relative mt-4 min-h-[16rem] flex-1 lg:min-h-0">
      <ul
        className="dashboard-scroll absolute inset-0 grid grid-cols-[minmax(0,7rem)_auto_minmax(1.5rem,1fr)_auto] content-start items-center gap-x-3 gap-y-2 overflow-y-auto"
        role="list"
      >
        {visibleRows.map((row) => (
          <li className="contents" key={row.name} role="listitem">
            <span
              className="min-w-0 truncate text-xs text-slate-400"
              title={row.name}
            >
              {row.name}
            </span>
            <span className="text-xs font-semibold tabular-nums text-slate-200">
              {idlePctLabel(row.idlePct)}
            </span>
            <span className="h-2 rounded bg-hairline">
              {row.idlePct !== null ? (
                <span
                  className={cx(
                    "block h-2 rounded",
                    idleBarColorClass(row.idlePct),
                  )}
                  style={{ width: `${Math.min(row.idlePct * 100, 100)}%` }}
                />
              ) : null}
            </span>
            <span className="text-xs font-semibold tabular-nums text-slate-200">
              {compactSpendLabel(row)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

Note: `cx` and `compactSpendLabel` already exist in this file (used by `RankedSpendBars`). `compactSpendLabel` takes a `RankedBarRow`, but only reads `.spend` and `.spendLabel`; `WarehouseIdleBarRow` has both, so widen its parameter type to `RankedSpendRow` (the shared supertype) if `tsc` complains:

```tsx
function compactSpendLabel(row: RankedSpendRow): string {
```

(Import `RankedSpendRow` if not already imported.)

- [ ] **Step 2: Wire the component into the warehouse panel**

In `apps/web/src/components/dashboard/spend-sections.tsx`, update the import from `./dashboard-design-system` to include `WarehouseIdleBars`. Then in `WarehouseSpendSection`, replace the warehouse ranking bars (the `<RankedSpendBars rows={viewModel.warehouseBars} />` inside the "Total spend by warehouse" `<section>`, ~line 246) with:

```tsx
                  <WarehouseIdleBars rows={viewModel.warehouseBars} />
```

Leave the "Total spend by user" panel's `<RankedSpendBars rows={viewModel.userBars} />` unchanged.

- [ ] **Step 3: Verify lint, type-check, and existing tests**

Run: `cd apps/web && npm run lint && npx tsc --noEmit && npm test`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/components/dashboard/dashboard-design-system.tsx apps/web/src/components/dashboard/spend-sections.tsx
git commit -m "feat: render warehouse idle pct bar in warehouse spend panel"
```

- [ ] **Step 5: Visual check (user-driven)**

Ask Kyle to load the dashboard (demo mode is enough — demo data now spans green/amber/red) and confirm the "Total spend by warehouse" panel shows: `name | idle% | colored bar | spend`, aligned, with `ADHOC_WH` red (~64%), `ETL_WH` amber (~45%), `BI_WH` green (~22%).

---

## Self-Review

**Spec coverage:**
- SQL column → Task 1 Step 3. ✓
- Allowlist → Task 1 Step 4. ✓
- Demo data (Python path serves demo + real) → Task 1 Step 5. ✓
- Idle % math + edge cases (null, epsilon clamp, raise, no blanket clamp) → Task 2 Steps 4-5 + tests Step 2. ✓
- `WarehouseIdleBarRow` built inline (not via `_build_ranked_bar_rows`) → Task 2 Step 5. ✓
- TS contract + parser (and decision NOT to touch raw `WarehouseSpendDaily`) → Task 3. ✓
- `WarehouseIdleBars` component: 4-col grid, reused scroll shell, color bands, dash, width cap, spend-keyed filter, spend-desc order → Task 4 Step 1. ✓
- Wiring warehouse panel only; user/storage/AI untouched → Task 4 Step 2. ✓
- Backend-only tests → Task 1/2; existing TS tests still pass → Task 3/4. ✓

**Placeholder scan:** none — every code/test step shows full content.

**Type consistency:** `idle_pct` (Python) ↔ `idlePct` (TS); `WarehouseIdleBarRow` is `RankedSpendRow` + idle field in both layers; `warehouse_bars`/`warehouseBars` retyped on both view models; `_warehouse_idle_pct` signature is keyword-only in helper, tests, and call site. ✓
