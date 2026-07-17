import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider, type AccountChrome } from "../../lib/account-context";
import * as api from "../../lib/cache-settings-api";
import CacheSettings from "./cache-settings";

function renderWith(overrides: Partial<AccountChrome>) {
  const value: AccountChrome = {
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
  render(
    <AccountChromeProvider value={value}>
      <CacheSettings />
    </AccountChromeProvider>,
  );
  return value;
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
    renderWith({});
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

  it("PATCHes the toggle and TTL change and shows success", async () => {
    vi.spyOn(api, "fetchCacheSettings").mockResolvedValue({
      cache_enabled: true,
      cache_ttl_seconds: 86_400,
    });
    const update = vi
      .spyOn(api, "updateCacheSettings")
      .mockResolvedValue({ cache_enabled: false, cache_ttl_seconds: 3_600 });
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /cache settings/i }));
    await waitFor(() => expect(api.fetchCacheSettings).toHaveBeenCalled());

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

  it("shows a validation message on a 422", async () => {
    vi.spyOn(api, "fetchCacheSettings").mockResolvedValue({
      cache_enabled: true,
      cache_ttl_seconds: 86_400,
    });
    vi.spyOn(api, "updateCacheSettings").mockRejectedValue(
      new api.CacheSettingsValidationError("TTL out of range."),
    );
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /cache settings/i }));
    await waitFor(() => expect(api.fetchCacheSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() =>
      expect(screen.getByText("TTL out of range.")).toBeInTheDocument(),
    );
  });
});
