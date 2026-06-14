# Dashboard Skeleton Loading & Staggered Reveal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's three blank loading boxes with the real section layout rendered in skeleton form, then reveal sections in a short stagger when data resolves.

**Architecture:** A `useSectionStatuses` hook in `CostDashboardContent` derives a per-section `loading | ready` map from the existing single fetch (split into "data-ready" vs timer-driven "revealed"). Section components take a discriminated-union `status` prop and render skeleton bodies when loading. This hook is the sole seam where future per-query (B2) readiness will plug in — components and layout stay untouched.

**Tech Stack:** Next.js (App Router) + React 18, TypeScript, Tremor charts, Tailwind, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-06-13-dashboard-skeleton-loading-design.md`

---

## File Structure

New files:
- `apps/web/src/lib/use-prefers-reduced-motion.ts` — tiny hook reading `prefers-reduced-motion`.
- `apps/web/src/lib/use-prefers-reduced-motion.test.ts`
- `apps/web/src/components/dashboard/use-section-statuses.ts` — the readiness/reveal hook (the B2 seam).
- `apps/web/src/components/dashboard/use-section-statuses.test.ts`

Modified files:
- `apps/web/src/components/dashboard/dashboard-design-system.tsx` — add skeleton primitives.
- `apps/web/src/components/dashboard/dashboard-design-system.test.tsx` — `ChartSkeleton` branch test (create if absent).
- `apps/web/src/components/dashboard/spend-sections.tsx` — `status` prop + skeleton bodies on the three sections.
- `apps/web/src/components/dashboard/spend-sections.test.tsx` — section skeleton-vs-content tests.
- `apps/web/src/components/dashboard/cost-dashboard.tsx` — wire the hook, replace the loading branch, failed-initial state.
- `apps/web/src/components/dashboard/cost-dashboard.test.tsx` — update two existing loading assertions; add reveal/edge-state tests.

Design notes that hold across tasks:
- **Skeleton path reads no data.** Section props are a discriminated union: `{ status: "loading" }` carries no data; `{ status: "ready"; ...data }` carries today's required props. This makes the no-`viewModel` initial render type-safe (spec §2).
- **Stagger only on a loading→ready transition.** A dashboard mounted already-ready (the `data` prop is provided, or a cached relative window) does not stagger. The hook tracks previous data-ready to distinguish (spec §1, §4).
- **All shimmer uses `animate-pulse` + `bg-hairline` tokens** already in the codebase. Heights mirror the real charts/lists exactly so the reveal never shifts layout (spec §2, §3): warehouse chart `h-96`, storage chart `h-80`, overview total-spend chart `h-80`, capacity line `h-80`, ranked list `min-h-[16rem] lg:min-h-0`, storage table fill-height.

---

## Task 1: `usePrefersReducedMotion` hook

**Files:**
- Create: `apps/web/src/lib/use-prefers-reduced-motion.ts`
- Test: `apps/web/src/lib/use-prefers-reduced-motion.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/lib/use-prefers-reduced-motion.test.ts
import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { usePrefersReducedMotion } from "./use-prefers-reduced-motion";

