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
  autoSuspend: 300, quiescing: 0, enabled: true, status: "idle" as const,
};

describe("WarehouseTable", () => {
  it("disables the toggle when AUTO_RESUME is off", () => {
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[{ ...base, autoResumeOk: false, enabled: false }]} onChange={() => {}} />);
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });
    expect(rowSwitch).toBeDisabled();
    expect(document.getElementById(rowSwitch.getAttribute("aria-describedby") ?? ""))
      .toHaveTextContent(/AUTO_RESUME is off/i);
  });

  it.each([[300, "300s"], [null, "—"]] as const)("renders AUTO_SUSPEND %s as plain text", (autoSuspend, display) => {
    render(
      <WarehouseTable
        orgId="org-1"
        isAdmin
        warehouses={[{ ...base, autoSuspend }]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText(display)).toBeInTheDocument();
  });

  it("disables the toggle and shows unsupported for non-STANDARD warehouses", () => {
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[{ ...base, type: "SNOWPARK-OPTIMIZED", enabled: false }]} onChange={() => {}} />);
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });
    expect(rowSwitch).toBeDisabled();
    expect(document.getElementById(rowSwitch.getAttribute("aria-describedby") ?? ""))
      .toHaveTextContent(/warehouse type isn't supported/i);
    expect(screen.getByText(/unsupported/i)).toBeInTheDocument();
  });

  it("disables the toggle for non-admins", () => {
    render(<WarehouseTable orgId="org-1" isAdmin={false} warehouses={[base]} onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: /WH1/i })).toBeDisabled();
  });

  it("presents a backend transition even when enrollment was disabled mid-transition", () => {
    render(
      <WarehouseTable
        orgId="org-1"
        isAdmin
        warehouses={[{
          ...base,
          autoResumeOk: false,
          enabled: false,
          status: "transitioning",
        }]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("Transitioning")).toBeInTheDocument();
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });
    expect(rowSwitch).toBeDisabled();
    expect(document.getElementById(rowSwitch.getAttribute("aria-describedby") ?? ""))
      .toHaveTextContent(/transitioning/i);
  });

  it.each([
    ["unsupported", { type: "SNOWPARK-OPTIMIZED", supported: false, status: "unsupported" as const }],
    ["AUTO_RESUME off", { autoResumeOk: false }],
    ["transitioning", { status: "transitioning" as const }],
  ])("allows an admin to disable an enrolled warehouse that is %s", async (_case, unsafeState) => {
    const warehouse = { ...base, ...unsafeState, enabled: true };
    const toggleSpy = vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockResolvedValue(undefined);
    const onChange = vi.fn();
    render(
      <WarehouseTable
        orgId="org-1"
        isAdmin
        accessToken="tok"
        warehouses={[warehouse]}
        onChange={onChange}
      />,
    );
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });

    expect(rowSwitch).not.toBeDisabled();
    expect(rowSwitch).not.toHaveAttribute("aria-describedby");
    fireEvent.click(rowSwitch);

    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...warehouse, enabled: false }));
    expect(toggleSpy).toHaveBeenCalledWith("org-1", "WH1", false, {
      accessToken: "tok",
    });
  });

  it("refreshes the authoritative row after first enrollment", async () => {
    const unenrolled: automatedSavingsApi.WarehouseRow = {
      ...base,
      enabled: false,
    };
    const enrolled = {
      ...unenrolled,
      enabled: true,
      status: "transitioning" as const,
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
    fireEvent.click(screen.getByRole("switch", { name: /WH1/i }));

    expect(await screen.findByText("Transitioning")).toBeInTheDocument();
    expect(automatedSavingsApi.fetchWarehouses).toHaveBeenCalledWith("org-1", {
      accessToken: "tok",
    });
  });

  it.each([
    [
      "hydration fails",
      () =>
        vi
          .spyOn(automatedSavingsApi, "fetchWarehouses")
          .mockRejectedValue(new Error("refresh failed")),
    ],
    [
      "hydration omits the warehouse",
      () =>
        vi
          .spyOn(automatedSavingsApi, "fetchWarehouses")
          .mockResolvedValue([]),
    ],
  ])(
    "keeps enrollment enabled and offers a parent refresh when %s",
    async (_case, mockFetch) => {
      const unenrolled: automatedSavingsApi.WarehouseRow = {
        ...base,
        enabled: false,
      };
      vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockResolvedValue(undefined);
      mockFetch();
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
    },
  );

  it("surfaces a toggle failure without changing enrollment", async () => {
    vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockRejectedValue(
      new Error("Automated savings API request failed with 502: Snowflake unavailable"),
    );
    const onChange = vi.fn();
    render(<WarehouseTable orgId="org-1" isAdmin accessToken="tok" warehouses={[base]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("switch", { name: /WH1/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Snowflake unavailable");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("protects a warehouse from overlapping toggle actions", async () => {
    let resolveToggle: (() => void) | undefined;
    const toggleSpy = vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockImplementation(
      () => new Promise<void>((resolve) => { resolveToggle = resolve; }),
    );
    render(<WarehouseTable orgId="org-1" isAdmin warehouses={[base]} onChange={() => {}} />);
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });

    fireEvent.click(rowSwitch);
    fireEvent.click(rowSwitch);

    expect(toggleSpy).toHaveBeenCalledOnce();
    resolveToggle?.();
    await waitFor(() => expect(rowSwitch).not.toBeDisabled());
  });
});
