"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => undefined;
  }
  const query = window.matchMedia(QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function getSnapshot(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia(QUERY).matches;
}

// Server (and matchMedia-less) snapshot: default to animate so the staggered
// reveal is the default and reduced motion is an opt-out.
function getServerSnapshot(): boolean {
  return false;
}

/**
 * Reports the user's reduced-motion preference via useSyncExternalStore, which
 * reads the live `matchMedia` value on the client, returns false during SSR to
 * avoid hydration mismatches, and re-renders when the preference changes —
 * without a synchronous setState inside an effect.
 */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
