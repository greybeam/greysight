import resolveApiUrl from "./api-client";
import parseDashboardDatasets, {
  parseAIDetailViewModel,
  parseDashboardRun,
  parseDashboardView,
  type AIDetailViewModel,
  type DashboardData,
  type DashboardRun,
  type DashboardView,
} from "./dashboard-contracts";

type DashboardApiOptions = {
  accessToken?: string | null;
};

type PollOptions = DashboardApiOptions & {
  intervalMs?: number;
  maxAttempts?: number;
};

type StartDashboardRunInput = {
  organizationId: string;
  windowDays: number;
};

export type DashboardViewRangeRequest =
  | { windowDays?: number; startDate?: never; endDate?: never }
  | { windowDays?: never; startDate: string; endDate: string };

export async function fetchDemoDashboardDatasets(): Promise<DashboardData> {
  return fetchDashboardDataPath("/api/dashboard-runs/demo/datasets");
}

export async function fetchDemoDashboardView(
  range: DashboardViewRangeRequest = { windowDays: 30 },
): Promise<DashboardView> {
  return fetchDashboardViewPath("/api/dashboard-runs/demo/view", range);
}

// Reserved for Snowflake runs; Phase 3 is demo-backed by default.
export async function fetchDashboardDatasets(
  runId: string,
  options: DashboardApiOptions = {},
): Promise<DashboardData> {
  return fetchDashboardDataPath(
    `/api/dashboard-runs/${runId}/datasets`,
    options,
  );
}

export async function fetchDashboardView(
  runId: string,
  range: DashboardViewRangeRequest = { windowDays: 30 },
  options: DashboardApiOptions = {},
): Promise<DashboardView> {
  return fetchDashboardViewPath(
    `/api/dashboard-runs/${runId}/view`,
    range,
    options,
  );
}

export async function fetchDashboardRun(
  runId: string,
  options: DashboardApiOptions = {},
): Promise<DashboardRun> {
  const payload = await fetchJson(`/api/dashboard-runs/${runId}`, {}, options);
  return parseDashboardRun(payload);
}

export async function startDashboardRun(
  input: StartDashboardRunInput,
  options: DashboardApiOptions = {},
): Promise<DashboardRun> {
  const payload = await fetchJson(
    "/api/dashboard-runs",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        organization_id: input.organizationId,
        source: "snowflake",
        window_days: input.windowDays,
      }),
    },
    options,
  );
  return parseDashboardRun(payload);
}

export type PollUntilOptions<T> = {
  intervalMs?: number;
  maxAttempts?: number;
  onResult?: (result: T) => void;
};

export async function pollUntilTerminal<T>(
  fetcher: () => Promise<T>,
  isTerminal: (result: T) => boolean,
  { intervalMs = 1_500, maxAttempts = 60, onResult }: PollUntilOptions<T> = {},
): Promise<T> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const result = await fetcher();
    onResult?.(result);
    if (isTerminal(result)) {
      return result;
    }
    if (intervalMs > 0) await delay(intervalMs);
  }
  throw new Error("Polling timed out before reaching a terminal status");
}

export async function pollDashboardRun(
  runId: string,
  { intervalMs = 2_000, maxAttempts = 30, accessToken }: PollOptions = {},
): Promise<DashboardRun> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const run = await fetchDashboardRun(runId, { accessToken });
    if (
      run.status === "completed" ||
      run.status === "failed" ||
      run.status === "expired" ||
      run.status === "deleted"
    ) {
      return run;
    }

    if (intervalMs > 0) await delay(intervalMs);
  }

  throw new Error("Dashboard run polling timed out");
}

export type DashboardSourceStatus =
  | "idle"
  | "pending"
  | "completed"
  | "failed"
  | "expired";

export type DashboardSourceResult = {
  status: DashboardSourceStatus;
  view: AIDetailViewModel | null;
};

export async function triggerDashboardSource(
  runId: string,
  sourceId: string,
  options: DashboardApiOptions = {},
): Promise<void> {
  await fetchJson(
    `/api/dashboard-runs/${runId}/sources/${sourceId}`,
    { method: "POST" },
    options,
  );
}

export async function fetchDashboardSource(
  runId: string,
  sourceId: string,
  range: DashboardViewRangeRequest = { windowDays: 30 },
  options: DashboardApiOptions = {},
): Promise<DashboardSourceResult> {
  const params = new URLSearchParams();
  if ("windowDays" in range && range.windowDays !== undefined) {
    params.set("window_days", String(range.windowDays));
  }
  if ("startDate" in range && range.startDate !== undefined && range.endDate !== undefined) {
    params.set("start_date", range.startDate);
    params.set("end_date", range.endDate);
  }
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  const payload = await fetchJson(
    `/api/dashboard-runs/${runId}/sources/${sourceId}${suffix}`,
    {},
    options,
  );
  const status = (payload as { status: DashboardSourceStatus }).status;
  const rawView = (payload as { view?: unknown }).view;
  return {
    status,
    view: status === "completed" && rawView != null ? parseAIDetailViewModel(rawView) : null,
  };
}

// Headroom: the heavy AI source query can take ~1 min; poll well past the existing 30x2s cap.
export async function pollDashboardSource(
  runId: string,
  sourceId: string,
  range: DashboardViewRangeRequest,
  { intervalMs = 3_000, maxAttempts = 40, accessToken }: PollOptions = {},
): Promise<DashboardSourceResult> {
  let last: DashboardSourceResult = { status: "pending", view: null };
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    last = await fetchDashboardSource(runId, sourceId, range, { accessToken });
    if (
      last.status === "completed" ||
      last.status === "failed" ||
      last.status === "expired"
    ) {
      return last;
    }
    if (intervalMs > 0) await delay(intervalMs);
  }
  return last;
}

export async function fetchDemoDashboardSource(
  sourceId: string,
  range: DashboardViewRangeRequest = { windowDays: 30 },
): Promise<DashboardSourceResult> {
  return fetchDashboardSource("demo", sourceId, range);
}

async function fetchDashboardDataPath(
  path: string,
  options: DashboardApiOptions = {},
): Promise<DashboardData> {
  const payload = await fetchJson(path, {}, options);
  return parseDashboardDatasets(payload);
}

async function fetchDashboardViewPath(
  path: string,
  range: DashboardViewRangeRequest,
  options: DashboardApiOptions = {},
): Promise<DashboardView> {
  const params = new URLSearchParams();

  if (range.windowDays !== undefined) {
    params.set("window_days", String(range.windowDays));
  }
  if (range.startDate !== undefined && range.endDate !== undefined) {
    params.set("start_date", range.startDate);
    params.set("end_date", range.endDate);
  }

  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  const payload = await fetchJson(`${path}${suffix}`, {}, options);
  return parseDashboardView(payload);
}

async function fetchJson(
  path: string,
  init: RequestInit = {},
  options: DashboardApiOptions = {},
): Promise<unknown> {
  const headers = new Headers(init.headers);
  const accessToken = options.accessToken?.trim();

  if (accessToken) {
    headers.set("authorization", `Bearer ${accessToken}`);
  }

  const response = await fetch(resolveApiUrl(path), {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Dashboard API request failed with ${response.status}`);
  }

  return response.json();
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}
