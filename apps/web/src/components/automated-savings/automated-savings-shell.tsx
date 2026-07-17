"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { useAccountChrome } from "../../lib/account-context";
import {
  checkAccess,
  fetchStatus,
  fetchWarehouses,
  setGlobalSwitch,
  type AutomatedSavingsStatus,
  type WarehouseRow,
} from "../../lib/automated-savings-api";
import { queryKeys } from "../../lib/query-keys";
import {
  useQueryIdentity,
  type QueryIdentitySnapshot,
} from "../../lib/query-identity";
import { DashboardApiError } from "../../lib/dashboard-errors";
import { useLatestRef } from "../../lib/use-latest-ref";
import { AppHeader } from "../dashboard/app-header";
import DashboardFailureMessage from "../dashboard/dashboard-failure-message";
import { Switch } from "../ui/switch";
import {
  buildGrantSql,
  normalizeRoleName,
  OptInGate,
  UNKNOWN_ROLE_PLACEHOLDER,
} from "./opt-in-gate";
import { SuspensionEventsTable } from "./suspension-events-table";
import { SuspensionsChart } from "./suspensions-chart";
import { WarehouseTable } from "./warehouse-table";

type LoadState = "idle" | "loading" | "ready" | "error";
type LoadFailure = { message: string; reportable: boolean };

function autoSavingsFailure(error: unknown): LoadFailure {
  if (error instanceof DashboardApiError && error.userSafeMessage) {
    return { message: error.userSafeMessage, reportable: false };
  }
  return {
    message: "We couldn’t load Auto Savings. Please try again.",
    reportable: true,
  };
}

// The dark app chrome the dashboard establishes (`dark … bg-canvas
// [color-scheme:dark]`). OrgShell renders its signed-in children bare, so —
// exactly like CostDashboard — this page must supply its own dark background,
// or the design tokens (bg-canvas/surface, text-slate-100, chart-purple) and
// Tremor badges render as washed-out light-on-white. Centered to the same
// 1200px content width as the dashboard.
function SavingsChrome({ children }: { children: React.ReactNode }) {
  return (
    <main className="dark min-h-screen bg-canvas [color-scheme:dark]">
      <AppHeader />
      <div className="mx-auto w-full max-w-[1200px] px-6 py-6">{children}</div>
    </main>
  );
}

function BlockingOptInModal({ children }: { children: React.ReactNode }) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || dialog.open) return;
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      // jsdom and older embedded browsers do not implement showModal. The
      // browser path above remains the real modal/top-layer implementation.
      dialog.setAttribute("open", "");
    }
  }, []);

  return (
    <dialog
      aria-labelledby="automated-savings-opt-in-title"
      className="dark m-auto w-[min(42rem,calc(100%-2rem))] max-w-none bg-transparent p-0 text-slate-100 backdrop:bg-black/70 [color-scheme:dark]"
      onCancel={(event) => event.preventDefault()}
      ref={dialogRef}
    >
      {children}
    </dialog>
  );
}

