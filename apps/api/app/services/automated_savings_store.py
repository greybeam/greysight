from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

import httpx

from app.services.pooled_requests import send_pooled_request


StoreFailureKind = Literal[
    "transport", "http_status", "invalid_json", "malformed_row", "not_found"
]


class AutomatedSavingsStoreError(RuntimeError):
    """Sanitized store failure metadata safe for structured server logs."""

    def __init__(
        self,
        *,
        kind: StoreFailureKind,
        status_code: int | None = None,
    ) -> None:
        super().__init__("Automated savings store operation failed")
        self.kind = kind
        self.status_code = status_code


def _store_error(response: httpx.Response) -> AutomatedSavingsStoreError:
    """Keep the actionable HTTP status without retaining the response body."""
    return AutomatedSavingsStoreError(
        kind="http_status", status_code=response.status_code
    )


@dataclass(frozen=True)
class SettingsRow:
    organization_id: str
    agreed_at: str | None
    global_enabled: bool
    grant_present: bool
    grant_checked_at: str | None


@dataclass(frozen=True)
class EnrollmentRow:
    organization_id: str
    warehouse_name: str
    enabled: bool
    warehouse_created_on: str
    updated_at: str


@dataclass(frozen=True)
class DailySuspensionsRow:
    day: str
    warehouse_name: str
    suspension_count: int


@dataclass(frozen=True)
class EventRow:
    id: int
    created_at: str
    warehouse_name: str
    action: str
    reason: str
    observed_started_clusters: int | None
    observed_resumed_on: str | None
    observed_at: str


