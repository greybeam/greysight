from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class CachedDashboardRun:
    """A completed run snapshot, cached per org for reload-free re-derivation."""

    organization_id: str
    run_id: str
    source: str
    window_days: int
    summary: dict[str, Any]
    datasets: dict[str, list[dict[str, Any]]]
    source_start_date: date
    source_end_date: date
    completed_at: datetime
    expires_at: datetime
    metadata: dict[str, Any] | None = field(default=None)
    # Snowflake account fingerprint captured at persist time. A /cached read that
    # sees a differing current org account_locator (including None/disconnected)
    # treats this row as a miss so a stale connection's data is never served.
    account_locator: str | None = field(default=None)


class RunCacheStoreError(RuntimeError):
    """Raised when the run cache cannot be read; callers fail loud on reads."""


class RunCacheStore(Protocol):
    """Upsert (write) and read the latest non-expired cached run for an org."""

    def upsert(self, cached_run: CachedDashboardRun) -> None:
        """Insert or replace the org's single cached run."""
        ...

    def get_active(
        self, organization_id: str, *, now: datetime | None = None
    ) -> CachedDashboardRun | None:
        """Return the cached run only if it exists and has not expired.

        ``expires_at <= now`` is treated as a miss (returns None).
        """
        ...

    def update_datasets_if_current(
        self, cached_run: CachedDashboardRun, datasets: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Update datasets only if the org cache row still matches cached_run."""
        ...

    def delete_if_current(self, cached_run: CachedDashboardRun) -> None:
        """Delete only if the org cache row still matches cached_run."""
        ...

    def delete(self, organization_id: str) -> None:
        """Remove the org's cached run (no-op if none exists)."""
        ...


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _is_expired(expires_at: datetime, now: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now


class InMemoryRunCacheStore:
    """Hermetic fake for tests — no network, no Supabase."""

    def __init__(self) -> None:
        self._rows: dict[str, CachedDashboardRun] = {}

    def clear(self) -> None:
        self._rows.clear()

    def upsert(self, cached_run: CachedDashboardRun) -> None:
        self._rows[cached_run.organization_id] = cached_run

    def get_active(
        self, organization_id: str, *, now: datetime | None = None
    ) -> CachedDashboardRun | None:
        row = self._rows.get(organization_id)
        if row is None:
            return None
        if _is_expired(row.expires_at, _now(now)):
            return None
        return row

    def update_datasets_if_current(
        self, cached_run: CachedDashboardRun, datasets: dict[str, list[dict[str, Any]]]
    ) -> None:
        current = self._rows.get(cached_run.organization_id)
        if (
            current is None
            or current.run_id != cached_run.run_id
            or current.completed_at != cached_run.completed_at
        ):
            return
        self._rows[cached_run.organization_id] = replace(current, datasets=datasets)

    def delete(self, organization_id: str) -> None:
        self._rows.pop(organization_id, None)

    def delete_if_current(self, cached_run: CachedDashboardRun) -> None:
        current = self._rows.get(cached_run.organization_id)
        if (
            current is None
            or current.run_id != cached_run.run_id
            or current.completed_at != cached_run.completed_at
        ):
            return
        self._rows.pop(cached_run.organization_id, None)


class SupabaseRunCacheStore:
    """Writes/reads dashboard_run_cache via the service role (sole writer)."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = supabase_url.rstrip("/")
        self._table_url = f"{base}/rest/v1/dashboard_run_cache"
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

    def upsert(self, cached_run: CachedDashboardRun) -> None:
        payload = {
            "organization_id": cached_run.organization_id,
            "run_id": cached_run.run_id,
            "source": cached_run.source,
            "window_days": cached_run.window_days,
            "account_locator": cached_run.account_locator,
            "summary": _json_ready(cached_run.summary),
            "metadata": _json_ready(cached_run.metadata),
            "datasets": _json_ready(cached_run.datasets),
            "source_start_date": cached_run.source_start_date.isoformat(),
            "source_end_date": cached_run.source_end_date.isoformat(),
            "completed_at": cached_run.completed_at.isoformat(),
            "expires_at": cached_run.expires_at.isoformat(),
        }
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.post(
                    self._table_url,
                    params={"on_conflict": "organization_id"},
                    json=payload,
                    headers=self._headers(
                        prefer="resolution=merge-duplicates,return=minimal"
                    ),
                )
        except httpx.HTTPError as exc:
            raise RunCacheStoreError() from exc
        if response.status_code not in (200, 201, 204):
            raise RunCacheStoreError()

    def get_active(
        self, organization_id: str, *, now: datetime | None = None
    ) -> CachedDashboardRun | None:
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.get(
                    self._table_url,
                    params={
                        "organization_id": f"eq.{organization_id}",
                        "select": (
                            "organization_id,run_id,source,window_days,"
                            "account_locator,summary,"
                            "metadata,datasets,source_start_date,source_end_date,"
                            "completed_at,expires_at"
                        ),
                        "limit": "1",
                    },
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise RunCacheStoreError() from exc
        if response.status_code != 200:
            raise RunCacheStoreError()
        try:
            rows = response.json()
        except ValueError as exc:
            raise RunCacheStoreError() from exc
        if not isinstance(rows, list):
            raise RunCacheStoreError()
        if not rows:
            return None
        cached = _parse_cached_run(rows[0])
        if _is_expired(cached.expires_at, _now(now)):
            return None
        return cached

    def update_datasets_if_current(
        self, cached_run: CachedDashboardRun, datasets: dict[str, list[dict[str, Any]]]
    ) -> None:
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.patch(
                    self._table_url,
                    params={
                        "organization_id": f"eq.{cached_run.organization_id}",
                        "run_id": f"eq.{cached_run.run_id}",
                        "completed_at": f"eq.{cached_run.completed_at.isoformat()}",
                    },
                    json={"datasets": _json_ready(datasets)},
                    headers=self._headers(prefer="return=minimal"),
                )
        except httpx.HTTPError as exc:
            raise RunCacheStoreError() from exc
        if response.status_code not in (200, 204):
            raise RunCacheStoreError()

    def delete(self, organization_id: str) -> None:
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.delete(
                    self._table_url,
                    params={"organization_id": f"eq.{organization_id}"},
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise RunCacheStoreError() from exc
        if response.status_code not in (200, 204):
            raise RunCacheStoreError()

    def delete_if_current(self, cached_run: CachedDashboardRun) -> None:
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.delete(
                    self._table_url,
                    params={
                        "organization_id": f"eq.{cached_run.organization_id}",
                        "run_id": f"eq.{cached_run.run_id}",
                        "completed_at": f"eq.{cached_run.completed_at.isoformat()}",
                    },
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise RunCacheStoreError() from exc
        if response.status_code not in (200, 204):
            raise RunCacheStoreError()


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Cached dashboard numeric value must be finite.")
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _parse_cached_run(row: object) -> CachedDashboardRun:
    if not isinstance(row, dict):
        raise RunCacheStoreError()
    try:
        return CachedDashboardRun(
            organization_id=str(row["organization_id"]),
            run_id=str(row["run_id"]),
            source=str(row["source"]),
            window_days=int(row["window_days"]),
            account_locator=(
                str(row["account_locator"])
                if row.get("account_locator") is not None
                else None
            ),
            summary=dict(row["summary"]),
            datasets=dict(row["datasets"]),
            metadata=(
                dict(row["metadata"]) if row.get("metadata") is not None else None
            ),
            source_start_date=date.fromisoformat(str(row["source_start_date"])),
            source_end_date=date.fromisoformat(str(row["source_end_date"])),
            completed_at=_parse_ts(row["completed_at"]),
            expires_at=_parse_ts(row["expires_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RunCacheStoreError() from exc


def _parse_ts(value: object) -> datetime:
    text = str(value)
    # PostgREST returns "+00:00" or "Z"; normalize the latter for fromisoformat.
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


_store: RunCacheStore | None = None


def configure_run_cache_store(store: RunCacheStore | None) -> None:
    global _store
    _store = store


def get_run_cache_store() -> RunCacheStore | None:
    return _store
