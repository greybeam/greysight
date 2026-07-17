"use client";

import { useId, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
} from "@tremor/react";

import {
  fetchWarehouses,
  toggleWarehouse,
  type WarehouseRow,
} from "../../lib/automated-savings-api";
import { queryKeys } from "../../lib/query-keys";
import { useQueryIdentity } from "../../lib/query-identity";
import { DashboardApiError } from "../../lib/dashboard-errors";
import { Switch } from "../ui/switch";
import { Tooltip } from "../ui/tooltip";

// fetchJson throws a DashboardApiError whose `userSafeMessage` carries the API's
// server-curated reason (see readUserSafeMessage). Surface that when present;
// otherwise fall back to a generic message so a mutation failure never bubbles
// up as an unhandled promise rejection or a raw "failed with 502" string.
function toUserMessage(error: unknown): string {
  if (error instanceof DashboardApiError && error.userSafeMessage) {
    return error.userSafeMessage;
  }
  return "Something went wrong. Please try again.";
}

type WarehouseTableProps = {
  orgId: string;
  warehouses: WarehouseRow[];
  isAdmin: boolean;
  accessToken?: string | null;
  onChange: (row: WarehouseRow) => void;
  onRefresh?: () => Promise<void>;
};

type DisplayStatus = {
  label: string;
  color: "emerald" | "amber" | "rose" | "slate";
};

// A warehouse is unsupported when it isn't a STANDARD warehouse, the API marks
// it unsupported, or its operational status is already "unsupported".
function isUnsupported(warehouse: WarehouseRow): boolean {
  return (
    warehouse.type !== "STANDARD" ||
    !warehouse.supported ||
    warehouse.status === "unsupported"
  );
}

// A single, derived answer to "is Auto Savings actually running on this
// warehouse right now?" — collapsing the old separate AUTO_RESUME-health and
// operational-status columns. First matching condition wins.
function deriveDisplayStatus(warehouse: WarehouseRow): DisplayStatus {
  if (isUnsupported(warehouse)) {
    return { label: "Unsupported", color: "slate" };
  }
  if (warehouse.status === "transitioning") {
    return { label: "Transitioning", color: "amber" };
  }
  if (!warehouse.autoResumeOk) return { label: "Can't automate", color: "rose" };
  if (!warehouse.enabled) return { label: "Disabled", color: "slate" };
  return { label: "Savings enabled", color: "emerald" };
}

function deriveToggleDisabledReason(
  warehouse: WarehouseRow,
  isAdmin: boolean,
  busy: boolean,
  unsupported: boolean,
): string | null {
  if (!isAdmin) return "Only owners and admins can change warehouse enrollment.";
  if (busy) return "Warehouse enrollment is updating.";
  if (warehouse.enabled) return null;
  if (warehouse.status === "transitioning") {
    return "This warehouse is transitioning and can't be enrolled yet.";
  }
  if (unsupported) return "This warehouse type isn't supported.";
  if (!warehouse.autoResumeOk) {
    return "AUTO_RESUME is off — Greysight can't automate safely.";
  }
  return null;
}

// A column header with an instant, styled explanatory tooltip (see ui/Tooltip).
// The dotted underline signals the header is hoverable for more detail.
function HeaderWithTooltip({
  label,
  tooltip,
}: {
  label: string;
  tooltip: string;
}) {
  return (
    <TableHeaderCell className="whitespace-nowrap px-4 py-3.5 text-xs font-semibold text-slate-100">
      <Tooltip
        className="underline decoration-dotted decoration-slate-500 underline-offset-4"
        content={tooltip}
      >
        {label}
      </Tooltip>
    </TableHeaderCell>
  );
}

export function WarehouseTable({
  orgId,
  warehouses,
  isAdmin,
  accessToken,
  onChange,
  onRefresh,
}: WarehouseTableProps) {
  return (
    <Table aria-label="Warehouses" className="w-full text-left">
      <TableHead>
        <TableRow>
          <TableHeaderCell className="whitespace-nowrap px-4 py-3.5 text-xs font-semibold text-slate-100">
            Name
          </TableHeaderCell>
          <HeaderWithTooltip
            label="Auto Suspend"
            tooltip="The current Snowflake AUTO_SUSPEND setting. Greysight requests safe Snowflake suspension after the billing floor."
          />
          <HeaderWithTooltip
            label="Status"
            tooltip="Whether Auto Savings is actively running on this warehouse."
          />
          <TableHeaderCell className="whitespace-nowrap px-4 py-3.5 text-xs font-semibold text-slate-100">
            Enabled
          </TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody className="divide-y divide-hairline align-top">
        {warehouses.map((warehouse) => (
          <WarehouseRowView
            key={warehouse.name}
            accessToken={accessToken}
            isAdmin={isAdmin}
            orgId={orgId}
            warehouse={warehouse}
            onChange={onChange}
            onRefresh={onRefresh}
          />
        ))}
      </TableBody>
    </Table>
  );
}

