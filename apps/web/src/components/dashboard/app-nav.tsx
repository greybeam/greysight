"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/dashboard", label: "Home" },
  { href: "/automated-savings", label: "Automated Savings" },
] as const;

export function AppNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Primary" className="inline-flex h-8 rounded-md border border-hairline bg-surface p-0.5">
      {TABS.map((tab) => {
        const active = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={active
              ? "h-full rounded bg-chart-purple px-4 text-xs font-semibold leading-7 text-white"
              : "h-full rounded px-4 text-xs font-medium leading-7 text-slate-400 hover:bg-white/5"}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
