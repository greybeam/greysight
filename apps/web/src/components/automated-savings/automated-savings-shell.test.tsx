import { QueryClient } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider } from "../../lib/account-context";
import type { WarehouseRow } from "../../lib/automated-savings-api";
import { queryKeys } from "../../lib/query-keys";
import {
  createTestQueryClient,
  QueryTestProvider,
} from "../../lib/query-test-utils";
import { DashboardApiError } from "../../lib/dashboard-errors";
import { AutomatedSavingsShell } from "./automated-savings-shell";

// A client that never expires or garbage-collects entries, so remounts and
// org switches exercise the shared cache rather than silently refetching.
function persistentClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, staleTime: Infinity },
    },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const fetchStatusMock = vi.fn();
const fetchWarehousesMock = vi.fn();
const checkAccessMock = vi.fn();
const setGlobalSwitchMock = vi.fn();
const agreeMock = vi.fn();
const toggleWarehouseMock = vi.fn();
// The shell mounts SuspensionsChart and SuspensionEventsTable once opted in;
// stub their data calls so those components render quietly instead of
// surfacing their own error alerts and breaking role="alert" queries below.
const fetchSuspensionStatsMock = vi.fn();
const fetchSuspensionEventsMock = vi.fn();

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
    fetchSuspensionStats: (...args: unknown[]) =>
      fetchSuspensionStatsMock(...args),
    fetchSuspensionEvents: (...args: unknown[]) =>
      fetchSuspensionEventsMock(...args),
  };
});