class SupabaseAutomatedSavingsStore:
    """Writes/reads automated_savings_* tables via the service role (sole writer)."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = supabase_url.rstrip("/")
        self._settings_url = f"{base}/rest/v1/automated_savings_settings"
        self._warehouses_url = f"{base}/rest/v1/automated_savings_warehouses"
        self._upsert_enrollment_url = (
            f"{base}/rest/v1/rpc/automated_savings_upsert_enrollment"
        )
        self._disable_enrollment_url = (
            f"{base}/rest/v1/rpc/automated_savings_disable_enrollment"
        )
        self._daily_suspensions_url = (
            f"{base}/rest/v1/rpc/automated_savings_daily_suspensions"
        )
        self._events_page_url = f"{base}/rest/v1/rpc/automated_savings_events_page"
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

    def _send(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        return send_pooled_request(
            method,
            url,
            transport=self._transport,
            timeout_seconds=self._timeout_seconds,
            **kwargs,
        )

    # -- settings -----------------------------------------------------

    def get_settings(self, organization_id: str) -> SettingsRow:
        try:
            response = self._send(
                "GET",
                self._settings_url,
                params={
                    "organization_id": f"eq.{organization_id}",
                    "select": (
                        "organization_id,agreed_at,global_enabled,"
                        "grant_present,grant_checked_at"
                    ),
                    "limit": "1",
                },
                headers=self._headers(),
            )
        except httpx.HTTPError:
            raise AutomatedSavingsStoreError(kind="transport") from None
        if response.status_code != 200:
            raise _store_error(response)
        rows = _parse_rows(response)
        if not rows:
            return SettingsRow(
                organization_id=organization_id,
                agreed_at=None,
                global_enabled=False,
                grant_present=False,
                grant_checked_at=None,
            )
        row = rows[0]
        return SettingsRow(
            organization_id=str(row["organization_id"]),
            agreed_at=row.get("agreed_at"),
            global_enabled=bool(row.get("global_enabled", False)),
            grant_present=bool(row.get("grant_present", False)),
            grant_checked_at=row.get("grant_checked_at"),
        )

    def upsert_agreement(self, organization_id: str, agreed_at: str) -> None:
        self._upsert_settings(organization_id, {"agreed_at": agreed_at})

    def set_global_enabled(self, organization_id: str, enabled: bool) -> None:
        self._upsert_settings(organization_id, {"global_enabled": enabled})

    def set_grant_status(
        self, organization_id: str, present: bool, checked_at: str
    ) -> None:
        self._upsert_settings(
            organization_id,
            {"grant_present": present, "grant_checked_at": checked_at},
        )

    def _upsert_settings(self, organization_id: str, fields: dict[str, Any]) -> None:
        payload = {"organization_id": organization_id, **fields}
        try:
            response = self._send(
                "POST",
                self._settings_url,
                params={"on_conflict": "organization_id"},
                json=payload,
                headers=self._headers(
                    prefer="resolution=merge-duplicates,return=minimal"
                ),
            )
        except httpx.HTTPError:
            raise AutomatedSavingsStoreError(kind="transport") from None
        if response.status_code not in (200, 201, 204):
            raise _store_error(response)

    # -- warehouses -----------------------------------------------------

    def list_warehouses(self, organization_id: str) -> list[EnrollmentRow]:
        try:
            response = self._send(
                "GET",
                self._warehouses_url,
                params={
                    "organization_id": f"eq.{organization_id}",
                    "select": (
                        "organization_id,warehouse_name,enabled,"
                        "warehouse_created_on,updated_at"
                    ),
                },
                headers=self._headers(),
            )
        except httpx.HTTPError:
            raise AutomatedSavingsStoreError(kind="transport") from None
        if response.status_code != 200:
            raise _store_error(response)
        return [_parse_enrollment_row(row) for row in _parse_rows(response)]

    def upsert_enrollment(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        enabled: bool,
        warehouse_created_on: str,
    ) -> None:
        payload = {
            "p_organization_id": organization_id,
            "p_warehouse_name": warehouse_name,
            "p_enabled": enabled,
            "p_warehouse_created_on": warehouse_created_on,
        }
        try:
            response = self._send(
                "POST",
                self._upsert_enrollment_url,
                json=payload,
                headers=self._headers(),
            )
        except httpx.HTTPError:
            raise AutomatedSavingsStoreError(kind="transport") from None
        if response.status_code not in (200, 201, 204):
            raise _store_error(response)

    def unenroll(self, organization_id: str, warehouse_name: str) -> None:
        payload = {
            "p_organization_id": organization_id,
            "p_warehouse_name": warehouse_name,
        }
        try:
            response = self._send(
                "POST",
                self._disable_enrollment_url,
                json=payload,
                headers=self._headers(),
            )
        except httpx.HTTPError:
            raise AutomatedSavingsStoreError(kind="transport") from None
        if response.status_code != 200:
            raise _store_error(response)
        try:
            disabled = response.json()
        except ValueError:
            raise AutomatedSavingsStoreError(kind="invalid_json") from None
        if disabled is not True:
            raise AutomatedSavingsStoreError(kind="not_found")

    # -- events -----------------------------------------------------

    def daily_suspensions(
        self, organization_id: str, day_count: int, end_day: str
    ) -> list[DailySuspensionsRow]:
        rows = self._call_read_rpc(
            self._daily_suspensions_url,
            {
                "p_organization_id": organization_id,
                "p_day_count": day_count,
                "p_end_day": end_day,
            },
        )
        return [_parse_daily_suspensions_row(row) for row in rows]

    def list_events(
        self,
        organization_id: str,
        *,
        limit: int,
        cursor_created_at: str | None = None,
        cursor_id: int | None = None,
    ) -> list[EventRow]:
        rows = self._call_read_rpc(
            self._events_page_url,
            {
                "p_organization_id": organization_id,
                "p_page_limit": limit,
                "p_cursor_created_at": cursor_created_at,
                "p_cursor_id": cursor_id,
            },
        )
        return [_parse_event_row(row) for row in rows]

    def _call_read_rpc(self, url: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            response = self._send("POST", url, json=payload, headers=self._headers())
        except httpx.HTTPError:
            raise AutomatedSavingsStoreError(kind="transport") from None
        if response.status_code != 200:
            raise _store_error(response)
        return _parse_rows(response)


def _parse_rows(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        rows = response.json()
    except ValueError:
        raise AutomatedSavingsStoreError(kind="invalid_json") from None
    if not isinstance(rows, list):
        raise AutomatedSavingsStoreError(kind="malformed_row")
    return rows


def _parse_enrollment_row(row: dict[str, Any]) -> EnrollmentRow:
    try:
        organization_id = _require_nonempty_string(row, "organization_id")
        warehouse_name = _require_nonempty_string(row, "warehouse_name")
        enabled = row["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        warehouse_created_on = _require_aware_timestamp(row, "warehouse_created_on")
        updated_at = _require_aware_timestamp(row, "updated_at")
        return EnrollmentRow(
            organization_id=organization_id,
            warehouse_name=warehouse_name,
            enabled=enabled,
            warehouse_created_on=warehouse_created_on,
            updated_at=updated_at,
        )
    except (KeyError, TypeError, ValueError):
        raise AutomatedSavingsStoreError(kind="malformed_row") from None


def _parse_daily_suspensions_row(row: dict[str, Any]) -> DailySuspensionsRow:
    try:
        day = _require_nonempty_string(row, "day")
        parsed_day = date.fromisoformat(day)
        if parsed_day.isoformat() != day:
            raise ValueError("day must be canonical YYYY-MM-DD")
        warehouse_name = _require_nonempty_string(row, "warehouse_name")
        count = row["suspension_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("suspension_count must be a non-negative integer")
        return DailySuspensionsRow(
            day=day, warehouse_name=warehouse_name, suspension_count=count
        )
    except (KeyError, TypeError, ValueError):
        raise AutomatedSavingsStoreError(kind="malformed_row") from None


def _parse_event_row(row: dict[str, Any]) -> EventRow:
    try:
        event_id = row["id"]
        if isinstance(event_id, bool) or not isinstance(event_id, int):
            raise ValueError("id must be an integer")
        if "observed_started_clusters" not in row:
            raise KeyError("observed_started_clusters")
        started_clusters = row["observed_started_clusters"]
        if started_clusters is not None and (
            isinstance(started_clusters, bool) or not isinstance(started_clusters, int)
        ):
            raise ValueError("observed_started_clusters must be an integer or null")
        if "observed_resumed_on" not in row:
            raise KeyError("observed_resumed_on")
        resumed_on = row["observed_resumed_on"]
        if resumed_on is not None:
            resumed_on = _require_aware_timestamp(row, "observed_resumed_on")
        return EventRow(
            id=event_id,
            created_at=_require_aware_timestamp(row, "created_at"),
            warehouse_name=_require_nonempty_string(row, "warehouse_name"),
            action=_require_nonempty_string(row, "action"),
            reason=_require_nonempty_string(row, "reason"),
            observed_started_clusters=started_clusters,
            observed_resumed_on=resumed_on,
            observed_at=_require_aware_timestamp(row, "observed_at"),
        )
    except (KeyError, TypeError, ValueError):
        raise AutomatedSavingsStoreError(kind="malformed_row") from None


def _require_nonempty_string(row: dict[str, Any], field: str) -> str:
    value = row[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_aware_timestamp(row: dict[str, Any], field: str) -> str:
    value = _require_nonempty_string(row, field)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return value


_store: SupabaseAutomatedSavingsStore | None = None


def configure_automated_savings_store(
    store: SupabaseAutomatedSavingsStore | None,
) -> None:
    global _store
    _store = store


def get_automated_savings_store() -> SupabaseAutomatedSavingsStore | None:
    return _store
