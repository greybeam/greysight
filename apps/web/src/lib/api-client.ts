import getPublicApiBaseUrl from "./env";

function normalizeApiPath(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

export function resolveApiUrl(path: string, apiBaseUrl = getPublicApiBaseUrl()): string {
  const normalizedPath = normalizeApiPath(path);
  const baseUrl = apiBaseUrl.trim();

  if (baseUrl === "") {
    return normalizedPath;
  }

  return `${baseUrl.replace(/\/+$/, "")}${normalizedPath}`;
}

export function authHeaders(
  accessToken: string | null | undefined,
  base: Record<string, string> = {},
): Headers {
  const headers = new Headers(base);
  const token = accessToken?.trim();
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }
  return headers;
}

export default resolveApiUrl;