function mockMatchMedia(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

describe("usePrefersReducedMotion", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns false when the media query does not match", () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(false);
  });

  it("returns true when reduced motion is preferred", () => {
    mockMatchMedia(true);
    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(true);
  });

  it("defaults to false when matchMedia is unavailable", () => {
    vi.stubGlobal("matchMedia", undefined);
    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --workspace apps/web run test -- use-prefers-reduced-motion`
Expected: FAIL — `usePrefersReducedMotion` is not exported / module not found.

- [ ] **Step 3: Write the implementation**

```ts
// apps/web/src/lib/use-prefers-reduced-motion.ts
"use client";

import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

/**
 * Reports the user's reduced-motion preference. Defaults to false (animate) on
 * the server and in environments without matchMedia, so the staggered reveal is
 * the default and reduced motion is an opt-out.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }

    const query = window.matchMedia(QUERY);
    setReduced(query.matches);

    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return reduced;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --workspace apps/web run test -- use-prefers-reduced-motion`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/use-prefers-reduced-motion.ts apps/web/src/lib/use-prefers-reduced-motion.test.ts
git commit -m "feat: add usePrefersReducedMotion hook"
```

---

## Task 2: Skeleton primitives

Adds `ChartSkeleton`, `RankedSpendBarsSkeleton`, `DetailTableSkeleton`, and `StatValueSkeleton` to the design system. Per the spec, only `ChartSkeleton` has meaningful branching (bar vs line) and gets a unit test; the others are static markup covered later by integration height-parity assertions.

**Files:**
- Modify: `apps/web/src/components/dashboard/dashboard-design-system.tsx`
- Test: `apps/web/src/components/dashboard/dashboard-design-system.test.tsx`

- [ ] **Step 1: Write the failing test**

Create the test file if it does not exist; otherwise append the `describe` block.

```tsx
// apps/web/src/components/dashboard/dashboard-design-system.test.tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChartSkeleton } from "./dashboard-design-system";

describe("ChartSkeleton", () => {
  afterEach(cleanup);

  it("renders vertical bar placeholders for the bar variant", () => {
    render(<ChartSkeleton variant="bar" heightClass="h-96" testId="chart-skel" />);
    const root = screen.getByTestId("chart-skel");
    expect(root).toHaveAttribute("data-chart-skeleton", "bar");
    expect(root).toHaveClass("h-96");
    // Bar variant renders the fixed set of bar placeholders.
    expect(root.querySelectorAll("[data-skeleton-bar]").length).toBeGreaterThan(0);
  });

  it("renders a line sweep placeholder for the line variant", () => {
    render(<ChartSkeleton variant="line" heightClass="h-80" testId="chart-skel" />);
    const root = screen.getByTestId("chart-skel");
    expect(root).toHaveAttribute("data-chart-skeleton", "line");
    expect(root).toHaveClass("h-80");
    expect(root.querySelector("[data-skeleton-line]")).not.toBeNull();
    expect(root.querySelectorAll("[data-skeleton-bar]").length).toBe(0);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --workspace apps/web run test -- dashboard-design-system`
Expected: FAIL — `ChartSkeleton` is not exported.

- [ ] **Step 3: Write the implementation**

Add the following exports to `apps/web/src/components/dashboard/dashboard-design-system.tsx`. Place them just after the `RankedSpendBars` export (around line 204). `cx`, `Card`, and `Text` are already imported/defined in this file.

```tsx
// Fixed bar heights (percent) for the bar-chart skeleton. A static array — the
// runtime forbids Math.random and a deterministic shape keeps tests stable.
const SKELETON_BAR_HEIGHTS = [40, 65, 50, 80, 55, 70, 45, 60, 75, 50, 68, 42];

/**
 * Placeholder that mimics a chart's frame (gridlines + plot area) at the exact
 * height of the real chart, so revealing data swaps content without a layout
 * shift. `bar` shows vertical bar stubs; `line` shows a single horizontal sweep.
 */
export function ChartSkeleton({
  variant,
  heightClass = "h-80",
  testId,
}: {
  variant: "bar" | "line";
  heightClass?: string;
  testId?: string;
}) {
  return (
    <div
      className={cx("relative mt-4 w-full", heightClass)}
      data-chart-skeleton={variant}
      data-testid={testId}
      role="presentation"
    >
      {/* Faint horizontal gridlines behind the plot placeholder. */}
      <div className="absolute inset-0 flex flex-col justify-between py-1">
        {[0, 1, 2, 3, 4].map((line) => (
          <div key={line} className="h-px w-full bg-hairline/50" />
        ))}
      </div>
      {variant === "bar" ? (
        <div className="absolute inset-x-1 bottom-0 top-2 flex items-end gap-1.5">
          {SKELETON_BAR_HEIGHTS.map((height, index) => (
            <div
              key={index}
              className="flex-1 animate-pulse rounded-sm bg-hairline/70"
              data-skeleton-bar
              style={{ height: `${height}%` }}
            />
          ))}
        </div>
      ) : (
        <div
          className="absolute inset-x-1 top-1/3 h-16 animate-pulse rounded-md bg-hairline/40"
          data-skeleton-line
        />
      )}
    </div>
  );
}

/** Shimmer rows matching the RankedSpendBars three-column grid + scroll wrapper. */
export function RankedSpendBarsSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div
      className="relative mt-4 min-h-[16rem] flex-1 lg:min-h-0"
      data-testid="ranked-spend-skeleton"
      role="presentation"
    >
      <ul className="absolute inset-0 grid grid-cols-[minmax(0,9rem)_minmax(1.5rem,1fr)_auto] content-start items-center gap-x-3 gap-y-2">
        {Array.from({ length: rows }, (_, index) => (
          <li className="contents" key={index}>
            <span className="h-3 animate-pulse rounded bg-hairline/70" />
            <span className="h-2 animate-pulse rounded bg-hairline/50" />
            <span className="h-3 w-10 animate-pulse rounded bg-hairline/70" />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Fill-height shimmer rows mirroring the storage DetailTable card frame. */
export function DetailTableSkeleton({
  title,
  rows = 6,
}: {
  title: string;
  rows?: number;
}) {
  return (
    <Card className="flex h-full flex-col p-4" data-testid="detail-table-skeleton">
      <Text>{title}</Text>
      <div className="relative mt-2 min-h-0 flex-1">
        <div className="absolute inset-0 flex flex-col gap-2 overflow-hidden">
          {Array.from({ length: rows }, (_, index) => (
            <div
              key={index}
              className="h-4 w-full animate-pulse rounded bg-hairline/60"
            />
          ))}
        </div>
      </div>
    </Card>
  );
}

/** Shimmer block standing in for a large KPI value. */
export function StatValueSkeleton() {
  return (
    <div
      className="mt-2 h-9 w-48 animate-pulse rounded bg-hairline/70"
      data-testid="stat-value-skeleton"
      role="presentation"
    />
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --workspace apps/web run test -- dashboard-design-system`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/dashboard/dashboard-design-system.tsx apps/web/src/components/dashboard/dashboard-design-system.test.tsx
git commit -m "feat: add dashboard skeleton primitives"
```

---

## Task 3: `useSectionStatuses` hook (the B2 seam)

Owns the `{ overview, warehouse, storage }` status map, the data-ready vs revealed split, the staggered timers, and all timer cleanup. This is the only place B2 later changes.

**Files:**
- Create: `apps/web/src/components/dashboard/use-section-statuses.ts`
- Test: `apps/web/src/components/dashboard/use-section-statuses.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/components/dashboard/use-section-statuses.test.ts
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  REVEAL_STEP_MS,
  useSectionStatuses,
} from "./use-section-statuses";

describe("useSectionStatuses", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts ready with no stagger when mounted already data-ready", () => {
    const { result } = renderHook(() =>
      useSectionStatuses({ dataReady: true, instant: false, revealGeneration: 0 }),
    );
    expect(result.current).toEqual({
      overview: "ready",
      warehouse: "ready",
      storage: "ready",
    });
  });

  it("staggers overview -> warehouse -> storage on a loading->ready transition", () => {
    const { result, rerender } = renderHook(
      ({ dataReady, gen }) =>
        useSectionStatuses({ dataReady, instant: false, revealGeneration: gen }),
      { initialProps: { dataReady: false, gen: 1 } },
    );
    expect(result.current).toEqual({
      overview: "loading",
      warehouse: "loading",
      storage: "loading",
    });

    rerender({ dataReady: true, gen: 1 });
    // Still loading until the first timer fires.
    expect(result.current.overview).toBe("loading");

    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS);
    });
    expect(result.current.overview).toBe("ready");
    expect(result.current.warehouse).toBe("loading");

    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS);
    });
    expect(result.current.warehouse).toBe("ready");
    expect(result.current.storage).toBe("loading");

    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS);
    });
    expect(result.current.storage).toBe("ready");
  });

  it("reveals all sections instantly when instant (reduced motion / cache)", () => {
    const { result, rerender } = renderHook(
      ({ dataReady }) =>
        useSectionStatuses({ dataReady, instant: true, revealGeneration: 1 }),
      { initialProps: { dataReady: false } },
    );
    rerender({ dataReady: true });
    expect(result.current).toEqual({
      overview: "ready",
      warehouse: "ready",
      storage: "ready",
    });
  });

  it("cancels a pending reveal when a new generation resets to loading", () => {
    const { result, rerender } = renderHook(
      ({ dataReady, gen }) =>
        useSectionStatuses({ dataReady, instant: false, revealGeneration: gen }),
      { initialProps: { dataReady: false, gen: 1 } },
    );
    rerender({ dataReady: true, gen: 1 });
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS);
    });
    expect(result.current.overview).toBe("ready");

    // New range request: generation bumps, data goes back to loading.
    rerender({ dataReady: false, gen: 2 });
    expect(result.current).toEqual({
      overview: "loading",
      warehouse: "loading",
      storage: "loading",
    });

    // Advancing past the OLD stagger must not flip anything ready.
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS * 5);
    });
    expect(result.current.warehouse).toBe("loading");
  });

  it("clears pending timers on unmount", () => {
    const clearSpy = vi.spyOn(globalThis, "clearTimeout");
    const { rerender, unmount } = renderHook(
      ({ dataReady }) =>
        useSectionStatuses({ dataReady, instant: false, revealGeneration: 1 }),
      { initialProps: { dataReady: false } },
    );
    rerender({ dataReady: true });
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS); // reveal overview, leave 2 pending
    });
    unmount();
    expect(clearSpy).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --workspace apps/web run test -- use-section-statuses`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```ts
// apps/web/src/components/dashboard/use-section-statuses.ts
"use client";

import { useEffect, useRef, useState } from "react";

export type SectionStatus = "loading" | "ready";

export type DashboardSectionKey = "overview" | "warehouse" | "storage";

export type DashboardSectionStatuses = Record<DashboardSectionKey, SectionStatus>;

// Order sections reveal in, and the gap between each reveal.
const SECTION_ORDER: DashboardSectionKey[] = ["overview", "warehouse", "storage"];
export const REVEAL_STEP_MS = 140;

const ALL_LOADING: DashboardSectionStatuses = {
  overview: "loading",
  warehouse: "loading",
  storage: "loading",
};
const ALL_READY: DashboardSectionStatuses = {
  overview: "ready",
  warehouse: "ready",
  storage: "ready",
};

type UseSectionStatusesArgs = {
  // Is the underlying data for the sections available? Today this is one boolean
  // (the single fetch resolved). B2 later makes this per-section without
  // changing anything below.
  dataReady: boolean;
  // Reveal everything at once with no timers: reduced motion or a cached view.
  instant: boolean;
  // Bumped on every new run / range request. Drives the effect and keys the
  // reveal generation so a stale timer from a superseded request is inert.
  revealGeneration: number;
};

/**
 * Derives the per-section loading|ready map. A section is "ready" only when it
 * is both data-ready and revealed (timer-driven). Mount-already-ready and
 * cached/reduced-motion views skip the stagger. All timers are cleared before
 * every reset and on unmount, so a superseded reveal can never flip a section
 * ready over a newer loading.
 */
export function useSectionStatuses({
  dataReady,
  instant,
  revealGeneration,
}: UseSectionStatusesArgs): DashboardSectionStatuses {
  const [statuses, setStatuses] = useState<DashboardSectionStatuses>(() =>
    dataReady ? ALL_READY : ALL_LOADING,
  );
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  // Distinguishes a real loading->ready transition (stagger) from a view that
  // was ready before this effect run (mount-with-data, or a cache swap that
  // never toggled loading) — those reveal instantly.
  const prevDataReadyRef = useRef(dataReady);
  // Latest generation, captured by each timer to no-op if superseded.
  const generationRef = useRef(revealGeneration);

  useEffect(() => {
    generationRef.current = revealGeneration;

    function clearTimers() {
      for (const timer of timersRef.current) {
        clearTimeout(timer);
      }
      timersRef.current = [];
    }

    clearTimers();

    const wasReady = prevDataReadyRef.current;
    prevDataReadyRef.current = dataReady;

    if (!dataReady) {
      setStatuses(ALL_LOADING);
      return clearTimers;
    }

    if (instant || wasReady) {
      setStatuses(ALL_READY);
      return clearTimers;
    }

    // Genuine loading -> ready transition: reset, then reveal in order.
    const scheduledGeneration = revealGeneration;
    setStatuses(ALL_LOADING);
    SECTION_ORDER.forEach((key, index) => {
      const timer = setTimeout(
        () => {
          if (generationRef.current !== scheduledGeneration) {
            return;
          }
          setStatuses((current) => ({ ...current, [key]: "ready" }));
        },
        REVEAL_STEP_MS * (index + 1),
      );
      timersRef.current.push(timer);
    });

    return clearTimers;
  }, [dataReady, instant, revealGeneration]);

  return statuses;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --workspace apps/web run test -- use-section-statuses`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/dashboard/use-section-statuses.ts apps/web/src/components/dashboard/use-section-statuses.test.ts
git commit -m "feat: add useSectionStatuses reveal hook"
```

---

## Task 4: Section `status` prop + skeleton bodies

Convert the three section components to discriminated-union props and render skeleton bodies when `status === "loading"`. The skeleton body keeps the section title and frame (so titles show during load) and reads no data.

**Files:**
- Modify: `apps/web/src/components/dashboard/spend-sections.tsx`
- Test: `apps/web/src/components/dashboard/spend-sections.test.tsx`

- [ ] **Step 0: Migrate existing section renders to `status="ready"`**

The discriminated-union props in this task make `status` required, so every existing render of `OverviewSection`, `WarehouseSpendSection`, and `StorageSpendSection` in `spend-sections.test.tsx` becomes a type error until updated. There are ~16 such renders (the `OverviewSection` blocks plus the `WarehouseSpendSection`/`StorageSpendSection` blocks). Add `status="ready"` as the first prop to each — the existing data props (`currency`, `capacityBalance`, `serviceSpend`, `totalSpend`, `range`, `viewModel`) stay as-is. Example:

```tsx
// before
<OverviewSection
  currency={demoDashboardView.header.currency}
  capacityBalance={demoDashboardView.capacityBalance}
  serviceSpend={demoDashboardView.serviceSpend}
  totalSpend={demoDashboardView.totalSpend}
/>
// after
<OverviewSection
  status="ready"
  currency={demoDashboardView.header.currency}
  capacityBalance={demoDashboardView.capacityBalance}
  serviceSpend={demoDashboardView.serviceSpend}
  totalSpend={demoDashboardView.totalSpend}
/>
```

- [ ] **Step 1: Write the failing test**

Append to `spend-sections.test.tsx` (preserve existing tests/imports; add `OverviewSection`, `WarehouseSpendSection`, `StorageSpendSection`, and the design-system testids to imports as needed):

```tsx
// apps/web/src/components/dashboard/spend-sections.test.tsx (additions)
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  OverviewSection,
  StorageSpendSection,
  WarehouseSpendSection,
} from "./spend-sections";

describe("section skeletons", () => {
  afterEach(cleanup);

  it("renders the Overview skeleton with title and chart skeleton when loading", () => {
    render(<OverviewSection status="loading" />);
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-section-overview")).toBeInTheDocument();
    expect(screen.getByTestId("overview-skeleton")).toBeInTheDocument();
    // The capacity line skeleton matches the real h-80 height.
    const lineChart = screen.getByTestId("overview-capacity-skeleton");
    expect(lineChart).toHaveAttribute("data-chart-skeleton", "line");
    expect(lineChart).toHaveClass("h-80");
  });

  it("renders the Warehouse skeleton chart at h-96 when loading", () => {
    render(<WarehouseSpendSection status="loading" />);
    expect(
      screen.getByTestId("dashboard-section-warehouse-spend"),
    ).toBeInTheDocument();
    const chart = screen.getByTestId("warehouse-spend-skeleton-chart");
    expect(chart).toHaveAttribute("data-chart-skeleton", "bar");
    expect(chart).toHaveClass("h-96");
  });

  it("renders the Storage skeleton with chart h-80 and table skeleton when loading", () => {
    render(<StorageSpendSection status="loading" />);
    expect(
      screen.getByTestId("dashboard-section-storage-spend"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("storage-spend-skeleton-chart")).toHaveClass("h-80");
    expect(screen.getByTestId("detail-table-skeleton")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --workspace apps/web run test -- spend-sections`
Expected: FAIL — `OverviewSection` does not accept `status` / skeleton testids absent (type error or missing elements).

- [ ] **Step 3: Write the implementation**

In `apps/web/src/components/dashboard/spend-sections.tsx`:

(a) Extend the design-system import to include the new skeletons and the existing `SpendBarChart` etc. already imported. Add to the import from `./dashboard-design-system`:

```tsx
  ChartSkeleton,
  DetailTableSkeleton,
  RankedSpendBarsSkeleton,
  StatValueSkeleton,
```

Add to the import from `./detail-tables` is unchanged. Add a `SectionStatus` import:

```tsx
import type { SectionStatus } from "./use-section-statuses";
```

(b) Convert `OverviewSection` to a discriminated union and add the skeleton branch. Replace the current signature/header (lines ~44-59) so the function becomes:

```tsx
type OverviewSectionProps =
  | { status: "loading" }
  | {
      status: "ready";
      capacityBalance?: CapacityBalanceViewModel | null;
      currency: string;
      range?: DashboardViewRange | null;
      serviceSpend: ServiceSpendViewModel;
      totalSpend: TotalSpendViewModel;
    };

export function OverviewSection(props: OverviewSectionProps) {
  if (props.status === "loading") {
    return <OverviewSectionSkeleton />;
  }
  const { capacityBalance, currency, range, serviceSpend, totalSpend } = props;
  const serviceChartData = flattenServiceDailySeries(serviceSpend.dailySeries);
  const totalSpendLabel = buildTotalSpendLabel(range);

  return (
    // ...existing JSX body unchanged...
  );
}

function OverviewSectionSkeleton() {
  return (
    <DashboardSection
      ariaLabel="Overview"
      testId="dashboard-section-overview"
      title="Overview"
    >
      <div data-testid="overview-skeleton" className="grid gap-4">
        <DashboardPanel ariaLabel="Capacity balance summary" title="Ending Balance">
          <StatValueSkeleton />
          <ChartSkeleton
            variant="line"
            heightClass="h-80"
            testId="overview-capacity-skeleton"
          />
        </DashboardPanel>
        <DashboardGrid columns={3} testId="dashboard-grid-overview">
          <section
            aria-label="Total spend summary"
            className="lg:col-span-2 h-full"
            data-dashboard-panel="true"
          >
            <Card className="flex h-full flex-col p-6">
              <StatValueSkeleton />
              <ChartSkeleton
                variant="bar"
                heightClass="h-80"
                testId="overview-total-skeleton-chart"
              />
            </Card>
          </section>
          <DashboardPanel ariaLabel="Total spend by service" fill title="Total spend by service">
            <RankedSpendBarsSkeleton />
          </DashboardPanel>
        </DashboardGrid>
      </div>
    </DashboardSection>
  );
}
```

> Note: `Card` is already imported at the top of `spend-sections.tsx` from `@tremor/react`. Keep the existing `OverviewSection` body verbatim inside the `status === "ready"` branch.

(c) Convert `WarehouseSpendSection` similarly. Replace its signature (lines ~134-145):

```tsx
type WarehouseSpendSectionProps =
  | { status: "loading" }
  | {
      status: "ready";
      currency: string;
      range?: DashboardViewRange | null;
      viewModel: WarehouseSpendViewModel;
    };

export function WarehouseSpendSection(props: WarehouseSpendSectionProps) {
  if (props.status === "loading") {
    return <WarehouseSpendSectionSkeleton />;
  }
  const { currency, range, viewModel } = props;
  const chartData = flattenServiceDailySeries(viewModel.dailySeries);
  const totalLabel = buildTotalWarehouseSpendLabel(range);

  return (
    // ...existing JSX body unchanged...
  );
}

function WarehouseSpendSectionSkeleton() {
  return (
    <DashboardSection
      ariaLabel="Warehouse spend"
      testId="dashboard-section-warehouse-spend"
      title="Warehouse spend"
    >
      <DashboardGrid columns={3} testId="dashboard-grid-warehouse-spend">
        <section
          aria-label="Total warehouse spend"
          className="lg:col-span-2 h-full"
          data-dashboard-panel="true"
        >
          <Card className="flex h-full flex-col p-6">
            <StatValueSkeleton />
            <ChartSkeleton
              variant="bar"
              heightClass="h-96"
              testId="warehouse-spend-skeleton-chart"
            />
          </Card>
        </section>
        <div className="flex h-full min-h-0 flex-col gap-4">
          <section
            aria-label="Warehouse ranking"
            className="flex min-h-0 flex-1 flex-col"
            data-dashboard-panel="true"
          >
            <Card className="flex h-full flex-col p-6">
              <Text>Total spend by warehouse</Text>
              <div className="flex min-h-0 flex-1 flex-col">
                <RankedSpendBarsSkeleton rows={4} />
              </div>
            </Card>
          </section>
          <section
            aria-label="User ranking"
            className="flex min-h-0 flex-1 flex-col"
            data-dashboard-panel="true"
          >
            <Card className="flex h-full flex-col p-6">
              <Text>Total spend by user</Text>
              <div className="flex min-h-0 flex-1 flex-col">
                <RankedSpendBarsSkeleton rows={4} />
              </div>
            </Card>
          </section>
        </div>
      </DashboardGrid>
    </DashboardSection>
  );
}
```

(d) Convert `StorageSpendSection`. Replace its signature (lines ~213-222):

```tsx
type StorageSpendSectionProps =
  | { status: "loading" }
  | {
      status: "ready";
      currency: string;
      range?: DashboardViewRange | null;
      viewModel: StorageSpendViewModel;
    };

export function StorageSpendSection(props: StorageSpendSectionProps) {
  if (props.status === "loading") {
    return <StorageSpendSectionSkeleton />;
  }
  const { currency, range, viewModel } = props;
  const totalLabel = buildStorageSpendLabel(range);

  return (
    // ...existing JSX body unchanged...
  );
}

function StorageSpendSectionSkeleton() {
  return (
    <DashboardSection
      ariaLabel="Storage spend"
      testId="dashboard-section-storage-spend"
      title="Storage spend"
    >
      <DashboardGrid columns={3} testId="dashboard-grid-storage-spend">
        <section
          aria-label="Storage spend"
          className="lg:col-span-2 h-full"
          data-dashboard-panel="true"
        >
          <Card className="flex h-full flex-col p-6">
            <StatValueSkeleton />
            <ChartSkeleton
              variant="bar"
              heightClass="h-80"
              testId="storage-spend-skeleton-chart"
            />
          </Card>
        </section>
        <section
          aria-label="Total spend by database"
          className="flex h-full min-h-0 flex-col"
          data-dashboard-panel="true"
        >
          <DetailTableSkeleton title="Total spend by database" />
        </section>
      </DashboardGrid>
    </DashboardSection>
  );
}
```

> The `Text` component is already imported from `@tremor/react` at the top of `spend-sections.tsx`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --workspace apps/web run test -- spend-sections`
Expected: PASS (existing tests + 3 new skeleton tests).

- [ ] **Step 5: Verify types**

Run: `npm run typecheck`
Expected: PASS — no type errors. (Callers in `cost-dashboard.tsx` are updated in Task 5; until then `tsc` will flag the old call sites. If running typecheck before Task 5, expect the cost-dashboard call sites to error — that is fixed in Task 5. Defer the clean typecheck assertion to Task 5 Step 6.)

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/dashboard/spend-sections.tsx apps/web/src/components/dashboard/spend-sections.test.tsx
git commit -m "feat: add status prop and skeleton bodies to dashboard sections"
```

---

## Task 5: Wire `cost-dashboard.tsx` + update/extend integration tests

Replace the blank-box loading branch with the always-rendered skeleton/ready sections, derive statuses via the hook, add the failed-initial state, and keep `FilterBar` gated on having a view.

**Files:**
- Modify: `apps/web/src/components/dashboard/cost-dashboard.tsx`
- Test: `apps/web/src/components/dashboard/cost-dashboard.test.tsx`

- [ ] **Step 1: Write the failing tests**

Replace the existing two assertions that reference `getByLabelText("Loading dashboard")` and add the new behavior tests.

First, fix the data-ready sentinels. Several existing tests use `await screen.findByText("Overview")` as a "demo data has loaded" gate, then immediately interact with the filter bar (`getByLabelText("Start date")`, `"Apply date range"`). After this change the skeleton renders the `"Overview"` title on mount, so that wait resolves before data lands and the `FilterBar` (gated on `viewModel`) does not yet exist — the subsequent `getByLabelText`/`getByRole` calls throw. Update each such sentinel (cost-dashboard.test.tsx lines ~123, 136, 155, 275, 469, 505) to wait on a ready-only signal instead — the `FilterBar` control they're about to use:

```tsx
// before
await screen.findByText("Overview");
// after — wait for the filter bar (only present once viewModel resolves)
await screen.findByLabelText("Start date");
```

For the test at line ~123 (which asserts on prefetch calls, not the filter bar), wait on a populated KPI such as `await screen.findByText("Total Spend in Last 30 Days")` instead, so the assertion still gates on real data rather than the skeleton title. General rule: replace every `findByText("Overview")` sentinel with a wait on a ready-only signal (a `FilterBar` control the test then uses, or a populated KPI) — never the section title, which now renders during the skeleton state.

Then, update the two existing tests:

In `"resets stale prepared view state when the organization changes"` (currently asserts `getByLabelText("Loading dashboard")` at ~line 257), change the final assertion to:

```tsx
    expect(
      screen.getByTestId("dashboard-section-overview"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("overview-skeleton")).toBeInTheDocument();
```

In `"disables the run action and shows placeholders while loading"` (~line 548), change the body to:

```tsx
  it("disables the run action and shows skeleton sections while loading", () => {
    vi.mocked(fetchDemoDashboardView).mockReturnValue(
      new Promise(() => undefined),
    );

    render(<CostDashboard demoMode />);

    expect(screen.getByRole("button", { name: "Run analysis" })).toBeDisabled();
    expect(screen.getByTestId("overview-skeleton")).toBeInTheDocument();
    expect(
      screen.getByTestId("dashboard-section-warehouse-spend"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("dashboard-section-storage-spend"),
    ).toBeInTheDocument();
    // The filter bar is not rendered until a view exists.
    expect(
      screen.queryByRole("button", { name: "Apply date range" }),
    ).not.toBeInTheDocument();
  });
```

Then add new tests inside the `describe("CostDashboard", ...)` block:

```tsx
  it("staggers section reveal on initial demo load", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);

      render(<CostDashboard demoMode />);

      // Flush the initial fetch microtasks.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      // Data resolved but sections still revealing: overview first.
      act(() => {
        vi.advanceTimersByTime(140);
      });
      expect(screen.getByTestId("dashboard-section-overview")).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(140 * 3);
      });
      // After the full stagger, ready content is present.
      expect(
        screen.getByText("Total Spend in Last 30 Days"),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("reveals all sections instantly under reduced motion", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    try {
      vi.mocked(fetchDemoDashboardView).mockResolvedValue(demoDashboardView);
      render(<CostDashboard demoMode />);
      expect(
        await screen.findByText("Total Spend in Last 30 Days"),
      ).toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows an error state instead of skeletons when the initial run fails", async () => {
    vi.mocked(fetchDemoDashboardView).mockRejectedValue(new Error("boom"));

    render(<CostDashboard demoMode />);

    // `RunStatus` (rendered above the content region) ALSO displays
    // loadState.message, which is "Could not load dashboard data." in the
    // failed state — so the SAME text appears twice on screen. A bare
    // findByText would throw "Found multiple elements". Scope the query to the
    // "Dashboard content" region so it matches only the SectionEmptyState.
    const content = screen.getByLabelText("Dashboard content");
    expect(
      await within(content).findByText("Could not load dashboard data."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("overview-skeleton")).not.toBeInTheDocument();
  });
```

> `within` must be imported from `@testing-library/react` in `cost-dashboard.test.tsx` (add it to the existing import if absent).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --workspace apps/web run test -- cost-dashboard`
Expected: FAIL — skeleton testids not present (still old blank boxes), failed-initial still shows boxes, etc.

- [ ] **Step 3: Update the imports and add the status derivation in `cost-dashboard.tsx`**

Add imports near the existing component imports:

```tsx
import { usePrefersReducedMotion } from "../../lib/use-prefers-reduced-motion";
import { useSectionStatuses } from "./use-section-statuses";
```

Add a `revealGeneration` state and bump it at each load start. Add this state alongside the existing `useState` calls in `CostDashboardContent`:

```tsx
  const [revealGeneration, setRevealGeneration] = useState(0);
```

Bump it at the start of each load path. Add `setRevealGeneration((value) => value + 1);` as the first line inside:
- `loadDemoRun` (just before `runGenerationRef.current += 1;`),
- `loadSnowflakeRun` (just before `runGenerationRef.current += 1;`),
- `loadRange` (just before `rangeRequestSeqRef.current += 1;`),
- and the initial-demo `fetchInitialDemoView` effect (just before `setRunInFlight(true);`).

- [ ] **Step 4: Derive statuses and replace the render branch**

After computing `viewModel` (the `const viewModel = loadState.view ?? data ?? null;` line) and before the `return`, add:

```tsx
  const reduceMotion = usePrefersReducedMotion();
  const dataReady = viewModel != null && loadState.status !== "loading";
  const isFailedWithoutView =
    !viewModel &&
    (loadState.status === "failed" ||
      loadState.status === "expired" ||
      loadState.status === "deleted");
  const sectionStatuses = useSectionStatuses({
    dataReady,
    instant: reduceMotion,
    revealGeneration,
  });
```

Replace the entire `viewModel ? ( ... ) : ( ...loading boxes... )` block inside the `aria-label="Dashboard content"` div with:

```tsx
        {viewModel?.unsupported ? (
          <SectionEmptyState
            message={`${viewModel.unsupported.title}. ${viewModel.unsupported.detail}`}
          />
        ) : isFailedWithoutView ? (
          <SectionEmptyState
            message={loadState.message ?? "Could not load dashboard data."}
          />
        ) : (
          <>
            {viewModel ? (
              <FilterBar
                range={activeRange ?? viewModel.range}
                currency={viewModel.header.currency}
                startDate={startDate}
                endDate={endDate}
                onWindowChange={handleWindowChange}
                onStartDateChange={setStartDate}
                onEndDateChange={setEndDate}
                onApplyDateRange={handleCustomRangeApply}
              />
            ) : null}
            <OverviewSection
              {...(sectionStatuses.overview === "ready" && viewModel
                ? {
                    status: "ready",
                    capacityBalance: viewModel.capacityBalance,
                    currency: viewModel.header.currency,
                    range: activeRange ?? viewModel.range,
                    serviceSpend: viewModel.serviceSpend,
                    totalSpend: viewModel.totalSpend,
                  }
                : { status: "loading" })}
            />
            <WarehouseSpendSection
              {...(sectionStatuses.warehouse === "ready" && viewModel
                ? {
                    status: "ready",
                    currency: viewModel.header.currency,
                    range: activeRange ?? viewModel.range,
                    viewModel: viewModel.warehouseSpend,
                  }
                : { status: "loading" })}
            />
            <StorageSpendSection
              {...(sectionStatuses.storage === "ready" && viewModel
                ? {
                    status: "ready",
                    currency: viewModel.header.currency,
                    range: activeRange ?? viewModel.range,
                    viewModel: viewModel.storageSpend,
                  }
                : { status: "loading" })}
            />
          </>
        )}
```

> The old loading `<section aria-label="Loading dashboard">…three boxes…</section>` block is deleted entirely.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm --workspace apps/web run test -- cost-dashboard`
Expected: PASS — all existing tests (with the two updated assertions) plus the four new tests.

- [ ] **Step 6: Full verification**

Run: `npm run typecheck`
Expected: PASS — clean (`tsc --noEmit`).

Run: `npm run lint:web`
Expected: PASS — eslint clean.

Run: `npm run test:web`
Expected: PASS — full web suite green.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/dashboard/cost-dashboard.tsx apps/web/src/components/dashboard/cost-dashboard.test.tsx
git commit -m "feat: render skeleton dashboard layout with staggered reveal"
```

---

## Task 6: Height-parity guard + manual visual check

A targeted test that the skeleton and ready states use identical height classes on the highest-risk containers (spec §2 "no layout jump"), plus a manual check in the browser.

**Files:**
- Test: `apps/web/src/components/dashboard/spend-sections.test.tsx`

- [ ] **Step 1: Write the failing height-parity test**

Append to `spend-sections.test.tsx`:

```tsx
describe("skeleton/ready height parity", () => {
  afterEach(cleanup);

  it("uses h-96 for the warehouse chart in both states", () => {
    const { unmount } = render(<WarehouseSpendSection status="loading" />);
    expect(screen.getByTestId("warehouse-spend-skeleton-chart")).toHaveClass(
      "h-96",
    );
    unmount();
    // The ready warehouse chart is rendered via SpendBarChart heightClass="h-96"
    // (see WarehouseSpendSection ready branch). Assert the class on its testid.
    // demoWarehouseSpend is a non-empty fixture imported below.
    render(
      <WarehouseSpendSection
        status="ready"
        currency="USD"
        range={null}
        viewModel={demoWarehouseSpend}
      />,
    );
    expect(
      screen.getByTestId("warehouse-spend-tremor-bar-chart"),
    ).toHaveClass("h-96");
  });
});
```

`demoDashboardView` is already imported at the top of `spend-sections.test.tsx` (line 4) — do **not** add a second import. Just derive the fixture from it (place this const near the top-level test helpers, or inline `demoDashboardView.warehouseSpend` at the call site):

```tsx
const demoWarehouseSpend = demoDashboardView.warehouseSpend;
```

- [ ] **Step 2: Run the test to verify it fails or passes**

Run: `npm --workspace apps/web run test -- spend-sections`
Expected: If `demoDashboardView.warehouseSpend.isEmpty` is false, this passes once classes match; if it fails, the mismatch is a real layout-jump bug to fix in `WarehouseSpendSectionSkeleton` (align the heightClass). Confirm the skeleton uses the same `h-96` the ready `SpendBarChart` uses.

> If the demo warehouse fixture is empty (`isEmpty === true`), the ready branch renders `SectionEmptyState` instead of a chart. In that case, drop the ready-state half of this assertion and keep only the skeleton `h-96` check plus a code comment pointing to `WarehouseSpendSection`'s ready `SpendBarChart heightClass="h-96"` as the source of truth. Verify the fixture with: `npm --workspace apps/web run test -- spend-sections` output, or by reading `apps/web/src/lib/demo-dashboard-view.ts`.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/dashboard/spend-sections.test.tsx
git commit -m "test: assert skeleton/ready chart height parity"
```

- [ ] **Step 4: Manual visual check**

Per the user's workflow (verify UI changes visually), ask the user to:
1. Run `npm run dev` and open the dashboard.
2. Click **Run analysis** and confirm: the three real sections appear immediately as shimmer skeletons (titles visible, no blank boxes), then reveal top-to-bottom as data lands, with no layout jump.
3. Toggle OS "Reduce motion" and confirm sections appear together with no stagger.

---

## Self-Review

**Spec coverage:**
- §1 Readiness derivation (hook, data-ready vs revealed, reset, cache/instant) → Task 3.
- §2 Real layout in skeleton form + no-`viewModel` discriminated-union rendering → Task 4 + Task 5 render branch.
- §3 Skeleton primitives (ChartSkeleton/RankedSpendBarsSkeleton/DetailTableSkeleton/StatValueSkeleton; no Tremor empty data; FilterBarSkeleton dropped) → Task 2; FilterBar gated on `viewModel` in Task 5.
- §4 Staggered reveal, generation keying, timer cleanup, reduced-motion/cache instant → Task 3 (hook) + Task 1 (reduced motion) + Task 5 (wiring + generation bumps).
- §5 Edge states: failed initial fetch → Task 5 `isFailedWithoutView`; unsupported bypasses reveal → Task 5 branch order; range-refetch keeps header+filter bar → Task 5 (FilterBar gated only on `viewModel`, which persists across a range refetch).
- Testing: named files, unmount-mid-reveal (Task 3), height-parity (Task 6), reduced-motion + failed-initial + stagger (Task 5), verification commands (Task 5 Step 6).

**Placeholder scan:** The `// ...existing JSX body unchanged...` markers in Task 4 refer to the current, committed section bodies that are explicitly preserved verbatim inside the new `status === "ready"` branch — not new code to invent. All new code is shown in full.

**Type consistency:** `SectionStatus`/`DashboardSectionStatuses` defined in Task 3 and imported in Task 4. Section prop unions (`status: "loading" | "ready"`) match the spread call sites in Task 5. `useSectionStatuses` args (`dataReady`, `instant`, `revealGeneration`) match the call in Task 5. Skeleton testids (`overview-skeleton`, `warehouse-spend-skeleton-chart`, `storage-spend-skeleton-chart`, `detail-table-skeleton`, `overview-capacity-skeleton`) are consistent between Task 4 implementation and Task 4/5/6 tests.
