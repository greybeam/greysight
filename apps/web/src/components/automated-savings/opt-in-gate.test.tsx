import {
  cleanup,
  fireEvent,
  render as rtlRender,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountChromeProvider } from "../../lib/account-context";
import * as automatedSavingsApi from "../../lib/automated-savings-api";
import { createTestQueryClient } from "../../lib/query-test-utils";
import { normalizeRoleName, OptInGate, quoteIdent } from "./opt-in-gate";

// OptInGate now reads the query identity (useQueryIdentity), which requires a
// QueryClientProvider. Wrap every render so the gate resolves one; identity
// falls back to the AccountChrome-derived snapshot (no QueryIdentityProvider).
function render(ui: ReactElement) {
  return rtlRender(
    <QueryClientProvider client={createTestQueryClient()}>
      {ui}
    </QueryClientProvider>,
  );
}

// The shared vitest setup registers no automatic DOM cleanup, so unmount each
// render explicitly (project convention, see chart-tooltip.test.tsx).
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function withRole(role: "owner" | "member") {
  return {
    userId: "test-user",
    identityEpoch: 0,
    email: "u@acme.com",
    onSignOut: () => {},
    signOutError: null,
    organizations: [
      {
        id: "org-1",
        name: "Acme",
        role,
        accountLocator: null,
        connectionStatus: null,
      },
    ],
    activeOrganizationId: "org-1",
    setActiveOrganization: () => {},
    openAddAccount: () => {},
    accessToken: "tok",
  };
}

describe("OptInGate", () => {
  it("disables Agree for members", () => {
    render(
      <AccountChromeProvider value={withRole("member")}>
        <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
      </AccountChromeProvider>,
    );
    expect(screen.getByRole("button", { name: /agree/i })).toBeDisabled();
  });

  it("escapes a quote in the role name", () => {
    expect(quoteIdent('WEIRD"ROLE')).toBe('"WEIRD""ROLE"');
  });

  it("normalizes a blank/whitespace-only role name to null instead of an empty-quoted role", () => {
    expect(normalizeRoleName("   ")).toBeNull();
    expect(normalizeRoleName("")).toBeNull();
    expect(normalizeRoleName(null)).toBeNull();
    expect(normalizeRoleName("  GREYSIGHT_RL  ")).toBe("  GREYSIGHT_RL  ");
  });

  it("calls agree then onAgreed for an owner", async () => {
    const agreeSpy = vi
      .spyOn(automatedSavingsApi, "agree")
      .mockResolvedValue(undefined);
    const onAgreed = vi.fn();
    render(
      <AccountChromeProvider value={withRole("owner")}>
        <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={onAgreed} />
      </AccountChromeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /agree/i }));

    await waitFor(() => expect(onAgreed).toHaveBeenCalled());
    expect(agreeSpy).toHaveBeenCalledWith("org-1", { accessToken: "tok" });
  });

  it("recovers the Agree button when the post-agreement status refetch fails and leaves the gate mounted", async () => {
    // agree() succeeds, but the shell's post-agreement status refetch fails, so
    // the cache keeps agreed:false and this same gate stays mounted. The button
    // must reset to enabled (recoverable) rather than stay stuck "submitting"
    // and disabled forever. onAgreed here does nothing — standing in for a
    // failed refetch that never unmounts the gate.
    vi.spyOn(automatedSavingsApi, "agree").mockResolvedValue(undefined);
    const onAgreed = vi.fn();
    render(
      <AccountChromeProvider value={withRole("owner")}>
        <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={onAgreed} />
      </AccountChromeProvider>,
    );

    const agreeButton = screen.getByRole("button", { name: /agree/i });
    fireEvent.click(agreeButton);

    await waitFor(() => expect(onAgreed).toHaveBeenCalled());
    await waitFor(() => expect(agreeButton).not.toBeDisabled());
  });

  it("keeps the Agree button disabled until the completion callback settles, preventing a duplicate agree POST", async () => {
    // agree() resolves, but the shell's post-agreement status invalidation/refetch
    // (onAgreed) is still pending. Until it settles the cache still reads
    // agreed:false, so this gate stays mounted. The button must remain disabled
    // during that window — a second click must NOT issue another agree POST.
    const agreeSpy = vi
      .spyOn(automatedSavingsApi, "agree")
      .mockResolvedValue(undefined);
    let resolveComplete: (() => void) | undefined;
    const onAgreed = vi.fn(
      () => new Promise<void>((resolve) => { resolveComplete = resolve; }),
    );
    render(
      <AccountChromeProvider value={withRole("owner")}>
        <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={onAgreed} />
      </AccountChromeProvider>,
    );

    const agreeButton = screen.getByRole("button", { name: /agree/i });
    fireEvent.click(agreeButton);

    await waitFor(() => expect(onAgreed).toHaveBeenCalled());
    // Completion still pending: the gate stays busy and a second click is a no-op.
    expect(agreeButton).toBeDisabled();
    fireEvent.click(agreeButton);
    expect(agreeSpy).toHaveBeenCalledOnce();

    // Once the completion settles (e.g. the refetch resolved or failed, but the
    // gate remained mounted), the button recovers to a clickable state.
    resolveComplete?.();
    await waitFor(() => expect(agreeButton).not.toBeDisabled());
  });

  it("shows an error message when agree fails", async () => {
    vi.spyOn(automatedSavingsApi, "agree").mockRejectedValue(new Error("boom"));
    render(
      <AccountChromeProvider value={withRole("owner")}>
        <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
      </AccountChromeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /agree/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /something went wrong/i,
    );
  });

  it("protects an organization from overlapping agreement requests", async () => {
    let resolveAgree: (() => void) | undefined;
    const agreeSpy = vi.spyOn(automatedSavingsApi, "agree").mockImplementation(
      () => new Promise<void>((resolve) => { resolveAgree = resolve; }),
    );
    render(
      <AccountChromeProvider value={withRole("owner")}>
        <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
      </AccountChromeProvider>,
    );
    const agreeButton = screen.getByRole("button", { name: /agree/i });

    fireEvent.click(agreeButton);
    fireEvent.click(agreeButton);

    expect(agreeSpy).toHaveBeenCalledOnce();
    resolveAgree?.();
  });

  it("copies the GRANT SQL to the clipboard, clearing a pending copy timeout on repeat clicks", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <AccountChromeProvider value={withRole("owner")}>
        <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
      </AccountChromeProvider>,
    );

    const copyButton = screen.getByRole("button", { name: /copy grant sql/i });
    fireEvent.click(copyButton);
    fireEvent.click(copyButton);

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        'GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE "GREYSIGHT_RL";',
      ),
    );
    expect(await screen.findByText("Copied!")).toBeInTheDocument();
  });

  it("falls back to matching orgId when account chrome has no active organization", () => {
    render(
      <AccountChromeProvider
        value={{ ...withRole("owner"), activeOrganizationId: null }}
      >
        <OptInGate orgId="org-1" roleName="GREYSIGHT_RL" onAgreed={() => {}} />
      </AccountChromeProvider>,
    );
    expect(screen.getByRole("button", { name: /agree/i })).not.toBeDisabled();
  });
});
