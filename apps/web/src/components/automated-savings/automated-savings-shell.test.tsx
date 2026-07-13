import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider } from "../../lib/account-context";

const fetchStatusMock = vi.fn();
const fetchWarehousesMock = vi.fn();
const checkAccessMock = vi.fn();
const setGlobalSwitchMock = vi.fn();
const agreeMock = vi.fn();
const toggleWarehouseMock = vi.fn();

vi.mock("../../lib/automated-savings-api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/automated-savings-api")>(
    "../../lib/automated-savings-api",
  );
  return {
    ...actual,
    fetchStatus: (...args: unknown[]) => fetchStatusMock(...args),
    fetchWarehouses: (...args: unknown[]) => fetchWarehousesMock(...args),
    checkAccess: (...args: unknown[]) => checkAccessMock(...args),
    setGlobalSwitch: (...args: unknown[]) => setGlobalSwitchMock(...args),
    agree: (...args: unknown[]) => agreeMock(...args),
    toggleWarehouse: (...args: unknown[]) => toggleWarehouseMock(...args),
  };
});

vi.mock("../org/org-shell", () => ({
  default: ({ children }: { children: React.ReactNode }) => (
    <AccountChromeProvider
      value={{
        email: "u@acme.com",
        onSignOut: () => {},
        signOutError: null,
        organizations: [
          { id: "org-1", name: "Acme", role: "owner", accountLocator: null },
        ],
        activeOrganizationId: "org-1",
        setActiveOrganization: () => {},
        openAddAccount: () => {},
        accessToken: "tok",
      }}
    >
      {children}
    </AccountChromeProvider>
  ),
}));

import { AutomatedSavingsShell } from "./automated-savings-shell";

const baseRow = {
  name: "WH1", size: "X-Small", state: "STARTED", type: "STANDARD", supported: true,
  minClusterCount: 1, maxClusterCount: 1, startedClusters: 1, autoResumeOk: true,
  managedDefault: 300, storedDefault: 300, enabled: true, driftState: "ok" as const,
  driftedValue: null, cooldownTs: null, status: "idle" as const,
};

describe("AutomatedSavingsShell", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the opt-in gate when not agreed", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: false,
      globalEnabled: false,
      grantPresent: false,
      grantCheckedAt: null,
      roleName: null,
    });

    render(<AutomatedSavingsShell authRequired={false} />);

    expect(await screen.findByText(/GRANT MANAGE WAREHOUSES/)).toBeInTheDocument();
    expect(fetchWarehousesMock).not.toHaveBeenCalled();
  });

  it("falls back to the placeholder role in the opt-in gate GRANT SQL when status has no role", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: false,
      globalEnabled: false,
      grantPresent: false,
      grantCheckedAt: null,
      roleName: null,
    });

    render(<AutomatedSavingsShell authRequired={false} />);

    expect(
      await screen.findByText(/GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE "<YOUR_SNOWFLAKE_ROLE>";/),
    ).toBeInTheDocument();
  });

  it("shows a grant-missing banner with the real role when the grant check fails", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: "GREYSIGHT_ROLE",
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    checkAccessMock.mockResolvedValue({
      grantPresent: false,
      grantCheckedAt: "2026-01-01T00:00:00Z",
      roleName: "GREYSIGHT_ROLE",
    });

    render(<AutomatedSavingsShell authRequired={false} />);

    fireEvent.click(await screen.findByRole("button", { name: /check access/i }));

    await waitFor(() =>
      expect(screen.getByText(/grant missing/i)).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE "GREYSIGHT_ROLE";/),
    ).toBeInTheDocument();
  });

  it("shows an error state with a retry when the status fetch fails", async () => {
    fetchStatusMock.mockRejectedValueOnce(new Error("boom"));
    fetchStatusMock.mockResolvedValueOnce({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);

    render(<AutomatedSavingsShell authRequired={false} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t load/i);

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByRole("table", { name: /warehouses/i })).toBeInTheDocument();
  });

  it("reflects status.globalEnabled and flips it via the global switch", async () => {
    // The server's global_enabled is false even though the (only) row happens
    // to be enabled — the switch must track status.globalEnabled, not the
    // per-row state, and reflect it on initial render.
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: false,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([{ ...baseRow, enabled: true }]);
    setGlobalSwitchMock.mockResolvedValue(undefined);

    render(<AutomatedSavingsShell authRequired={false} />);

    const globalSwitch = await screen.findByRole("switch", {
      name: /automated savings enabled for all warehouses/i,
    });
    expect(globalSwitch).not.toBeChecked();

    // The row's `enabled` came from the server as true even though the
    // global switch is off — the master switch must never locally fake a
    // per-warehouse enrollment the server hasn't actually saved.
    const rowSwitchBefore = await screen.findByRole("switch", { name: "WH1" });
    expect(rowSwitchBefore).toBeChecked();

    fireEvent.click(globalSwitch);

    await waitFor(() => expect(globalSwitch).toBeChecked());
    expect(setGlobalSwitchMock).toHaveBeenCalledWith("org-1", true, { accessToken: "tok" });

    // Flipping the master switch persists only global_enabled — it must not
    // touch the per-row enabled state (no bulk local toggle of warehouses).
    const rowSwitchAfter = await screen.findByRole("switch", { name: "WH1" });
    expect(rowSwitchAfter).toBeChecked();
    expect(toggleWarehouseMock).not.toHaveBeenCalled();
  });

  it("reloads status after agreeing from the opt-in gate", async () => {
    fetchStatusMock
      .mockResolvedValueOnce({
        agreed: false,
        globalEnabled: false,
        grantPresent: false,
        grantCheckedAt: null,
        roleName: null,
      })
      .mockResolvedValueOnce({
        agreed: true,
        globalEnabled: true,
        grantPresent: true,
        grantCheckedAt: null,
        roleName: null,
      });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    agreeMock.mockResolvedValue(undefined);

    render(<AutomatedSavingsShell authRequired={false} />);

    fireEvent.click(await screen.findByRole("button", { name: /agree/i }));

    expect(await screen.findByRole("table", { name: /warehouses/i })).toBeInTheDocument();
  });

  it("updates a single warehouse row after toggling it in the table", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    toggleWarehouseMock.mockResolvedValue(undefined);

    render(<AutomatedSavingsShell authRequired={false} />);

    const rowSwitch = await screen.findByRole("switch", { name: "WH1" });
    expect(rowSwitch).toBeChecked();

    fireEvent.click(rowSwitch);

    await waitFor(() => expect(rowSwitch).not.toBeChecked());
  });
});
