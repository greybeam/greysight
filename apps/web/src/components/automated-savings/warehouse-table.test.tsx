import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as automatedSavingsApi from "../../lib/automated-savings-api";
import { WarehouseTable } from "./warehouse-table";

// The shared vitest setup registers no automatic DOM cleanup, so unmount each
// render explicitly (project convention, see chart-tooltip.test.tsx).
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

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

  it("disables the toggle and shows unsupported for non-STANDARD warehouses", () => {
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[{ ...base, type: "SNOWPARK-OPTIMIZED" }]} onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: /WH1/i })).toBeDisabled();
    expect(screen.getByText(/unsupported/i)).toBeInTheDocument();
  });

  it("disables editing for non-admins", () => {
    render(<WarehouseTable orgId="org-1" isAdmin={false} warehouses={[base]} onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: /WH1/i })).toBeDisabled();
    expect(screen.getByLabelText(/WH1 auto_suspend/i)).toBeDisabled();
  });

  it("calls toggleWarehouse and reports the change on toggle", async () => {
    const toggleSpy = vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockResolvedValue(undefined);
    const onChange = vi.fn();
    render(<WarehouseTable orgId="org-1" isAdmin accessToken="tok" warehouses={[base]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("switch", { name: /WH1/i }));

    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...base, enabled: false }));
    expect(toggleSpy).toHaveBeenCalledWith("org-1", "WH1", false, { accessToken: "tok" });
  });

  it("commits a valid managed-default edit on blur", async () => {
    const setSpy = vi.spyOn(automatedSavingsApi, "setManagedDefault").mockResolvedValue(undefined);
    const onChange = vi.fn();
    render(<WarehouseTable orgId="org-1" isAdmin accessToken="tok" warehouses={[base]} onChange={onChange} />);

    const input = screen.getByLabelText(/WH1 auto_suspend/i);
    fireEvent.change(input, { target: { value: "120" } });
    fireEvent.blur(input);

    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...base, managedDefault: 120 }));
    expect(setSpy).toHaveBeenCalledWith("org-1", "WH1", 120, { accessToken: "tok" });
  });

  it("rejects a managed-default edit below the floor and reverts it", async () => {
    const setSpy = vi.spyOn(automatedSavingsApi, "setManagedDefault").mockResolvedValue(undefined);
    const onChange = vi.fn();
    render(<WarehouseTable orgId="org-1" isAdmin accessToken="tok" warehouses={[base]} onChange={onChange} />);

    const input = screen.getByLabelText(/WH1 auto_suspend/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "30" } });
    fireEvent.blur(input);

    await waitFor(() => expect(input.value).toBe("300"));
    expect(setSpy).not.toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not call the API when the managed-default value is unchanged", async () => {
    const setSpy = vi.spyOn(automatedSavingsApi, "setManagedDefault").mockResolvedValue(undefined);
    render(<WarehouseTable orgId="org-1" isAdmin accessToken="tok" warehouses={[base]} onChange={() => {}} />);

    const input = screen.getByLabelText(/WH1 auto_suspend/i);
    fireEvent.blur(input);

    expect(setSpy).not.toHaveBeenCalled();
  });

  it("commits a managed-default edit on Enter and ignores other keys", async () => {
    const setSpy = vi.spyOn(automatedSavingsApi, "setManagedDefault").mockResolvedValue(undefined);
    const onChange = vi.fn();
    render(<WarehouseTable orgId="org-1" isAdmin accessToken="tok" warehouses={[base]} onChange={onChange} />);

    const input = screen.getByLabelText(/WH1 auto_suspend/i);
    fireEvent.change(input, { target: { value: "120" } });
    fireEvent.keyDown(input, { key: "Tab" });
    expect(setSpy).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...base, managedDefault: 120 }));
    expect(setSpy).toHaveBeenCalledWith("org-1", "WH1", 120, { accessToken: "tok" });
  });

  it("reconciles a drifted warehouse", async () => {
    const reconcileSpy = vi.spyOn(automatedSavingsApi, "reconcileWarehouse").mockResolvedValue(undefined);
    const onChange = vi.fn();
    render(<WarehouseTable orgId="org-1" isAdmin accessToken="tok" warehouses={[{ ...base, driftState: "drifted", status: "drifted" }]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /reconcile/i }));

    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...base, driftState: "ok", status: "idle" }));
    expect(reconcileSpy).toHaveBeenCalledWith("org-1", "WH1", true, { accessToken: "tok" });
  });
});
