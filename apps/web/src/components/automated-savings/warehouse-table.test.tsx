import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
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

  it.each([false, true])("disables a null managed-default input when enabled=%s", (enabled) => {
    render(
      <WarehouseTable
        orgId="org-1"
        isAdmin
        warehouses={[{ ...base, managedDefault: null, enabled }]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText(/WH1 auto_suspend/i)).toBeDisabled();
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

  it("hydrates the captured managed default after first enrollment", async () => {
    const unenrolled: automatedSavingsApi.WarehouseRow = {
      ...base,
      enabled: false,
      managedDefault: null,
      storedDefault: null,
    };
    const enrolled = {
      ...unenrolled,
      enabled: true,
      managedDefault: 300,
      storedDefault: 300,
    };
    vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockResolvedValue(undefined);
    vi.spyOn(automatedSavingsApi, "fetchWarehouses").mockResolvedValue([enrolled]);

    function Harness() {
      const [row, setRow] = useState(unenrolled);
      return (
        <WarehouseTable
          orgId="org-1"
          isAdmin
          accessToken="tok"
          warehouses={[row]}
          onChange={setRow}
        />
      );
    }

    render(<Harness />);
    const input = screen.getByLabelText(/WH1 auto_suspend/i) as HTMLInputElement;
    expect(input).toBeDisabled();

    fireEvent.click(screen.getByRole("switch", { name: /WH1/i }));

    await waitFor(() => expect(input).not.toBeDisabled());
    expect(input.value).toBe("300");
    expect(automatedSavingsApi.fetchWarehouses).toHaveBeenCalledWith("org-1", {
      accessToken: "tok",
    });
  });

  it("keeps enrollment enabled and offers a parent refresh when hydration fails", async () => {
    const unenrolled: automatedSavingsApi.WarehouseRow = {
      ...base,
      enabled: false,
      managedDefault: null,
      storedDefault: null,
    };
    vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockResolvedValue(undefined);
    vi.spyOn(automatedSavingsApi, "fetchWarehouses").mockRejectedValue(
      new Error("refresh failed"),
    );
    const onRefresh = vi.fn().mockResolvedValue(undefined);

    function Harness() {
      const [row, setRow] = useState(unenrolled);
      return (
        <WarehouseTable
          orgId="org-1"
          isAdmin
          accessToken="tok"
          warehouses={[row]}
          onChange={setRow}
          onRefresh={onRefresh}
        />
      );
    }

    render(<Harness />);
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });
    fireEvent.click(rowSwitch);

    await waitFor(() => expect(rowSwitch).toBeChecked());
    fireEvent.click(await screen.findByRole("button", { name: /retry refresh/i }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
    expect(automatedSavingsApi.toggleWarehouse).toHaveBeenCalledOnce();
  });

  it("keeps enrollment enabled when hydration omits the warehouse", async () => {
    const unenrolled: automatedSavingsApi.WarehouseRow = {
      ...base,
      enabled: false,
      managedDefault: null,
      storedDefault: null,
    };
    vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockResolvedValue(undefined);
    vi.spyOn(automatedSavingsApi, "fetchWarehouses").mockResolvedValue([]);

    function Harness() {
      const [row, setRow] = useState(unenrolled);
      return (
        <WarehouseTable
          orgId="org-1"
          isAdmin
          accessToken="tok"
          warehouses={[row]}
          onChange={setRow}
          onRefresh={vi.fn().mockResolvedValue(undefined)}
        />
      );
    }

    render(<Harness />);
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });
    fireEvent.click(rowSwitch);

    await waitFor(() => expect(rowSwitch).toBeChecked());
    expect(await screen.findByRole("button", { name: /retry refresh/i })).toBeVisible();
    expect(automatedSavingsApi.toggleWarehouse).toHaveBeenCalledOnce();
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
    expect(screen.getByRole("alert")).toHaveTextContent(/can't go below 60s/i);
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
