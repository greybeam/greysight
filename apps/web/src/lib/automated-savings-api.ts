import resolveApiUrl, { authHeaders } from "./api-client";
import { DashboardApiError } from "./dashboard-errors";

export type AutomatedSavingsApiOptions = {
  accessToken?: string | null;
};

export type SavingsStatus = "idle" | "transitioning" | "unsupported";

export type AutomatedSavingsStatus = {
  agreed: boolean;
  globalEnabled: boolean;
  grantPresent: boolean;
  grantCheckedAt: string | null;
  roleName: string | null;
};

// The API's check-access response (`CheckAccessResponse`) is a strict subset
// of StatusResponse — it does not include `agreed`/`global_enabled`.
export type CheckAccessResult = {
  grantPresent: boolean;
  grantCheckedAt: string | null;
  roleName: string | null;
};

export type WarehouseRow = {
  name: string;
  // size/state/type and the cluster-count columns are nullable in the API
  // contract: SHOW WAREHOUSES on Standard-edition Snowflake omits the
  // Enterprise-only cluster columns (so max_cluster_count arrives null), and
  // size/state/type can also be absent. The parser must mirror that or it
  // throws and blanks the whole page.
  size: string | null;
  state: string | null;
  type: string | null;
  supported: boolean;
  minClusterCount: number | null;
  maxClusterCount: number | null;
  startedClusters: number | null;
  autoResumeOk: boolean;
  autoSuspend: number | null;
  quiescing: number | null;
  enabled: boolean;
  status: SavingsStatus;
};

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null) {
    throw new Error("Malformed automated-savings API response");
  }
  return value as Record<string, unknown>;
}

function asSavingsStatus(value: unknown): SavingsStatus {
  if (value === "idle" || value === "transitioning" || value === "unsupported") {
    return value;
  }
  throw new Error("Malformed automated-savings API response");
}

// The single snake_case → camelCase boundary for warehouse rows: the API
// returns snake_case JSON; everything downstream consumes only the camelCase
// WarehouseRow type. We validate the status enum (it drives display/branching)
// and trust the TS contract for the rest, matching the repo norm in
// dashboard-api.ts.
export function parseWarehouseRow(raw: unknown): WarehouseRow {
  const record = asRecord(raw);
  return {
    name: record.name as string,
    size: record.size as string | null,
    state: record.state as string | null,
    type: record.type as string | null,
    supported: record.supported as boolean,
    minClusterCount: record.min_cluster_count as number | null,
    maxClusterCount: record.max_cluster_count as number | null,
    startedClusters: record.started_clusters as number | null,
    autoResumeOk: record.auto_resume_ok as boolean,
    autoSuspend: record.auto_suspend as number | null,
    quiescing: record.quiescing as number | null,
    enabled: record.enabled as boolean,
    status: asSavingsStatus(record.status),
  };
}

// The single snake_case → camelCase boundary for the status contract.
export function parseStatus(raw: unknown): AutomatedSavingsStatus {
  const record = asRecord(raw);
  return {
    agreed: record.agreed as boolean,
    globalEnabled: record.global_enabled as boolean,
    grantPresent: record.grant_present as boolean,
    grantCheckedAt: (record.grant_checked_at as string | null) ?? null,
    roleName: (record.role_name as string | null) ?? null,
  };
}

// The check-access contract (`CheckAccessResponse`) is a strict subset of
// StatusResponse — parse it separately instead of requiring the full shape.
export function parseCheckAccessResult(raw: unknown): CheckAccessResult {
  const record = asRecord(raw);
  return {
    grantPresent: record.grant_present as boolean,
    grantCheckedAt: (record.grant_checked_at as string | null) ?? null,
    roleName: (record.role_name as string | null) ?? null,
  };
}

async function fetchJson(
  path: string,
  init: RequestInit = {},
  options: AutomatedSavingsApiOptions = {},
): Promise<unknown> {
  const headers = authHeaders(
    options.accessToken,
    Object.fromEntries(new Headers(init.headers)),
  );

  const response = await fetch(resolveApiUrl(path), {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    const userSafeMessage = await readUserSafeMessage(response);
    throw new DashboardApiError(
      `Auto Savings API request failed with ${response.status}`,
      userSafeMessage,
    );
  }

  return response.json();
}

async function readUserSafeMessage(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as unknown;
    if (typeof body !== "object" || body === null) return null;
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail !== "object" || detail === null) return null;
    const message = (detail as { user_safe_message?: unknown })
      .user_safe_message;
    return typeof message === "string" && message.length > 0 ? message : null;
  } catch {
    return null;
  }
}

export async function fetchStatus(
  orgId: string,
  options: AutomatedSavingsApiOptions = {},
): Promise<AutomatedSavingsStatus> {
  const payload = await fetchJson(
    `/api/automated-savings/${orgId}/status`,
    {},
    options,
  );
  return parseStatus(payload);
}

export async function fetchWarehouses(
  orgId: string,
  options: AutomatedSavingsApiOptions = {},
): Promise<WarehouseRow[]> {
  const payload = await fetchJson(
    `/api/automated-savings/${orgId}/warehouses`,
    {},
    options,
  );
  if (!Array.isArray(payload)) {
    throw new Error("Malformed automated-savings API response");
  }
  return payload.map(parseWarehouseRow);
}

export async function agree(
  orgId: string,
  options: AutomatedSavingsApiOptions = {},
): Promise<void> {
  await fetchJson(
    `/api/automated-savings/${orgId}/agree`,
    { method: "POST" },
    options,
  );
}

export async function setGlobalSwitch(
  orgId: string,
  enabled: boolean,
  options: AutomatedSavingsApiOptions = {},
): Promise<void> {
  await fetchJson(
    `/api/automated-savings/${orgId}/global-switch`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
    options,
  );
}

export async function toggleWarehouse(
  orgId: string,
  name: string,
  enabled: boolean,
  options: AutomatedSavingsApiOptions = {},
): Promise<void> {
  await fetchJson(
    `/api/automated-savings/${orgId}/warehouses/${encodeURIComponent(name)}/toggle`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
    options,
  );
}

export async function checkAccess(
  orgId: string,
  options: AutomatedSavingsApiOptions = {},
): Promise<CheckAccessResult> {
  const payload = await fetchJson(
    `/api/automated-savings/${orgId}/check-access`,
    { method: "POST" },
    options,
  );
  return parseCheckAccessResult(payload);
}
