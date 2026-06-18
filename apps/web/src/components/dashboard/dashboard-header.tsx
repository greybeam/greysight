"use client";

import { useAccountChrome } from "../../lib/account-context";
import { showBrandLogo } from "../../lib/brand";
import type { HeaderViewModel } from "../../lib/dashboard-contracts";
import Spinner from "../ui/spinner";

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
};

export default function DashboardHeader({
  header,
  accountLocator,
  runDisabled,
  running = false,
  onRun,
}: DashboardHeaderProps) {
  const account = useAccountChrome();
  const locator = accountLocator ?? header?.accountLocator ?? null;
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
          {locator ? (
            <span className="text-xs text-slate-400">
              Account:{" "}
              <span className="font-mono text-slate-200">{locator}</span>
            </span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          {header?.dataModeLabel === "Estimated" ? (
            <span className="text-xs font-medium text-amber-400">
              Estimated spend at {header.estimatedCreditPriceLabel}/credit - billed data
              unavailable
            </span>
          ) : null}
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
