"use client";

import { useEffect, useRef, useState } from "react";

import type {
  DashboardViewSectionKey,
  DashboardViewSectionStatuses,
} from "../../lib/dashboard-contracts";

export type SectionStatus = "idle" | "loading" | "ready";

export type DashboardSectionStatuses = Record<
  DashboardViewSectionKey,
  SectionStatus
>;

// Order sections reveal in, and the gap between each reveal.
const SECTION_ORDER: DashboardViewSectionKey[] = [
  "overview",
  "warehouse",
  "storage",
];
export const REVEAL_STEP_MS = 140;

const ALL_IDLE: DashboardSectionStatuses = {
  overview: "idle",
  warehouse: "idle",
  storage: "idle",
};
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
  // Pre-run Snowflake: no run has been started and no data is present. When set,
  // every section resolves to "idle" (a static empty state, NOT the animated
  // skeleton) so users aren't misled into thinking the dashboard is loading.
  // Takes precedence over the loading/reveal paths; ignored once a run starts.
  idle?: boolean;
  // Reveal everything at once with no timers: reduced motion or a cached view.
  instant: boolean;
  // Bumped on every new run / range request. Drives the effect and keys the
  // reveal generation so a stale timer from a superseded request is inert.
  revealGeneration: number;
  // When present (progressive Snowflake runs), reveal is driven by the server's
  // per-section readiness instead of the timed stagger. Absent for demo/cached
  // views, which keep the original stagger.
  sectionReadiness?: DashboardViewSectionStatuses;
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
  idle = false,
  instant,
  revealGeneration,
  sectionReadiness,
}: UseSectionStatusesArgs): DashboardSectionStatuses {
  const [statuses, setStatuses] = useState<DashboardSectionStatuses>(() =>
    idle ? ALL_IDLE : dataReady ? ALL_READY : ALL_LOADING,
  );
  // Depend on the per-section status primitives, not the object reference, so a
  // freshly-parsed `sectionReadiness` with identical values doesn't re-run the
  // effect (and re-setState) on every render — which would loop indefinitely.
  const hasReadiness = sectionReadiness !== undefined;
  const readinessOverview = sectionReadiness?.overview;
  const readinessWarehouse = sectionReadiness?.warehouse;
  const readinessStorage = sectionReadiness?.storage;
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

    if (idle || !dataReady) {
      // Static reset to a non-animated (idle) or skeleton (loading) state:
      //  - idle: pre-run Snowflake — a stable boolean holding every section in
      //    the static empty state until a run starts and flips it false.
      //  - !dataReady: a new run/range started — every section must immediately
      //    return to its skeleton so a stale reveal can't paint over the next
      //    load.
      // Both are derived-state synchronization, not a cascading update loop.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStatuses(idle ? ALL_IDLE : ALL_LOADING);
      return clearTimers;
    }

    // Progressive path: map server readiness directly; no timers. `unavailable`
    // deliberately maps to "loading" (skeleton) — this task adds no explicit
    // unavailable/error UI.
    if (hasReadiness) {
      setStatuses({
        overview: readinessOverview === "ready" ? "ready" : "loading",
        warehouse: readinessWarehouse === "ready" ? "ready" : "loading",
        storage: readinessStorage === "ready" ? "ready" : "loading",
      });
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
  }, [
    dataReady,
    idle,
    instant,
    revealGeneration,
    hasReadiness,
    readinessOverview,
    readinessWarehouse,
    readinessStorage,
  ]);

  // Derive the returned map during render so the idle<->non-idle transition is
  // synchronous. The `setStatuses` calls above only take effect after commit;
  // when `idle` flips false (user clicks "Run analysis"), that stale ALL_IDLE
  // state would otherwise be committed for one frame before the effect swaps it
  // to ALL_LOADING — flashing the static idle CTA over the skeletons. Coercing
  // here guarantees no "idle" status is ever reported once `idle` is false, and
  // conversely reports idle immediately when `idle` is true.
  if (idle) {
    return ALL_IDLE;
  }
  if (
    statuses.overview === "idle" ||
    statuses.warehouse === "idle" ||
    statuses.storage === "idle"
  ) {
    return ALL_LOADING;
  }
  return statuses;
}
