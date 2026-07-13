"use client";

import { useAccountChrome } from "../../lib/account-context";
import { showBrandLogo } from "../../lib/brand";
import type { HeaderViewModel } from "../../lib/dashboard-contracts";
import Spinner from "../ui/spinner";
import AccountSwitcher from "./account-switcher";
import { AppNav } from "./app-nav";
import { formatCachedAsOfLabel } from "./dashboard-design-system";
import InviteUser from "./invite-user";

export type DashboardModeLabel =
  | "Demo"
  | "Local Snowflake"
  | "Authenticated Snowflake";

type DashboardHeaderProps = {
  header: HeaderViewModel | null;
  // Snowflake account locator sourced from the persisted org connection so it
  // can render before any analysis run; falls back to the run's view model.
  accountLocator?: string | null;
  runDisabled: boolean;
  // True while an analysis run is in flight; swaps the button label for a
  // spinner so the in-progress state is visible while the button is disabled.
  running?: boolean;
  onRun: () => void;
  // ISO8601 timestamp of the cached run currently on screen, or null/undefined
  // when the view came from a fresh run. Purely frontend-derived — not part of
  // the HeaderViewModel contract.
  cachedAsOf?: string | null;
};

export default function DashboardHeader({
  header,
  runDisabled,
  running = false,
  onRun,
  cachedAsOf,
}: DashboardHeaderProps) {
  const account = useAccountChrome();
  // The "Greybeam" wordmark renders in every build; the env flag only gates the
  // logo image (see brand.ts), not the brand name.
  const brandLogo = showBrandLogo();
  return (
    <header className="border-b border-hairline bg-surface">
      <div className="mx-auto flex w-full max-w-[1200px] flex-wrap items-center justify-between gap-3 px-6 py-3">
        <div className="flex flex-wrap items-center gap-3">
          {brandLogo ? (
            // Static brand mark served from /public; next/image would force
            // dangerouslyAllowSVG config for no optimization benefit here.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              alt="Greybeam"
              className="h-7 w-7 rounded-md"
              height={28}
              src="/greybeam_assets/greybeam_logo.svg"
              width={28}
            />
          ) : null}
          <h1 className="font-display text-lg font-semibold text-slate-50">
            Greybeam
          </h1>
          <AccountSwitcher />
          <AppNav />
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          {cachedAsOf ? (
            <span className="text-xs font-medium text-slate-400" role="status">
              Using cached view as of {formatCachedAsOfLabel(cachedAsOf)}
            </span>
          ) : null}
          {header?.dataModeLabel === "Estimated" ? (
            <span className="text-xs font-medium text-amber-400">
              Estimated spend at {header.estimatedCreditPriceLabel}/credit - billed data
              unavailable
            </span>
          ) : null}
          <InviteUser />
          <button
            aria-busy={running}
            className="flex h-9 items-center gap-2 rounded-md bg-chart-purple px-3 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:bg-surface disabled:text-slate-500 disabled:opacity-100"
            disabled={runDisabled}
            type="button"
            onClick={onRun}
          >
            {running ? (
              <>
                <Spinner />
                Running…
              </>
            ) : (
              "Run analysis"
            )}
          </button>
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
  );
}
