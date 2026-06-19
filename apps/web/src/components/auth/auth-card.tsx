"use client";

import type { ReactNode } from "react";

// Subtle brand glow behind the card: a purple wash toward the top and a lime
// wash toward the bottom, both low-opacity. Literal hex (rgba) is required here
// because Tailwind has no alpha token for these brand colors. Kept faint so the
// card stays the focus.
const GLOW_BACKGROUND =
  "radial-gradient(60% 50% at 30% 15%, rgba(159, 87, 231, 0.14), transparent 70%)," +
  "radial-gradient(55% 45% at 75% 90%, rgba(201, 233, 48, 0.10), transparent 70%)";

export default function AuthCard({ children }: { children: ReactNode }) {
  return (
    <main className="dark relative flex min-h-screen items-center justify-center overflow-hidden bg-canvas p-6 [color-scheme:dark]">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{ background: GLOW_BACKGROUND }}
      />
      <section className="relative w-full max-w-sm rounded-xl border border-hairline bg-surface p-6 shadow-xl">
        <div className="flex flex-col items-center gap-2">
          {/* Static brand mark from /public; next/image would force
              dangerouslyAllowSVG for no benefit. Decorative — the wordmark below
              already names the brand to assistive tech. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            alt=""
            className="h-10 w-10 rounded-md"
            height={40}
            src="/greybeam_assets/greybeam_logo.svg"
            width={40}
          />
          <h1 className="font-display text-xl font-semibold text-slate-50">
            Greybeam
          </h1>
        </div>
        <div className="mt-6">{children}</div>
      </section>
    </main>
  );
}
