import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider, type AccountChrome } from "../../lib/account-context";
import * as api from "../../lib/cache-settings-api";
import { createTestQueryClient } from "../../lib/query-test-utils";
import { QueryClientProvider } from "@tanstack/react-query";
import AccountSwitcher from "./account-switcher";

function renderWith(overrides: Partial<AccountChrome>) {
  const value: AccountChrome = {
    userId: "test-user",
    identityEpoch: 0,
    email: "user@example.com",
    onSignOut: vi.fn(),
    signOutError: null,
    organizations: [],
    activeOrganizationId: null,
    setActiveOrganization: vi.fn(),
    openAddAccount: vi.fn(),
    accessToken: null,
    ...overrides,
  };
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AccountChromeProvider value={value}>
        <AccountSwitcher />
      </AccountChromeProvider>
    </QueryClientProvider>,
  );
  return value;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AccountSwitcher", () => {
  it("shows the active org locator on the trigger", () => {
    renderWith({
      organizations: [{ id: "org-1", name: "Alpha", role: "member", accountLocator: "AAA-111" }],
      activeOrganizationId: "org-1",
    });
    expect(screen.getByRole("button", { name: /AAA-111/ })).toBeInTheDocument();
  });

  it("switches org on selection", () => {
    const value = renderWith({
      organizations: [
        { id: "org-1", name: "Alpha", role: "member", accountLocator: "AAA-111" },
        { id: "org-2", name: "Beta", role: "member", accountLocator: "BBB-222" },
      ],
      activeOrganizationId: "org-1",
    });
    fireEvent.click(screen.getByRole("button", { name: /AAA-111/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Beta/ }));
    expect(value.setActiveOrganization).toHaveBeenCalledWith("org-2");
  });

  it("invokes openAddAccount from the Add Account item", () => {
    const value = renderWith({
      organizations: [{ id: "org-1", name: "Alpha", role: "member", accountLocator: "AAA-111" }],
      activeOrganizationId: "org-1",
    });
    fireEvent.click(screen.getByRole("button", { name: /AAA-111/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Add Account/ }));
    expect(value.openAddAccount).toHaveBeenCalled();
  });

  it("opens cache settings from an admin account row without switching accounts", async () => {
    vi.spyOn(api, "fetchCacheSettings").mockResolvedValue({
      cache_enabled: true,
      cache_ttl_seconds: 86_400,
    });
    const value = renderWith({
      organizations: [
        { id: "org-1", name: "Alpha", role: "member", accountLocator: "AAA-111" },
        { id: "org-2", name: "Beta", role: "admin", accountLocator: "BBB-222" },
      ],
      activeOrganizationId: "org-1",
      accessToken: "tok",
    });

    fireEvent.click(screen.getByRole("button", { name: /AAA-111/ }));

    expect(
      screen.queryByRole("menuitem", { name: /cache settings for alpha/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /cache settings for beta/i }));

    await waitFor(() =>
      expect(api.fetchCacheSettings).toHaveBeenCalledWith("org-2", {
        accessToken: "tok",
      }),
    );
    expect(
      screen.getByRole("dialog", { name: "Cache settings for Beta (BBB-222)" }),
    ).toBeInTheDocument();
    expect(value.setActiveOrganization).not.toHaveBeenCalled();
  });

  it("renders nothing when there is no AccountChromeProvider", () => {
    // Render without wrapping in any provider so useAccountChrome() returns null.
    const { container } = render(<AccountSwitcher />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when organizations is empty", () => {
    const { container } = render(
      <AccountChromeProvider
        value={{
          userId: "test-user",
    identityEpoch: 0,
    email: "user@example.com",
          onSignOut: vi.fn(),
          signOutError: null,
          organizations: [],
          activeOrganizationId: null,
          setActiveOrganization: vi.fn(),
          openAddAccount: vi.fn(),
          accessToken: null,
        }}
      >
        <AccountSwitcher />
      </AccountChromeProvider>,
    );
    expect(container.firstChild).toBeNull();
  });

  it("falls back to org name on the trigger when accountLocator is null", () => {
    renderWith({
      organizations: [{ id: "org-1", name: "Alpha Corp", role: "member", accountLocator: null }],
      activeOrganizationId: "org-1",
    });
    // The trigger must display the org name when accountLocator is absent.
    expect(screen.getByRole("button", { name: /Alpha Corp/ })).toBeInTheDocument();
  });

  it("closes the menu when clicking outside the container", () => {
    renderWith({
      organizations: [{ id: "org-1", name: "Alpha", role: "member", accountLocator: "AAA-111" }],
      activeOrganizationId: "org-1",
    });
    // Open the menu.
    fireEvent.click(screen.getByRole("button", { name: /AAA-111/ }));
    expect(screen.getByRole("menu")).toBeInTheDocument();

    // Simulate a mousedown outside the component (on document.body).
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
