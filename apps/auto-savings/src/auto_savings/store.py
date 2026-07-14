from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

from auto_savings.config import WorkerConfig


@dataclass(frozen=True)
class EnrollmentRow:
    """Versioned warehouse enrollment used by the final authorization check."""

    organization_id: str
    warehouse_name: str
    enabled: bool
    warehouse_created_on: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SettingsRow:
    organization_id: str
    global_enabled: bool


@dataclass(frozen=True)
class SavingsEvent:
    """Append-only audit record for an accepted direct suspension request."""

    organization_id: str
    warehouse_name: str
    action: str
    reason: str
    observed_state: str
    observed_running: int
    observed_queued: int
    observed_quiescing: int
    observed_resumed_on: datetime
    observed_started_clusters: int | None
    observed_min_cluster_count: int | None
    observed_max_cluster_count: int | None
    observed_at: datetime


class StoreError(RuntimeError):
    """Raised when the Supabase store cannot safely complete a request."""


class Store(Protocol):
    def list_enrollments(self, organization_id: str) -> list[EnrollmentRow]: ...

    def authorize_suspend(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool: ...

    def delete_stale_enrollment(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool: ...

    def record_event(self, event: SavingsEvent) -> None: ...

    def worker_tenants(self) -> list[str]: ...


class InMemoryStore:
    """Hermetic implementation of the direct store contract for worker tests."""

    def __init__(self) -> None:
        self._enrollments: dict[tuple[str, str], EnrollmentRow] = {}
        self._settings: dict[str, SettingsRow] = {}
        self._events: list[SavingsEvent] = []

    def seed_enrollment(self, row: EnrollmentRow) -> None:
        self._enrollments[(row.organization_id, row.warehouse_name)] = row

    def seed_settings(self, row: SettingsRow) -> None:
        self._settings[row.organization_id] = row

    def list_enrollments(self, organization_id: str) -> list[EnrollmentRow]:
        return [
            row
            for (org_id, _warehouse), row in self._enrollments.items()
            if org_id == organization_id
        ]

    def authorize_suspend(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool:
        settings = self._settings.get(organization_id)
        enrollment = self._enrollments.get((organization_id, warehouse_name))
        return bool(
            settings is not None
            and settings.global_enabled
            and enrollment is not None
            and enrollment.enabled
            and enrollment.warehouse_created_on == warehouse_created_on
            and enrollment.updated_at == enrollment_updated_at
        )

    def delete_stale_enrollment(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool:
        key = (organization_id, warehouse_name)
        enrollment = self._enrollments.get(key)
        if (
            enrollment is None
            or enrollment.warehouse_created_on != warehouse_created_on
            or enrollment.updated_at != enrollment_updated_at
        ):
            return False
        del self._enrollments[key]
        return True

    def record_event(self, event: SavingsEvent) -> None:
        self._events.append(event)

    def list_events(self, organization_id: str) -> list[SavingsEvent]:
        return [
            event for event in self._events if event.organization_id == organization_id
        ]

    def worker_tenants(self) -> list[str]:
        enabled_warehouse_orgs = {
            org_id
            for (org_id, _warehouse), row in self._enrollments.items()
            if row.enabled
        }
        return sorted(
            row.organization_id
            for row in self._settings.values()
            if row.global_enabled and row.organization_id in enabled_warehouse_orgs
        )


class SupabaseStore:
    """Direct automated-savings reads and writes through the service role."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = f"{config.supabase_url.rstrip('/')}/rest/v1"
        self._service_role_key = config.supabase_service_role_key
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
        return httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            transport=self._transport,
        )

    def list_enrollments(self, organization_id: str) -> list[EnrollmentRow]:
        rows = self._get("/automated_savings_warehouses", organization_id)
        return [_parse_enrollment(row) for row in rows]

    def authorize_suspend(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool:
        return self._direct_state_rpc(
            "/rpc/automated_savings_authorize_suspend",
            "authorize suspend",
            organization_id,
            warehouse_name,
            warehouse_created_on=warehouse_created_on,
            enrollment_updated_at=enrollment_updated_at,
        )

    def delete_stale_enrollment(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool:
        return self._direct_state_rpc(
            "/rpc/automated_savings_delete_stale_enrollment",
            "delete stale enrollment",
            organization_id,
            warehouse_name,
            warehouse_created_on=warehouse_created_on,
            enrollment_updated_at=enrollment_updated_at,
        )

    def record_event(self, event: SavingsEvent) -> None:
        payload = {
            "organization_id": event.organization_id,
            "warehouse_name": event.warehouse_name,
            "action": event.action,
            "reason": event.reason,
            "observed_state": event.observed_state,
            "observed_running": event.observed_running,
            "observed_queued": event.observed_queued,
            "observed_quiescing": event.observed_quiescing,
            "observed_resumed_on": event.observed_resumed_on.isoformat(),
            "observed_started_clusters": event.observed_started_clusters,
            "observed_min_cluster_count": event.observed_min_cluster_count,
            "observed_max_cluster_count": event.observed_max_cluster_count,
            "observed_at": event.observed_at.isoformat(),
        }
        try:
            with self._client() as client:
                response = client.post(
                    "/automated_savings_events",
                    json=payload,
                    headers=self._headers(prefer="return=minimal"),
                )
        except httpx.HTTPError as exc:
            raise StoreError("record savings event request failed") from exc
        if response.status_code not in (200, 201, 204):
            raise StoreError(
                f"record savings event failed with HTTP {response.status_code}"
            )

    def worker_tenants(self) -> list[str]:
        try:
            with self._client() as client:
                response = client.post(
                    "/rpc/automated_savings_worker_tenants",
                    json={},
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise StoreError("worker tenants request failed") from exc
        rows = _response_json(response, "worker tenants")
        if not isinstance(rows, list):
            raise StoreError(
                f"worker tenants expected list result, got {_json_kind(rows)}"
            )
        tenants: list[str] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise StoreError(
                    f"worker tenants expected object at item {index}, "
                    f"got {_json_kind(row)}"
                )
            organization_id = row.get("organization_id")
            if not isinstance(organization_id, str) or not organization_id:
                raise StoreError(
                    f"worker tenants item {index} has invalid organization_id"
                )
            tenants.append(organization_id)
        return tenants

    def _direct_state_rpc(
        self,
        path: str,
        operation: str,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool:
        payload = {
            "p_organization_id": organization_id,
            "p_warehouse_name": warehouse_name,
            "p_warehouse_created_on": warehouse_created_on.isoformat(),
            "p_enrollment_updated_at": enrollment_updated_at.isoformat(),
        }
        try:
            with self._client() as client:
                response = client.post(path, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise StoreError(f"{operation} request failed") from exc
        result = _response_json(response, operation)
        if not isinstance(result, bool):
            raise StoreError(
                f"{operation} expected boolean result, got {_json_kind(result)}"
            )
        return result

    def _get(self, path: str, organization_id: str) -> list[object]:
        try:
            with self._client() as client:
                response = client.get(
                    path,
                    params={
                        "select": "*",
                        "organization_id": f"eq.{organization_id}",
                    },
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise StoreError(f"{path} request failed") from exc
        rows = _response_json(response, path)
        if not isinstance(rows, list):
            raise StoreError(f"{path} expected list result, got {_json_kind(rows)}")
        return rows


def _response_json(response: httpx.Response, operation: str) -> object:
    if response.status_code != 200:
        raise StoreError(f"{operation} failed with HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise StoreError(f"{operation} returned invalid JSON") from exc


def _json_kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _parse_ts(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise StoreError()
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise StoreError() from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StoreError()
    return parsed


def _required_str(row: dict[object, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise StoreError()
    return value


def _required_bool(row: dict[object, object], key: str) -> bool:
    value = row.get(key)
    if not isinstance(value, bool):
        raise StoreError()
    return value


def _parse_enrollment(row: object) -> EnrollmentRow:
    if not isinstance(row, dict):
        raise StoreError()
    return EnrollmentRow(
        organization_id=_required_str(row, "organization_id"),
        warehouse_name=_required_str(row, "warehouse_name"),
        enabled=_required_bool(row, "enabled"),
        warehouse_created_on=_parse_ts(row.get("warehouse_created_on")),
        updated_at=_parse_ts(row.get("updated_at")),
    )
