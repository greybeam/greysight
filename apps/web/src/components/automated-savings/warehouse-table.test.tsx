import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { QueryClient, useQuery } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as automatedSavingsApi from "../../lib/automated-savings-api";
import { DashboardApiError } from "../../lib/dashboard-errors";
import { queryKeys } from "../../lib/query-keys";
import { QueryIdentityProvider } from "../../lib/query-identity";
import {
  createTestQueryClient,
  QueryTestProvider,
} from "../../lib/query-test-utils";
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

const WH_KEY = queryKeys.autoSavings.warehouses("test-user", "org-1");

type TestIdentity = {
  userId?: string;
  activeOrganizationId?: string | null;
  identityEpoch?: number;
};

// The warehouse toggle now reads/writes the shared query cache and guards those
// writes on the current query identity, so every render needs a QueryClient and
// a live QueryIdentityProvider ref (the provider stays mounted across identity
// switches, matching how OrgShell owns identity in production).
function Providers({
  children,
  client = createTestQueryClient(),
  identity = { userId: "test-user", activeOrganizationId: "org-1" },
}: {
  children: ReactNode;
  client?: QueryClient;
  identity?: TestIdentity;
}) {
  return (
    <QueryTestProvider client={client} identity={identity}>
      <QueryIdentityProvider>{children}</QueryIdentityProvider>
    </QueryTestProvider>
  );
}

