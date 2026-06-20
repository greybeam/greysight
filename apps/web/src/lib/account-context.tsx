"use client";

import { createContext, useContext } from "react";

import type { MembershipOrganization } from "./session-memberships";

// Account-level chrome (signed-in identity + sign-out) lifted out of OrgShell so
// the dashboard's own app bar can render it as a single unified header instead
// of OrgShell stacking a second bar above the dashboard. Consumers read it via
// useAccountChrome(); it is null in unauthenticated/demo contexts where no
// provider wraps the tree, so the dashboard header simply omits the user menu.
export type AccountChrome = {
  email: string;
  onSignOut: () => void;
  signOutError: string | null;
  // Org switcher: the user's orgs, the active selection, and the actions the
  // header dropdown drives. Empty list / null active in the single-org demo
  // contexts where the switcher simply shows the lone (or no) account.
  organizations: MembershipOrganization[];
  activeOrganizationId: string | null;
  setActiveOrganization: (id: string) => void;
  openAddAccount: () => void;
  // Bearer token for authenticated calls the header makes (e.g. inviting users).
  accessToken: string | null;
};

const AccountChromeContext = createContext<AccountChrome | null>(null);

export const AccountChromeProvider = AccountChromeContext.Provider;

export function useAccountChrome(): AccountChrome | null {
  return useContext(AccountChromeContext);
}
