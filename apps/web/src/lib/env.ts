export function getPublicApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL?.trim() ?? "";
}

export default getPublicApiBaseUrl;
