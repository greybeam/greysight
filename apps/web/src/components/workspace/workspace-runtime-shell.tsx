"use client";

import { createContext, useContext, useMemo, useState } from "react";

import OrgShell, { type SelectedOrganization } from "../org/org-shell";

export type WorkspaceDataSource = "demo" | "snowflake";

type WorkspaceRuntime = {
  accessToken: string | null;
  authRequired: boolean;
  dataSource: WorkspaceDataSource;
  organization: SelectedOrganization | null;
};

const WorkspaceRuntimeContext = createContext<WorkspaceRuntime | null>(null);

export function useWorkspaceRuntime(): WorkspaceRuntime | null {
  return useContext(WorkspaceRuntimeContext);
}

type WorkspaceRuntimeShellProps = {
  authRequired: boolean;
  children: React.ReactNode;
  dataSource: WorkspaceDataSource;
};

export function WorkspaceRuntimeShell({
  authRequired,
  children,
  dataSource,
}: WorkspaceRuntimeShellProps) {
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [organization, setOrganization] = useState<SelectedOrganization | null>(
    null,
  );
  const runtime = useMemo(
    () => ({ accessToken, authRequired, dataSource, organization }),
    [accessToken, authRequired, dataSource, organization],
  );
  const bypassModeLabel =
    dataSource === "snowflake" ? "Local Snowflake mode" : "Demo mode";

  return (
    <OrgShell
      authRequired={authRequired}
      bypassModeLabel={bypassModeLabel}
      onAccessTokenChange={setAccessToken}
      onOrganizationChange={setOrganization}
    >
      <WorkspaceRuntimeContext.Provider value={runtime}>
        {children}
      </WorkspaceRuntimeContext.Provider>
    </OrgShell>
  );
}
