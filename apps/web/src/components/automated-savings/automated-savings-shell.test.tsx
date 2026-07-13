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
      roleName: "GREYSIGHT_RL",
    });

    render(<AutomatedSavingsShell authRequired={false} />);

    expect(await screen.findByText(/GRANT MANAGE WAREHOUSES/)).toBeInTheDocument();
    expect(fetchWarehousesMock).not.toHaveBeenCalled();
  });

  it("shows the warehouse table when agreed", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      roleName: "GREYSIGHT_RL",
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);

    render(<AutomatedSavingsShell authRequired={false} />);

    expect(await screen.findByRole("table", { name: /warehouses/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /check access/i })).toBeInTheDocument();
  });

  it("shows a grant-missing banner when the grant check fails", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      roleName: "GREYSIGHT_RL",
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    checkAccessMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: false,
      roleName: "GREYSIGHT_RL",
    });

    render(<AutomatedSavingsShell authRequired={false} />);

    fireEvent.click(await screen.findByRole("button", { name: /check access/i }));

    await waitFor(() =>
      expect(screen.getByText(/grant missing/i)).toBeInTheDocument(),
    );
  });

  it("shows a loading state while the status fetch is in flight", () => {
    fetchStatusMock.mockReturnValue(new Promise(() => {}));

    render(<AutomatedSavingsShell authRequired={false} />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  });

  it("shows an error state with a retry when the status fetch fails", async () => {
    fetchStatusMock.mockRejectedValueOnce(new Error("boom"));
    fetchStatusMock.mockResolvedValueOnce({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      roleName: "GREYSIGHT_RL",
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);

    render(<AutomatedSavingsShell authRequired={false} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t load/i);

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByRole("table", { name: /warehouses/i })).toBeInTheDocument();
  });

  it("flips all warehouses on with the global switch", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      roleName: "GREYSIGHT_RL",
    });
    fetchWarehousesMock.mockResolvedValue([{ ...baseRow, enabled: false }]);
    setGlobalSwitchMock.mockResolvedValue(undefined);

    render(<AutomatedSavingsShell authRequired={false} />);

    const globalSwitch = await screen.findByRole("switch", {
      name: /automated savings enabled for all warehouses/i,
    });
    expect(globalSwitch).not.toBeChecked();

    fireEvent.click(globalSwitch);

    await waitFor(() => expect(globalSwitch).toBeChecked());
    expect(setGlobalSwitchMock).toHaveBeenCalledWith("org-1", true, { accessToken: "tok" });
  });

  it("reloads status after agreeing from the opt-in gate", async () => {
    fetchStatusMock
      .mockResolvedValueOnce({
        agreed: false,
        globalEnabled: false,
        grantPresent: false,
        roleName: "GREYSIGHT_RL",
      })
      .mockResolvedValueOnce({
        agreed: true,
        globalEnabled: true,
        grantPresent: true,
        roleName: "GREYSIGHT_RL",
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
      roleName: "GREYSIGHT_RL",
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
