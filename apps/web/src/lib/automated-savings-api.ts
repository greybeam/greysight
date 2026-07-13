import resolveApiUrl, { authHeaders } from "./api-client";

export type AutomatedSavingsApiOptions = {
  accessToken?: string | null;
};

export type SavingsStatus =
  | "idle"
  | "mid_suspend"
  | "in_cooldown"
  | "drifted"
  | "unsupported";

export type DriftState = "ok" | "drifted";

export type AutomatedSavingsStatus = {
  agreed: boolean;
  globalEnabled: boolean;
  grantPresent: boolean;
  grantCheckedAt: string | null;
};

// The API's check-access response (`CheckAccessResponse`) is a strict subset
// of StatusResponse — it does not include `agreed`/`global_enabled`.
export type CheckAccessResult = {
  grantPresent: boolean;
  grantCheckedAt: string | null;
};

export type WarehouseRow = {
  name: string;
  size: string;
  state: string;
  type: string;
  supported: boolean;
  minClusterCount: number;
  maxClusterCount: number;
  startedClusters: number;
  autoResumeOk: boolean;
  // Null for warehouses that have never been enrolled — the API only
  // populates these once an enrollment row exists.
  managedDefault: number | null;
  storedDefault: number | null;
  enabled: boolean;
  driftState: DriftState;
  driftedValue: number | null;
  cooldownTs: string | null;
  status: SavingsStatus;
};

// Thrown when the API rejects a managed-default write with 422 (below the
// server-enforced floor). The UI catches this specifically to surface the
// floor message instead of a generic error.
export class ManagedDefaultFloorError extends Error {
  constructor(message = "AUTO_SUSPEND can't be set below the floor.") {
    super(message);
    this.name = "ManagedDefaultFloorError";
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null) {
    throw new Error("Malformed automated-savings API response");
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown): string {
  if (typeof value !== "string") {
    throw new Error("Malformed automated-savings API response");
  }
  return value;
}

function asNumber(value: unknown): number {
  if (typeof value !== "number") {
    throw new Error("Malformed automated-savings API response");
  }
  return value;
}

function asBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new Error("Malformed automated-savings API response");
  }
  return value;
}

function asNullableNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

// The single snake_case → camelCase boundary for warehouse rows: the API
// returns snake_case JSON; everything downstream of this function (Tasks
// 18/19/20) consumes only the camelCase WarehouseRow type.
export function parseWarehouseRow(raw: unknown): WarehouseRow {
  const record = asRecord(raw);
  return {
    name: asString(record.name),
    size: asString(record.size),
    state: asString(record.state),
    type: asString(record.type),
    supported: asBoolean(record.supported),
    minClusterCount: asNumber(record.min_cluster_count),
    maxClusterCount: asNumber(record.max_cluster_count),
    startedClusters: asNumber(record.started_clusters),
    autoResumeOk: asBoolean(record.auto_resume_ok),
    managedDefault: asNullableNumber(record.managed_default),
    storedDefault: asNullableNumber(record.stored_default),
    enabled: asBoolean(record.enabled),
    driftState: asString(record.drift_state) as DriftState,
    driftedValue: asNullableNumber(record.drifted_value),
    cooldownTs: asNullableString(record.cooldown_ts),
    status: asString(record.status) as SavingsStatus,
  };
}

// The single snake_case → camelCase boundary for the status contract.
export function parseStatus(raw: unknown): AutomatedSavingsStatus {
  const record = asRecord(raw);
  return {
    agreed: asBoolean(record.agreed),
    globalEnabled: asBoolean(record.global_enabled),
    grantPresent: asBoolean(record.grant_present),
    grantCheckedAt: asNullableString(record.grant_checked_at),
  };
}

// The check-access contract (`CheckAccessResponse`) is a strict subset of
// StatusResponse — parse it separately instead of requiring the full shape.
export function parseCheckAccessResult(raw: unknown): CheckAccessResult {
  const record = asRecord(raw);
  return {
    grantPresent: asBoolean(record.grant_present),
    grantCheckedAt: asNullableString(record.grant_checked_at),
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
    throw new Error(`Automated savings API request failed with ${response.status}`);
  }

  return response.json();
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

export async function setManagedDefault(
  orgId: string,
  name: string,
  value: number,
  options: AutomatedSavingsApiOptions = {},
): Promise<void> {
  const headers = authHeaders(options.accessToken, {
    "content-type": "application/json",
  });
  const response = await fetch(
    resolveApiUrl(
      `/api/automated-savings/${orgId}/warehouses/${encodeURIComponent(name)}/managed-default`,
    ),
    {
      method: "POST",
      headers,
      body: JSON.stringify({ value }),
      cache: "no-store",
    },
  );

  if (response.status === 422) {
    throw new ManagedDefaultFloorError();
  }

  if (!response.ok) {
    throw new Error(`Automated savings API request failed with ${response.status}`);
  }
}

export async function reconcileWarehouse(
  orgId: string,
  name: string,
  accept: boolean,
  options: AutomatedSavingsApiOptions = {},
): Promise<void> {
  await fetchJson(
    `/api/automated-savings/${orgId}/warehouses/${encodeURIComponent(name)}/reconcile`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ accept }),
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
