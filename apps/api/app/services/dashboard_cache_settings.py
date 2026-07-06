from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

import httpx

# Contract defaults. When an org has no settings row, cache is ON with a 24h
# TTL. TTL is clamped to [1h, 7d] at the API boundary; the DB enforces the same
# range via a CHECK constraint.
DEFAULT_CACHE_ENABLED = True
DEFAULT_CACHE_TTL_SECONDS = 86_400
MIN_CACHE_TTL_SECONDS = 3_600
MAX_CACHE_TTL_SECONDS = 604_800


@dataclass(frozen=True)
class CacheSettings:
    cache_enabled: bool
    cache_ttl_seconds: int


DEFAULT_CACHE_SETTINGS = CacheSettings(
    cache_enabled=DEFAULT_CACHE_ENABLED,
    cache_ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
)


class CacheSettingsStoreError(RuntimeError):
    """Raised when cache settings cannot be read or written; callers fail loud."""


class CacheSettingsStore(Protocol):
    """Read + upsert an org's dashboard cache settings row."""

    def get(self, organization_id: str) -> CacheSettings | None:
        """Return the stored settings, or None when no row exists."""
        ...

    def upsert(
        self,
        organization_id: str,
        *,
        cache_enabled: bool | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> CacheSettings:
        """Insert or update the org's settings and return the full row.

        A field left as None is not changed on an existing row and falls back to
        the default on an insert.
        """
        ...


def read_cache_settings(
    organization_id: str, store: CacheSettingsStore | None
) -> CacheSettings:
    """Read settings, returning contract defaults when no row (or no store)."""
    if store is None:
        return DEFAULT_CACHE_SETTINGS
    settings = store.get(organization_id)
    return settings if settings is not None else DEFAULT_CACHE_SETTINGS


class InMemoryCacheSettingsStore:
    """Hermetic fake for tests — no network, no Supabase."""

    def __init__(self) -> None:
        self._rows: dict[str, CacheSettings] = {}

    def clear(self) -> None:
        self._rows.clear()

    def get(self, organization_id: str) -> CacheSettings | None:
        return self._rows.get(organization_id)

    def upsert(
        self,
        organization_id: str,
        *,
        cache_enabled: bool | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> CacheSettings:
        current = self._rows.get(organization_id, DEFAULT_CACHE_SETTINGS)
        updated = current
        if cache_enabled is not None:
            updated = replace(updated, cache_enabled=cache_enabled)
        if cache_ttl_seconds is not None:
            updated = replace(updated, cache_ttl_seconds=cache_ttl_seconds)
        self._rows[organization_id] = updated
        return updated


class SupabaseCacheSettingsStore:
    """Reads/writes organization_dashboard_cache_settings via the service role."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = supabase_url.rstrip("/")
        self._table_url = f"{base}/rest/v1/organization_dashboard_cache_settings"
        self._service_role_key = service_role_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _headers(self, *, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._service_role_key,
            "authorization": f"Bearer {self._service_role_key}",
            "content-type": "application/json",
        }
        if prefer is not None:
            headers["prefer"] = prefer
        return headers

    def get(self, organization_id: str) -> CacheSettings | None:
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.get(
                    self._table_url,
                    params={
                        "organization_id": f"eq.{organization_id}",
                        "select": "cache_enabled,cache_ttl_seconds",
                        "limit": "1",
                    },
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise CacheSettingsStoreError() from exc
        if response.status_code != 200:
            raise CacheSettingsStoreError()
        try:
            rows = response.json()
        except ValueError as exc:
            raise CacheSettingsStoreError() from exc
        if not isinstance(rows, list):
            raise CacheSettingsStoreError()
        if not rows:
            return None
        return _parse_settings(rows[0])

    def upsert(
        self,
        organization_id: str,
        *,
        cache_enabled: bool | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> CacheSettings:
        # Single atomic upsert: send only the provided columns. PostgREST's
        # merge-duplicates builds INSERT ... ON CONFLICT DO UPDATE SET over just
        # the payload columns, so an existing row's untouched column is preserved
        # at the DB (no read-modify-write, no lost concurrent update). On a fresh
        # insert, omitted columns take the table defaults, which match
        # DEFAULT_CACHE_SETTINGS.
        payload: dict[str, object] = {"organization_id": organization_id}
        if cache_enabled is not None:
            payload["cache_enabled"] = cache_enabled
        if cache_ttl_seconds is not None:
            payload["cache_ttl_seconds"] = cache_ttl_seconds
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.post(
                    self._table_url,
                    params={"on_conflict": "organization_id"},
                    json=payload,
                    headers=self._headers(
                        prefer="resolution=merge-duplicates,return=representation"
                    ),
                )
        except httpx.HTTPError as exc:
            raise CacheSettingsStoreError() from exc
        if response.status_code not in (200, 201):
            raise CacheSettingsStoreError()
        try:
            rows = response.json()
        except ValueError as exc:
            raise CacheSettingsStoreError() from exc
        if not isinstance(rows, list) or len(rows) != 1:
            raise CacheSettingsStoreError()
        return _parse_settings(rows[0])


def _parse_settings(row: object) -> CacheSettings:
    if not isinstance(row, dict):
        raise CacheSettingsStoreError()
    enabled = row.get("cache_enabled")
    ttl = row.get("cache_ttl_seconds")
    if not isinstance(enabled, bool) or not isinstance(ttl, int):
        raise CacheSettingsStoreError()
    return CacheSettings(cache_enabled=enabled, cache_ttl_seconds=ttl)


_store: CacheSettingsStore | None = None


def configure_cache_settings_store(store: CacheSettingsStore | None) -> None:
    global _store
    _store = store


def get_cache_settings_store() -> CacheSettingsStore | None:
    return _store
