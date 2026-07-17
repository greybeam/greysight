import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider, type AccountChrome } from "../../lib/account-context";
import * as api from "../../lib/org-invitations-api";
import InviteUser from "./invite-user";

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
      <InviteUser />
    </AccountChromeProvider>,
  );
  return value;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InviteUser", () => {
  it("renders nothing for a member", () => {
    const { container } = render(
      <AccountChromeProvider
        value={{
          userId: "test-user",
          identityEpoch: 0,
          email: "u@e.com",
          onSignOut: vi.fn(),
          signOutError: null,
          organizations: [
            { id: "org-1", name: "Acme", role: "member", accountLocator: null },
          ],
          activeOrganizationId: "org-1",
          setActiveOrganization: vi.fn(),
          openAddAccount: vi.fn(),
          accessToken: "tok",
        }}
      >
        <InviteUser />
      </AccountChromeProvider>,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the org name and locator in the popover heading", () => {
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    expect(screen.getByText(/Add user to Acme \(AAA-111\)/)).toBeInTheDocument();
  });

  it("rejects a non-work email without calling the API", () => {
    const spy = vi.spyOn(api, "inviteUser");
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    fireEvent.change(screen.getByPlaceholderText(/work-email/i), {
      target: { value: "x@gmail.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));
    expect(screen.getByText("Please use your work email.")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("shows success after a successful invite", async () => {
    vi.spyOn(api, "inviteUser").mockResolvedValue("new@acme.com");
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    fireEvent.change(screen.getByPlaceholderText(/work-email/i), {
      target: { value: "new@acme.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));
    await waitFor(() =>
      expect(
        screen.getByText("Invited: new@acme.com to Acme"),
      ).toBeInTheDocument(),
    );
  });

  it("shows the already-a-member message on conflict", async () => {
    vi.spyOn(api, "inviteUser").mockRejectedValue(
      new api.InviteConflictError("new@acme.com is already a member."),
    );
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    fireEvent.change(screen.getByPlaceholderText(/work-email/i), {
      target: { value: "new@acme.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));
    await waitFor(() =>
      expect(
        screen.getByText("new@acme.com is already a member."),
      ).toBeInTheDocument(),
    );
  });

  it("renders the heading without parentheses when there is no account locator", () => {
    renderWith({
      organizations: [
        { id: "org-1", name: "Acme", role: "owner", accountLocator: null },
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    expect(screen.getByText("Add user to Acme")).toBeInTheDocument();
    expect(screen.queryByText(/Add user to Acme \(/)).toBeNull();
  });

  it("shows the generic message on an unclassified error", async () => {
    vi.spyOn(api, "inviteUser").mockRejectedValue(new Error("network boom"));
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    fireEvent.change(screen.getByPlaceholderText(/work-email/i), {
      target: { value: "new@acme.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));
    await waitFor(() =>
      expect(
        screen.getByText("Something went wrong. Please try again."),
      ).toBeInTheDocument(),
    );
  });

  it("shows the validation message on InviteValidationError", async () => {
    vi.spyOn(api, "inviteUser").mockRejectedValue(
      new api.InviteValidationError("Please use a valid work email."),
    );
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    fireEvent.change(screen.getByPlaceholderText(/work-email/i), {
      target: { value: "new@acme.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));
    await waitFor(() =>
      expect(
        screen.getByText("Please use a valid work email."),
      ).toBeInTheDocument(),
    );
  });
});