function shellAccountValue(activeOrganizationId: string) {
  return {
    userId: "test-user",
    identityEpoch: 0,
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

function shellForOrganization(
  activeOrganizationId: string,
  client: QueryClient,
) {
  return (
    <QueryTestProvider client={client}>
      <AccountChromeProvider value={shellAccountValue(activeOrganizationId)}>
        <AutomatedSavingsShell />
      </AccountChromeProvider>
    </QueryTestProvider>
  );
}

function renderShell(client: QueryClient = createTestQueryClient()) {
  return render(shellForOrganization("org-1", client));
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
  beforeEach(() => {
    fetchSuspensionStatsMock.mockResolvedValue({
      days: 7,
      warehouses: [],
      buckets: [],
    });
    fetchSuspensionEventsMock.mockResolvedValue({
      events: [],
      nextCursor: null,
    });
  });

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
    expect(
      screen.getByRole("link", { name: /report this issue/i }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(
      await screen.findByRole("table", { name: /warehouses/i }),
    ).toBeInTheDocument();
  });

  it("shows a classified Snowflake failure without the report link", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockRejectedValue(
      new DashboardApiError(
        "Auto Savings API request failed with 502",
        "Snowflake blocked the connection under its network policy.",
      ),
    );

    renderShell();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /network policy/i,
    );
    expect(
      screen.queryByRole("link", { name: /report this issue/i }),
    ).not.toBeInTheDocument();
  });

  it("hides analytics and expands the config pane when no warehouse is enabled", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([{ ...baseRow, enabled: false }]);

    renderShell();

    expect(
      await screen.findByRole("table", { name: /warehouses/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("table", { name: /suspension events/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/enable a warehouse below to start saving idle compute/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/0 of 1 enabled/i)).toBeInTheDocument();
    const details = screen.getByText("Warehouse configuration").closest("details");
    expect(details).toHaveAttribute("open");
  });

  it("keeps the config pane open when the first warehouse is enabled from within it", async () => {
    // The pane starts open because no warehouse is enabled yet; enabling the
    // first one must not auto-collapse it out from under the user (the
    // `configOpen` default is frozen from the first ready snapshot — see the
    // effect in automated-savings-shell.tsx).
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock
      .mockResolvedValueOnce([{ ...baseRow, enabled: false }])
      .mockResolvedValueOnce([{ ...baseRow, enabled: true }]);
    toggleWarehouseMock.mockResolvedValue(undefined);

    renderShell();

    const detailsBefore = (
      await screen.findByText("Warehouse configuration")
    ).closest("details");
    expect(detailsBefore).toHaveAttribute("open");
    expect(screen.queryByText("Suspension events")).not.toBeInTheDocument();

    const rowSwitch = await screen.findByRole("switch", { name: "WH1" });
    fireEvent.click(rowSwitch);

    await waitFor(() => expect(toggleWarehouseMock).toHaveBeenCalledWith(
      "org-1",
      "WH1",
      true,
      { accessToken: "tok" },
    ));
    // Analytics (chart + events table sections) only mount once
    // status.agreed && hasEnabledConfig — the events table renders "No
    // recorded suspensions yet" here since fetchSuspensionEventsMock resolves
    // an empty page by default, so assert on the section heading rather than
    // the (absent) table role.
    await waitFor(() =>
      expect(screen.getByText("Suspension events")).toBeInTheDocument(),
    );

    const detailsAfter = screen
      .getByText("Warehouse configuration")
      .closest("details");
    expect(detailsAfter).toHaveAttribute("open");
  });

  it("shows analytics and the enabled count when a warehouse is enabled", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);

    renderShell();

    expect(
      await screen.findByRole("table", { name: /warehouses/i }),
    ).toBeInTheDocument();
    await waitFor(() => expect(fetchSuspensionStatsMock).toHaveBeenCalled());
    await waitFor(() => expect(fetchSuspensionEventsMock).toHaveBeenCalled());
    expect(screen.getByText(/1 of 1 enabled/i)).toBeInTheDocument();
    const details = screen.getByText("Warehouse configuration").closest("details");
    expect(details).not.toHaveAttribute("open");
  });

  it("reflects status.globalEnabled and flips it via the global switch", async () => {
    // The server's global_enabled is false even though the (only) row happens
    // to be enabled — the switch must track status.globalEnabled, not the
    // per-row state, and reflect it on initial render.
    // Initial status is global-off; after the switch persists, the shell
    // invalidates the scope and the authoritative refetch returns global-on.
    fetchStatusMock
      .mockResolvedValueOnce({
        agreed: true,
        globalEnabled: false,
        grantPresent: true,
        grantCheckedAt: null,
        roleName: null,
      })
      .mockResolvedValue({
        agreed: true,
        globalEnabled: true,
        grantPresent: true,
        grantCheckedAt: null,
        roleName: null,
      });
    fetchWarehousesMock.mockResolvedValue([{ ...baseRow, enabled: true }]);
    setGlobalSwitchMock.mockResolvedValue(undefined);

    renderShell();

    const globalSwitch = await screen.findByRole("switch", {
      name: /enabled for all warehouses/i,
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

  it("invalidates status and warehouses after the global switch persists", async () => {
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
      name: /enabled for all warehouses/i,
    });
    await waitFor(() => expect(fetchStatusMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(fetchWarehousesMock).toHaveBeenCalledTimes(1));

    fireEvent.click(globalSwitch);

    // Both scoped reads refetch from the server rather than trusting an
    // optimistic local patch of global_enabled.
    await waitFor(() => expect(setGlobalSwitchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(fetchStatusMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(fetchWarehousesMock).toHaveBeenCalledTimes(2));
  });

  it("invalidates warehouses and status after disabling a warehouse", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    toggleWarehouseMock.mockResolvedValue(undefined);

    renderShell();

    const rowSwitch = await screen.findByRole("switch", { name: "WH1" });
    await waitFor(() => expect(fetchWarehousesMock).toHaveBeenCalledTimes(1));
    const statusCallsBefore = fetchStatusMock.mock.calls.length;

    fireEvent.click(rowSwitch);

    await waitFor(() =>
      expect(toggleWarehouseMock).toHaveBeenCalledWith("org-1", "WH1", false, {
        accessToken: "tok",
      }),
    );
    await waitFor(() => expect(fetchWarehousesMock).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(fetchStatusMock.mock.calls.length).toBeGreaterThan(
        statusCallsBefore,
      ),
    );
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
      name: /enabled for all warehouses/i,
    });

    fireEvent.click(globalSwitch);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /couldn.t update auto savings/i,
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
      name: /enabled for all warehouses/i,
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
    const client = createTestQueryClient();
    const view = render(shellForOrganization("org-1", client));
    fireEvent.click(await screen.findByRole("button", { name: /agree/i }));

    view.rerender(shellForOrganization("org-2", client));
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

  it("serves cached status and warehouses on remount without refetching", async () => {
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    fetchWarehousesMock.mockResolvedValue([baseRow]);
    const client = persistentClient();

    const first = render(shellForOrganization("org-1", client));
    await screen.findByRole("table", { name: /warehouses/i });
    expect(fetchStatusMock).toHaveBeenCalledTimes(1);
    expect(fetchWarehousesMock).toHaveBeenCalledTimes(1);
    first.unmount();

    render(shellForOrganization("org-1", client));
    // Fresh cached data paints immediately — no "Loading configuration" panel —
    // and neither reader fires a second request.
    expect(
      screen.getByRole("table", { name: /warehouses/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/loading configuration/i)).not.toBeInTheDocument();
    expect(fetchStatusMock).toHaveBeenCalledTimes(1);
    expect(fetchWarehousesMock).toHaveBeenCalledTimes(1);
  });

  it("keeps cached config visible while a remount revalidates in the background", async () => {
    const client = createTestQueryClient();
    client.setQueryData(queryKeys.autoSavings.status("test-user", "org-1"), {
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    client.setQueryData(queryKeys.autoSavings.warehouses("test-user", "org-1"), [
      baseRow,
    ]);
    // Deferred so the revalidation never settles during the assertions: the
    // cached UI must remain, not flip to a loading panel.
    const statusDeferred = deferred<never>();
    const warehousesDeferred = deferred<never>();
    fetchStatusMock.mockReturnValue(statusDeferred.promise);
    fetchWarehousesMock.mockReturnValue(warehousesDeferred.promise);

    render(shellForOrganization("org-1", client));

    expect(
      screen.getByRole("table", { name: /warehouses/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/loading configuration/i)).not.toBeInTheDocument();
  });

  it("never shows the previous org's warehouses while the next org is unresolved", async () => {
    const client = createTestQueryClient();
    fetchStatusMock.mockResolvedValue({
      agreed: true,
      globalEnabled: true,
      grantPresent: true,
      grantCheckedAt: null,
      roleName: null,
    });
    const orgTwoWarehouses = deferred<WarehouseRow[]>();
    fetchWarehousesMock.mockImplementation((orgId: string) =>
      orgId === "org-1"
        ? Promise.resolve([{ ...baseRow, name: "WH_ORG_1" }])
        : orgTwoWarehouses.promise,
    );

    const view = render(shellForOrganization("org-1", client));
    await screen.findByRole("switch", { name: "WH_ORG_1" });

    view.rerender(shellForOrganization("org-2", client));
    // org-2's warehouses are still pending: the shell shows its loading panel,
    // never the stale org-1 rows.
    await waitFor(() =>
      expect(
        screen.queryByRole("switch", { name: "WH_ORG_1" }),
      ).not.toBeInTheDocument(),
    );

    orgTwoWarehouses.resolve([{ ...baseRow, name: "WH_ORG_2" }]);
    expect(
      await screen.findByRole("switch", { name: "WH_ORG_2" }),
    ).toBeInTheDocument();
  });
});
