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

describe("useSectionStatuses idle", () => {
  it("never reports a section as idle during render once idle is false (no flash)", () => {
    // Capture the hook's return value on EVERY render (render phase runs before
    // effects). If the idle->loading transition were only handled in the effect,
    // the first render after `idle` flips false would still return the stale
    // ALL_IDLE state — captured here as a one-frame flash. The render-derived
    // guard must prevent any "idle" from ever being reported when idle=false.
    const renders: string[][] = [];
    const { rerender } = renderHook(
      ({ idle }) => {
        const statuses = useSectionStatuses({
          dataReady: false,
          instant: false,
          revealGeneration: idle ? 0 : 1,
          idle,
        });
        renders.push([
          statuses.overview,
          statuses.warehouse,
          statuses.storage,
        ]);
        return statuses;
      },
      { initialProps: { idle: true } },
    );

    // Precondition: while idle, every render reports idle.
    expect(renders.every((r) => r.every((s) => s === "idle"))).toBe(true);
    const rendersDuringIdle = renders.length;

    // Flip out of idle (Run analysis). No render committed from this point on may
    // report "idle" for any section — including the immediate post-toggle frame.
    rerender({ idle: false });
    const rendersAfterToggle = renders.slice(rendersDuringIdle);
    expect(rendersAfterToggle.length).toBeGreaterThan(0);
    for (const frame of rendersAfterToggle) {
      expect(frame).not.toContain("idle");
    }
  });
});

describe("useSectionStatuses per-section readiness", () => {
  it("reveals only sections marked ready when sectionReadiness is provided", () => {
    const { result } = renderHook(() =>
      useSectionStatuses({
        dataReady: true,
        instant: true,
        revealGeneration: 1,
        sectionReadiness: {
          overview: "pending",
          warehouse: "ready",
          storage: "unavailable",
        },
      }),
    );
    expect(result.current).toEqual({
      overview: "loading",
      warehouse: "ready",
      storage: "loading",
    });
  });
});
