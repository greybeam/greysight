from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import httpx


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

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout_seconds, transport=self._transport)

    # -- settings -----------------------------------------------------

    def get_settings(self, organization_id: str) -> SettingsRow:
        try:
            with self._client() as client:
                response = client.get(
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
            with self._client() as client:
                response = client.post(
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
            with self._client() as client:
                response = client.get(
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
            with self._client() as client:
                response = client.post(
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
            with self._client() as client:
                response = client.post(
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
