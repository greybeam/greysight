"use client";

import type { ButtonHTMLAttributes } from "react";

type SwitchProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "onChange" | "type" | "role" | "aria-checked" | "onClick"
> & {
  checked: boolean;
  onCheckedChange?: (checked: boolean) => void;
};

// A pill-shaped on/off toggle styled after shadcn/ui's Switch, without pulling
// in Radix: rendered as a role="switch" button so screen readers announce the
// on/off state and jest-dom's toBeChecked() reads aria-checked. Green track
// when on, grey when off, with a sliding white thumb.
export function Switch({
  checked,
  onCheckedChange,
  disabled,
  className = "",
  ...props
}: SwitchProps) {
  return (
    <button
      {...props}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange?.(!checked)}
      className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer items-center rounded-full border-0 p-0 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chart-purple focus-visible:ring-offset-2 focus-visible:ring-offset-canvas disabled:cursor-not-allowed disabled:opacity-50 ${
        checked ? "bg-emerald-500" : "bg-slate-600"
      } ${className}`}
    >
      <span
        className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-5" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}
