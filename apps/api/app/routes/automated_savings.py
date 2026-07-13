from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import (
    AuthContext,
    require_auth_context,
    require_org_admin,
    require_org_membership,
)
from app.config import Settings
from app.services.automated_savings_store import (
    AutomatedSavingsStoreError,
    get_automated_savings_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/automated-savings", tags=["automated-savings"])

# A captured AUTO_SUSPEND of 0 (never suspend) or 1 (Snowflake's own minimum,
# indistinguishable from "already managed") can't be safely adopted as the
# enrollment's stored/managed default.
_SENTINEL_DEFAULTS = {0, 1}
MANAGED_DEFAULT_FLOOR_SECONDS = 60


class StatusResponse(BaseModel):
    agreed: bool
    global_enabled: bool
    grant_present: bool
    grant_checked_at: str | None
    role_name: str | None = None


class GlobalSwitchRequest(BaseModel):
    enabled: bool


class ToggleRequest(BaseModel):
    enabled: bool


class ManagedDefaultRequest(BaseModel):
    value: int


class ReconcileRequest(BaseModel):
    accept: bool


class CheckAccessResponse(BaseModel):
    grant_present: bool
    grant_checked_at: str | None
    role_name: str | None = None


class WarehouseResponse(BaseModel):
    name: str
    size: str | None
    state: str | None
    type: str | None
    supported: bool
    min_cluster_count: int | None
    max_cluster_count: int | None
    started_clusters: int | None
    auto_resume_ok: bool
    managed_default: int | None
    stored_default: int | None
    enabled: bool
    drift_state: str
    drifted_value: int | None
    cooldown_ts: str | None
    status: str


@dataclass(frozen=True)
class CapturedWarehouseDefaults:
    stored_default: int | None
    warehouse_created_on: str | None


def capture_stored_default(
    *, organization_id: str, warehouse_name: str, settings: Settings
) -> CapturedWarehouseDefaults:
    """Indirection seam: fetch the warehouse's current AUTO_SUSPEND and
    created_on from Snowflake in a single live lookup.

    Split out so tests can stub the Snowflake round-trip without touching the
    resolver/connection plumbing. Both values are captured from the same live
    row so the worker's drop+recreate detection (which keys off
    `warehouse_created_on`) has a value to compare against.
    """
    from app.services.org_connection_resolver import resolve_snowflake_config
    from app.services.snowflake_runtime import get_connection_fetcher
    from app.services import warehouse_directory

    config = resolve_snowflake_config(
        organization_id,
        settings,
        fetch_connection=get_connection_fetcher(settings),
    )
    live = warehouse_directory.list_live_warehouses(config)
    for row in live:
        if str(row.get("name", "")).upper() == warehouse_name.upper():
            value = row.get("auto_suspend")
            created_on = row.get("created_on")
            return CapturedWarehouseDefaults(
                stored_default=int(value) if value is not None else None,
                warehouse_created_on=(
                    str(created_on) if created_on is not None else None
                ),
            )
    return CapturedWarehouseDefaults(stored_default=None, warehouse_created_on=None)


def _resolve_role_name(organization_id: str, settings: Settings) -> str | None:
    """Best-effort lookup of the org's Snowflake role for display purposes.

    Never raises: status must render even when Snowflake isn't configured or
    the resolution fails for any other reason.
    """
    from app.services.org_connection_resolver import (
        OrgConnectionNotConfiguredError,
        resolve_snowflake_config,
    )
    from app.services.snowflake_runtime import get_connection_fetcher

    try:
        config = resolve_snowflake_config(
            organization_id,
            settings,
            fetch_connection=get_connection_fetcher(settings),
        )
    except OrgConnectionNotConfiguredError:
        return None
    except Exception:  # noqa: BLE001 — status must not fail on resolution errors
        return None
    return config.role


def _require_store():
    store = get_automated_savings_store()
    if store is None:
        raise HTTPException(
            status_code=503, detail="Automated savings is not configured."
        )
    return store


@router.get("/{organization_id}/status", response_model=StatusResponse)
def get_status(
    organization_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> StatusResponse:
    require_org_membership(auth_context, organization_id)
    role_name = _resolve_role_name(organization_id, Settings())
    store = get_automated_savings_store()
    if store is None:
        return StatusResponse(
            agreed=False,
            global_enabled=False,
            grant_present=False,
            grant_checked_at=None,
            role_name=role_name,
        )
    settings_row = store.get_settings(organization_id)
    return StatusResponse(
        agreed=settings_row.agreed_at is not None,
        global_enabled=settings_row.global_enabled,
        grant_present=settings_row.grant_present,
        grant_checked_at=settings_row.grant_checked_at,
        role_name=role_name,
    )


@router.post("/{organization_id}/agree", response_model=StatusResponse)
def agree(
    organization_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> StatusResponse:
    require_org_admin(auth_context, organization_id)
    store = _require_store()
    try:
        store.upsert_agreement(
            organization_id, datetime.now(timezone.utc).isoformat()
        )
    except AutomatedSavingsStoreError:
        raise HTTPException(
            status_code=502, detail="Could not record agreement."
        ) from None
    settings_row = store.get_settings(organization_id)
    return StatusResponse(
        agreed=settings_row.agreed_at is not None,
        global_enabled=settings_row.global_enabled,
        grant_present=settings_row.grant_present,
        grant_checked_at=settings_row.grant_checked_at,
    )


@router.post("/{organization_id}/global-switch", response_model=StatusResponse)
def set_global_switch(
    organization_id: str,
    request: GlobalSwitchRequest,
    auth_context: AuthContext = Depends(require_auth_context),
) -> StatusResponse:
    require_org_admin(auth_context, organization_id)
    store = _require_store()
    try:
        store.set_global_enabled(organization_id, request.enabled)
    except AutomatedSavingsStoreError:
        raise HTTPException(
            status_code=502, detail="Could not update the global switch."
        ) from None
    settings_row = store.get_settings(organization_id)
    return StatusResponse(
        agreed=settings_row.agreed_at is not None,
        global_enabled=settings_row.global_enabled,
        grant_present=settings_row.grant_present,
        grant_checked_at=settings_row.grant_checked_at,
    )


@router.get("/{organization_id}/warehouses", response_model=list[WarehouseResponse])
def list_warehouses(
    organization_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> list[WarehouseResponse]:
    require_org_membership(auth_context, organization_id)

    from app.services.org_connection_resolver import (
        OrgConnectionNotConfiguredError,
        resolve_snowflake_config,
    )
    from app.services.snowflake_runtime import get_connection_fetcher
    from app.services import warehouse_directory

    settings = Settings()
    try:
        config = resolve_snowflake_config(
            organization_id,
            settings,
            fetch_connection=get_connection_fetcher(settings),
        )
    except OrgConnectionNotConfiguredError:
        raise HTTPException(
            status_code=409,
            detail="This organization has no Snowflake connection configured.",
        ) from None

    try:
        live = warehouse_directory.list_live_warehouses(config)
    except Exception:  # noqa: BLE001 — user-safe query failure surface
        raise HTTPException(
            status_code=502, detail="Could not list Snowflake warehouses."
        ) from None

    store = get_automated_savings_store()
    enrollments = store.list_warehouses(organization_id) if store is not None else []

    views = warehouse_directory.join_warehouse_view(live, enrollments)
    return [WarehouseResponse(**vars(view)) for view in views]


@router.post("/{organization_id}/warehouses/{warehouse_name}/toggle")
def toggle_warehouse(
    organization_id: str,
    warehouse_name: str,
    request: ToggleRequest,
    auth_context: AuthContext = Depends(require_auth_context),
) -> None:
    require_org_admin(auth_context, organization_id)

    if not request.enabled:
        # Unenroll only clears `enabled` — never writes a restore-intent. Any
        # already-outstanding intent still drains on the worker's next tick
        # via worker_tenants()'s union over outstanding intents.
        store = _require_store()
        try:
            store.unenroll(organization_id, warehouse_name)
        except AutomatedSavingsStoreError:
            raise HTTPException(
                status_code=502, detail="Could not unenroll the warehouse."
            ) from None
        return None

    settings = Settings()
    try:
        captured = capture_stored_default(
            organization_id=organization_id,
            warehouse_name=warehouse_name,
            settings=settings,
        )
    except Exception:  # noqa: BLE001 — user-safe query failure surface
        logger.exception(
            "capture_stored_default failed for org=%s warehouse=%s",
            organization_id,
            warehouse_name,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not read the warehouse's current AUTO_SUSPEND.",
        ) from None

    stored_default = captured.stored_default
    if stored_default is None or stored_default in _SENTINEL_DEFAULTS:
        raise HTTPException(
            status_code=422,
            detail=(
                "This warehouse's current AUTO_SUSPEND is not eligible for "
                "automated savings."
            ),
        )

    # stored_default is the immutable capture of the customer's real value (may
    # be as low as 2s). managed_default — the restore target the worker writes
    # and the DB constrains to >= 60 — must be floored, or a sub-60 warehouse
    # (e.g. a dev warehouse at AUTO_SUSPEND=30) fails the CHECK on write (502).
    managed_default = max(stored_default, MANAGED_DEFAULT_FLOOR_SECONDS)

    store = _require_store()
    try:
        store.upsert_enrollment(
            organization_id,
            warehouse_name,
            enabled=True,
            stored_default=stored_default,
            managed_default=managed_default,
            warehouse_created_on=captured.warehouse_created_on,
        )
    except AutomatedSavingsStoreError:
        logger.exception(
            "upsert_enrollment failed for org=%s warehouse=%s",
            organization_id,
            warehouse_name,
        )
        raise HTTPException(
            status_code=502, detail="Could not enroll the warehouse."
        ) from None
    return None


@router.post("/{organization_id}/warehouses/{warehouse_name}/managed-default")
def set_managed_default(
    organization_id: str,
    warehouse_name: str,
    request: ManagedDefaultRequest,
    auth_context: AuthContext = Depends(require_auth_context),
) -> None:
    require_org_admin(auth_context, organization_id)
    if request.value < MANAGED_DEFAULT_FLOOR_SECONDS:
        raise HTTPException(
            status_code=422,
            detail=f"managed-default must be >= {MANAGED_DEFAULT_FLOOR_SECONDS} seconds.",
        )
    store = _require_store()
    try:
        store.set_managed_default(organization_id, warehouse_name, request.value)
    except AutomatedSavingsStoreError:
        raise HTTPException(
            status_code=502, detail="Could not update the managed default."
        ) from None
    return None


@router.post("/{organization_id}/warehouses/{warehouse_name}/reconcile")
def reconcile_warehouse(
    organization_id: str,
    warehouse_name: str,
    request: ReconcileRequest,
    auth_context: AuthContext = Depends(require_auth_context),
) -> None:
    require_org_admin(auth_context, organization_id)
    store = _require_store()
    try:
        store.reconcile(organization_id, warehouse_name, accept=request.accept)
    except AutomatedSavingsStoreError:
        raise HTTPException(
            status_code=502, detail="Could not reconcile the warehouse."
        ) from None
    return None


@router.post("/{organization_id}/check-access", response_model=CheckAccessResponse)
def check_access(
    organization_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> CheckAccessResponse:
    require_org_membership(auth_context, organization_id)

    from app.services.org_connection_resolver import (
        OrgConnectionNotConfiguredError,
        resolve_snowflake_config,
    )
    from app.services.snowflake_runtime import get_connection_fetcher
    from app.services import warehouse_directory

    settings = Settings()
    try:
        config = resolve_snowflake_config(
            organization_id,
            settings,
            fetch_connection=get_connection_fetcher(settings),
        )
    except OrgConnectionNotConfiguredError:
        raise HTTPException(
            status_code=409,
            detail="This organization has no Snowflake connection configured.",
        ) from None

    role_name = config.role
    if not role_name:
        raise HTTPException(
            status_code=409,
            detail="This organization's Snowflake connection has no role configured.",
        )

    try:
        present = warehouse_directory.check_manage_warehouses_grant(config, role_name)
    except Exception:  # noqa: BLE001 — user-safe query failure surface
        raise HTTPException(
            status_code=502, detail="Could not check the Snowflake grant."
        ) from None

    checked_at = datetime.now(timezone.utc).isoformat()
    store = get_automated_savings_store()
    if store is not None:
        try:
            store.set_grant_status(organization_id, present, checked_at)
        except AutomatedSavingsStoreError:
            raise HTTPException(
                status_code=502, detail="Could not persist the grant status."
            ) from None

    return CheckAccessResponse(
        grant_present=present, grant_checked_at=checked_at, role_name=role_name
    )
