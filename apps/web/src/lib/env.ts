export function getPublicApiBaseUrl(): string {
  const configuredApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (configuredApiBaseUrl) {
    return configuredApiBaseUrl;
  }
  if (process.env.NODE_ENV === "development") {
    return "http://localhost:8000";
  }
  return "";
}

export default getPublicApiBaseUrl;
