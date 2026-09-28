from __future__ import annotations

from typing import Any

import httpx

from app.models.waste_alert_state import WasteAlertState
from app.services.pooled_requests import send_pooled_request


class WasteAlertStoreError(RuntimeError):
    """Raised when the Supabase waste-alert table cannot be read or written."""


class SupabaseWasteAlertStore:
    """Service-role REST client for ``waste_alert_state``.

    Matches ``SupabaseAutomatedSavingsStore``: pooled sync httpx, not supabase-py.
    """

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = supabase_url.rstrip("/")
        self._table_url = f"{base}/rest/v1/waste_alert_state"
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

    async def get_waste_alert_state(
        self, warehouse_name: str
    ) -> WasteAlertState | None:
        try:
            response = self._send(
                "GET",
                self._table_url,
                params={
                    "warehouse_name": f"eq.{warehouse_name}",
                    "select": (
                        "warehouse_name,last_alerted_at,"
                        "last_projected_monthly_idle_spend"
                    ),
                    "limit": "1",
                },
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise WasteAlertStoreError("waste_alert_state read failed") from exc
        if response.status_code != 200:
            raise WasteAlertStoreError("waste_alert_state read failed")
        rows = _parse_rows(response)
        if not rows:
            return None
        return WasteAlertState.model_validate(rows[0])

    async def set_waste_alert_state(self, state: WasteAlertState) -> None:
        try:
            response = self._send(
                "POST",
                self._table_url,
                json=state.model_dump(mode="json"),
                headers=self._headers(
                    prefer="resolution=merge-duplicates,return=minimal"
                ),
            )
        except httpx.HTTPError as exc:
            raise WasteAlertStoreError("waste_alert_state write failed") from exc
        if response.status_code not in {200, 201, 204}:
            raise WasteAlertStoreError("waste_alert_state write failed")


def _parse_rows(response: httpx.Response) -> list[dict[str, Any]]:
    payload = response.json()
    if not isinstance(payload, list):
        raise WasteAlertStoreError("waste_alert_state read failed")
    return payload
