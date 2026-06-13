# Dashboard Skeleton Loading & Staggered Reveal — Design

**Date:** 2026-06-13
**Status:** Approved (design)
**Scope:** Frontend only (`apps/web`). No backend, API contract, run/poll, or view-builder changes.

## Problem

The dashboard's initial/loading state renders three blank `animate-pulse` boxes
(`cost-dashboard.tsx`, the `viewModel`-absent branch). It reads as "empty and
broken" rather than "loading." We want the real section layout (Overview /
Warehouse spend / Storage spend, with their chart frames) rendered immediately
in skeleton form with a loading shimmer, then populated as data lands.

## Goal & Non-Goals

**Goal (this work, "A"):** Render the real dashboard layout in skeleton form
during load, then reveal sections with a short staggered animation when data
resolves — purely frontend, built so true per-section/per-query progressive
loading drops in later without a rewrite.

**Eventual vision ("B2", NOT this work):** Each piece of SQL that powers a chart
renders as it completes — real progressive loading driven by per-section/
per-dataset readiness from the backend. This requires the run executor to
persist datasets incrementally, a readiness API, and frontend polling. Out of
scope here; this design only leaves a clean seam for it.

**Non-goals:** No changes to `startDashboardRun` / `pollDashboardRun` /
`fetchDashboardView`, the API contracts, or `build_dashboard_view`. No
partial-data frontend contracts.

## Background: why this is a frontend-only change

The latency users wait on is the **run** (Snowflake queries during the
`queued → running` phase), not the view fetch. Flow:

1. `startDashboardRun` (POST) kicks off the slow Snowflake queries.
2. `pollDashboardRun` waits until run `status === "completed"`.
3. `fetchDashboardView` → `build_dashboard_view` does a fast in-memory transform
   of already-stored datasets into the **entire** view (Overview + Warehouse +
   Storage, all charts/rankings) in one pass.

So today all section data arrives in one payload at one instant. The staggered
reveal is therefore presentation-only for now; B2 is what makes sections
genuinely complete at different times. `build_dashboard_view` is already
decomposed per section (`_build_capacity_balance`, `_build_warehouse_spend`,
`_build_storage_spend`, `_build_service_spend`), so the seam aligns with an
existing boundary.

## Design

### 1. Readiness derivation (the B2 seam)

A single helper/hook inside `CostDashboardContent` owns readiness and is the
**only** integration point for B2. It maps each section to the datasets it
needs and derives a section status shape that components consume:

```ts
type SectionStatus = "loading" | "ready";

type DashboardSectionStatuses = {
  overview: SectionStatus;   // composite: capacity balance + total spend + service spend
  warehouse: SectionStatus;
  storage: SectionStatus;
};
```

- **Today:** derivation is trivial — when the single fetch is in flight, all
  three are `loading`; when it resolves, all three are `ready` (revealed via the
  stagger below). A view served from cache resolves to `ready` immediately
  (no stagger).
- **B2 later:** only the *derivation source* changes — readiness is computed
  from per-section/per-dataset completion events instead of the single fetch.
  Section components, skeletons, and layout are untouched.

Readiness derivation lives in one place (helper/hook), not scattered as inline
state across components. Overview is explicitly the composite/edge case: its
readiness may later depend on multiple datasets resolving, so the helper owns
that composition rather than a component.

Sections **only ever** consume `loading | ready`. They never see datasets,
fetch state, or partial-data shapes — that keeps the adapter boundary clean for
B2.

### 2. Real layout in skeleton form

Replace the three-blank-box branch in `cost-dashboard.tsx` with the actual
`OverviewSection` / `WarehouseSpendSection` / `StorageSpendSection`, rendered
through the same `DashboardSection` / `DashboardGrid` / `DashboardPanel` /
`Card` frames, titles, and column spans as the loaded state.

Each section component gains a `status: SectionStatus` prop:

- `status === "loading"` → render the section's **skeleton body** (chart/list/
  table slots filled with shimmer placeholders) inside the normal frames.
- `status === "ready"` → render existing content unchanged.