function renderTable(ui: ReactNode, client?: QueryClient) {
  return render(<Providers client={client}>{ui}</Providers>);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

// A read-only observer of the org-1 warehouses cache entry. It never fetches
// (enabled: false) and renders the names of whatever is written to WH_KEY, so a
// stale authoritative write becomes visible in the DOM (rather than probing the
// cache via getQueryData).
function WarehouseCacheProbe() {
  const { data } = useQuery({
    queryKey: WH_KEY,
    queryFn: () => Promise.resolve<automatedSavingsApi.WarehouseRow[]>([]),
    enabled: false,
  });
  return (
    <div data-testid="wh-cache-probe">
      {(data ?? []).map((row) => row.name).join(",")}
    </div>
  );
}

describe("WarehouseTable", () => {
  it("disables the toggle when AUTO_RESUME is off", () => {
    renderTable(<WarehouseTable orgId="org-1" isAdmin warehouses={[{ ...base, autoResumeOk: false, enabled: false }]} onChange={() => {}} />);
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });
    expect(rowSwitch).toBeDisabled();
    expect(document.getElementById(rowSwitch.getAttribute("aria-describedby") ?? ""))
      .toHaveTextContent(/AUTO_RESUME is off/i);
  });

  it.each([[300, "300s"], [null, "—"]] as const)("renders AUTO_SUSPEND %s as plain text", (autoSuspend, display) => {
    renderTable(
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
    renderTable(<WarehouseTable orgId="org-1" isAdmin warehouses={[{ ...base, type: "SNOWPARK-OPTIMIZED", enabled: false }]} onChange={() => {}} />);
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });
    expect(rowSwitch).toBeDisabled();
    expect(document.getElementById(rowSwitch.getAttribute("aria-describedby") ?? ""))
      .toHaveTextContent(/warehouse type isn't supported/i);
    expect(screen.getByText(/unsupported/i)).toBeInTheDocument();
  });

  it("disables the toggle for non-admins", () => {
    renderTable(<WarehouseTable orgId="org-1" isAdmin={false} warehouses={[base]} onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: /WH1/i })).toBeDisabled();
  });

  it("presents a backend transition even when enrollment was disabled mid-transition", () => {
    renderTable(
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
    renderTable(
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

  it("writes the authoritative warehouse list to the cache after first enrollment", async () => {
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
    const client = createTestQueryClient();

    // A cache-connected harness: the table reads its rows from the warehouses
    // query. The authoritative post-enrollment write lands in that cache and the
    // follow-up invalidation refetch returns the same enrolled row, so the
    // transitioning status appears without any explicit onChange plumbing.
    function CacheTable() {
      const { data } = useQuery({
        queryKey: WH_KEY,
        queryFn: () => Promise.resolve([enrolled]),
        initialData: [unenrolled],
      });
      return (
        <WarehouseTable
          orgId="org-1"
          isAdmin
          accessToken="tok"
          warehouses={data ?? []}
          onChange={(row) =>
            client.setQueryData<automatedSavingsApi.WarehouseRow[]>(
              WH_KEY,
              (prev) =>
                (prev ?? []).map((r) => (r.name === row.name ? row : r)),
            )
          }
        />
      );
    }

    renderTable(<CacheTable />, client);
    fireEvent.click(screen.getByRole("switch", { name: /WH1/i }));

    // Observable behavior: the authoritative transitioning status is rendered
    // and the toggle now reads as enrolled.
    expect(await screen.findByText("Transitioning")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /WH1/i })).toBeChecked(),
    );
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

      renderTable(<Harness />);
      const rowSwitch = screen.getByRole("switch", { name: /WH1/i });
      fireEvent.click(rowSwitch);

      await waitFor(() => expect(rowSwitch).toBeChecked());
      fireEvent.click(await screen.findByRole("button", { name: /retry refresh/i }));

      await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
      expect(automatedSavingsApi.toggleWarehouse).toHaveBeenCalledOnce();
    },
  );

  it("drops the post-enrollment authoritative cache write after an org switch", async () => {
    const client = createTestQueryClient();
    const unenrolled = { ...base, enabled: false };
    const whFetch = deferred<automatedSavingsApi.WarehouseRow[]>();
    vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockResolvedValue(undefined);
    vi.spyOn(automatedSavingsApi, "fetchWarehouses").mockReturnValue(
      whFetch.promise,
    );

    const view = render(
      <Providers client={client}>
        <WarehouseCacheProbe />
        <WarehouseTable
          orgId="org-1"
          isAdmin
          accessToken="tok"
          warehouses={[unenrolled]}
          onChange={() => {}}
        />
      </Providers>,
    );
    fireEvent.click(screen.getByRole("switch", { name: /WH1/i }));

    // The workspace switches before the authoritative fetch resolves.
    view.rerender(
      <Providers
        client={client}
        identity={{ userId: "test-user", activeOrganizationId: "org-2" }}
      >
        <WarehouseCacheProbe />
        <WarehouseTable
          orgId="org-2"
          isAdmin
          accessToken="tok"
          warehouses={[]}
          onChange={() => {}}
        />
      </Providers>,
    );

    whFetch.resolve([{ ...unenrolled, enabled: true, status: "transitioning" }]);
    await Promise.resolve();
    await Promise.resolve();

    // The stale org-1 result must not be written to the old org's cache, so the
    // probe observing WH_KEY stays empty.
    expect(screen.getByTestId("wh-cache-probe")).toHaveTextContent("");
  });

  it("drops the post-enrollment authoritative cache write after an account switch", async () => {
    const client = createTestQueryClient();
    const unenrolled = { ...base, enabled: false };
    const whFetch = deferred<automatedSavingsApi.WarehouseRow[]>();
    vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockResolvedValue(undefined);
    vi.spyOn(automatedSavingsApi, "fetchWarehouses").mockReturnValue(
      whFetch.promise,
    );

    const view = render(
      <Providers client={client}>
        <WarehouseCacheProbe />
        <WarehouseTable
          orgId="org-1"
          isAdmin
          accessToken="tok"
          warehouses={[unenrolled]}
          onChange={() => {}}
        />
      </Providers>,
    );
    fireEvent.click(screen.getByRole("switch", { name: /WH1/i }));

    // The signed-in identity is re-issued (epoch bump) before the fetch resolves.
    view.rerender(
      <Providers
        client={client}
        identity={{
          userId: "test-user",
          activeOrganizationId: "org-1",
          identityEpoch: 1,
        }}
      >
        <WarehouseCacheProbe />
        <WarehouseTable
          orgId="org-1"
          isAdmin
          accessToken="tok"
          warehouses={[unenrolled]}
          onChange={() => {}}
        />
      </Providers>,
    );

    whFetch.resolve([{ ...unenrolled, enabled: true, status: "transitioning" }]);
    await Promise.resolve();
    await Promise.resolve();

    // The stale write is dropped on the epoch bump, so the probe stays empty.
    expect(screen.getByTestId("wh-cache-probe")).toHaveTextContent("");
  });

  it("surfaces a toggle failure's user-safe message without changing enrollment", async () => {
    vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockRejectedValue(
      new DashboardApiError(
        "Auto Savings API request failed with 502",
        "Could not list Snowflake warehouses.",
      ),
    );
    const onChange = vi.fn();
    renderTable(<WarehouseTable orgId="org-1" isAdmin accessToken="tok" warehouses={[base]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("switch", { name: /WH1/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not list Snowflake warehouses.",
    );
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows a generic message when a toggle failure has no user-safe detail", async () => {
    vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockRejectedValue(
      new Error("Auto Savings API request failed with 502"),
    );
    const onChange = vi.fn();
    renderTable(<WarehouseTable orgId="org-1" isAdmin accessToken="tok" warehouses={[base]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("switch", { name: /WH1/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    expect(onChange).not.toHaveBeenCalled();
  });

  it("clears the row busy state after the identity goes stale mid-toggle", async () => {
    // Busy is local UI state: even when the toggle resolves against a stale
    // identity (org switched out and back), the per-operation token must still
    // release busy so the row isn't left permanently disabled.
    const client = createTestQueryClient();
    const toggle = deferred<void>();
    vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockReturnValue(
      toggle.promise,
    );

    const view = render(
      <Providers client={client}>
        <WarehouseTable
          orgId="org-1"
          isAdmin
          accessToken="tok"
          warehouses={[base]}
          onChange={() => {}}
        />
      </Providers>,
    );
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });
    fireEvent.click(rowSwitch);
    expect(rowSwitch).toBeDisabled();

    // The signed-in identity is re-issued while the toggle is in flight.
    view.rerender(
      <Providers
        client={client}
        identity={{
          userId: "test-user",
          activeOrganizationId: "org-1",
          identityEpoch: 1,
        }}
      >
        <WarehouseTable
          orgId="org-1"
          isAdmin
          accessToken="tok"
          warehouses={[base]}
          onChange={() => {}}
        />
      </Providers>,
    );

    toggle.resolve();
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /WH1/i })).not.toBeDisabled(),
    );
  });

  it("protects a warehouse from overlapping toggle actions", async () => {
    let resolveToggle: (() => void) | undefined;
    const toggleSpy = vi.spyOn(automatedSavingsApi, "toggleWarehouse").mockImplementation(
      () => new Promise<void>((resolve) => { resolveToggle = resolve; }),
    );
    renderTable(<WarehouseTable orgId="org-1" isAdmin warehouses={[base]} onChange={() => {}} />);
    const rowSwitch = screen.getByRole("switch", { name: /WH1/i });

    fireEvent.click(rowSwitch);
    fireEvent.click(rowSwitch);

    expect(toggleSpy).toHaveBeenCalledOnce();
    resolveToggle?.();
    await waitFor(() => expect(rowSwitch).not.toBeDisabled());
  });
});