type WarehouseRowViewProps = {
  orgId: string;
  warehouse: WarehouseRow;
  isAdmin: boolean;
  accessToken?: string | null;
  onChange: (row: WarehouseRow) => void;
  onRefresh?: () => Promise<void>;
};

function WarehouseRowView({
  orgId,
  warehouse,
  isAdmin,
  accessToken,
  onChange,
  onRefresh,
}: WarehouseRowViewProps) {
  const disabledReasonId = useId();
  const queryClient = useQueryClient();
  const queryIdentity = useQueryIdentity();
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);

  const unsupported = isUnsupported(warehouse);
  const disabledReason = deriveToggleDisabledReason(
    warehouse,
    isAdmin,
    busy,
    unsupported,
  );
  const toggleDisabled = disabledReason !== null;
  const displayStatus = deriveDisplayStatus(warehouse);

  async function handleToggle() {
    if (toggleDisabled) return;
    // Capture identity at the start of the mutation so a late-arriving
    // authoritative result (below) is dropped if the org/account switches
    // out from under us while the request is in flight.
    const captured = queryIdentity.capture();
    const nextEnabled = !warehouse.enabled;
    setBusy(true);
    setActionError(null);
    setRefreshFailed(false);
    try {
      await toggleWarehouse(orgId, warehouse.name, nextEnabled, { accessToken });
    } catch (error) {
      if (queryIdentity.isCurrent(captured)) {
        setActionError(toUserMessage(error));
        setBusy(false);
      }
      return;
    }

    if (!queryIdentity.isCurrent(captured)) return;
    // Optimistic row feedback; the authoritative query result (a full-list
    // write on enrollment, or an invalidation refetch on disable) follows.
    onChange({ ...warehouse, enabled: nextEnabled });

    const warehousesKey = queryKeys.autoSavings.warehouses(
      captured.userId,
      orgId,
    );
    const statusKey = queryKeys.autoSavings.status(captured.userId, orgId);

    if (nextEnabled) {
      // First enrollment hydrates authoritative warehouse details (e.g. a
      // transitioning status) directly into the cache rather than invalidating
      // the warehouses query, so the just-written truth is not immediately
      // clobbered by a refetch.
      try {
        const rows = await fetchWarehouses(orgId, { accessToken });
        const authoritative = rows.find((row) => row.name === warehouse.name);
        if (!authoritative) {
          throw new Error("The enrolled warehouse could not be refreshed.");
        }
        if (!queryIdentity.isCurrent(captured)) return;
        queryClient.setQueryData(warehousesKey, rows);
      } catch {
        if (queryIdentity.isCurrent(captured)) {
          setRefreshFailed(true);
          setActionError(
            "Enrollment was saved, but its details could not be refreshed.",
          );
        }
      }
      if (queryIdentity.isCurrent(captured)) {
        await queryClient.invalidateQueries({ queryKey: statusKey });
      }
    } else if (queryIdentity.isCurrent(captured)) {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: warehousesKey }),
        queryClient.invalidateQueries({ queryKey: statusKey }),
      ]);
    }
    if (queryIdentity.isCurrent(captured)) setBusy(false);
  }

  async function handleRefresh() {
    if (!onRefresh || busy) return;
    setBusy(true);
    try {
      await onRefresh();
      setRefreshFailed(false);
      setActionError(null);
    } catch {
      setActionError("The warehouse details could not be refreshed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <TableRow>
      <TableCell className="px-4 py-2 text-xs font-semibold text-slate-100">
        {warehouse.name}
      </TableCell>
      <TableCell className="px-4 py-2 text-xs text-slate-300">
        {warehouse.autoSuspend === null ? "—" : `${warehouse.autoSuspend}s`}
      </TableCell>
      <TableCell className="px-4 py-2 text-xs text-slate-300">
        <Badge color={displayStatus.color}>{displayStatus.label}</Badge>
      </TableCell>
      <TableCell className="px-4 py-2 text-xs text-slate-300">
        <span className="group relative inline-flex">
          <Switch
            aria-label={warehouse.name}
            aria-describedby={disabledReason ? disabledReasonId : undefined}
            checked={warehouse.enabled}
            disabled={toggleDisabled}
            onCheckedChange={handleToggle}
          />
          {disabledReason ? (
            <span className="sr-only" id={disabledReasonId}>
              {disabledReason}
            </span>
          ) : null}
        </span>
        {actionError ? (
          <p className="mt-1 max-w-[16rem] text-xs text-rose-300" role="alert">
            {actionError}
          </p>
        ) : null}
        {refreshFailed && onRefresh ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void handleRefresh()}
            className="mt-1 text-xs font-medium text-slate-300 underline disabled:opacity-50"
          >
            Retry refresh
          </button>
        ) : null}
      </TableCell>
    </TableRow>
  );
}
