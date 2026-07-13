import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { WarehouseTable } from "./warehouse-table";

// The shared vitest setup registers no automatic DOM cleanup, so unmount each
// render explicitly (project convention, see chart-tooltip.test.tsx).
afterEach(cleanup);

const base = {
  name: "WH1", size: "X-Small", state: "STARTED", type: "STANDARD", supported: true,
  minClusterCount: 1, maxClusterCount: 1, startedClusters: 1, autoResumeOk: true,
  managedDefault: 300, storedDefault: 300, enabled: true, driftState: "ok" as const,
  driftedValue: null, cooldownTs: null, status: "idle" as const,
};

describe("WarehouseTable", () => {
  it("disables the toggle when AUTO_RESUME is off", () => {
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[{ ...base, autoResumeOk: false }]} onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: /WH1/i })).toBeDisabled();
  });

  it("enforces the 60 floor on the managed-default input", () => {
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[base]} onChange={() => {}} />);
    const input = screen.getByLabelText(/WH1 auto_suspend/i) as HTMLInputElement;
    expect(input.min).toBe("60");
  });

  it("surfaces Reconcile when drifted", () => {
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[{ ...base, driftState: "drifted", status: "drifted" }]} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /reconcile/i })).toBeInTheDocument();
  });
});
