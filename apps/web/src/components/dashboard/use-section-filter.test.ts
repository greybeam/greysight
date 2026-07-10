import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useSectionFilter } from "./use-section-filter";

describe("useSectionFilter", () => {
  it("defaults to all options selected", () => {
    const { result } = renderHook(() => useSectionFilter(["a", "b"]));
    expect(result.current.selected).toEqual(["a", "b"]);
  });

  it("keeps the current selection when the option set is unchanged by value", () => {
    const { result, rerender } = renderHook(({ opts }) => useSectionFilter(opts), {
      initialProps: { opts: ["a", "b", "c"] },
    });
    act(() => result.current.setSelected(["a"]));
    expect(result.current.selected).toEqual(["a"]);
    // Same entities, different array reference/order → selection preserved.
    rerender({ opts: ["c", "b", "a"] });
    expect(result.current.selected).toEqual(["a"]);
  });

  it("re-syncs to all when the option set differs by value", () => {
    const { result, rerender } = renderHook(({ opts }) => useSectionFilter(opts), {
      initialProps: { opts: ["a", "b", "c"] },
    });
    act(() => result.current.setSelected(["a"]));
    rerender({ opts: ["a", "b", "d"] }); // set changed
    expect(result.current.selected).toEqual(["a", "b", "d"]);
  });

  it("preserves the selection across a null (loading) round trip and only re-syncs on a real option-set change", () => {
    const { result, rerender } = renderHook(
      ({ opts }: { opts: string[] | null }) => useSectionFilter(opts),
      { initialProps: { opts: ["a", "b", "c"] as string[] | null } },
    );
    act(() => result.current.setSelected(["a"]));
    expect(result.current.selected).toEqual(["a"]);

    // Transient loading render: options go null — selection must be preserved,
    // not reset.
    rerender({ opts: null });
    expect(result.current.selected).toEqual(["a"]);

    // Back to ready with the SAME options — still preserved, not reset to all.
    rerender({ opts: ["a", "b", "c"] });
    expect(result.current.selected).toEqual(["a"]);

    // Ready again with a genuinely different option set — re-syncs to all.
    rerender({ opts: ["a", "b", "d"] });
    expect(result.current.selected).toEqual(["a", "b", "d"]);
  });
});