Layout must be **pixel-identical** between skeleton and ready so nothing jumps
when data lands. Skeletons occupy the same body slots and match exact existing
height classes — `h-80`, `h-96`, the ranked-list scroll container, and the
storage table's fill-height behavior.

### 3. Skeleton primitives (`dashboard-design-system.tsx`)

New components, reusing existing Card frames and `animate-pulse`:

- **`ChartSkeleton`** — mimics a chart frame: faint y-axis tick labels,
  horizontal gridlines, x-axis date placeholders, shimmering plot area.
  Variants: `bar` (spend charts) and `line` (capacity balance).
- **`RankedSpendBarsSkeleton`** — a few shimmer rows matching the ranked-list
  grid (`RankedSpendBars` layout), inside the same scroll container.
- **`DetailTableSkeleton`** — shimmer rows for the storage database table,
  preserving fill-height.
- **`StatValueSkeleton`** — shimmer block for the large currency value + its
  label (e.g. the `$112,015.86` / "Ending Balance as of …" slot).

**Decision:** do NOT feed Tremor charts empty data — empty Tremor charts render
awkward axes / misleading blank plots with library-specific empty-state
behavior. Skeletons are styled frames + shimmer that occupy the chart body slot.

`FilterBarSkeleton` is intentionally dropped: the filter bar only appears once a
view exists (it needs range + currency), so there is no pre-range control
surface to skeletonize.

### 4. Staggered reveal (race-safe)

When data resolves, sections flip `loading → ready` in sequence ~120–150ms
apart (Overview → Warehouse → Storage) so it feels progressive. The stagger is
**presentation-only and never data truth.**

Safety requirements (mirroring the existing `runGenerationRef` /
`rangeRequestSeqRef` guards in `CostDashboardContent`):

- Reveal timers are keyed to the current run-generation / request-sequence. A
  timer that fires after a newer request has superseded it is a no-op.
- All pending reveal timers are cleared on refetch, range change, and unmount.
- **Skip the stagger** (reveal instantly) for: cached views, and when
  `prefers-reduced-motion: reduce` is set.
- A range-change refetch that sets sections back to `loading` must cancel any
  in-flight reveal so an old timer cannot flip a section `ready` over a newer
  `loading`.

The header and `RunStatus` badge keep rendering throughout
(`queued → running → loading → complete`), as they do today.

## Affected files

- `apps/web/src/components/dashboard/cost-dashboard.tsx` — readiness helper/hook,
  replace blank-box branch with skeleton layout, reveal-timer logic + cleanup.
- `apps/web/src/components/dashboard/spend-sections.tsx` — `status` prop on the
  three section components; render skeleton vs content.
- `apps/web/src/components/dashboard/dashboard-design-system.tsx` — new skeleton
  primitives.
- Tests (see below).

## Testing

Concentrate on behavior and integration, not static markup. Per-primitive unit
tests only where a skeleton has meaningful branching (e.g. `ChartSkeleton`
bar vs line) — not for static variants.

Integration (`cost-dashboard` test):

- Initial render shows the **skeleton layout** (the three real sections in
  skeleton form), not the old blank boxes.
- After the fetch resolves, sections transition to `ready` and render content.
- Stagger verified with fake timers (Overview before Warehouse before Storage).
- Reduced-motion path reveals all sections instantly (no stagger).
- Timer cleanup: a range-change refetch mid-reveal cancels pending timers and
  no stale timer flips a section `ready` over the newer `loading`.
- Cached-view path reveals instantly.

Update the existing blank-box loading test to assert the new skeleton layout.

## Risks & mitigations

- **Timer/refetch races** → generation-keyed timers + cleanup on every refetch/
  unmount (§4).
- **Layout jump on reveal** → skeletons match exact height classes and occupy
  identical body slots (§2, §3).
- **Over-modeling for B2** → no partial-data contracts now; readiness stays
  `loading | ready` behind one adapter (§1).

## B2 follow-on (recorded, not built)

When ready: make the run persist datasets as query groups complete, expose
per-section/per-dataset readiness, and have the readiness helper derive section
status from those events instead of the single fetch. No changes to section
components, skeletons, or layout required — that is the payoff of the seam.
