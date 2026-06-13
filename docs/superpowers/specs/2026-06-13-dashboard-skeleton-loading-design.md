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

A single hook inside `CostDashboardContent` owns readiness and is the **only**
integration point for B2. It maps each section to the data it needs and derives
a section status shape that components consume:

```ts
type SectionStatus = "loading" | "ready";

type DashboardSectionKey = "overview" | "warehouse" | "storage";

type DashboardSectionStatuses = Record<DashboardSectionKey, SectionStatus>;
```

**Two distinct concepts — keep them separate:**

- **data-ready** — is the underlying data for a section available? Today this is
  a single boolean (the one fetch resolved), the same for all three sections.
- **revealed** — has the staggered-reveal animation flipped this section on yet?
  Per-section, driven by timers (§4).

A section's public `status` is `"ready"` only when it is **both** data-ready and
revealed; otherwise `"loading"`.

**Hook interface (concrete):**

```ts
// Input: the existing loadState + the guard refs already in CostDashboardContent.
function useSectionStatuses(args: {
  view: DashboardView | null;        // loadState.view
  loadStatus: LoadState["status"];   // "loading" | run status
  servedFromCache: boolean;          // cache hit → skip stagger
  reduceMotion: boolean;             // prefers-reduced-motion
  revealGeneration: number;          // bumped on every new run/range request
}): DashboardSectionStatuses;
```

- **Derivation today:** `dataReady = view != null && loadStatus !== "loading"`.
  When `dataReady` is false → all sections `"loading"`. When it flips true →
  sections become `"ready"` in stagger order (§4), unless `servedFromCache` or
  `reduceMotion`, in which case all three flip to `"ready"` synchronously with
  no timers.
- **Reset:** any bump of `revealGeneration` (new run, range change) resets all
  sections to `"loading"` and cancels pending reveal timers (§4) before the next
  derivation runs.
- **Reveal state lives in the hook** (a `useState<DashboardSectionStatuses>` plus
  a timer ref), never in section components.
- **B2 later:** only `dataReady` changes — from one boolean to a per-section
  value computed from per-dataset/per-query completion events. The `revealed`
  layer, the section components, the skeletons, and the layout are untouched.
  Overview is the flagged composite case: its `dataReady` may later require
  multiple datasets (capacity balance + total spend + service spend) to resolve;
  the hook owns that composition, not the component.

Sections **only ever** consume `loading | ready`. They never see datasets,
fetch state, cache flags, or partial-data shapes — that keeps the adapter
boundary clean for B2.

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

**Rendering with no `viewModel`:** during initial load there is no view at all —
no currency, range, service/warehouse names, or values. The `status="loading"`
branch of each section therefore must render purely from the frame + skeletons
and **must not read any data props**. Concretely, the section components take
their data props as optional and only dereference them in the `status="ready"`
branch:

```ts
function WarehouseSpendSection({
  status,
  currency,         // only read when status === "ready"
  range,
  viewModel,        // optional; absent during initial load
}: {
  status: SectionStatus;
  currency?: string;
  range?: DashboardViewRange | null;
  viewModel?: WarehouseSpendViewModel;
}) { ... }
```

This means the skeleton path never depends on header/range data, so it can
render before the first fetch resolves. The `"ready"` path keeps today's
required props and behavior unchanged.

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

- **Single reveal generation token.** The hook keeps one `revealGeneration`
  number, bumped on every new run and every range request. Each scheduled timer
  captures the generation value live at schedule time; on fire it no-ops unless
  the captured value still equals the current generation. A timer that fires
  after a newer request has superseded it is therefore inert.
- **Single timer ref, cleared at every reset and on unmount.** All pending
  reveal timeouts are tracked in one ref and cleared (a) before every transition
  back to `"loading"` (new run / range change), and (b) in the reveal effect's
  cleanup function (covers unmount and dependency change). There is no code path
  that leaves a timer pending across a `loading` reset.
- **Skip the stagger** (reveal all sections synchronously, no timers) for:
  cached views (`servedFromCache`), and `prefers-reduced-motion: reduce`.
- A range-change refetch that sets sections back to `loading` cancels any
  in-flight reveal (via the two mechanisms above) so an old timer cannot flip a
  section `ready` over a newer `loading`.

