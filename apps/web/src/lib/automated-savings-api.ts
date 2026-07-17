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
    // FastAPI string details on this API are server-curated and user-safe, so
    // surface them directly (e.g. "Could not list Snowflake warehouses.").
    if (typeof detail === "string") {
      return detail.length > 0 ? detail : null;
    }
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

export type SuspensionStatsBucket = {
  day: string;
  counts: Record<string, number>;
};

export type SuspensionStatsResponse = {
  days: number;
  warehouses: string[];
  buckets: SuspensionStatsBucket[];
};

export type SuspensionEvent = {
  id: string;
  createdAt: string;
  warehouseName: string;
  action: string;
  reason: string;
  observedStartedClusters: number | null;
  observedResumedOn: string | null;
  observedAt: string;
};

export type SuspensionEventsPage = {
  events: SuspensionEvent[];
  nextCursor: string | null;
};

const MALFORMED = "Malformed automated-savings API response";

function asNonEmptyString(value: unknown): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(MALFORMED);
  }
  return value;
}

// Requires an explicit timezone offset (`Z`/`z` or `±HH:MM`/`±HHMM`) so a
// timezone-naive timestamp — which JS's Date parsing would silently treat as
// local time — is rejected rather than mislabeled.
const TIMEZONE_OFFSET_PATTERN = /([Zz]|[+-]\d{2}:?\d{2})$/;

// Extracts the leading `YYYY-MM-DD` date portion of an ISO-8601 timestamp so
// it can be round-trip-checked by `asCalendarDay` — this is what catches
// rollover dates like "2026-02-30T..." that `new Date` would otherwise
// silently normalize (to March 2) instead of rejecting.
const ISO_TIMESTAMP_DATE_PATTERN = /^(\d{4}-\d{2}-\d{2})T/;

// Requires a canonical `YYYY-MM-DD` calendar day and rejects dates that JS
// would otherwise roll over (e.g. "2026-02-30" rolls to March 2) by
// round-tripping through toISOString and comparing back to the input.
function asCalendarDay(value: unknown): string {
  const text = asNonEmptyString(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    throw new Error(MALFORMED);
  }
  const parsed = new Date(`${text}T00:00:00Z`);
  if (
    Number.isNaN(parsed.getTime()) ||
    parsed.toISOString().slice(0, 10) !== text
  ) {
    throw new Error(MALFORMED);
  }
  return text;
}

function asTimestamp(value: unknown): string {
  const text = asNonEmptyString(value);
  const dateMatch = ISO_TIMESTAMP_DATE_PATTERN.exec(text);
  if (
    !dateMatch ||
    Number.isNaN(new Date(text).getTime()) ||
    !TIMEZONE_OFFSET_PATTERN.test(text)
  ) {
    throw new Error(MALFORMED);
  }
  // Reuse the same calendar-day validation the bucket-`day` parser uses so a
  // rollover date (e.g. "2026-02-30") is rejected here too, instead of being
  // silently normalized by `new Date`.
  asCalendarDay(dateMatch[1]);
  return text;
}

function requirePresent(record: Record<string, unknown>, key: string): void {
  if (!(key in record)) {
    throw new Error(MALFORMED);
  }
}

function asNullable<T>(value: unknown, parse: (value: unknown) => T): T | null {
  return value === null || value === undefined ? null : parse(value);
}

function asCount(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new Error(MALFORMED);
  }
  return value;
}

// The single snake_case → camelCase boundary for suspension events.
export function parseSuspensionEvent(raw: unknown): SuspensionEvent {
  const record = asRecord(raw);
  // These keys are nullable in the contract, but a MISSING key is not the
  // same as an explicit `null` — require presence before treating an absent
  // value as the same as null.
  requirePresent(record, "observed_started_clusters");
  requirePresent(record, "observed_resumed_on");
  return {
    id: asNonEmptyString(record.id),
    createdAt: asTimestamp(record.created_at),
    warehouseName: asNonEmptyString(record.warehouse_name),
    action: asNonEmptyString(record.action),
    reason: asNonEmptyString(record.reason),
    observedStartedClusters: asNullable(
      record.observed_started_clusters,
      asCount,
    ),
    observedResumedOn: asNullable(record.observed_resumed_on, asTimestamp),
    observedAt: asTimestamp(record.observed_at),
  };
}

function asCountsRecord(value: unknown): Record<string, number> {
  const record = asRecord(value);
  const counts: Record<string, number> = {};
  for (const [key, count] of Object.entries(record)) {
    counts[key] = asCount(count);
  }
  return counts;
}

export function parseSuspensionStatsBucket(raw: unknown): SuspensionStatsBucket {
  const record = asRecord(raw);
  return {
    day: asCalendarDay(record.day),
    counts: asCountsRecord(record.counts),
  };
}

export async function fetchSuspensionStats(
  orgId: string,
  days: number,
  options: AutomatedSavingsApiOptions = {},
): Promise<SuspensionStatsResponse> {
  const payload = await fetchJson(
    `/api/automated-savings/${orgId}/stats/suspensions?days=${days}`,
    {},
    options,
  );
  const record = asRecord(payload);
  if (typeof record.days !== "number") {
    throw new Error(MALFORMED);
  }
  if (!Array.isArray(record.warehouses)) {
    throw new Error(MALFORMED);
  }
  if (!Array.isArray(record.buckets)) {
    throw new Error(MALFORMED);
  }
  return {
    days: record.days,
    warehouses: record.warehouses.map(asNonEmptyString),
    buckets: record.buckets.map(parseSuspensionStatsBucket),
  };
}

export async function fetchSuspensionEvents(
  orgId: string,
  cursor: string | null = null,
  options: AutomatedSavingsApiOptions = {},
): Promise<SuspensionEventsPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  const payload = await fetchJson(
    `/api/automated-savings/${orgId}/events${query}`,
    {},
    options,
  );
  const record = asRecord(payload);
  if (!Array.isArray(record.events)) {
    throw new Error(MALFORMED);
  }
  return {
    events: record.events.map(parseSuspensionEvent),
    nextCursor: asNullable(record.next_cursor, asNonEmptyString),
  };
}
