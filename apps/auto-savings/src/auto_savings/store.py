from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

import httpx

from auto_savings.config import WorkerConfig


@dataclass(frozen=True)
class EnrollmentRow:
    """A warehouse's automated-savings enrollment state.

    ``managed_auto_suspend`` is the live restore target + drift baseline;
    ``stored_default_auto_suspend`` is the immutable reference captured at
    enrollment time.
    """

    organization_id: str
    warehouse_name: str
    enabled: bool
    managed_auto_suspend: int | None
    stored_default_auto_suspend: int | None
    warehouse_created_on: datetime | None
    cooldown_ts: datetime | None
    drift_state: str | None
    drifted_value: int | None


@dataclass(frozen=True)
class SettingsRow:
    organization_id: str
    agreed_at: datetime | None
    global_enabled: bool
    grant_present: bool
    grant_checked_at: datetime | None


@dataclass(frozen=True)
class RestoreIntent:
    organization_id: str
    warehouse_name: str
    restore_to: int
    set_at: datetime
    baseline_resumed_on: datetime | None = None
    cycle_id: str | None = None
    kind: str = "sentinel"  # 'sentinel' (worker suspend) | 'reapply' (admin re-apply)
    # True only once reconcile has observed the sentinel land (live AUTO_SUSPEND
    # == 1). While False, reconcile must not treat any live value as a terminal
    # outcome — a stale SHOW ahead of the ALTER would otherwise strand the intent.
    sentinel_confirmed: bool = False


@dataclass(frozen=True)
class SavingsEvent:
    """An append-only audit record of one AUTO_SUSPEND mutation the worker
    issued on a customer warehouse. ``cycle_id`` pairs a ``set_sentinel`` with
    its later ``restore`` so reclaimed idle time can be derived downstream."""

    organization_id: str
    warehouse_name: str
    action: str  # 'set_sentinel' | 'restore'
    reason: str  # 'decide' | 'suspended' | 'busy' | 'resume_aware' | 'aged_out'
    to_value: int
    observed_at: datetime
    from_value: int | None = None
    observed_state: str | None = None
    observed_running: int | None = None
    observed_queued: int | None = None
    observed_resumed_on: datetime | None = None
    cycle_id: str | None = None


class StoreError(RuntimeError):
    """Raised when the Supabase store cannot complete a request."""


class Store(Protocol):
    """Reads/writes for the automated-savings worker, keyed by organization_id."""

    def get_settings(self, organization_id: str) -> SettingsRow | None: ...

    def list_enrollments(self, organization_id: str) -> list[EnrollmentRow]: ...

    def list_intents(self, organization_id: str) -> list[RestoreIntent]: ...

    def write_intent(
        self,
        organization_id: str,
        warehouse_name: str,
        restore_to: int,
        *,
        set_at: datetime | None = None,
        baseline_resumed_on: datetime | None = None,
        cycle_id: str | None = None,
        kind: str = "sentinel",
    ) -> None: ...

    def record_event(self, event: SavingsEvent) -> None: ...

    def confirm_sentinel(
        self, organization_id: str, warehouse_name: str, cycle_id: str
    ) -> None: ...

    def delete_intent(self, organization_id: str, warehouse_name: str) -> None: ...

    def set_cooldown(
        self, organization_id: str, warehouse_name: str, cooldown_ts: datetime
    ) -> None: ...

    def mark_drifted(
        self, organization_id: str, warehouse_name: str, drifted_value: int
    ) -> None: ...

    def mark_unsupported(self, organization_id: str, warehouse_name: str) -> None: ...

    def clear_enrollment(self, organization_id: str, warehouse_name: str) -> None: ...

    def worker_tenants(self) -> list[str]: ...


def _now(set_at: datetime | None) -> datetime:
    return set_at if set_at is not None else datetime.now(timezone.utc)


