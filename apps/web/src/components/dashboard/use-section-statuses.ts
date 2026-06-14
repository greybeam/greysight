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
