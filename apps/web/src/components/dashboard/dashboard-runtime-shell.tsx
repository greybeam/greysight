"use client";

import { useWorkspaceRuntime } from "../workspace/workspace-runtime-shell";
import CostDashboard, {
  type CostDashboardRuntime,
  type DashboardModeLabel,
} from "./cost-dashboard";

const LOCAL_SNOWFLAKE_ORGANIZATION = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Local Snowflake",
};

export default function DashboardRuntimeShell() {
  const workspace = useWorkspaceRuntime();

  if (!workspace) {
    throw new Error("DashboardRuntimeShell requires WorkspaceRuntimeShell");
  }

  const { accessToken, authRequired, dataSource, organization } = workspace;
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
          accountLocator: organization.accountLocator,
        }
      : null;
  const runtime = authenticatedRuntime ?? localSnowflakeRuntime;
  const modeLabel: DashboardModeLabel = authRequired
    ? "Authenticated Snowflake"
    : dataSource === "snowflake"
      ? "Local Snowflake"
      : "Demo";
  return (
    <CostDashboard
      demoMode={!authRequired && dataSource !== "snowflake"}
      modeLabel={modeLabel}
      runtime={runtime}
    />
  );
}
