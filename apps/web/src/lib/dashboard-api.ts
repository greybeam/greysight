import resolveApiUrl from "./api-client";
import parseDashboardDatasets, {
  parseDashboardRun,
  type DashboardData,
  type DashboardRun,
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

export async function fetchDemoDashboardDatasets(): Promise<DashboardData> {
  return fetchDashboardDataPath("/api/dashboard-runs/demo/datasets");
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

async function fetchDashboardDataPath(
  path: string,
  options: DashboardApiOptions = {},
): Promise<DashboardData> {
  const payload = await fetchJson(path, {}, options);
  return parseDashboardDatasets(payload);
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
