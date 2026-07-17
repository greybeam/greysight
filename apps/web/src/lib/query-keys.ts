import type { DashboardViewRangeRequest } from "./dashboard-api";

export type QueryUserId = string;
export type QueryOrganizationId = string;

// Restrict query-key param values to key-safe primitives so callers cannot
// smuggle an access token or credential object into a cache key.
export type QueryKeyParamValue = string | number | boolean | null | undefined;

// Normalize range inputs so semantically identical range keys are byte-for-byte
// identical (e.g. `{}` and `{ windowDays: 30 }` collapse to the same relative
// window), keeping the cache from splitting into redundant entries.
function normalizedRange(range: DashboardViewRangeRequest) {
  return range.startDate !== undefined && range.endDate !== undefined
    ? {
        mode: "custom" as const,
        startDate: range.startDate,
        endDate: range.endDate,
      }
    : { mode: "relative" as const, windowDays: range.windowDays ?? 30 };
}

const orgScope = (userId: string, orgId: string) => [userId, orgId] as const;
const dashboardScope = (userId: string, orgId: string) =>
  [...orgScope(userId, orgId), "dashboard"] as const;
const autoSavingsScope = (userId: string, orgId: string) =>
  [...orgScope(userId, orgId), "auto-savings"] as const;

export const queryKeys = {
  scope: orgScope,
  memberships: (userId: string) => [userId, "memberships"] as const,
  dashboard: {
    scope: dashboardScope,
    cachedRun: (userId: string, orgId: string) =>
      [...dashboardScope(userId, orgId), "cached-run"] as const,
    view: (
      userId: string,
      orgId: string,
      runId: string,
      range: DashboardViewRangeRequest,
    ) =>
      [
        ...dashboardScope(userId, orgId),
        "view",
        runId,
        normalizedRange(range),
      ] as const,
    settings: (userId: string, orgId: string) =>
      [...dashboardScope(userId, orgId), "cache-settings"] as const,
    source: (
      userId: string,
      orgId: string,
      runId: string,
      sourceId: string,
      range: DashboardViewRangeRequest,
    ) =>
      [
        ...dashboardScope(userId, orgId),
        "source",
        runId,
        sourceId,
        normalizedRange(range),
      ] as const,
  },
  autoSavings: {
    scope: autoSavingsScope,
    status: (userId: string, orgId: string) =>
      [...autoSavingsScope(userId, orgId), "status"] as const,
    warehouses: (userId: string, orgId: string) =>
      [...autoSavingsScope(userId, orgId), "warehouses"] as const,
    access: (userId: string, orgId: string) =>
      [...autoSavingsScope(userId, orgId), "access"] as const,
    stats: (
      userId: string,
      orgId: string,
      params: Readonly<Record<string, QueryKeyParamValue>>,
    ) =>
      [...autoSavingsScope(userId, orgId), "suspension-stats", params] as const,
    events: (userId: string, orgId: string, cursor: string | null) =>
      [...autoSavingsScope(userId, orgId), "suspension-events", cursor] as const,
  },
} as const;
