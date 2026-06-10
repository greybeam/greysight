"use client";

import { useState } from "react";

import OrgShell, { type SelectedOrganization } from "../org/org-shell";
import CostDashboard, { type CostDashboardRuntime } from "./cost-dashboard";

type DashboardRuntimeShellProps = {
  authRequired: boolean;
};

export default function DashboardRuntimeShell({
  authRequired,
}: DashboardRuntimeShellProps) {
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [organization, setOrganization] =
    useState<SelectedOrganization | null>(null);

  const runtime: CostDashboardRuntime | null =
    authRequired && organization
      ? {
          accessToken,
          organizationId: organization.id,
          organizationName: organization.name,
        }
      : null;

  return (
    <OrgShell
      authRequired={authRequired}
      onAccessTokenChange={setAccessToken}
      onOrganizationChange={setOrganization}
    >
      <CostDashboard demoMode={!authRequired} runtime={runtime} />
    </OrgShell>
  );
}
