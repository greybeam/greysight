// Local-dev mock for the Auto Savings API. Enabled with
// NEXT_PUBLIC_AUTO_SAVINGS_MOCK=true so the page can be developed without
// Supabase or a live org. Responses use the API's snake_case wire shape so the
// real parsers in automated-savings-api.ts still run on them.

export const MOCK_ORG_ID = "mock-org";

export function isAutoSavingsMockEnabled(
  // Must reference the static NEXT_PUBLIC_ token so Next inlines it client-side.
  value: string | undefined = process.env.NEXT_PUBLIC_AUTO_SAVINGS_MOCK,
): boolean {
  return value?.trim().toLowerCase() === "true";
}

const EVENTS_PAGE_SIZE = 25;
const PRICE_PER_CREDIT = 3;
const CREDITS_PER_HOUR: Record<string, number> = {
  "X-Small": 1,
  Small: 2,
  Medium: 4,
  Large: 8,
};

type MockWarehouse = {
  name: string;
  size: string;
  enabled: boolean;
  // Share of daily suspensions and typical idle seconds avoided per suspension.
  weight: number;
  avoidedSeconds: number;
};

const ENROLLED: MockWarehouse[] = [
  { name: "WH_PROD_CONSUMER", size: "Medium", enabled: true, weight: 0.86, avoidedSeconds: 235 },
  { name: "WH_PROD_TRANSFORM", size: "Large", enabled: true, weight: 0.09, avoidedSeconds: 180 },
  { name: "WH_PROD_BI", size: "Small", enabled: true, weight: 0.05, avoidedSeconds: 290 },
];

const OTHER_WAREHOUSES = [
  "WH_DEV", "WH_DEV_DBT", "WH_STAGING", "WH_STAGING_DBT", "WH_ADHOC",
  "WH_LOADING", "WH_FIVETRAN", "WH_AIRBYTE", "WH_LOOKER", "WH_TABLEAU",
  "WH_HEX", "WH_DS_NOTEBOOKS", "WH_ML_TRAINING", "WH_ML_INFERENCE",
  "WH_REVERSE_ETL", "WH_CENSUS", "WH_MONITORING", "WH_SNOWPIPE",
  "WH_FINANCE", "WH_MARKETING", "WH_SALES_OPS", "WH_PRODUCT_ANALYTICS",
  "WH_EXPORTS", "WH_BACKFILL", "WH_QA", "WH_SANDBOX_1", "WH_SANDBOX_2",
  "WH_SANDBOX_3", "WH_CI", "WH_STREAMLIT", "WH_ALERTS", "WH_TASKS",
  "WH_AUDIT", "WH_SECURITY", "WH_PARTNER_SHARE", "WH_LEGACY_ETL",
  "WH_ARCHIVE", "COMPUTE_WH",
];

// Deterministic PRNG so the mock data is stable across reloads.
function rng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const state = {
  agreed: true,
  globalEnabled: true,
  enabled: new Map<string, boolean>([
    ...ENROLLED.map((w) => [w.name, w.enabled] as const),
    ...OTHER_WAREHOUSES.map((name) => [name, false] as const),
  ]),
};

function warehouseRows(): unknown[] {
  const sizes = ["X-Small", "Small", "Medium", "Large"];
  const enrolled = ENROLLED.map((w) => ({ name: w.name, size: w.size }));
  const others = OTHER_WAREHOUSES.map((name, i) => ({
    name,
    size: sizes[i % sizes.length],
  }));
  return [...enrolled, ...others].map(({ name, size }, i) => ({
    name,
    size,
    state: i % 3 === 0 ? "STARTED" : "SUSPENDED",
    type: "STANDARD",
    supported: true,
    min_cluster_count: 1,
    max_cluster_count: name === "WH_PROD_CONSUMER" ? 3 : 1,
    started_clusters: i % 3 === 0 ? 1 : 0,
    auto_resume_ok: true,
    auto_suspend: 300,
    quiescing: 0,
    enabled: state.enabled.get(name) ?? false,
    status: "idle",
  }));
}

function enabledEnrolled(): MockWarehouse[] {
  return ENROLLED.filter((w) => state.enabled.get(w.name));
}

function lastDays(days: number): string[] {
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);
  return Array.from({ length: days }, (_, i) => {
    const day = new Date(today);
    day.setUTCDate(today.getUTCDate() - (days - 1 - i));
    return day.toISOString().slice(0, 10);
  });
}

// Daily suspension totals: busier on weekdays, quieter on weekends.
function dailyTotal(day: string): number {
  const random = rng(Number(day.replaceAll("-", "")));
  const weekday = new Date(`${day}T00:00:00Z`).getUTCDay();
  const base = weekday === 0 || weekday === 6 ? 380 : 680;
  return Math.round(base + (random() - 0.5) * 80);
}

