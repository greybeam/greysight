"use client";

import { useId, useState } from "react";
import { Badge } from "@tremor/react";

import {
  reconcileWarehouse,
  setManagedDefault,
  toggleWarehouse,
  type WarehouseRow,
} from "../../lib/automated-savings-api";
import { Switch } from "../ui/switch";

const MANAGED_DEFAULT_FLOOR = 60;

// The API surfaces a user-facing reason as the `detail` after the status code
// (see fetchJson). Prefer that over the raw "failed with 502: …" prefix; fall
// back to a generic message so a mutation failure never bubbles up as an
// unhandled promise rejection.
function toUserMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    const marker = error.message.indexOf(": ");
    return marker >= 0 ? error.message.slice(marker + 2) : error.message;
  }
  return "Something went wrong. Please try again.";
}

type WarehouseTableProps = {
  orgId: string;
  warehouses: WarehouseRow[];
  isAdmin: boolean;
  accessToken?: string | null;
  onChange: (row: WarehouseRow) => void;
};

function statusLabel(status: WarehouseRow["status"]): string {
  switch (status) {
    case "idle":
      return "Idle";
    case "mid_suspend":
      return "Mid-suspend";
    case "in_cooldown":
      return "In cooldown";
    case "drifted":
      return "Drifted";
    case "unsupported":
      return "Unsupported";
    default:
      return status;
  }
}

function statusColor(status: WarehouseRow["status"]): "emerald" | "amber" | "rose" | "slate" {
  switch (status) {
    case "idle":
      return "emerald";
    case "mid_suspend":
    case "in_cooldown":
      return "amber";
    case "drifted":
      return "rose";
    default:
      return "slate";
  }
}

export function WarehouseTable({ orgId, warehouses, isAdmin, accessToken, onChange }: WarehouseTableProps) {
  return (
    <table aria-label="Warehouses" className="w-full text-left text-xs text-slate-300">
      <thead className="text-slate-100">
        <tr>
          <th className="whitespace-nowrap px-4 py-3.5 font-semibold">Name</th>
          <th className="whitespace-nowrap px-4 py-3.5 font-semibold">Size</th>
          <th className="whitespace-nowrap px-4 py-3.5 font-semibold"># clusters</th>
          <th className="whitespace-nowrap px-4 py-3.5 font-semibold">State</th>
          <th className="whitespace-nowrap px-4 py-3.5 font-semibold">AUTO_SUSPEND</th>
          <th className="whitespace-nowrap px-4 py-3.5 font-semibold">AUTO_RESUME health</th>
          <th className="whitespace-nowrap px-4 py-3.5 font-semibold">Status</th>
          <th className="whitespace-nowrap px-4 py-3.5 font-semibold">Enabled</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-hairline align-top">
        {warehouses.map((warehouse) => (
          <WarehouseRowView
            key={warehouse.name}
            accessToken={accessToken}
            isAdmin={isAdmin}
            orgId={orgId}
            warehouse={warehouse}
            onChange={onChange}
          />
        ))}
      </tbody>
    </table>
  );
}

type WarehouseRowViewProps = {
  orgId: string;
  warehouse: WarehouseRow;
  isAdmin: boolean;
  accessToken?: string | null;
  onChange: (row: WarehouseRow) => void;
};

