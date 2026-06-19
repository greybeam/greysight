"use client";

export default function AuthStatus({ label }: { label: string }) {
  return (
    <div
      aria-live="polite"
      className="flex flex-col items-center gap-3 py-2"
      role="status"
    >
      <span
        aria-hidden
        className="h-6 w-6 animate-spin rounded-full border-2 border-hairline border-t-chart-purple"
      />
      <p className="text-sm text-slate-300">{label}</p>
    </div>
  );
}
