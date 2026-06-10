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

export default resolveApiUrl;
