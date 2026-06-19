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

  it("renders nothing when there is no AccountChromeProvider", () => {
    // Render without wrapping in any provider so useAccountChrome() returns null.
    const { container } = render(<AccountSwitcher />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when organizations is empty", () => {
    const { container } = render(
      <AccountChromeProvider
        value={{
          email: "user@example.com",
          onSignOut: vi.fn(),
          signOutError: null,
          organizations: [],
          activeOrganizationId: null,
          setActiveOrganization: vi.fn(),
          openAddAccount: vi.fn(),
        }}
      >
        <AccountSwitcher />
      </AccountChromeProvider>,
    );
    expect(container.firstChild).toBeNull();
  });

  it("falls back to org name on the trigger when accountLocator is null", () => {
    renderWith({
      organizations: [{ id: "org-1", name: "Alpha Corp", accountLocator: null }],
      activeOrganizationId: "org-1",
    });
    // The trigger must display the org name when accountLocator is absent.
    expect(screen.getByRole("button", { name: /Alpha Corp/ })).toBeInTheDocument();
  });

  it("closes the menu when clicking outside the container", () => {
    renderWith({
      organizations: [{ id: "org-1", name: "Alpha", accountLocator: "AAA-111" }],
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
