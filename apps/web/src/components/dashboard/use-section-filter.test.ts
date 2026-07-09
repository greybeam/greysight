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
});
