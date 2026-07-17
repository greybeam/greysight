from __future__ import annotations

import base64
import binascii
import logging
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
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
from app.services.snowflake_client import SnowflakeQueryError
from app.services.ttl_cache import TtlCache
from app.services.warehouse_directory import WarehouseStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/automated-savings", tags=["automated-savings"])


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


class CheckAccessResponse(BaseModel):
    grant_present: bool
    grant_checked_at: str | None
    role_name: str | None = None


class SuspensionStatsBucket(BaseModel):
    day: str
    counts: dict[str, int]


class SuspensionStatsResponse(BaseModel):
    days: int
    warehouses: list[str]
    buckets: list[SuspensionStatsBucket]


class SuspensionEventResponse(BaseModel):
    id: str
    created_at: str
    warehouse_name: str
    action: str
    reason: str
    observed_started_clusters: int | None
    observed_resumed_on: str | None
    observed_at: str


class SuspensionEventsPageResponse(BaseModel):
    events: list[SuspensionEventResponse]
    next_cursor: str | None


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
    auto_suspend: int | None
    quiescing: int | None
    enabled: bool
    status: WarehouseStatus


def _resolve_config(organization_id: str, settings: Settings):
    """Resolve the org's live Snowflake config via the connection fetcher.

    Imports are local so tests can monkeypatch the resolver seam, and to avoid
    import cycles. Callers own their own error handling for a missing config.
    """
    from app.services.org_connection_resolver import resolve_snowflake_config
    from app.services.snowflake_runtime import get_connection_fetcher

    return resolve_snowflake_config(
        organization_id,
        settings,
        fetch_connection=get_connection_fetcher(settings),
    )


def capture_warehouse_identity(
    *, organization_id: str, warehouse_name: str, settings: Settings
) -> str | None:
    """Fetch the live warehouse identity used to detect name reuse."""
    from app.services import warehouse_directory

    config = _resolve_config(organization_id, settings)
    live = warehouse_directory.list_live_warehouses(config)
    for row in live:
        if row.get("name") == warehouse_name:
            created_on = row.get("created_on")
            return str(created_on) if created_on is not None else None
    return None


def _has_valid_warehouse_identity(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _resolve_role_name(organization_id: str, settings: Settings) -> str | None:
    """Best-effort lookup of the org's Snowflake role for display purposes.

    Never raises: status must render even when Snowflake isn't configured or
    the resolution fails for any other reason.
    """
    from app.services.org_connection_resolver import OrgConnectionNotConfiguredError

    try:
        config = _resolve_config(organization_id, settings)
    except OrgConnectionNotConfiguredError:
        return None
    except Exception:  # noqa: BLE001 — status must not fail on resolution errors
        return None
    return config.role


def _status_response(settings_row, *, role_name: str | None = None) -> StatusResponse:
    return StatusResponse(
        agreed=settings_row.agreed_at is not None,
        global_enabled=settings_row.global_enabled,
        grant_present=settings_row.grant_present,
        grant_checked_at=settings_row.grant_checked_at,
        role_name=role_name,
    )


# Stats responses are cheap but page-refresh-hot; 60s staleness is acceptable.
_stats_cache = TtlCache(ttl_seconds=60.0, max_entries=256)

_ALLOWED_STATS_DAYS = (7, 30)


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


_CURSOR_VERSION = "v1"
_CURSOR_MAX_LENGTH = 256
_EVENTS_MAX_LIMIT = 100
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def _encode_cursor(created_at: str, event_id: int) -> str:
    raw = f"{_CURSOR_VERSION}|{created_at}|{event_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[str, int]:
    invalid = HTTPException(status_code=400, detail="Invalid cursor.")
    if len(cursor) > _CURSOR_MAX_LENGTH:
        raise invalid
    if not _BASE64URL_RE.match(cursor):
        raise invalid
    try:
        # urlsafe_b64decode has no validate parameter; strict-decode via altchars.
        raw = base64.b64decode(cursor.encode(), altchars=b"-_", validate=True).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise invalid from None
    parts = raw.split("|")
    if len(parts) != 3 or parts[0] != _CURSOR_VERSION:
        raise invalid
    _, created_at, id_text = parts
    try:
        parsed = datetime.fromisoformat(created_at)
        event_id = int(id_text)
    except ValueError:
        raise invalid from None
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or event_id <= 0
        or event_id > 2**63 - 1
    ):
        raise invalid
    return created_at, event_id


