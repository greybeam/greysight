// Per-browser persistence of the dashboard's active organization. Kept in
// localStorage only (no backend column); SSR-safe via a typeof window guard.
const STORAGE_KEY = "greysight.activeOrganizationId";

export function readActiveOrganizationId(): string | null {
  if (typeof window === "undefined") return null;
  const value = window.localStorage.getItem(STORAGE_KEY);
  return value && value.length > 0 ? value : null;
}

export function writeActiveOrganizationId(id: string | null): void {
  if (typeof window === "undefined") return;
  if (id && id.length > 0) {
    window.localStorage.setItem(STORAGE_KEY, id);
  } else {
    window.localStorage.removeItem(STORAGE_KEY);
  }
}
