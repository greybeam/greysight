import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider, type AccountChrome } from "../../lib/account-context";
import AccountSwitcher from "./account-switcher";

function renderWith(overrides: Partial<AccountChrome>) {
  const value: AccountChrome = {
    email: "user@example.com",
    onSignOut: vi.fn(),
    signOutError: null,
    organizations: [],
    activeOrganizationId: null,
    setActiveOrganization: vi.fn(),
    openAddAccount: vi.fn(),
    ...overrides,
  };
  render(
    <AccountChromeProvider value={value}>
      <AccountSwitcher />
    </AccountChromeProvider>,
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
      organizations: [{ id: "org-1", name: "Alpha", accountLocator: "AAA-111" }],
      activeOrganizationId: "org-1",
    });
    expect(screen.getByRole("button", { name: /AAA-111/ })).toBeInTheDocument();
  });

  it("switches org on selection", () => {
    const value = renderWith({
      organizations: [
        { id: "org-1", name: "Alpha", accountLocator: "AAA-111" },
        { id: "org-2", name: "Beta", accountLocator: "BBB-222" },
      ],
      activeOrganizationId: "org-1",
    });
    fireEvent.click(screen.getByRole("button", { name: /AAA-111/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Beta/ }));
    expect(value.setActiveOrganization).toHaveBeenCalledWith("org-2");
  });

  it("invokes openAddAccount from the Add Account item", () => {
    const value = renderWith({
      organizations: [{ id: "org-1", name: "Alpha", accountLocator: "AAA-111" }],
      activeOrganizationId: "org-1",
    });
    fireEvent.click(screen.getByRole("button", { name: /AAA-111/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Add Account/ }));
    expect(value.openAddAccount).toHaveBeenCalled();
  });
});
