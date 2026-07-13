"use client";

import { useCallback, useEffect, useState } from "react";

import { useAccountChrome } from "../../lib/account-context";
import {
  checkAccess,
  fetchStatus,
  fetchWarehouses,
  setGlobalSwitch,
  type AutomatedSavingsStatus,
  type WarehouseRow,
} from "../../lib/automated-savings-api";
import { Switch } from "../ui/switch";
import OrgShell from "../org/org-shell";
import { normalizeRoleName, OptInGate, quoteIdent, UNKNOWN_ROLE_PLACEHOLDER } from "./opt-in-gate";
import { WarehouseTable } from "./warehouse-table";

type AutomatedSavingsShellProps = {
  authRequired: boolean;
};

// The client entry: wraps its content in OrgShell internally (like
// DashboardRuntimeShell), so the page component just renders this shell.
export function AutomatedSavingsShell({ authRequired }: AutomatedSavingsShellProps) {
  return (
    <OrgShell authRequired={authRequired} bypassModeLabel="Demo mode">
      <AutomatedSavingsContent />
    </OrgShell>
  );
}

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
      <div className="mx-auto w-full max-w-[1200px]">{children}</div>
    </main>
  );
}

function AutomatedSavingsContent() {
  const account = useAccountChrome();
  const orgId = account?.activeOrganizationId ?? null;
  const accessToken = account?.accessToken ?? null;
  const role = account?.organizations.find((org) => org.id === orgId)?.role ?? null;
  const isAdmin = role === "owner" || role === "admin";

  const [status, setStatus] = useState<AutomatedSavingsStatus | null>(null);
  const [warehouses, setWarehouses] = useState<WarehouseRow[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [checking, setChecking] = useState(false);

  const load = useCallback(async () => {
    if (!orgId) return;
    setLoadState("loading");
    try {
      const nextStatus = await fetchStatus(orgId, { accessToken });
      setStatus(nextStatus);
      if (nextStatus.agreed) {
        const rows = await fetchWarehouses(orgId, { accessToken });
        setWarehouses(rows);
      }
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, [orgId, accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!orgId) {
    return null;
  }

  if (loadState === "idle" || loadState === "loading") {
    return (
      <SavingsChrome>
        <p className="p-6 text-sm text-slate-400" role="status">
          Loading Automated Savings…
        </p>
      </SavingsChrome>
    );
  }

  if (loadState === "error" || !status) {
    return (
      <SavingsChrome>
        <div className="p-6">
          <p className="text-sm font-medium text-red-400" role="alert">
            We couldn’t load Automated Savings. Please try again.
          </p>
          <button
            type="button"
            className="mt-3 rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            onClick={() => void load()}
          >
            Retry
          </button>
        </div>
      </SavingsChrome>
    );
  }

  if (!status.agreed) {
    return (
      <SavingsChrome>
        <div className="p-6">
          <OptInGate
            orgId={orgId}
            roleName={normalizeRoleName(status.roleName) ?? UNKNOWN_ROLE_PLACEHOLDER}
            onAgreed={() => void load()}
          />
        </div>
      </SavingsChrome>
    );
  }

  async function handleGlobalToggle() {
    if (!orgId || !isAdmin || !status) return;
    const nextEnabled = !status.globalEnabled;
    await setGlobalSwitch(orgId, nextEnabled, { accessToken });
    setStatus({ ...status, globalEnabled: nextEnabled });
  }

  async function handleCheckAccess() {
    if (!orgId || !status) return;
    setChecking(true);
    try {
      const result = await checkAccess(orgId, { accessToken });
      setStatus({
        ...status,
        grantPresent: result.grantPresent,
        grantCheckedAt: result.grantCheckedAt,
        roleName: result.roleName,
      });
    } finally {
      setChecking(false);
    }
  }

  function handleRowChange(row: WarehouseRow) {
    setWarehouses((prev) => prev.map((existing) => (existing.name === row.name ? row : existing)));
  }

  const grantSql = `GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE ${quoteIdent(normalizeRoleName(status.roleName) ?? UNKNOWN_ROLE_PLACEHOLDER)};`;

  return (
    <SavingsChrome>
      <div className="space-y-4 p-6">
        {status.grantPresent === false ? (
        <div
          className="rounded-md border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200"
          role="alert"
        >
          <p className="font-semibold">Grant missing</p>
          <p className="mt-1 text-rose-300">
            The Snowflake role no longer has MANAGE WAREHOUSES. Automation is
            paused until the grant is restored:
          </p>
          <pre className="mt-2 overflow-auto rounded-md border border-hairline bg-canvas p-3 text-xs text-slate-100">
            <code>{grantSql}</code>
          </pre>
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm font-medium text-slate-200">
          <Switch
            aria-label="Automated Savings enabled for all warehouses"
            checked={status.globalEnabled}
            disabled={!isAdmin}
            onCheckedChange={() => void handleGlobalToggle()}
          />
          {status.globalEnabled ? "Automated Savings on" : "Automated Savings off"}
        </label>
        <button
          type="button"
          disabled={checking}
          aria-busy={checking}
          onClick={() => void handleCheckAccess()}
          className="h-9 rounded-md border border-hairline px-3 text-sm font-medium text-slate-300 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {checking ? "Checking…" : "Check access / Refresh"}
        </button>
      </div>

      <WarehouseTable
        accessToken={accessToken}
        isAdmin={isAdmin}
        orgId={orgId}
        warehouses={warehouses}
        onChange={handleRowChange}
      />
      </div>
    </SavingsChrome>
  );
}
