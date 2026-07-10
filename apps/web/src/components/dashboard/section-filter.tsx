"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { isFullSelection } from "../../lib/section-filters";

export type SectionFilterProps = {
  options: string[];
  selected: string[];
  onChange: (names: string[]) => void;
  disabled?: boolean;
  label?: string;
};

export function SectionFilter({
  options,
  selected,
  onChange,
  disabled = false,
  label = "Filter",
}: SectionFilterProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const sorted = useMemo(() => [...options].sort(), [options]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const isSubset = !isFullSelection(selected, options);

  useEffect(() => {
    if (!open) return;

    function handleMouseDown(event: MouseEvent) {
      if (
        containerRef.current &&
        event.target instanceof Node &&
        !containerRef.current.contains(event.target)
      ) {
        setOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    document.addEventListener("mousedown", handleMouseDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleMouseDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  function toggle(name: string) {
    // Sort BOTH branches so onChange output is deterministic (the remove branch
    // must sort too — filter alone preserves the incoming order).
    onChange(
      selectedSet.has(name)
        ? selected.filter((n) => n !== name).sort()
        : [...selected, name].sort(),
    );
  }

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={
          isSubset
            ? "h-8 rounded-md border border-slate-500 bg-white/5 px-4 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:bg-surface disabled:text-slate-500"
            : "h-8 rounded-md border border-hairline bg-surface px-4 text-xs font-semibold text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:bg-surface disabled:text-slate-500"
        }
      >
        {label}
        {isSubset ? (
          <span
            data-testid="section-filter-count"
            className="ml-2 rounded-full bg-white/15 px-2 text-xs text-white"
          >
            {selected.length}
          </span>
        ) : null}
      </button>
      {open ? (
        <div
          data-testid="section-filter-popover"
          className="absolute z-10 mt-2 w-72 rounded-md border border-hairline bg-surface p-4 shadow-lg"
        >
          <div className="mb-2 flex gap-4">
            <button
              type="button"
              className="rounded border border-hairline px-2 py-1 text-xs font-medium uppercase tracking-wide text-slate-300 hover:bg-white/5 hover:text-white"
              onClick={() => onChange([...options].sort())}
            >
              Select all
            </button>
            <button
              type="button"
              className="rounded border border-hairline px-2 py-1 text-xs font-medium uppercase tracking-wide text-slate-300 hover:bg-white/5 hover:text-white"
              onClick={() => onChange([])}
            >
              Clear
            </button>
          </div>
          <ul className="max-h-64 space-y-2 overflow-auto">
            {sorted.map((name) => (
              <li key={name}>
                <label className="flex items-center gap-2 text-sm text-slate-200">
                  <input
                    type="checkbox"
                    className="h-4 w-4 shrink-0 accent-slate-300"
                    value={name}
                    aria-label={name}
                    checked={selectedSet.has(name)}
                    onChange={() => toggle(name)}
                  />
                  {name}
                </label>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
