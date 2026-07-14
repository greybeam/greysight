"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useAccountChrome } from "../../lib/account-context";
import {
  checkAccess,
  fetchStatus,
  fetchWarehouses,
  setGlobalSwitch,
  type AutomatedSavingsStatus,
  type WarehouseRow,
} from "../../lib/automated-savings-api";
import { AppHeader } from "../dashboard/app-header";
import { Switch } from "../ui/switch";
import {
  buildGrantSql,
  normalizeRoleName,
  OptInGate,
  UNKNOWN_ROLE_PLACEHOLDER,
} from "./opt-in-gate";
import { WarehouseTable } from "./warehouse-table";

type LoadState = "idle" | "loading" | "ready" | "error";

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

  const [status, setStatus] = useState<AutomatedSavingsStatus | null>(null);
  const [warehouses, setWarehouses] = useState<WarehouseRow[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [checking, setChecking] = useState(false);
  const [globalSwitching, setGlobalSwitching] = useState(false);
  const [controlError, setControlError] = useState<string | null>(null);
  const loadSequenceRef = useRef(0);
  const currentOrgIdRef = useRef<string | null>(null);
  const checkOperationRef = useRef<object | null>(null);
  const globalOperationRef = useRef<object | null>(null);

  const load = useCallback(async () => {
    const requestSequence = ++loadSequenceRef.current;
    const requestOrgId = orgId;
    if (!requestOrgId) return;
    const isCurrentRequest = () =>
      loadSequenceRef.current === requestSequence &&
      currentOrgIdRef.current === requestOrgId;

    setLoadState("loading");
    setStatus(null);
    setWarehouses([]);
    checkOperationRef.current = null;
    globalOperationRef.current = null;
    setChecking(false);
    setGlobalSwitching(false);
    setControlError(null);
    try {
      const nextStatus = await fetchStatus(requestOrgId, { accessToken });
      if (!isCurrentRequest()) return;
      let rows: WarehouseRow[] = [];
      if (nextStatus.agreed) {
        rows = await fetchWarehouses(requestOrgId, { accessToken });
        if (!isCurrentRequest()) return;
      }
      setStatus(nextStatus);
      setWarehouses(rows);
      setLoadState("ready");
    } catch {
      if (!isCurrentRequest()) return;
      setLoadState("error");
    }
  }, [orgId, accessToken]);

  useEffect(() => {
    currentOrgIdRef.current = orgId;
    // This effect synchronizes the selected workspace with its remote status.
    // The load function owns the visible loading state for initial load, org
    // switches, retries, and successful agreement refreshes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    return () => {
      // Supersede in-flight reads and mutations before the next workspace load.
      loadSequenceRef.current += 1;
      if (currentOrgIdRef.current === orgId) currentOrgIdRef.current = null;
    };
  }, [load, orgId]);

  async function handleGlobalToggle() {
    if (!orgId || !isAdmin || !status?.agreed || globalOperationRef.current) {
      return;
    }
    const operationOrgId = orgId;
    const operationSequence = loadSequenceRef.current;
    const operation = {};
    const isCurrentOperation = () =>
      currentOrgIdRef.current === operationOrgId &&
      loadSequenceRef.current === operationSequence;
    const nextEnabled = !status.globalEnabled;
    globalOperationRef.current = operation;
    setGlobalSwitching(true);
    setControlError(null);
    try {
      await setGlobalSwitch(operationOrgId, nextEnabled, { accessToken });
      if (!isCurrentOperation()) return;
      setStatus((current) =>
        current?.agreed ? { ...current, globalEnabled: nextEnabled } : current,
      );
    } catch {
      if (isCurrentOperation()) {
        setControlError(
          "Couldn’t update Auto Savings. Please try again.",
        );
      }
    } finally {
      if (globalOperationRef.current === operation) {
        globalOperationRef.current = null;
        if (isCurrentOperation()) setGlobalSwitching(false);
      }
    }
  }

  async function handleCheckAccess() {
    if (!orgId || !status?.agreed || checkOperationRef.current) return;
    const operationOrgId = orgId;
    const operationSequence = loadSequenceRef.current;
    const operation = {};
    const isCurrentOperation = () =>
      currentOrgIdRef.current === operationOrgId &&
      loadSequenceRef.current === operationSequence;
    checkOperationRef.current = operation;
    setChecking(true);
    setControlError(null);
    try {
      const result = await checkAccess(operationOrgId, { accessToken });
      if (!isCurrentOperation()) return;
      setStatus((current) =>
        current?.agreed
          ? {
              ...current,
              grantPresent: result.grantPresent,
              grantCheckedAt: result.grantCheckedAt,
              roleName: result.roleName,
            }
          : current,
      );
    } catch {
      if (isCurrentOperation()) {
        setControlError("Couldn’t check Snowflake access. Please try again.");
      }
    } finally {
      if (checkOperationRef.current === operation) {
        checkOperationRef.current = null;
        if (isCurrentOperation()) setChecking(false);
      }
    }
  }

  const agreementGeneration = loadSequenceRef.current;
  function handleAgreementComplete() {
    if (
      !orgId ||
      currentOrgIdRef.current !== orgId ||
      loadSequenceRef.current !== agreementGeneration
    ) {
      return;
    }
    void load();
  }

  function handleRowChange(row: WarehouseRow) {
    if (!orgId || currentOrgIdRef.current !== orgId) return;
    setWarehouses((prev) =>
      prev.map((existing) => (existing.name === row.name ? row : existing)),
    );
  }

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
            We couldn’t load Auto Savings. Please try again.
          </p>
          <button
            type="button"
            className="mt-3 rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            onClick={() => void load()}
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          <div className="space-y-4">
            {status.agreed && status.grantPresent === false && grantSql ? (
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

            {controlError ? (
              <p className="text-sm font-medium text-red-400" role="alert">
                {controlError}
              </p>
            ) : null}

            <WarehouseTable
              accessToken={accessToken}
              isAdmin={isAdmin && status.agreed}
              orgId={orgId}
              warehouses={warehouses}
              onChange={handleRowChange}
              onRefresh={load}
            />
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