function WarehouseRowView({ orgId, warehouse, isAdmin, accessToken, onChange }: WarehouseRowViewProps) {
  const inputId = useId();
  // Unenrolled warehouses have a null managed_default — show an empty input
  // rather than coercing to a misleading number.
  const [draftValue, setDraftValue] = useState(
    warehouse.managedDefault === null ? "" : String(warehouse.managedDefault),
  );
  const [floorWarning, setFloorWarning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const unsupported = warehouse.type !== "STANDARD";
  const toggleDisabled = !isAdmin || !warehouse.autoResumeOk || unsupported || busy;
  const editDisabled = !isAdmin || unsupported || busy;

  async function commitManagedDefault() {
    const parsed = Number(draftValue);
    if (!Number.isFinite(parsed) || parsed < MANAGED_DEFAULT_FLOOR) {
      setFloorWarning(true);
      setDraftValue(
        warehouse.managedDefault === null ? "" : String(warehouse.managedDefault),
      );
      return;
    }
    setFloorWarning(false);
    if (parsed === warehouse.managedDefault) return;
    setBusy(true);
    setActionError(null);
    try {
      await setManagedDefault(orgId, warehouse.name, parsed, { accessToken });
      onChange({ ...warehouse, managedDefault: parsed });
    } catch (error) {
      setActionError(toUserMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function handleToggle() {
    if (toggleDisabled) return;
    const nextEnabled = !warehouse.enabled;
    setBusy(true);
    setActionError(null);
    try {
      await toggleWarehouse(orgId, warehouse.name, nextEnabled, { accessToken });
      onChange({ ...warehouse, enabled: nextEnabled });
    } catch (error) {
      setActionError(toUserMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function handleReconcile() {
    setBusy(true);
    setActionError(null);
    try {
      await reconcileWarehouse(orgId, warehouse.name, true, { accessToken });
      onChange({ ...warehouse, driftState: "ok", status: "idle" });
    } catch (error) {
      setActionError(toUserMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr>
      <td className="px-4 py-2 font-semibold text-slate-100">{warehouse.name}</td>
      <td className="px-4 py-2">{warehouse.size}</td>
      <td className="px-4 py-2">
        {warehouse.startedClusters ?? "—"}
        {warehouse.maxClusterCount !== null ? `/${warehouse.maxClusterCount}` : ""}
        {warehouse.minClusterCount ? ` (min ${warehouse.minClusterCount})` : ""}
      </td>
      <td className="px-4 py-2">{warehouse.state}</td>
      <td className="px-4 py-2">
        <span className="group relative inline-flex items-center gap-1">
          <label className="sr-only" htmlFor={inputId}>
            {`${warehouse.name} AUTO_SUSPEND`}
          </label>
          <input
            id={inputId}
            type="number"
            min={MANAGED_DEFAULT_FLOOR}
            placeholder="—"
            disabled={editDisabled}
            value={draftValue}
            className="w-20 rounded border border-hairline bg-canvas px-2 py-1 text-slate-100 disabled:opacity-50"
            onChange={(event) => setDraftValue(event.target.value)}
            onBlur={commitManagedDefault}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void commitManagedDefault();
              }
            }}
          />
          {floorWarning ||
          (warehouse.managedDefault !== null &&
            warehouse.managedDefault <= MANAGED_DEFAULT_FLOOR) ? (
            <span
              className="cursor-help text-amber-400"
              role="img"
              aria-label="the AUTO_SUSPEND we restore this warehouse to — defaults to the value captured at opt-in; edit to change it."
              title="the AUTO_SUSPEND we restore this warehouse to — defaults to the value captured at opt-in; edit to change it."
            >
              &#9888;
            </span>
          ) : null}
        </span>
      </td>
      <td className="px-4 py-2">
        {warehouse.autoResumeOk ? (
          <Badge color="emerald">OK</Badge>
        ) : (
          <span className="inline-flex items-center gap-1">
            <Badge color="rose">Off</Badge>
            <span className="text-rose-300">
              AUTO_RESUME off — can&apos;t automate safely
            </span>
          </span>
        )}
      </td>
      <td className="px-4 py-2">
        <span className="inline-flex items-center gap-1">
          <Badge color={statusColor(unsupported ? "unsupported" : warehouse.status)}>
            {statusLabel(unsupported ? "unsupported" : warehouse.status)}
          </Badge>
          {warehouse.status === "drifted" ? (
            <button
              type="button"
              disabled={!isAdmin || busy}
              onClick={handleReconcile}
              className="rounded border border-hairline px-2 py-0.5 text-xs font-medium text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Reconcile
            </button>
          ) : null}
        </span>
      </td>
      <td className="px-4 py-2">
        <span
          className="group relative inline-flex"
          title={
            unsupported
              ? "Snowpark-optimized warehouses aren't supported yet"
              : !warehouse.autoResumeOk
                ? "AUTO_RESUME off — can't automate safely"
                : undefined
          }
        >
          <Switch
            aria-label={warehouse.name}
            checked={warehouse.enabled}
            disabled={toggleDisabled}
            onCheckedChange={handleToggle}
          />
        </span>
        {actionError ? (
          <p className="mt-1 max-w-[16rem] text-xs text-rose-300" role="alert">
            {actionError}
          </p>
        ) : null}
      </td>
    </tr>
  );
}
