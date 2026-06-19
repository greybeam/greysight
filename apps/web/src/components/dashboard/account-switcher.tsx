"use client";

import { useEffect, useRef, useState } from "react";

import { useAccountChrome } from "../../lib/account-context";

export default function AccountSwitcher() {
  const account = useAccountChrome();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  // No provider (demo/unauthenticated) or no orgs yet: render nothing — the
  // zero-org case is handled by OrgShell's inline wizard.
  if (!account || account.organizations.length === 0) return null;

  const active =
    account.organizations.find((org) => org.id === account.activeOrganizationId) ??
    account.organizations[0];

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        Account:{" "}
        <span className="font-mono text-slate-200">
          {active.accountLocator ?? active.name}
        </span>
        <span aria-hidden="true">▾</span>
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute left-0 z-50 mt-2 min-w-56 rounded-md border border-hairline bg-surface py-1 shadow-lg"
        >
          {account.organizations.map((org) => (
            <button
              key={org.id}
              role="menuitem"
              type="button"
              className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm text-slate-200 hover:bg-white/5"
              onClick={() => {
                account.setActiveOrganization(org.id);
                setOpen(false);
              }}
            >
              <span className="min-w-0">
                <span className="block truncate">{org.name}</span>
                {org.accountLocator ? (
                  <span className="block truncate font-mono text-xs text-slate-400">
                    {org.accountLocator}
                  </span>
                ) : null}
              </span>
              {org.id === active.id ? (
                <span aria-hidden="true" className="text-chart-purple">
                  ✓
                </span>
              ) : null}
            </button>
          ))}
          <div className="my-1 border-t border-hairline" />
          <button
            role="menuitem"
            type="button"
            className="w-full px-3 py-2 text-left text-sm font-medium text-slate-200 hover:bg-white/5"
            onClick={() => {
              account.openAddAccount();
              setOpen(false);
            }}
          >
            + Add Account
          </button>
        </div>
      ) : null}
    </div>
  );
}
