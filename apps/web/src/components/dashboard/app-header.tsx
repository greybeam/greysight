"use client";

import { useAccountChrome } from "../../lib/account-context";
import { showBrandLogo } from "../../lib/brand";
import AccountSwitcher from "./account-switcher";
import { AppNav } from "./app-nav";
import InviteUser from "./invite-user";

export function AppHeader({ children }: { children?: React.ReactNode }) {
  const account = useAccountChrome();
  const brandLogo = showBrandLogo();

  return (
    <>
      <header className="border-b border-hairline bg-surface">
        <div className="mx-auto flex w-full max-w-[1200px] flex-wrap items-center justify-between gap-3 px-6 py-3">
          <div className="flex flex-wrap items-center gap-3">
            {brandLogo ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                alt="Greybeam"
                className="h-7 w-7 rounded-md"
                height={28}
                src="/greybeam_assets/greybeam_logo.svg"
                width={28}
              />
            ) : null}
            <p className="font-display text-lg font-semibold text-slate-50">
              Greybeam
            </p>
            <AccountSwitcher />
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3">
            {children}
            <InviteUser />
            {account ? (
              <button
                className="h-9 rounded-md border border-hairline px-3 text-sm font-medium text-slate-300 hover:bg-white/5"
                onClick={account.onSignOut}
                type="button"
              >
                Sign out
              </button>
            ) : null}
          </div>
        </div>
        {account?.signOutError ? (
          <div className="mx-auto w-full max-w-[1200px] px-6 pb-2">
            <p className="text-xs font-medium text-red-400" role="alert">
              {account.signOutError}
            </p>
          </div>
        ) : null}
      </header>
      <div className="mx-auto w-full max-w-[1200px] px-6">
        <div>
          <AppNav />
        </div>
      </div>
    </>
  );
}
