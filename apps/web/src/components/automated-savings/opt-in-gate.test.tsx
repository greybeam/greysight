import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountChromeProvider } from "../../lib/account-context";
import * as automatedSavingsApi from "../../lib/automated-savings-api";
import { OptInGate, quoteIdent } from "./opt-in-gate";

// The shared vitest setup registers no automatic DOM cleanup, so unmount each
// render explicitly (project convention, see chart-tooltip.test.tsx).
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function withRole(role: "owner" | "member") {
  return {
    email: "u@acme.com", onSignOut: () => {}, signOutError: null,
    organizations: [{ id: "org-1", name: "Acme", role, accountLocator: null, connectionStatus: null }],
    activeOrganizationId: "org-1", setActiveOrganization: () => {}, openAddAccount: () => {},
    accessToken: "tok",
  };
}

describe("OptInGate", () => {
  it("shows GRANT SQL with the role name", () => {
    render(<AccountChromeProvider value={withRole("owner")}>
      <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
    </AccountChromeProvider>);
    expect(screen.getByText(/GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE "GREYSIGHT_RL"/)).toBeInTheDocument();
  });

  it("disables Agree for members", () => {
    render(<AccountChromeProvider value={withRole("member")}>
      <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
    </AccountChromeProvider>);
    expect(screen.getByRole("button", { name: /agree/i })).toBeDisabled();
  });

  it("escapes a quote in the role name", () => {
    expect(quoteIdent('WEIRD"ROLE')).toBe('"WEIRD""ROLE"');
  });

  it("calls agree then onAgreed for an owner", async () => {
    const agreeSpy = vi.spyOn(automatedSavingsApi, "agree").mockResolvedValue(undefined);
    const onAgreed = vi.fn();
    render(<AccountChromeProvider value={withRole("owner")}>
      <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={onAgreed} />
    </AccountChromeProvider>);

    fireEvent.click(screen.getByRole("button", { name: /agree/i }));

    await waitFor(() => expect(onAgreed).toHaveBeenCalled());
    expect(agreeSpy).toHaveBeenCalledWith("org-1", { accessToken: "tok" });
  });

  it("shows an error message when agree fails", async () => {
    vi.spyOn(automatedSavingsApi, "agree").mockRejectedValue(new Error("boom"));
    render(<AccountChromeProvider value={withRole("owner")}>
      <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
    </AccountChromeProvider>);

    fireEvent.click(screen.getByRole("button", { name: /agree/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/something went wrong/i);
  });

  it("copies the GRANT SQL to the clipboard, clearing a pending copy timeout on repeat clicks", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<AccountChromeProvider value={withRole("owner")}>
      <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
    </AccountChromeProvider>);

    const copyButton = screen.getByRole("button", { name: /copy grant sql/i });
    fireEvent.click(copyButton);
    fireEvent.click(copyButton);

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(
      'GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE "GREYSIGHT_RL";',
    ));
    expect(await screen.findByText("Copied!")).toBeInTheDocument();
  });

  it("falls back to matching orgId when the account chrome has no active organization", () => {
    render(<AccountChromeProvider value={{ ...withRole("owner"), activeOrganizationId: null }}>
      <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
    </AccountChromeProvider>);
    expect(screen.getByRole("button", { name: /agree/i })).not.toBeDisabled();
  });
});