The header and `RunStatus` badge keep rendering throughout
(`queued → running → loading → complete`), as they do today.

### 5. Edge states (non-`loading`/`ready` paths)

These must be explicit so the skeleton path doesn't swallow them:

- **Failed initial fetch (no `viewModel`).** When the run/fetch fails and there
  is no view, do **not** render perpetual skeletons. The skeleton layout is only
  for `loadStatus === "loading"` with a fetch genuinely in flight. On `failed`/
  `expired`/`deleted` with no view, render an error/empty state (reuse
  `SectionEmptyState` with the run's `user_safe_message`/error) in the content
  region; the `RunStatus` badge already reflects the failure. This replaces the
  current behavior where the blank boxes persist behind a failed badge.
- **Unsupported views.** The existing `viewModel.unsupported` branch is unchanged
  and **bypasses section reveal entirely** — it renders the unsupported
  `SectionEmptyState`, never skeletons.
- **Range-refetch UI continuity.** On a range change the header **and the filter
  bar remain mounted and visible** (the user just interacted with them); only the
  three section bodies revert to `"loading"` skeletons. The skeleton-with-no-
  `viewModel` rule (§2) applies only to the *initial* load; during a range
  refetch the previously loaded `currency`/`range`/names are still available, so
  sections may keep their frames fully populated and only swap chart/list bodies
  to shimmer. Either way the filter bar is never replaced by a skeleton.

## Affected files

Source:

- `apps/web/src/components/dashboard/cost-dashboard.tsx` — `useSectionStatuses`
  hook, replace blank-box branch with skeleton layout, reveal-timer logic +
  cleanup, failed-initial-fetch state.
- `apps/web/src/components/dashboard/spend-sections.tsx` — `status` prop + optional
  data props on the three section components; render skeleton vs content.
- `apps/web/src/components/dashboard/dashboard-design-system.tsx` — new skeleton
  primitives (`ChartSkeleton`, `RankedSpendBarsSkeleton`, `DetailTableSkeleton`,
  `StatValueSkeleton`).

Tests (Vitest):

- `apps/web/src/components/dashboard/cost-dashboard.test.tsx` — update existing
  blank-box loading assertion; add reveal/transition/edge-state cases.
- `apps/web/src/components/dashboard/spend-sections.test.tsx` — section skeleton
  vs content per `status`.
- `apps/web/src/components/dashboard/dashboard-design-system.test.tsx` —
  `ChartSkeleton` bar-vs-line branching only.

## Testing

Concentrate on behavior and integration, not static markup. **Deliberate
decision** (per the design review): we do *not* write per-primitive markup tests
for static skeleton variants — they're brittle and low-value. Per-primitive unit
tests only where a skeleton has meaningful branching (e.g. `ChartSkeleton` bar vs
line). Layout/height parity is covered by the integration height-parity assertions
below, not by per-primitive snapshots.

Integration (`cost-dashboard` test):

- Initial render shows the **skeleton layout** (the three real sections in
  skeleton form), not the old blank boxes.
- After the fetch resolves, sections transition to `ready` and render content.
- Stagger verified with fake timers (Overview before Warehouse before Storage).
- Reduced-motion path reveals all sections instantly (no stagger).
- Cached-view path reveals instantly (no timers).
- **Range-change race:** a range-change refetch mid-reveal cancels pending
  timers; advancing fake timers past the old stagger does not flip a section
  `ready` over the newer `loading`.
- **Unmount mid-reveal:** unmounting while reveal timers are pending clears them
  (no post-unmount state update / act warning).
- **Failed initial fetch:** with no `viewModel`, renders the error/empty state,
  not perpetual skeletons.
- **Height parity:** the highest-risk containers assert identical height classes
  between skeleton and ready states — the warehouse chart (`h-96`), storage chart
  (`h-80`), the ranked-list scroll container, and the storage table fill-height —
  so the reveal cannot cause a layout jump.

Update the existing blank-box loading test to assert the new skeleton layout.

### Verification commands

- `npm run test:web` — full web Vitest suite green.
- `npm run typecheck` — `tsc --noEmit` clean.
- `npm run lint:web` — eslint clean.
- Targeted during development: `npm --workspace apps/web run test -- cost-dashboard`.

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
