import resolveApiUrl, { authHeaders } from "./api-client";

// Per-org caching preferences. `cache_ttl_seconds` is the cached-run lifetime;
// the backend clamps it to [3600, 604800] and rejects out-of-range values (422).
export type CacheSettings = {
  cache_enabled: boolean;
  cache_ttl_seconds: number;
};

export type CacheSettingsPatch = {
  cache_enabled?: boolean;
  cache_ttl_seconds?: number;
};

type CacheSettingsOptions = {
  accessToken?: string | null;
};

export class CacheSettingsValidationError extends Error {}
export class CacheSettingsForbiddenError extends Error {}

function parseCacheSettings(payload: unknown): CacheSettings {
  if (typeof payload !== "object" || payload === null) {
    throw new Error("Malformed cache-settings response");
  }
  const record = payload as {
    cache_enabled?: unknown;
    cache_ttl_seconds?: unknown;
  };
  if (
    typeof record.cache_enabled !== "boolean" ||
    typeof record.cache_ttl_seconds !== "number" ||
    !Number.isFinite(record.cache_ttl_seconds)
  ) {
    throw new Error("Malformed cache-settings response");
  }
  return {
    cache_enabled: record.cache_enabled,
    cache_ttl_seconds: record.cache_ttl_seconds,
  };
}

export async function fetchCacheSettings(
  organizationId: string,
  options: CacheSettingsOptions = {},
): Promise<CacheSettings> {
  const response = await fetch(
    resolveApiUrl(
      `/api/organizations/${encodeURIComponent(organizationId)}/cache-settings`,
    ),
    {
      headers: authHeaders(options.accessToken),
      cache: "no-store",
    },
  );
  if (!response.ok) {
    throw new Error(`Cache-settings lookup failed with ${response.status}`);
  }
  return parseCacheSettings(await response.json());
}

export async function updateCacheSettings(
  organizationId: string,
  patch: CacheSettingsPatch,
  options: CacheSettingsOptions = {},
): Promise<CacheSettings> {
  const response = await fetch(
    resolveApiUrl(
      `/api/organizations/${encodeURIComponent(organizationId)}/cache-settings`,
    ),
    {
      method: "PATCH",
      headers: authHeaders(options.accessToken, {
        "content-type": "application/json",
      }),
      cache: "no-store",
      body: JSON.stringify(patch),
    },
  );

  if (response.status === 200) {
    return parseCacheSettings(await response.json());
  }

  const detail = await safeDetail(response);
  if (response.status === 422) throw new CacheSettingsValidationError(detail);
  if (response.status === 403) throw new CacheSettingsForbiddenError(detail);
  throw new Error(detail || `Cache-settings update failed with ${response.status}`);
}

async function safeDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : "";
  } catch {
    return "";
  }
}
