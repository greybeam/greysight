import { useMemo, useState } from "react";

// Stable value-key for an option set: sorted, then JSON-encoded so entity names
// containing spaces can't alias (a plain join with a space separator would let
// ["a b","c"] and ["a","b c"] compare equal).
function optionsKey(options: string[]): string {
  return JSON.stringify([...options].sort());
}

export function useSectionFilter(options: string[]): {
  selected: string[];
  setSelected: (names: string[]) => void;
} {
  const key = useMemo(() => optionsKey(options), [options]);
  const [selected, setSelected] = useState<string[]>(options);
  const [lastKey, setLastKey] = useState(key);

  // Reconcile DURING render (React's recommended pattern for deriving state from
  // a changing prop) rather than in an effect — so the option-set change and the
  // reset to "all" commit in the same frame, with no stale-selection flash. We
  // re-sync only when the available entities differ by value; a range switch
  // yielding the same set keeps the current selection.
  if (key !== lastKey) {
    setLastKey(key);
    setSelected(options);
    return { selected: options, setSelected };
  }

  return { selected, setSelected };
}
