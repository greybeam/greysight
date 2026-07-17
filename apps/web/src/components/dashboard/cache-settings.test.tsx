import { QueryClient } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider, type AccountChrome } from "../../lib/account-context";
import * as api from "../../lib/cache-settings-api";
import { queryKeys } from "../../lib/query-keys";
import { QueryTestProvider, createTestQueryClient } from "../../lib/query-test-utils";
import CacheSettings from "./cache-settings";

function buildChrome(overrides: Partial<AccountChrome>): AccountChrome {
  return {
    userId: "test-user",
    identityEpoch: 0,
    email: "user@example.com",
    onSignOut: vi.fn(),
    signOutError: null,
    organizations: [
      { id: "org-1", name: "Acme", role: "owner", accountLocator: "AAA-111" },
    ],
    activeOrganizationId: "org-1",
    setActiveOrganization: vi.fn(),
    openAddAccount: vi.fn(),
    accessToken: "tok",
    ...overrides,
  };
}

function renderWith(
  overrides: Partial<AccountChrome> = {},
  client?: QueryClient,
) {
  const value = buildChrome(overrides);
  const queryClient = client ?? createTestQueryClient();
  render(
    <QueryTestProvider client={queryClient}>
      <AccountChromeProvider value={value}>
        <CacheSettings />
      </AccountChromeProvider>
    </QueryTestProvider>,
  );
  return { value, queryClient };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CacheSettings", () => {
  it("loads current settings when the surface opens", async () => {
    vi.spyOn(api, "fetchCacheSettings").mockResolvedValue({
      cache_enabled: false,
      cache_ttl_seconds: 21_600,
    });
    renderWith();
    fireEvent.click(screen.getByRole("button", { name: /cache settings/i }));

    await waitFor(() =>
      expect(api.fetchCacheSettings).toHaveBeenCalledWith("org-1", {
        accessToken: "tok",
      }),
    );
    await waitFor(() =>
      expect(
        (screen.getByRole("checkbox", { name: /enable caching/i }) as HTMLInputElement)
          .checked,
      ).toBe(false),
    );
    expect(
      (screen.getByRole("combobox", { name: /cache lifetime/i }) as HTMLSelectElement)
        .value,
    ).toBe("21600");
  });

  it("reuses cached settings on reopen within staleTime without a second GET", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: Infinity, staleTime: 60_000 },
      },
    });
    const fetchSpy = vi.spyOn(api, "fetchCacheSettings").mockResolvedValue({
      cache_enabled: false,
      cache_ttl_seconds: 21_600,
    });
    renderWith({}, client);
    const trigger = screen.getByRole("button", { name: /cache settings/i });

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(
        (screen.getByRole("checkbox", { name: /enable caching/i }) as HTMLInputElement)
          .checked,
      ).toBe(false),
    );
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    // Close and reopen the surface within staleTime.
    fireEvent.click(trigger);
    await waitFor(() =>
      expect(
        screen.queryByRole("checkbox", { name: /enable caching/i }),
      ).not.toBeInTheDocument(),
    );
    fireEvent.click(trigger);

    // The cached settings paint immediately without another network request.
    expect(
      (screen.getByRole("checkbox", { name: /enable caching/i }) as HTMLInputElement)
        .checked,
    ).toBe(false);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("PATCHes the toggle and TTL change and shows success", async () => {
    vi.spyOn(api, "fetchCacheSettings").mockResolvedValue({
      cache_enabled: true,
      cache_ttl_seconds: 86_400,
    });
    const update = vi
      .spyOn(api, "updateCacheSettings")
      .mockResolvedValue({ cache_enabled: false, cache_ttl_seconds: 3_600 });
    renderWith();
    fireEvent.click(screen.getByRole("button", { name: /cache settings/i }));
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: /enable caching/i }),
      ).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByRole("checkbox", { name: /enable caching/i }));
    fireEvent.change(screen.getByRole("combobox", { name: /cache lifetime/i }), {
      target: { value: "3600" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        "org-1",
        { cache_enabled: false, cache_ttl_seconds: 3_600 },
        { accessToken: "tok" },
      ),
    );
    await waitFor(() =>
      expect(screen.getByText("Cache settings saved.")).toBeInTheDocument(),
    );
  });

  it("caches the updated settings and invalidates discovery after a save", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: Infinity, staleTime: Infinity },
      },
    });
    // Seed a fresh discovery entry so the invalidation is observable.
    client.setQueryData(queryKeys.dashboard.cachedRun("test-user", "org-1"), {
      run: { id: "run-1" },
      cachedAsOf: "2026-07-16T00:00:00Z",
    });
    vi.spyOn(api, "fetchCacheSettings").mockResolvedValue({
      cache_enabled: true,
      cache_ttl_seconds: 86_400,
    });
    const updated = { cache_enabled: false, cache_ttl_seconds: 3_600 };
    vi.spyOn(api, "updateCacheSettings").mockResolvedValue(updated);

    renderWith({}, client);
    fireEvent.click(screen.getByRole("button", { name: /cache settings/i }));
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: /enable caching/i }),
      ).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByRole("checkbox", { name: /enable caching/i }));
    fireEvent.change(screen.getByRole("combobox", { name: /cache lifetime/i }), {
      target: { value: "3600" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(screen.getByText("Cache settings saved.")).toBeInTheDocument(),
    );

    // The settings key holds the updated policy, so a re-read needs no GET.
    expect(
      client.getQueryData(queryKeys.dashboard.settings("test-user", "org-1")),
    ).toEqual(updated);
    // Discovery is invalidated so the next rendered run follows the new policy.
    expect(
      client.getQueryState(queryKeys.dashboard.cachedRun("test-user", "org-1"))
        ?.isInvalidated,
    ).toBe(true);
  });

  it("shows a validation message on a 422", async () => {
    vi.spyOn(api, "fetchCacheSettings").mockResolvedValue({
      cache_enabled: true,
      cache_ttl_seconds: 86_400,
    });
    vi.spyOn(api, "updateCacheSettings").mockRejectedValue(
      new api.CacheSettingsValidationError("TTL out of range."),
    );
    renderWith();
    fireEvent.click(screen.getByRole("button", { name: /cache settings/i }));
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: /enable caching/i }),
      ).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() =>
      expect(screen.getByText("TTL out of range.")).toBeInTheDocument(),
    );
  });

  it("shows a forbidden message on a 403", async () => {
    vi.spyOn(api, "fetchCacheSettings").mockResolvedValue({
      cache_enabled: true,
      cache_ttl_seconds: 86_400,
    });
    vi.spyOn(api, "updateCacheSettings").mockRejectedValue(
      new api.CacheSettingsForbiddenError("Only admins can change this."),
    );
    renderWith();
    fireEvent.click(screen.getByRole("button", { name: /cache settings/i }));
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: /enable caching/i }),
      ).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() =>
      expect(screen.getByText("Only admins can change this.")).toBeInTheDocument(),
    );
  });
});
