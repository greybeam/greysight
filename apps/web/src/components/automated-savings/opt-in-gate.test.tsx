import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AccountChromeProvider } from "../../lib/account-context";
import { OptInGate } from "./opt-in-gate";

// The shared vitest setup registers no automatic DOM cleanup, so unmount each
// render explicitly (project convention, see chart-tooltip.test.tsx).
afterEach(cleanup);

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
});
