"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/dashboard", label: "Home" },
  { href: "/automated-savings", label: "Auto Savings" },
] as const;

export function AppNav() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Primary"
      className="relative z-10 -mt-px inline-flex h-9 border-b border-hairline bg-surface"
    >
      {TABS.map((tab) => {
        const active = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={
              active
                ? "flex h-full items-center bg-chart-purple px-4 text-xs font-semibold text-white"
                : "flex h-full items-center px-4 text-xs font-medium text-slate-500 hover:bg-white/5 hover:text-slate-300"
            }
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
