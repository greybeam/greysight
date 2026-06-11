"use client";

import { useState } from "react";

import OrgShell, { type SelectedOrganization } from "../org/org-shell";
import CostDashboard, {
  type CostDashboardRuntime,
  type DashboardModeLabel,
} from "./cost-dashboard";

type DashboardDataSource = "demo" | "snowflake";

type DashboardRuntimeShellProps = {
  authRequired: boolean;
  dataSource?: DashboardDataSource;
};

const LOCAL_SNOWFLAKE_ORGANIZATION = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Local Snowflake",
};

export default function DashboardRuntimeShell({
  authRequired,
  dataSource = "demo",
}: DashboardRuntimeShellProps) {
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [organization, setOrganization] = useState<SelectedOrganization | null>(
    null,
  );
  const localSnowflakeRuntime: CostDashboardRuntime | null =
    !authRequired && dataSource === "snowflake"
      ? {
          accessToken: null,
          organizationId: LOCAL_SNOWFLAKE_ORGANIZATION.id,
          organizationName: LOCAL_SNOWFLAKE_ORGANIZATION.name,
        }
      : null;
  const authenticatedRuntime: CostDashboardRuntime | null =
    authRequired && organization
      ? {
          accessToken,
          organizationId: organization.id,
          organizationName: organization.name,
        }
      : null;
  const runtime = authenticatedRuntime ?? localSnowflakeRuntime;
  const modeLabel: DashboardModeLabel = authRequired
    ? "Authenticated Snowflake"
    : dataSource === "snowflake"
      ? "Local Snowflake"
      : "Demo";

  return (
    <OrgShell
      authRequired={authRequired}
      onAccessTokenChange={setAccessToken}
      onOrganizationChange={setOrganization}
    >
      <CostDashboard
        demoMode={!authRequired && dataSource !== "snowflake"}
        modeLabel={modeLabel}
        runtime={runtime}
      />
    </OrgShell>
  );
}
