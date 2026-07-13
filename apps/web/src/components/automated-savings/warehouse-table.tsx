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
import { Tooltip } from "../ui/tooltip";

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

type DisplayStatus = {
  label: string;
  color: "emerald" | "amber" | "rose" | "slate";
};

// A single, derived answer to "is automated savings actually running on this
// warehouse right now?" — collapsing the old separate AUTO_RESUME-health and
// operational-status columns. First matching condition wins.
function deriveDisplayStatus(warehouse: WarehouseRow): DisplayStatus {
  if (warehouse.type !== "STANDARD") return { label: "Unsupported", color: "slate" };
  if (!warehouse.autoResumeOk) return { label: "Can't automate", color: "rose" };
  if (!warehouse.enabled) return { label: "Disabled", color: "slate" };
  switch (warehouse.status) {
    case "drifted":
      return { label: "Drifted", color: "rose" };
    case "mid_suspend":
      return { label: "Suspending", color: "amber" };
    case "in_cooldown":
      return { label: "In cooldown", color: "amber" };
    default:
      return { label: "Savings enabled", color: "emerald" };
  }
}

// A column header with an instant, styled explanatory tooltip (see ui/Tooltip).
// The dotted underline signals the header is hoverable for more detail.
function HeaderWithTooltip({ label, tooltip }: { label: string; tooltip: string }) {
  return (
    <th className="whitespace-nowrap px-4 py-3.5 font-semibold">
      <Tooltip
        className="underline decoration-dotted decoration-slate-500 underline-offset-4"
        content={tooltip}
      >
        {label}
      </Tooltip>
    </th>
  );
}

export function WarehouseTable({ orgId, warehouses, isAdmin, accessToken, onChange }: WarehouseTableProps) {
  return (
    <table aria-label="Warehouses" className="w-full text-left text-xs text-slate-300">
      <thead className="text-slate-100">
        <tr>
          <th className="whitespace-nowrap px-4 py-3.5 font-semibold">Name</th>
          <HeaderWithTooltip
            label="Default Auto Suspend"
            tooltip="This is the AUTO_SUSPEND a warehouse will be restored to after Greysight suspends it."
          />
          <HeaderWithTooltip
            label="Status"
            tooltip="Whether automated savings is actively running on this warehouse."
          />
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
  // A warehouse that isn't enrolled (or has no captured managed default yet)
  // has no server-side row to persist an edit against — editing the blank
  // input would create stale/partial state that the next enroll overwrites.
  const editDisabled =
    !isAdmin || unsupported || busy || !warehouse.enabled || warehouse.managedDefault == null;
  const displayStatus = deriveDisplayStatus(warehouse);

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
      <td className="px-4 py-2">
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
        {floorWarning ? (
          <p className="mt-1 max-w-[10rem] text-xs text-amber-300" role="alert">
            AUTO_SUSPEND can&apos;t go below {MANAGED_DEFAULT_FLOOR}s — Snowflake&apos;s billing floor.
          </p>
        ) : null}
      </td>
      <td className="px-4 py-2">
        <span className="inline-flex items-center gap-1">
          <Badge color={displayStatus.color}>{displayStatus.label}</Badge>
          {displayStatus.label === "Drifted" ? (
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
