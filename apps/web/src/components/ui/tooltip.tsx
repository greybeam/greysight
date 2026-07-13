"use client";

import { useId, type ReactNode } from "react";

type TooltipProps = {
  // The hover/focus content shown in the bubble.
  content: ReactNode;
  // The trigger the tooltip describes (text, icon, etc.).
  children: ReactNode;
  // Extra classes for the trigger wrapper (e.g. a dotted-underline label).
  className?: string;
};

// An instant, CSS-only hover/focus tooltip — no JS timers, so it appears the
// moment the pointer or keyboard focus lands, unlike the browser's native
// `title` (which waits ~2–5s and can't be styled). Extracted from the
// account-identifier tooltip in the connect wizard so the styling stays shared.
// The trigger is focusable (`tabIndex=0`) so keyboard users reach the content
// via `group-focus-within`, and `aria-describedby` links the bubble for screen
// readers.
export function Tooltip({ content, children, className = "" }: TooltipProps) {
  const tooltipId = useId();
  return (
    <span className="group relative inline-flex">
      <span
        aria-describedby={tooltipId}
        className={`inline-flex cursor-help focus-visible:outline focus-visible:outline-1 focus-visible:outline-slate-400 ${className}`}
        tabIndex={0}
      >
        {children}
      </span>
      <span
        id={tooltipId}
        role="tooltip"
        className="pointer-events-none absolute left-0 top-full z-10 mt-1 hidden w-max max-w-[min(20rem,80vw)] rounded bg-slate-800 px-3 py-2 text-left text-[11px] font-normal normal-case leading-snug tracking-normal text-slate-200 shadow-lg group-hover:block group-focus-within:block"
      >
        {content}
      </span>
    </span>
  );
}
