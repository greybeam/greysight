"use client";

import { useEffect, useRef, useState } from "react";

import { useAccountChrome } from "../../lib/account-context";
import {
  CacheSettingsForbiddenError,
  CacheSettingsValidationError,
  fetchCacheSettings,
  updateCacheSettings,
} from "../../lib/cache-settings-api";

const GENERIC_ERROR = "Something went wrong. Please try again.";
const SAVED_MESSAGE = "Cache settings saved.";
const DEFAULT_TTL_SECONDS = 86_400;

// TTL presets shown in the selector. Values are seconds and stay inside the
// backend-accepted [3600, 604800] range.
const TTL_PRESETS: ReadonlyArray<{ label: string; value: number }> = [
  { label: "1 hour", value: 3_600 },
  { label: "6 hours", value: 21_600 },
  { label: "12 hours", value: 43_200 },
  { label: "24 hours", value: 86_400 },
];

type CacheSettingsProps = {
  organizationId?: string;
  triggerClassName?: string;
  triggerRole?: "button" | "menuitem";
};

export default function CacheSettings({
  organizationId,
  triggerClassName = "flex h-9 w-9 items-center justify-center rounded-md border border-hairline text-slate-300 hover:bg-white/5",
  triggerRole = "button",
}: CacheSettingsProps = {}) {
  const account = useAccountChrome();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const [cacheEnabled, setCacheEnabled] = useState(true);
  const [ttlSeconds, setTtlSeconds] = useState(DEFAULT_TTL_SECONDS);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  const active =
    account?.organizations.find((o) => o.id === organizationId) ??
    account?.organizations.find((o) => o.id === account.activeOrganizationId) ??
    account?.organizations[0];
  const isAdmin =
    active != null && (active.role === "owner" || active.role === "admin");

  // Load the current settings each time the surface opens so the controls
  // reflect the persisted state rather than stale local defaults.
  useEffect(() => {
    if (!open || !active) return;
    let isActive = true;
    // Reset the surface to its loading state before the async fetch resolves so
    // stale settings/messages are never shown on reopen. This is derived-state
    // synchronization with the settings API, not a cascading render loop.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    setSuccess(null);
    void (async () => {
      try {
        const settings = await fetchCacheSettings(active.id, {
          accessToken: account?.accessToken ?? null,
        });
        if (!isActive) return;
        setCacheEnabled(settings.cache_enabled);
        setTtlSeconds(settings.cache_ttl_seconds);
      } catch {
        if (isActive) setError(GENERIC_ERROR);
      } finally {
        if (isActive) setLoading(false);
      }
    })();
    return () => {
      isActive = false;
    };
  }, [open, active, account?.accessToken]);

  if (!account || !active || !isAdmin) {
    return null;
  }

  const heading = active.accountLocator
    ? `Cache settings for ${active.name} (${active.accountLocator})`
    : `Cache settings for ${active.name}`;

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    setError(null);
    setSuccess(null);
    setPending(true);
    try {
      const updated = await updateCacheSettings(
        active.id,
        { cache_enabled: cacheEnabled, cache_ttl_seconds: ttlSeconds },
        { accessToken: account?.accessToken ?? null },
      );
      setCacheEnabled(updated.cache_enabled);
      setTtlSeconds(updated.cache_ttl_seconds);
      setSuccess(SAVED_MESSAGE);
    } catch (err: unknown) {
      if (
        err instanceof CacheSettingsValidationError ||
        err instanceof CacheSettingsForbiddenError
      ) {
        setError(err.message || GENERIC_ERROR);
      } else {
        setError(GENERIC_ERROR);
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        role={triggerRole}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Cache settings for ${active.name}`}
        className={triggerClassName}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-5 w-5"
        >
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label={heading}
          className="absolute right-0 z-50 mt-2 w-80 rounded-md border border-hairline bg-surface p-3 shadow-lg"
        >
          <p className="mb-3 text-sm font-medium text-slate-200">{heading}</p>
          <form className="flex flex-col gap-3" onSubmit={submit}>
            <label className="flex items-center justify-between gap-3 text-sm text-slate-200">
              <span>Enable caching</span>
              <input
                type="checkbox"
                aria-label="Enable caching"
                disabled={pending || loading}
                checked={cacheEnabled}
                onChange={(e) => setCacheEnabled(e.target.checked)}
                className="h-4 w-4 accent-chart-purple"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-slate-200">
              <span>Cache lifetime</span>
              <select
                aria-label="Cache lifetime"
                disabled={pending || loading || !cacheEnabled}
                value={ttlSeconds}
                onChange={(e) => setTtlSeconds(Number(e.target.value))}
                className="rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-slate-100 focus:border-chart-purple focus:outline-none focus:ring-1 focus:ring-chart-purple disabled:opacity-60"
              >
                {TTL_PRESETS.map((preset) => (
                  <option key={preset.value} value={preset.value}>
                    {preset.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              disabled={pending || loading}
              className="rounded-md bg-chart-purple px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            >
              {pending ? "Saving" : "Save"}
            </button>
          </form>
          {error ? (
            <p className="mt-2 text-sm font-medium text-red-400" role="alert">
              {error}
            </p>
          ) : null}
          {success ? (
            <p className="mt-2 text-sm font-medium text-emerald-400" role="status">
              {success}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