function countsFor(day: string): Record<string, number> {
  const total = dailyTotal(day);
  const counts: Record<string, number> = {};
  for (const w of enabledEnrolled()) {
    counts[w.name] = Math.round(total * w.weight);
  }
  return counts;
}

function suspensionStats(days: number): unknown {
  return {
    days,
    warehouses: enabledEnrolled().map((w) => w.name),
    buckets: lastDays(days).map((day) => ({ day, counts: countsFor(day) })),
  };
}

function savingsStats(days: number): unknown {
  let totalDollars = 0;
  let totalHours = 0;
  let totalSuspensions = 0;
  const buckets = lastDays(days).map((day) => {
    const counts = countsFor(day);
    const dollars: Record<string, number> = {};
    for (const w of enabledEnrolled()) {
      const count = counts[w.name] ?? 0;
      const hours = (count * w.avoidedSeconds) / 3600;
      const value = hours * (CREDITS_PER_HOUR[w.size] ?? 1) * PRICE_PER_CREDIT;
      dollars[w.name] = Math.round(value * 100) / 100;
      totalDollars += value;
      totalHours += hours;
      totalSuspensions += count;
    }
    return { day, dollars };
  });
  return {
    days,
    price_per_credit: PRICE_PER_CREDIT,
    total_dollars: Math.round(totalDollars * 100) / 100,
    total_avoided_hours: Math.round(totalHours * 10) / 10,
    total_suspensions: totalSuspensions,
    assumed_settings_count: 0,
    warehouses: enabledEnrolled().map((w) => w.name),
    buckets,
  };
}

// Events mirror the real feed: WH_PROD_CONSUMER suspended every ~1–3 minutes,
// ~62–76s after resume, with 1–3 running clusters. Newest first.
function suspensionEvents(cursor: string | null): unknown {
  const offset = cursor ? Number(cursor) : 0;
  const random = rng(42);
  let observed = Date.now();
  const events: unknown[] = [];
  for (let i = 0; i < offset + EVENTS_PAGE_SIZE; i += 1) {
    const uptime = 62 + Math.floor(random() * (random() < 0.85 ? 4 : 15));
    const clusters = 1 + Math.floor(random() * random() * 3);
    const gap = 65 + Math.floor(random() * 140);
    const observedAt = new Date(observed);
    const resumedOn = new Date(observed - uptime * 1000);
    if (i >= offset) {
      events.push({
        id: `mock-event-${i}`,
        created_at: observedAt.toISOString(),
        warehouse_name: "WH_PROD_CONSUMER",
        action: "suspend",
        reason: "idle",
        observed_started_clusters: clusters,
        observed_resumed_on: resumedOn.toISOString(),
        observed_at: observedAt.toISOString(),
      });
    }
    observed -= (uptime + gap) * 1000;
  }
  const nextOffset = offset + EVENTS_PAGE_SIZE;
  return { events, next_cursor: nextOffset < 500 ? String(nextOffset) : null };
}

function statusPayload(): unknown {
  return {
    agreed: state.agreed,
    global_enabled: state.globalEnabled,
    grant_present: true,
    grant_checked_at: new Date().toISOString(),
    role_name: "GREYSIGHT_ROLE",
  };
}

function readBody(init: RequestInit): Record<string, unknown> {
  if (typeof init.body !== "string") return {};
  return JSON.parse(init.body) as Record<string, unknown>;
}

// Serves `/api/automated-savings/{org}/...` paths with mock payloads.
export async function mockAutoSavingsResponse(
  path: string,
  init: RequestInit = {},
): Promise<unknown> {
  const url = new URL(path, "http://mock.local");
  const route = url.pathname.replace(/^\/api\/automated-savings\/[^/]+/, "");
  const days = Number(url.searchParams.get("days") ?? 7);

  if (route === "/status") return statusPayload();
  if (route === "/check-access") return statusPayload();
  if (route === "/warehouses") return warehouseRows();
  if (route === "/stats/suspensions") return suspensionStats(days);
  if (route === "/stats/savings") return savingsStats(days);
  if (route === "/events") {
    return suspensionEvents(url.searchParams.get("cursor"));
  }
  if (route === "/agree") {
    state.agreed = true;
    return {};
  }
  if (route === "/global-switch") {
    state.globalEnabled = readBody(init).enabled === true;
    return {};
  }
  const toggle = /^\/warehouses\/([^/]+)\/toggle$/.exec(route);
  if (toggle) {
    state.enabled.set(
      decodeURIComponent(toggle[1]),
      readBody(init).enabled === true,
    );
    return {};
  }
  throw new Error(`No Auto Savings mock for ${path}`);
}
