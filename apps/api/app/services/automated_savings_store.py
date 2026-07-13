from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class AutomatedSavingsStoreError(RuntimeError):
    """Raised when the automated-savings store request fails; callers fail loud."""


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
    managed_auto_suspend: int | None
    stored_default_auto_suspend: int | None
    warehouse_created_on: str | None
    cooldown_ts: str | None
    drift_state: str
    drifted_value: int | None


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
        self._intents_url = f"{base}/rest/v1/automated_savings_restore_intents"
        self._upsert_enrollment_url = (
            f"{base}/rest/v1/rpc/automated_savings_upsert_enrollment"
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
        except httpx.HTTPError as exc:
            raise AutomatedSavingsStoreError() from exc
        if response.status_code != 200:
            raise AutomatedSavingsStoreError()
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
        except httpx.HTTPError as exc:
            raise AutomatedSavingsStoreError() from exc
        if response.status_code not in (200, 201, 204):
            raise AutomatedSavingsStoreError()

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
                            "managed_auto_suspend,stored_default_auto_suspend,"
                            "warehouse_created_on,cooldown_ts,drift_state,"
                            "drifted_value"
                        ),
                    },
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise AutomatedSavingsStoreError() from exc
        if response.status_code != 200:
            raise AutomatedSavingsStoreError()
        return [_parse_enrollment_row(row) for row in _parse_rows(response)]

    def upsert_enrollment(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        enabled: bool,
        stored_default: int,
        managed_default: int,
        warehouse_created_on: str | None,
    ) -> None:
        # On a warehouse's FIRST enroll (or a re-enroll of the SAME physical
        # warehouse — i.e. its stored `warehouse_created_on` matches the
        # freshly-captured live `created_on`), capture the customer's current
        # AUTO_SUSPEND as the immutable `stored_default` AND seed
        # `managed_default` from it, or preserve both across a re-enroll.
        # On a re-enroll where the warehouse was dropped and recreated (same
        # name, different `created_on`) — or where the stored `created_on` is
        # unknown — treat it as a FRESH capture instead of inheriting a stale
        # default from the old, now-gone warehouse.
        #
        # This decision (preserve vs. fresh-capture) is made atomically inside
        # a single Postgres upsert (see `automated_savings_upsert_enrollment`
        # in the migration) rather than via a read-then-upsert here: a
        # read-then-upsert that omits the default columns to "preserve" them
        # would, if the row were deleted between the read and the write,
        # INSERT a brand-new enabled row with NULL defaults — which the
        # worker cannot handle. The RPC's INSERT branch always carries the
        # freshly-captured stored_default/managed_default, so that race can
        # never produce a null-default row.
        payload = {
            "p_organization_id": organization_id,
            "p_warehouse_name": warehouse_name,
            "p_enabled": enabled,
            "p_stored_default": stored_default,
            "p_managed_default": managed_default,
            "p_warehouse_created_on": warehouse_created_on,
        }
        try:
            with self._client() as client:
                response = client.post(
                    self._upsert_enrollment_url,
                    json=payload,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise AutomatedSavingsStoreError() from exc
        if response.status_code not in (200, 201, 204):
            raise AutomatedSavingsStoreError()

    def set_managed_default(
        self, organization_id: str, warehouse_name: str, value: int
    ) -> None:
        # `managed_auto_suspend` is the live restore target + drift baseline
        # the worker reads — this is the only write path for it besides
        # enrollment/reconcile.
        self._upsert_warehouse(
            {
                "organization_id": organization_id,
                "warehouse_name": warehouse_name,
                "managed_auto_suspend": value,
            }
        )

    def unenroll(self, organization_id: str, warehouse_name: str) -> None:
        # Only clears `enabled`. Must NEVER write a restore-intent here: the
        # worker drains any *already-outstanding* intent regardless of
        # `enabled` (worker_tenants() unions on outstanding intents), and
        # writing one here would wrongly claim ownership of a customer-set
        # AUTO_SUSPEND that has no intent.
        self._upsert_warehouse(
            {
                "organization_id": organization_id,
                "warehouse_name": warehouse_name,
                "enabled": False,
            }
        )

    def reconcile(
        self, organization_id: str, warehouse_name: str, *, accept: bool
    ) -> None:
        row = self._get_warehouse(organization_id, warehouse_name)
        if row is None:
            raise AutomatedSavingsStoreError()

        if accept:
            # Adopt the drifted value as the new managed baseline (if it
            # clears the floor) and clear drift.
            update: dict[str, Any] = {
                "organization_id": organization_id,
                "warehouse_name": warehouse_name,
                "drift_state": "ok",
                "drifted_value": None,
            }
            if row.drifted_value is not None and row.drifted_value >= 60:
                update["managed_auto_suspend"] = row.drifted_value
            self._upsert_warehouse(update)
            return

        # "Re-apply old default": the API can't ALTER Snowflake directly, so
        # enqueue a restore-intent for the worker to apply next tick, and
        # clear drift so the warehouse isn't left flagged while the intent
        # drains.
        self._enqueue_restore_intent(
            organization_id, warehouse_name, row.managed_auto_suspend
        )
        self._upsert_warehouse(
            {
                "organization_id": organization_id,
                "warehouse_name": warehouse_name,
                "drift_state": "ok",
                "drifted_value": None,
            }
        )

    def _get_warehouse(
        self, organization_id: str, warehouse_name: str
    ) -> EnrollmentRow | None:
        try:
            with self._client() as client:
                response = client.get(
                    self._warehouses_url,
                    params={
                        "organization_id": f"eq.{organization_id}",
                        "warehouse_name": f"eq.{warehouse_name}",
                        "select": (
                            "organization_id,warehouse_name,enabled,"
                            "managed_auto_suspend,stored_default_auto_suspend,"
                            "warehouse_created_on,cooldown_ts,drift_state,"
                            "drifted_value"
                        ),
                        "limit": "1",
                    },
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise AutomatedSavingsStoreError() from exc
        if response.status_code != 200:
            raise AutomatedSavingsStoreError()
        rows = _parse_rows(response)
        if not rows:
            return None
        return _parse_enrollment_row(rows[0])

    def _upsert_warehouse(self, payload: dict[str, Any]) -> None:
        try:
            with self._client() as client:
                response = client.post(
                    self._warehouses_url,
                    params={"on_conflict": "organization_id,warehouse_name"},
                    json=payload,
                    headers=self._headers(
                        prefer="resolution=merge-duplicates,return=minimal"
                    ),
                )
        except httpx.HTTPError as exc:
            raise AutomatedSavingsStoreError() from exc
        if response.status_code not in (200, 201, 204):
            raise AutomatedSavingsStoreError()

    def _enqueue_restore_intent(
        self, organization_id: str, warehouse_name: str, restore_to: int | None
    ) -> None:
        payload = {
            "organization_id": organization_id,
            "warehouse_name": warehouse_name,
            "restore_to": restore_to,
            # 'reapply' tells the worker to overwrite the drifted live value with
            # restore_to instead of treating the mismatch as a fresh customer edit.
            "kind": "reapply",
        }
        try:
            with self._client() as client:
                response = client.post(
                    self._intents_url,
                    params={"on_conflict": "organization_id,warehouse_name"},
                    json=payload,
                    headers=self._headers(
                        prefer="resolution=merge-duplicates,return=minimal"
                    ),
                )
        except httpx.HTTPError as exc:
            raise AutomatedSavingsStoreError() from exc
        if response.status_code not in (200, 201, 204):
            raise AutomatedSavingsStoreError()


def _parse_rows(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        rows = response.json()
    except ValueError as exc:
        raise AutomatedSavingsStoreError() from exc
    if not isinstance(rows, list):
        raise AutomatedSavingsStoreError()
    return rows


def _parse_enrollment_row(row: dict[str, Any]) -> EnrollmentRow:
    try:
        return EnrollmentRow(
            organization_id=str(row["organization_id"]),
            warehouse_name=str(row["warehouse_name"]),
            enabled=bool(row.get("enabled", False)),
            managed_auto_suspend=row.get("managed_auto_suspend"),
            stored_default_auto_suspend=row.get("stored_default_auto_suspend"),
            warehouse_created_on=row.get("warehouse_created_on"),
            cooldown_ts=row.get("cooldown_ts"),
            drift_state=str(row.get("drift_state", "ok")),
            drifted_value=row.get("drifted_value"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AutomatedSavingsStoreError() from exc


_store: SupabaseAutomatedSavingsStore | None = None


def configure_automated_savings_store(
    store: SupabaseAutomatedSavingsStore | None,
) -> None:
    global _store
    _store = store


def get_automated_savings_store() -> SupabaseAutomatedSavingsStore | None:
    return _store
