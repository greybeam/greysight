import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { createTestQueryClient } from "./query-test-utils";
import { queryKeys } from "./query-keys";

function Consumer({
  queryKey,
  queryFn,
}: {
  queryKey: readonly unknown[];
  queryFn: () => Promise<string>;
}) {
  const query = useQuery({ queryKey, queryFn });
  return <span>{query.data ?? (query.isPending ? "loading" : "error")}</span>;
}

describe("session query cache", () => {
  it("deduplicates simultaneous consumers of the same key", async () => {
    const client = createTestQueryClient();
    const queryFn = vi.fn().mockResolvedValue("shared");
    const key = queryKeys.autoSavings.status("user-1", "org-1");
    render(
      <QueryClientProvider client={client}>
        <Consumer queryKey={key} queryFn={queryFn} />
        <Consumer queryKey={key} queryFn={queryFn} />
      </QueryClientProvider>,
    );
    expect(await screen.findAllByText("shared")).toHaveLength(2);
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("serves a stale value while revalidating, then replaces it", async () => {
    const client = createTestQueryClient();
    const key = queryKeys.autoSavings.status("user-1", "org-1");
    client.setQueryData(key, "stale");
    // Force the cached entry to look expired so the mount triggers a refetch.
    const entry = client.getQueryCache().find({ queryKey: key });
    if (entry) {
      entry.state = { ...entry.state, dataUpdatedAt: 0 };
    }

    let resolve!: (value: string) => void;
    const blocked = new Promise<string>((res) => {
      resolve = res;
    });
    const queryFn = vi.fn().mockReturnValue(blocked);

    render(
      <QueryClientProvider client={client}>
        <Consumer queryKey={key} queryFn={queryFn} />
      </QueryClientProvider>,
    );

    // Stale value renders immediately, without a loading placeholder.
    expect(screen.getByText("stale")).toBeInTheDocument();
    expect(screen.queryByText("loading")).not.toBeInTheDocument();
    expect(queryFn).toHaveBeenCalledTimes(1);

    resolve("fresh");
    await waitFor(() => expect(screen.getByText("fresh")).toBeInTheDocument());
  });

  it("keeps organizations and event cursors in distinct entries", () => {
    expect(queryKeys.autoSavings.events("user-1", "org-1", null)).not.toEqual(
      queryKeys.autoSavings.events("user-1", "org-2", null),
    );
    expect(queryKeys.autoSavings.events("user-1", "org-1", null)).not.toEqual(
      queryKeys.autoSavings.events("user-1", "org-1", "cursor-2"),
    );
  });

  it("normalizes semantically identical range keys", () => {
    expect(
      queryKeys.dashboard.view("user-1", "org-1", "run-1", { windowDays: 30 }),
    ).toEqual(queryKeys.dashboard.view("user-1", "org-1", "run-1", {}));
  });

  it("never accepts an access token in a key builder", () => {
    expectTypeOf(queryKeys.autoSavings.status).parameters.toEqualTypeOf<
      [string, string]
    >();
    expectTypeOf(queryKeys.autoSavings.events).parameters.toEqualTypeOf<
      [string, string, string | null]
    >();
    expectTypeOf(queryKeys.dashboard.cachedRun).parameters.toEqualTypeOf<
      [string, string]
    >();
    expect(queryKeys.autoSavings.status("user-1", "org-1")).not.toContain(
      "secret-access-token",
    );
  });
});
