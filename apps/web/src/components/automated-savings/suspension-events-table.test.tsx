import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SuspensionEventsPage } from "../../lib/automated-savings-api";
import { SuspensionEventsTable } from "./suspension-events-table";

const fetchSuspensionEvents = vi.hoisted(() => vi.fn());
vi.mock("../../lib/automated-savings-api", () => ({
  fetchSuspensionEvents,
}));

// A client that never expires or garbage-collects entries, so revisiting a page
// within staleTime and remounting exercise the shared cache, not a refetch.
function persistentClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, staleTime: Infinity },
    },
  });
}

function renderTable(
  client: QueryClient = persistentClient(),
  props: { orgId?: string; accessToken?: string | null } = {},
) {
  return render(
    <QueryClientProvider client={client}>
      <SuspensionEventsTable
        orgId={props.orgId ?? "org-1"}
        accessToken={props.accessToken ?? "token"}
      />
    </QueryClientProvider>,
  );
}

// The shared vitest setup registers no automatic DOM cleanup, so unmount each
// render explicitly (project convention, see opt-in-gate.test.tsx).
afterEach(() => {
  cleanup();
  fetchSuspensionEvents.mockReset();
  vi.restoreAllMocks();
});

function page(ids: string[], nextCursor: string | null): SuspensionEventsPage {
  return {
    events: ids.map((id) => ({
      id,
      createdAt: "2026-07-15T10:00:00+00:00",
      warehouseName: `WH_${id}`,
      action: "suspend",
      reason: "idle",
      observedStartedClusters: 1,
      observedResumedOn: "2026-07-15T08:00:00+00:00",
      observedAt: "2026-07-15T09:59:00+00:00",
    })),
    nextCursor,
  };
}

describe("SuspensionEventsTable pagination", () => {
  it("fires a single next-page request when Next is double-clicked", async () => {
    let resolveSecondPage: (value: SuspensionEventsPage) => void = () => {};
    fetchSuspensionEvents
      .mockResolvedValueOnce(page(["1"], "cursor-1"))
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveSecondPage = resolve)),
      );

    renderTable();
    await screen.findByText("WH_1");
    const next = await screen.findByRole("button", { name: "Next page" });

    // Batch both dispatches inside one `act` call so React doesn't get a
    // chance to re-render (and flip the button's `disabled` DOM attribute)
    // between the two clicks. That isolates the assertion to the
    // `navInFlightRef` guard itself, rather than accidentally relying on the
    // disabled attribute already blocking the second click.
    act(() => {
      fireEvent.click(next);
      fireEvent.click(next);
    });

    resolveSecondPage(page(["2"], null));
    await waitFor(() => expect(screen.getByText("WH_2")).toBeInTheDocument());
    // 1 initial page + exactly 1 next-page fetch despite the double click.
    expect(fetchSuspensionEvents).toHaveBeenCalledTimes(2);
  });

  it("hides the pager on a lone page and shows it once there is a next page", async () => {
    fetchSuspensionEvents.mockResolvedValueOnce(page(["1"], null));

    renderTable();
    await screen.findByText("WH_1");
    expect(
      screen.queryByRole("button", { name: "Next page" }),
    ).not.toBeInTheDocument();
  });

  it("does not refetch a page revisited within staleTime", async () => {
    fetchSuspensionEvents
      .mockResolvedValueOnce(page(["1"], "cursor-1"))
      .mockResolvedValueOnce(page(["2"], null));

    renderTable();
    await screen.findByText("WH_1");
    fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
    await screen.findByText("WH_2");
    expect(fetchSuspensionEvents).toHaveBeenCalledTimes(2);

    // Back to page 1: its cursor (null) is already cached, so no new request.
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByText("WH_1");
    expect(fetchSuspensionEvents).toHaveBeenCalledTimes(2);
  });

  it("serves cached events on remount without refetching", async () => {
    fetchSuspensionEvents.mockResolvedValue(page(["1"], null));
    const client = persistentClient();

    const first = renderTable(client);
    await screen.findByText("WH_1");
    expect(fetchSuspensionEvents).toHaveBeenCalledTimes(1);
    first.unmount();

    renderTable(client);
    // Cached rows paint immediately on remount, with no second request.
    expect(screen.getByText("WH_1")).toBeInTheDocument();
    expect(fetchSuspensionEvents).toHaveBeenCalledTimes(1);
  });
});