export function AutomatedSavingsShell() {
  const account = useAccountChrome();
  const orgId = account?.activeOrganizationId ?? null;
  const accessToken = account?.accessToken ?? null;
  const role =
    account?.organizations.find((org) => org.id === orgId)?.role ?? null;
  const isAdmin = role === "owner" || role === "admin";

  const identity = useQueryIdentity();
  const identitySnapshot = identity.snapshot;
  const userId = identitySnapshot.userId;
  const queryClient = useQueryClient();

  // Read the access token at call time rather than keying queries on it: Supabase
  // rotates it roughly hourly, and a rotation must not invalidate cache entries
  // or restart in-flight reads.
  const accessTokenRef = useLatestRef(accessToken);

  const statusKey = queryKeys.autoSavings.status(userId, orgId ?? "");
  const warehousesKey = queryKeys.autoSavings.warehouses(userId, orgId ?? "");

  const statusQuery = useQuery({
    queryKey: statusKey,
    queryFn: () => fetchStatus(orgId!, { accessToken: accessTokenRef.current }),
    enabled: Boolean(orgId),
  });
  // Access is a manual, on-demand check: it stays disabled until the user asks
  // for it (Check access / Refresh) or the post-agreement flow refetches it.
  const accessQuery = useQuery({
    queryKey: queryKeys.autoSavings.access(userId, orgId ?? ""),
    queryFn: () => checkAccess(orgId!, { accessToken: accessTokenRef.current }),
    enabled: false,
  });
  const warehousesQuery = useQuery({
    queryKey: warehousesKey,
    queryFn: () =>
      fetchWarehouses(orgId!, { accessToken: accessTokenRef.current }),
    enabled: Boolean(orgId && statusQuery.data?.agreed),
  });

  // Merge the on-demand access-grant fields over the base status at render time
  // only; the merged shape is never written back under either source key.
  const baseStatus = statusQuery.data ?? null;
  const access = accessQuery.data ?? null;
  const status: AutomatedSavingsStatus | null =
    baseStatus && access
      ? {
          ...baseStatus,
          grantPresent: access.grantPresent,
          grantCheckedAt: access.grantCheckedAt,
          roleName: access.roleName,
        }
      : baseStatus;
  const warehouses: WarehouseRow[] = warehousesQuery.data ?? [];

  const [globalSwitching, setGlobalSwitching] = useState(false);
  const [controlError, setControlError] = useState<string | null>(null);
  // null = "not yet initialized for this org" — the init effect below sets a
  // one-time default from the first ready snapshot so that enabling a warehouse
  // mid-setup doesn't yank the pane closed out from under the user.
  const [configOpen, setConfigOpen] = useState<boolean | null>(null);
  const checkOperationRef = useRef<object | null>(null);
  const globalOperationRef = useRef<object | null>(null);

  useEffect(() => {
    // Reset per-org UI state when the workspace changes. The queries themselves
    // switch cache entries automatically via their identity-scoped keys.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setConfigOpen(null);
    setControlError(null);
    setGlobalSwitching(false);
    checkOperationRef.current = null;
    globalOperationRef.current = null;
  }, [orgId]);

  // Derive the coarse load state from the two initial reads. Cached data stays
  // visible during background refetches: an error panel appears only when a read
  // fails *and* there is nothing cached to show.
  let loadState: LoadState;
  if (!orgId) {
    loadState = "idle";
  } else if (statusQuery.isError && baseStatus === null) {
    loadState = "error";
  } else if (baseStatus === null) {
    loadState = "loading";
  } else if (baseStatus.agreed) {
    if (warehousesQuery.isError && warehousesQuery.data === undefined) {
      loadState = "error";
    } else if (warehousesQuery.data === undefined) {
      loadState = "loading";
    } else {
      loadState = "ready";
    }
  } else {
    loadState = "ready";
  }

  const activeError =
    statusQuery.isError && baseStatus === null
      ? statusQuery.error
      : warehousesQuery.error;
  const loadFailure: LoadFailure | null =
    loadState === "error" ? autoSavingsFailure(activeError) : null;

  const enabledCount = warehouses.filter((warehouse) => warehouse.enabled).length;
  const hasEnabledConfig = enabledCount > 0;

  useEffect(() => {
    if (loadState === "ready" && status?.agreed && configOpen === null) {
      // Freeze the collapsible's default from the first ready snapshot only —
      // otherwise enabling a warehouse mid-setup would collapse the pane out
      // from under the user the instant hasEnabledConfig flips to true.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setConfigOpen(!hasEnabledConfig);
    }
  }, [loadState, status, configOpen, hasEnabledConfig]);

  const checking = accessQuery.isFetching;

  function handleRetry() {
    void statusQuery.refetch();
    if (statusQuery.data?.agreed) void warehousesQuery.refetch();
  }

  async function handleGlobalToggle() {
    if (!orgId || !isAdmin || !status?.agreed || globalOperationRef.current) {
      return;
    }
    const operation = {};
    const captured = identity.capture();
    const nextEnabled = !status.globalEnabled;
    globalOperationRef.current = operation;
    setGlobalSwitching(true);
    setControlError(null);
    try {
      await setGlobalSwitch(orgId, nextEnabled, {
        accessToken: accessTokenRef.current,
      });
      if (!identity.isCurrent(captured)) return;
      // Re-read both status and warehouses from the server rather than faking
      // the new global_enabled locally: a global flip can change warehouse
      // operational status, so the authoritative refetch keeps the whole scope
      // consistent instead of splitting truth across an optimistic patch.
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.autoSavings.status(
            captured.userId,
            captured.orgId,
          ),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.autoSavings.warehouses(
            captured.userId,
            captured.orgId,
          ),
        }),
      ]);
    } catch {
      if (identity.isCurrent(captured)) {
        setControlError("Couldn’t update Auto Savings. Please try again.");
      }
    } finally {
      // Always release the busy flag for the latest operation, regardless of
      // identity staleness: it is local UI state and a switch back must not
      // leave the global switch permanently disabled.
      if (globalOperationRef.current === operation) {
        globalOperationRef.current = null;
        setGlobalSwitching(false);
      }
    }
  }

  async function handleCheckAccess() {
    if (!orgId || !status?.agreed || checkOperationRef.current) return;
    const operation = {};
    checkOperationRef.current = operation;
    setControlError(null);
    try {
      await accessQuery.refetch();
    } finally {
      if (checkOperationRef.current === operation) {
        checkOperationRef.current = null;
      }
    }
  }

  async function handleAgreementComplete(captured: QueryIdentitySnapshot) {
    // Identity is captured when `agree` STARTS (in OptInGate) and threaded here,
    // so the entire completion — invalidation and the access refetch — is
    // dropped if the org/account switched out from under the in-flight request.
    if (!orgId || !identity.isCurrent(captured)) return;
    // Invalidating the whole auto-savings scope refetches the actively observed
    // status query (one status GET) and marks warehouses stale; the warehouse
    // query performs its single initial fetch when the refreshed agreed status
    // flips its `enabled` on. Reading the refreshed status from the cache — not
    // a second statusQuery.refetch() — avoids a duplicate status GET.
    await queryClient.invalidateQueries({
      queryKey: queryKeys.autoSavings.scope(captured.userId, captured.orgId),
    });
    if (!identity.isCurrent(captured)) return;
    const refreshed = queryClient.getQueryData<AutomatedSavingsStatus>(
      queryKeys.autoSavings.status(captured.userId, captured.orgId),
    );
    // Access is a manual check, so refresh it explicitly after agreement.
    if (refreshed?.agreed) void accessQuery.refetch();
  }

  function handleRowChange(row: WarehouseRow) {
    if (!orgId) return;
    queryClient.setQueryData<WarehouseRow[]>(warehousesKey, (prev) =>
      (prev ?? []).map((existing) =>
        existing.name === row.name ? row : existing,
      ),
    );
  }

  const handleRefresh = useCallback(async () => {
    await warehousesQuery.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [warehousesKey]);

  // A failed access check surfaces the same control-level error the previous
  // imperative flow set; a successful retry clears it because isError flips back.
  const shownControlError =
    controlError ??
    (accessQuery.isError
      ? "Couldn’t check Snowflake access. Please try again."
      : null);

  const grantSql = status ? buildGrantSql(status.roleName) : null;

  return (
    <SavingsChrome>
      <div className="mb-6">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-display text-2xl font-semibold text-slate-50">
            Auto Savings
          </h1>
          <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-300">
            Experimental
          </span>
        </div>
        <p className="mt-2 text-sm text-slate-400">
          This experimental feature reduces idle warehouse time by safely
          suspending enrolled warehouses when they are not in use. {" "}
          <a
            className="text-slate-300 underline decoration-slate-600 underline-offset-2 hover:text-slate-100"
            href="https://github.com/greybeam/greysight/blob/main/docs/automated-savings-how-it-works.md"
            target="_blank"
            rel="noreferrer"
          >
            Learn more {" "}
          </a>
           about how Auto Savings works.
        </p>
      </div>

      {!orgId ? (
        <p className="rounded-lg border border-hairline bg-surface p-6 text-sm text-slate-400">
          Auto Savings requires an authenticated Snowflake workspace.
        </p>
      ) : loadState === "idle" || loadState === "loading" ? (
        <p
          className="rounded-lg border border-hairline bg-surface p-6 text-sm text-slate-400"
          role="status"
        >
          Loading configuration…
        </p>
      ) : loadState === "error" || !status ? (
        <div className="rounded-lg border border-hairline bg-surface p-6">
          <p className="text-sm font-medium text-red-400" role="alert">
            <DashboardFailureMessage
              message={
                loadFailure?.message ??
                "We couldn’t load Auto Savings. Please try again."
              }
              reportable={loadFailure?.reportable ?? true}
            />
          </p>
          <button
            type="button"
            className="mt-3 rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            onClick={handleRetry}
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          <div className="space-y-4">
            {status.agreed &&
            status.grantPresent === false &&
            status.grantCheckedAt !== null &&
            grantSql ? (
              <div
                className="rounded-md border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200"
                role="alert"
              >
                <p className="font-semibold">Grant missing</p>
                <p className="mt-1 text-rose-300">
                  The Snowflake role no longer has MANAGE WAREHOUSES. Suspend
                  commands will fail and back off until the grant is restored:
                </p>
                <pre className="mt-2 overflow-auto rounded-md border border-hairline bg-canvas p-3 text-xs text-slate-100">
                  <code>{grantSql}</code>
                </pre>
              </div>
            ) : null}

            <div className="flex items-center justify-between gap-3">
              <label className="flex items-center gap-2 text-sm font-medium text-slate-200">
                <Switch
                  aria-label="Auto Savings enabled for all warehouses"
                  aria-busy={globalSwitching}
                  checked={status.globalEnabled}
                  disabled={!isAdmin || !status.agreed || globalSwitching}
                  onCheckedChange={() => void handleGlobalToggle()}
                />
                {status.globalEnabled
                  ? "Auto Savings on"
                  : "Auto Savings off"}
              </label>
              <button
                type="button"
                disabled={checking || !status.agreed}
                aria-busy={checking}
                onClick={() => void handleCheckAccess()}
                className="h-9 rounded-md border border-hairline px-3 text-sm font-medium text-slate-300 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {checking ? "Checking…" : "Check access / Refresh"}
              </button>
            </div>

            {shownControlError ? (
              <p className="text-sm font-medium text-red-400" role="alert">
                {shownControlError}
              </p>
            ) : null}

            <details
              className="group/config rounded-lg border border-hairline bg-surface"
              open={configOpen ?? !hasEnabledConfig}
              onToggle={(event) => setConfigOpen(event.currentTarget.open)}
            >
              <summary className="flex list-none items-center justify-between gap-3 p-4 text-left [&::-webkit-details-marker]:hidden cursor-pointer">
                <span className="text-sm font-semibold text-slate-100">
                  Warehouse configuration
                </span>
                <span className="flex items-center gap-2 text-xs text-slate-400">
                  {enabledCount} of {warehouses.length} enabled
                  <svg
                    aria-hidden="true"
                    className="h-4 w-4 shrink-0 text-slate-500 transition-transform group-open/config:rotate-180"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                    viewBox="0 0 24 24"
                  >
                    <path
                      d="M6 9l6 6 6-6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
              </summary>
              <div className="border-t border-hairline p-4">
                {!hasEnabledConfig ? (
                  <p className="mb-3 text-sm text-slate-400">
                    Enable a warehouse below to start saving idle compute.
                  </p>
                ) : null}
                <WarehouseTable
                  accessToken={accessToken}
                  isAdmin={isAdmin && status.agreed}
                  orgId={orgId}
                  warehouses={warehouses}
                  onChange={handleRowChange}
                  onRefresh={handleRefresh}
                />
              </div>
            </details>

            {status.agreed && hasEnabledConfig ? (
              <>
                <SuspensionsChart
                  key={`chart-${orgId}`}
                  accessToken={accessToken}
                  orgId={orgId}
                />
                <SuspensionEventsTable
                  key={`events-${orgId}`}
                  accessToken={accessToken}
                  orgId={orgId}
                />
              </>
            ) : null}
          </div>
          {!status.agreed ? (
            <BlockingOptInModal>
              <OptInGate
                orgId={orgId}
                roleName={
                  normalizeRoleName(status.roleName) ?? UNKNOWN_ROLE_PLACEHOLDER
                }
                onAgreed={handleAgreementComplete}
              />
            </BlockingOptInModal>
          ) : null}
        </>
      )}
    </SavingsChrome>
  );
}
