import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider } from "../../lib/account-context";
import type { WarehouseRow } from "../../lib/automated-savings-api";
import { AutomatedSavingsShell } from "./automated-savings-shell";

const fetchStatusMock = vi.fn();
const fetchWarehousesMock = vi.fn();
const checkAccessMock = vi.fn();
const setGlobalSwitchMock = vi.fn();
const agreeMock = vi.fn();
const toggleWarehouseMock = vi.fn();

vi.mock("../../lib/automated-savings-api", async () => {
  const actual = await vi.importActual<
    typeof import("../../lib/automated-savings-api")
  >("../../lib/automated-savings-api");
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

function shellAccountValue(activeOrganizationId: string) {
  return {
    email: "u@acme.com",
    onSignOut: () => {},
    signOutError: null,
    organizations: [
      { id: "org-1", name: "Acme", role: "owner" as const, accountLocator: null },
      { id: "org-2", name: "Beta", role: "owner" as const, accountLocator: null },
    ],
    activeOrganizationId,
    setActiveOrganization: () => {},
    openAddAccount: () => {},
    accessToken: "tok",
  };
}

function shellForOrganization(activeOrganizationId: string) {
  return (
    <AccountChromeProvider
      value={shellAccountValue(activeOrganizationId)}
    >
      <AutomatedSavingsShell />
    </AccountChromeProvider>
  );
}

function renderShell() {
  return render(shellForOrganization("org-1"));
}

const baseRow: WarehouseRow = {
  name: "WH1",
  size: "X-Small",
  state: "STARTED",
  type: "STANDARD",
  supported: true,
  minClusterCount: 1,
  maxClusterCount: 1,
  startedClusters: 1,
  autoResumeOk: true,
  autoSuspend: 300,
  quiescing: 0,
  enabled: true,
  status: "idle",
};

describe("AutomatedSavingsShell", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the opt-in gate without fetching warehouses when not agreed", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: false,
      globalEnabled: false,
      grantPresent: false,
      grantCheckedAt: null,
      roleName: null,
    });

    renderShell();

    expect(
      await screen.findByText(/GRANT MANAGE WAREHOUSES/),
    ).toBeInTheDocument();
    expect(fetchWarehousesMock).not.toHaveBeenCalled();
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

    renderShell();

    fireEvent.click(
      await screen.findByRole("button", { name: /check access/i }),
    );

    await waitFor(() =>
      expect(screen.getByText(/grant missing/i)).toBeInTheDocument(),
    );
    const warning = screen.getByRole("alert");
    expect(warning).toHaveTextContent(
      /suspend commands will fail and back off until the grant is restored/i,
    );
    expect(warning).not.toHaveTextContent(/automation is paused/i);
    expect(
      screen.getByText(
        /GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE "GREYSIGHT_ROLE";/,
      ),
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

    renderShell();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /couldn.t load/i,
    );

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(
      await screen.findByRole("table", { name: /warehouses/i }),
    ).toBeInTheDocument();
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

    renderShell();

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
    expect(setGlobalSwitchMock).toHaveBeenCalledWith("org-1", true, {
      accessToken: "tok",
    });

    // Flipping the master switch persists only global_enabled — it must not
    // touch the per-row enabled state (no bulk local toggle of warehouses).
    const rowSwitchAfter = await screen.findByRole("switch", { name: "WH1" });
    expect(rowSwitchAfter).toBeChecked();
    expect(toggleWarehouseMock).not.toHaveBeenCalled();
  });

  it("reports a global-switch failure without changing authoritative state", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: false,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    setGlobalSwitchMock.mockRejectedValue(new Error("network failure"));
    renderShell();
    const globalSwitch = await screen.findByRole("switch", {
      name: /automated savings enabled for all warehouses/i,
    });

    fireEvent.click(globalSwitch);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /couldn.t update automated savings/i,
    );
    expect(globalSwitch).not.toBeChecked();
    expect(globalSwitch).not.toBeDisabled();
  });

  it("blocks overlapping global-switch requests", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: false,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    let resolveSwitch: (() => void) | undefined;
    setGlobalSwitchMock.mockImplementation(
      () => new Promise<void>((resolve) => { resolveSwitch = resolve; }),
    );
    renderShell();
    const globalSwitch = await screen.findByRole("switch", {
      name: /automated savings enabled for all warehouses/i,
    });

    fireEvent.click(globalSwitch);
    fireEvent.click(globalSwitch);

    expect(setGlobalSwitchMock).toHaveBeenCalledOnce();
    expect(globalSwitch).toBeDisabled();
    resolveSwitch?.();
    await waitFor(() => expect(globalSwitch).not.toBeDisabled());
  });

  it("reports an access-check failure and allows retry", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    checkAccessMock
      .mockRejectedValueOnce(new Error("network failure"))
      .mockResolvedValueOnce({
        grantPresent: true,
        grantCheckedAt: "2026-01-01T00:00:00Z",
        roleName: "GREYSIGHT_ROLE",
      });
    renderShell();
    const checkButton = await screen.findByRole("button", {
      name: /check access/i,
    });

    fireEvent.click(checkButton);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /couldn.t check snowflake access/i,
    );
    expect(checkButton).not.toBeDisabled();
    fireEvent.click(checkButton);

    await waitFor(() => expect(checkAccessMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("reloads status and warehouses after agreeing from the opt-in gate", async () => {
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
        globalEnabled: false,
        grantPresent: true,
        grantCheckedAt: null,
        roleName: null,
      });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    agreeMock.mockResolvedValue(undefined);

    renderShell();

    fireEvent.click(await screen.findByRole("button", { name: /agree/i }));

    expect(
      await screen.findByRole("switch", { name: "WH1" }),
    ).toBeInTheDocument();
    expect(fetchStatusMock).toHaveBeenCalledTimes(2);
    expect(fetchWarehousesMock).toHaveBeenCalledTimes(1);
  });

  it("lets a new organization load settle when an old agreement completes", async () => {
    let resolveAgreement: (() => void) | undefined;
    let resolveOrgTwoStatus: ((status: {
      agreed: boolean;
      globalEnabled: boolean;
      grantPresent: boolean;
      grantCheckedAt: null;
      roleName: null;
    }) => void) | undefined;
    agreeMock.mockImplementation(
      () => new Promise<void>((resolve) => { resolveAgreement = resolve; }),
    );
    fetchStatusMock.mockImplementation((orgId: string) => {
      if (orgId === "org-1") {
        return Promise.resolve({
          agreed: false,
          globalEnabled: false,
          grantPresent: false,
          grantCheckedAt: null,
          roleName: null,
        });
      }
      return new Promise((resolve) => { resolveOrgTwoStatus = resolve; });
    });
    fetchWarehousesMock.mockResolvedValue([{ ...baseRow, name: "WH_ORG_2" }]);
    const view = renderShell();
    fireEvent.click(await screen.findByRole("button", { name: /agree/i }));

    view.rerender(shellForOrganization("org-2"));
    await waitFor(() =>
      expect(fetchStatusMock).toHaveBeenCalledWith("org-2", { accessToken: "tok" }),
    );
    resolveAgreement?.();
    resolveOrgTwoStatus?.({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });

    expect(await screen.findByRole("switch", { name: "WH_ORG_2" }))
      .toBeInTheDocument();
    expect(fetchStatusMock.mock.calls.filter(([orgId]) => orgId === "org-1"))
      .toHaveLength(1);
  });
});