class InMemoryStore:
    """Hermetic fake for tests — no network, no Supabase."""

    def __init__(self) -> None:
        self._enrollments: dict[tuple[str, str], EnrollmentRow] = {}
        self._settings: dict[str, SettingsRow] = {}
        self._intents: dict[tuple[str, str], RestoreIntent] = {}
        self._events: list[SavingsEvent] = []

    def seed_enrollment(self, row: EnrollmentRow) -> None:
        self._enrollments[(row.organization_id, row.warehouse_name)] = row

    def seed_settings(self, row: SettingsRow) -> None:
        self._settings[row.organization_id] = row

    def get_settings(self, organization_id: str) -> SettingsRow | None:
        return self._settings.get(organization_id)

    def list_enrollments(self, organization_id: str) -> list[EnrollmentRow]:
        return [
            row
            for (org_id, _wh), row in self._enrollments.items()
            if org_id == organization_id
        ]

    def list_intents(self, organization_id: str) -> list[RestoreIntent]:
        return [
            intent
            for (org_id, _wh), intent in self._intents.items()
            if org_id == organization_id
        ]

    def write_intent(
        self,
        organization_id: str,
        warehouse_name: str,
        restore_to: int,
        *,
        set_at: datetime | None = None,
        baseline_resumed_on: datetime | None = None,
        cycle_id: str | None = None,
        kind: str = "sentinel",
    ) -> None:
        self._intents[(organization_id, warehouse_name)] = RestoreIntent(
            organization_id=organization_id,
            warehouse_name=warehouse_name,
            restore_to=restore_to,
            set_at=_now(set_at),
            baseline_resumed_on=baseline_resumed_on,
            cycle_id=cycle_id or uuid4().hex,
            kind=kind,
        )

    def record_event(self, event: SavingsEvent) -> None:
        self._events.append(event)

    def list_events(self, organization_id: str) -> list[SavingsEvent]:
        return [e for e in self._events if e.organization_id == organization_id]

    def confirm_sentinel(
        self, organization_id: str, warehouse_name: str, cycle_id: str
    ) -> None:
        key = (organization_id, warehouse_name)
        intent = self._intents.get(key)
        if (
            intent is None
            or intent.cycle_id != cycle_id
            or intent.kind != "sentinel"
        ):
            raise StoreError()
        self._intents[key] = replace(intent, sentinel_confirmed=True)

    def delete_intent(self, organization_id: str, warehouse_name: str) -> None:
        self._intents.pop((organization_id, warehouse_name), None)

    def set_cooldown(
        self, organization_id: str, warehouse_name: str, cooldown_ts: datetime
    ) -> None:
        key = (organization_id, warehouse_name)
        row = self._enrollments.get(key)
        if row is None:
            return
        self._enrollments[key] = EnrollmentRow(
            organization_id=row.organization_id,
            warehouse_name=row.warehouse_name,
            enabled=row.enabled,
            managed_auto_suspend=row.managed_auto_suspend,
            stored_default_auto_suspend=row.stored_default_auto_suspend,
            warehouse_created_on=row.warehouse_created_on,
            cooldown_ts=cooldown_ts,
            drift_state=row.drift_state,
            drifted_value=row.drifted_value,
        )

    def mark_drifted(
        self, organization_id: str, warehouse_name: str, drifted_value: int
    ) -> None:
        key = (organization_id, warehouse_name)
        row = self._enrollments.get(key)
        if row is None:
            return
        self._enrollments[key] = EnrollmentRow(
            organization_id=row.organization_id,
            warehouse_name=row.warehouse_name,
            enabled=row.enabled,
            managed_auto_suspend=row.managed_auto_suspend,
            stored_default_auto_suspend=row.stored_default_auto_suspend,
            warehouse_created_on=row.warehouse_created_on,
            cooldown_ts=row.cooldown_ts,
            drift_state="drifted",
            drifted_value=drifted_value,
        )

    def mark_unsupported(self, organization_id: str, warehouse_name: str) -> None:
        key = (organization_id, warehouse_name)
        row = self._enrollments.get(key)
        if row is None:
            return
        self._enrollments[key] = EnrollmentRow(
            organization_id=row.organization_id,
            warehouse_name=row.warehouse_name,
            enabled=row.enabled,
            managed_auto_suspend=row.managed_auto_suspend,
            stored_default_auto_suspend=row.stored_default_auto_suspend,
            warehouse_created_on=row.warehouse_created_on,
            cooldown_ts=row.cooldown_ts,
            drift_state="unsupported",
            drifted_value=row.drifted_value,
        )

    def clear_enrollment(self, organization_id: str, warehouse_name: str) -> None:
        self._enrollments.pop((organization_id, warehouse_name), None)

    def worker_tenants(self) -> list[str]:
        enabled_warehouse_orgs = {
            org_id for (org_id, _wh), row in self._enrollments.items() if row.enabled
        }
        globally_enrolled_orgs = {
            row.organization_id
            for row in self._settings.values()
            if row.global_enabled and row.organization_id in enabled_warehouse_orgs
        }
        intent_orgs = {org_id for org_id, _wh in self._intents}
        return sorted(globally_enrolled_orgs | intent_orgs)