def _require_store():
    store = get_automated_savings_store()
    if store is None:
        raise HTTPException(
            status_code=503, detail="Automated savings is not configured."
        )
    return store


def _log_store_failure(operation: str, error: AutomatedSavingsStoreError) -> None:
    logger.warning(
        "automated savings store failure operation=%s kind=%s status=%s",
        operation,
        error.kind,
        error.status_code if error.status_code is not None else "none",
    )


def _snowflake_failure_detail(
    error: SnowflakeQueryError, fallback: str
) -> str | dict[str, str]:
    if error.user_safe_message:
        return {"user_safe_message": error.user_safe_message}
    return fallback


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
    return _status_response(settings_row, role_name=role_name)


@router.post("/{organization_id}/agree", response_model=StatusResponse)
def agree(
    organization_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> StatusResponse:
    require_org_admin(auth_context, organization_id)
    store = _require_store()
    try:
        store.upsert_agreement(organization_id, datetime.now(timezone.utc).isoformat())
    except AutomatedSavingsStoreError as exc:
        _log_store_failure("upsert_agreement", exc)
        raise HTTPException(
            status_code=502, detail="Could not record agreement."
        ) from None
    settings_row = store.get_settings(organization_id)
    return _status_response(settings_row)


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
    except AutomatedSavingsStoreError as exc:
        _log_store_failure("set_global_switch", exc)
        raise HTTPException(
            status_code=502, detail="Could not update the global switch."
        ) from None
    settings_row = store.get_settings(organization_id)
    return _status_response(settings_row)


@router.get("/{organization_id}/warehouses", response_model=list[WarehouseResponse])
def list_warehouses(
    organization_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> list[WarehouseResponse]:
    require_org_membership(auth_context, organization_id)

    from app.services.org_connection_resolver import OrgConnectionNotConfiguredError
    from app.services import warehouse_directory

    settings = Settings()
    try:
        config = _resolve_config(organization_id, settings)
    except OrgConnectionNotConfiguredError:
        raise HTTPException(
            status_code=409,
            detail="This organization has no Snowflake connection configured.",
        ) from None

    try:
        live = warehouse_directory.list_live_warehouses(config)
    except SnowflakeQueryError as exc:
        raise HTTPException(
            status_code=502,
            detail=_snowflake_failure_detail(
                exc, "Could not list Snowflake warehouses."
            ),
        ) from None
    except Exception:  # noqa: BLE001 — user-safe query failure surface
        raise HTTPException(
            status_code=502, detail="Could not list Snowflake warehouses."
        ) from None

    store = get_automated_savings_store()
    try:
        enrollments = (
            store.list_warehouses(organization_id) if store is not None else []
        )
    except AutomatedSavingsStoreError as exc:
        _log_store_failure("list_warehouse_enrollments", exc)
        raise HTTPException(
            status_code=502, detail="Could not load warehouse enrollments."
        ) from None

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
        store = _require_store()
        try:
            store.unenroll(organization_id, warehouse_name)
        except AutomatedSavingsStoreError as exc:
            _log_store_failure("unenroll_warehouse", exc)
            raise HTTPException(
                status_code=502, detail="Could not unenroll the warehouse."
            ) from None
        return None

    settings = Settings()
    try:
        warehouse_created_on = capture_warehouse_identity(
            organization_id=organization_id,
            warehouse_name=warehouse_name,
            settings=settings,
        )
    except SnowflakeQueryError as exc:
        logger.warning(
            "automated savings upstream failure "
            "operation=capture_warehouse_identity kind=query status=none"
        )
        raise HTTPException(
            status_code=502,
            detail=_snowflake_failure_detail(
                exc, "Could not read the warehouse's identity."
            ),
        ) from None
    except Exception:  # noqa: BLE001 — user-safe query failure surface
        logger.warning(
            "automated savings upstream failure "
            "operation=capture_warehouse_identity kind=query status=none"
        )
        raise HTTPException(
            status_code=502,
            detail="Could not read the warehouse's identity.",
        ) from None

    if not _has_valid_warehouse_identity(warehouse_created_on):
        raise HTTPException(
            status_code=422,
            detail="This warehouse does not have a valid identity.",
        )

    store = _require_store()
    try:
        store.upsert_enrollment(
            organization_id,
            warehouse_name,
            enabled=True,
            warehouse_created_on=warehouse_created_on,
        )
    except AutomatedSavingsStoreError as exc:
        _log_store_failure("upsert_warehouse_enrollment", exc)
        raise HTTPException(
            status_code=502, detail="Could not enroll the warehouse."
        ) from None
    return None


@router.post("/{organization_id}/check-access", response_model=CheckAccessResponse)
def check_access(
    organization_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> CheckAccessResponse:
    require_org_membership(auth_context, organization_id)

    from app.services.org_connection_resolver import OrgConnectionNotConfiguredError
    from app.services import warehouse_directory

    settings = Settings()
    try:
        config = _resolve_config(organization_id, settings)
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
    except SnowflakeQueryError as exc:
        raise HTTPException(
            status_code=502,
            detail=_snowflake_failure_detail(
                exc, "Could not check the Snowflake grant."
            ),
        ) from None
    except Exception:  # noqa: BLE001 — user-safe query failure surface
        raise HTTPException(
            status_code=502, detail="Could not check the Snowflake grant."
        ) from None

    checked_at = datetime.now(timezone.utc).isoformat()
    store = get_automated_savings_store()
    if store is not None:
        try:
            store.set_grant_status(organization_id, present, checked_at)
        except AutomatedSavingsStoreError as exc:
            _log_store_failure("set_grant_status", exc)
            raise HTTPException(
                status_code=502, detail="Could not persist the grant status."
            ) from None

    return CheckAccessResponse(
        grant_present=present, grant_checked_at=checked_at, role_name=role_name
    )


@router.get(
    "/{organization_id}/stats/suspensions",
    response_model=SuspensionStatsResponse,
)
def get_suspension_stats(
    organization_id: str,
    days: int = Query(7),
    auth_context: AuthContext = Depends(require_auth_context),
) -> SuspensionStatsResponse:
    require_org_membership(auth_context, organization_id)
    if days not in _ALLOWED_STATS_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"days must be one of {list(_ALLOWED_STATS_DAYS)}.",
        )

    today = _utc_today()
    cache_key = (organization_id, days, today.isoformat())
    cached = _stats_cache.get(cache_key)
    if cached is not None:
        return cached

    store = _require_store()
    try:
        rows = store.daily_suspensions(organization_id, days, today.isoformat())
    except AutomatedSavingsStoreError as exc:
        _log_store_failure("daily_suspensions", exc)
        raise HTTPException(
            status_code=502, detail="Could not load suspension stats."
        ) from None

    warehouses = sorted({row.warehouse_name for row in rows})
    counts_by_day_wh = {
        (row.day, row.warehouse_name): row.suspension_count for row in rows
    }
    start = today - timedelta(days=days - 1)
    buckets = []
    for offset in range(days):
        day_iso = (start + timedelta(days=offset)).isoformat()
        counts = {wh: counts_by_day_wh.get((day_iso, wh), 0) for wh in warehouses}
        buckets.append(SuspensionStatsBucket(day=day_iso, counts=counts))
    response = SuspensionStatsResponse(
        days=days, warehouses=warehouses, buckets=buckets
    )
    _stats_cache.set(cache_key, response)
    return response


@router.get(
    "/{organization_id}/events",
    response_model=SuspensionEventsPageResponse,
)
def list_suspension_events(
    organization_id: str,
    limit: int = Query(25),
    cursor: str | None = Query(None),
    auth_context: AuthContext = Depends(require_auth_context),
) -> SuspensionEventsPageResponse:
    require_org_membership(auth_context, organization_id)
    page_limit = max(1, min(limit, _EVENTS_MAX_LIMIT))
    cursor_created_at: str | None = None
    cursor_id: int | None = None
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)

    store = _require_store()
    try:
        rows = store.list_events(
            organization_id,
            limit=page_limit + 1,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )
    except AutomatedSavingsStoreError as exc:
        _log_store_failure("list_events", exc)
        raise HTTPException(
            status_code=502, detail="Could not load suspension events."
        ) from None

    has_more = len(rows) > page_limit
    page = rows[:page_limit]
    next_cursor = (
        _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
    )
    return SuspensionEventsPageResponse(
        events=[
            SuspensionEventResponse(
                id=str(row.id),
                created_at=row.created_at,
                warehouse_name=row.warehouse_name,
                action=row.action,
                reason=row.reason,
                observed_started_clusters=row.observed_started_clusters,
                observed_resumed_on=row.observed_resumed_on,
                observed_at=row.observed_at,
            )
            for row in page
        ],
        next_cursor=next_cursor,
    )
