import { useRef, type MutableRefObject } from "react";

// Keeps a ref pointed at the latest `value` by assigning during render, so async
// callbacks/effects can read the freshest value without re-subscribing or
// re-keying on reference churn. Callers read `ref.current` at call time.
//
// The render-time assignment is the whole point of the pattern (the ref must
// track the value every render), so the `react-hooks/refs` rule is suppressed
// here — once, inside the hook — rather than at every call site.
export function useLatestRef<T>(value: T): MutableRefObject<T> {
  const ref = useRef(value);
  // eslint-disable-next-line react-hooks/refs -- latest-ref pattern; see use-latest-ref.ts
  ref.current = value;
  return ref;
}