class SupabaseStore:
    """Reads/writes automated-savings tables via the service role (sole writer)."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = config.supabase_url.rstrip("/")
        self._base_url = f"{base}/rest/v1"
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

    def get_settings(self, organization_id: str) -> SettingsRow | None:
        rows = self._get(
            "/automated_savings_settings", organization_id=organization_id
        )
        if not rows:
            return None
        return _parse_settings(rows[0])

    def list_enrollments(self, organization_id: str) -> list[EnrollmentRow]:
        rows = self._get(
            "/automated_savings_warehouses", organization_id=organization_id
        )
        return [_parse_enrollment(row) for row in rows]

    def list_intents(self, organization_id: str) -> list[RestoreIntent]:
        rows = self._get(
            "/automated_savings_restore_intents", organization_id=organization_id
        )
        return [_parse_intent(row) for row in rows]

    def write_intent(
        self,
        organization_id: str,
        warehouse_name: str,
        restore_to: int,
        *,
        set_at: datetime | None = None,
        baseline_resumed_on: datetime | None = None,
        cycle_id: str | None = None,
        kind: str = "sentinel",
    ) -> None:
        payload = {
            "organization_id": organization_id,
            "warehouse_name": warehouse_name,
            "restore_to": restore_to,
            "set_at": _now(set_at).isoformat(),
            "baseline_resumed_on": (
                baseline_resumed_on.isoformat()
                if baseline_resumed_on is not None
                else None
            ),
            "kind": kind,
            # Reset on every (upsert) write so a re-armed sentinel cannot inherit
            # a prior row's confirmation — the merge-duplicates upsert would keep
            # the stale value otherwise.
            "sentinel_confirmed": False,
        }
        if cycle_id is not None:
            payload["cycle_id"] = cycle_id
        try:
            with self._client() as client:
                response = client.post(
                    "/automated_savings_restore_intents",
                    json=payload,
                    headers=self._headers(
                        prefer="resolution=merge-duplicates,return=minimal"
                    ),
                )
        except httpx.HTTPError as exc:
            raise StoreError() from exc
        if response.status_code not in (200, 201, 204):
            raise StoreError()

    def record_event(self, event: SavingsEvent) -> None:
        payload = {
            "organization_id": event.organization_id,
            "warehouse_name": event.warehouse_name,
            "cycle_id": event.cycle_id,
            "action": event.action,
            "reason": event.reason,
            "from_value": event.from_value,
            "to_value": event.to_value,
            "observed_state": event.observed_state,
            "observed_running": event.observed_running,
            "observed_queued": event.observed_queued,
            "observed_resumed_on": (
                event.observed_resumed_on.isoformat()
                if event.observed_resumed_on is not None
                else None
            ),
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
            raise StoreError() from exc
        if response.status_code not in (200, 201, 204):
            raise StoreError()

    def confirm_sentinel(
        self, organization_id: str, warehouse_name: str, cycle_id: str
    ) -> None:
        try:
            with self._client() as client:
                response = client.patch(
                    "/automated_savings_restore_intents",
                    params={
                        "select": (
                            "organization_id,warehouse_name,cycle_id,kind,"
                            "sentinel_confirmed"
                        ),
                        "organization_id": f"eq.{organization_id}",
                        "warehouse_name": f"eq.{warehouse_name}",
                        "cycle_id": f"eq.{cycle_id}",
                        "kind": "eq.sentinel",
                    },
                    json={"sentinel_confirmed": True},
                    headers=self._headers(prefer="return=representation"),
                )
        except httpx.HTTPError as exc:
            raise StoreError() from exc
        if response.status_code != 200:
            raise StoreError()
        try:
            rows = response.json()
        except ValueError as exc:
            raise StoreError() from exc
        if not isinstance(rows, list) or len(rows) != 1:
            raise StoreError()
        row = rows[0]
        if not isinstance(row, dict) or row != {
            "organization_id": organization_id,
            "warehouse_name": warehouse_name,
            "cycle_id": cycle_id,
            "kind": "sentinel",
            "sentinel_confirmed": True,
        }:
            raise StoreError()

    def delete_intent(self, organization_id: str, warehouse_name: str) -> None:
        try:
            with self._client() as client:
                response = client.delete(
                    "/automated_savings_restore_intents",
                    params={
                        "organization_id": f"eq.{organization_id}",
                        "warehouse_name": f"eq.{warehouse_name}",
                    },
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise StoreError() from exc
        if response.status_code not in (200, 204):
            raise StoreError()

    def set_cooldown(
        self, organization_id: str, warehouse_name: str, cooldown_ts: datetime
    ) -> None:
        self._patch_enrollment(
            organization_id,
            warehouse_name,
            {"cooldown_ts": cooldown_ts.isoformat()},
        )

    def mark_drifted(
        self, organization_id: str, warehouse_name: str, drifted_value: int
    ) -> None:
        self._patch_enrollment(
            organization_id,
            warehouse_name,
            {"drift_state": "drifted", "drifted_value": drifted_value},
        )

    def mark_unsupported(self, organization_id: str, warehouse_name: str) -> None:
        self._patch_enrollment(
            organization_id, warehouse_name, {"drift_state": "unsupported"}
        )

    def clear_enrollment(self, organization_id: str, warehouse_name: str) -> None:
        try:
            with self._client() as client:
                response = client.delete(
                    "/automated_savings_warehouses",
                    params={
                        "organization_id": f"eq.{organization_id}",
                        "warehouse_name": f"eq.{warehouse_name}",
                    },
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise StoreError() from exc
        if response.status_code not in (200, 204):
            raise StoreError()

    def worker_tenants(self) -> list[str]:
        try:
            with self._client() as client:
                response = client.post(
                    "/rpc/automated_savings_worker_tenants",
                    json={},
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise StoreError() from exc
        if response.status_code != 200:
            raise StoreError()
        try:
            rows = response.json()
        except ValueError as exc:
            raise StoreError() from exc
        if not isinstance(rows, list):
            raise StoreError()
        try:
            return [
                row["organization_id"] if isinstance(row, dict) else str(row)
                for row in rows
            ]
        except (KeyError, TypeError) as exc:
            raise StoreError() from exc

    def _get(self, path: str, *, organization_id: str) -> list[dict]:
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
            raise StoreError() from exc
        if response.status_code != 200:
            raise StoreError()
        try:
            rows = response.json()
        except ValueError as exc:
            raise StoreError() from exc
        if not isinstance(rows, list):
            raise StoreError()
        return rows

    def _patch_enrollment(
        self, organization_id: str, warehouse_name: str, payload: dict
    ) -> None:
        try:
            with self._client() as client:
                response = client.patch(
                    "/automated_savings_warehouses",
                    params={
                        "organization_id": f"eq.{organization_id}",
                        "warehouse_name": f"eq.{warehouse_name}",
                    },
                    json=payload,
                    headers=self._headers(prefer="return=minimal"),
                )
        except httpx.HTTPError as exc:
            raise StoreError() from exc
        if response.status_code not in (200, 204):
            raise StoreError()


def _parse_ts(value: object) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_optional_ts(value: object) -> datetime | None:
    if value is None:
        return None
    return _parse_ts(value)


def _parse_optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _parse_settings(row: object) -> SettingsRow:
    if not isinstance(row, dict):
        raise StoreError()
    try:
        return SettingsRow(
            organization_id=str(row["organization_id"]),
            agreed_at=_parse_optional_ts(row.get("agreed_at")),
            global_enabled=bool(row["global_enabled"]),
            grant_present=bool(row["grant_present"]),
            grant_checked_at=_parse_optional_ts(row.get("grant_checked_at")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError() from exc


def _parse_enrollment(row: object) -> EnrollmentRow:
    """Parse one ``automated_savings_warehouses`` row.

    ``managed_auto_suspend``, ``stored_default_auto_suspend``, and
    ``warehouse_created_on`` are nullable in the DB (an unenrolled or
    partially-provisioned row) — parse them as Optional and never raise on
    null so one malformed/partial row cannot fail the entire tenant cycle
    (finding #6). Enforcement of non-null for ENABLED enrollments belongs
    downstream (reconcile/engine already guard on None where it matters).
    """
    if not isinstance(row, dict):
        raise StoreError()
    try:
        return EnrollmentRow(
            organization_id=str(row["organization_id"]),
            warehouse_name=str(row["warehouse_name"]),
            enabled=bool(row["enabled"]),
            managed_auto_suspend=_parse_optional_int(row.get("managed_auto_suspend")),
            stored_default_auto_suspend=_parse_optional_int(
                row.get("stored_default_auto_suspend")
            ),
            warehouse_created_on=_parse_optional_ts(row.get("warehouse_created_on")),
            cooldown_ts=_parse_optional_ts(row.get("cooldown_ts")),
            drift_state=(
                str(row["drift_state"]) if row.get("drift_state") is not None else None
            ),
            drifted_value=(
                int(row["drifted_value"])
                if row.get("drifted_value") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError() from exc


def _parse_intent(row: object) -> RestoreIntent:
    if not isinstance(row, dict):
        raise StoreError()
    try:
        return RestoreIntent(
            organization_id=str(row["organization_id"]),
            warehouse_name=str(row["warehouse_name"]),
            restore_to=int(row["restore_to"]),
            set_at=_parse_ts(row["set_at"]),
            baseline_resumed_on=_parse_optional_ts(row.get("baseline_resumed_on")),
            cycle_id=(
                str(row["cycle_id"]) if row.get("cycle_id") is not None else None
            ),
            kind=str(row.get("kind") or "sentinel"),
            # Absent for pre-migration rows -> unconfirmed (safe default: HOLD).
            sentinel_confirmed=bool(row.get("sentinel_confirmed", False)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError() from exc
