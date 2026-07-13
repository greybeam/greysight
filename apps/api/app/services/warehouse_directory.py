from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from greysight_connect.snowflake_client import (
    SnowflakeConnectionConfig,
    execute_metadata_query,
)

# Module-scope import above so `execute_metadata_query` is a module attribute
# that tests can monkeypatch (`warehouse_directory.execute_metadata_query`).

_VALID_ROLE_NAME = re.compile(r'^[A-Za-z0-9_$ "]+$')


@dataclass(frozen=True)
class WarehouseView:
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


def list_live_warehouses(
    config: SnowflakeConnectionConfig | None = None,
) -> list[dict[str, Any]]:
    return execute_metadata_query("SHOW WAREHOUSES", config=config)


def check_manage_warehouses_grant(
    config: SnowflakeConnectionConfig | None,
    role_name: str,
) -> bool:
    escaped = _escape_role_identifier(role_name)
    rows = execute_metadata_query(f'SHOW GRANTS TO ROLE "{escaped}"', config=config)
    if not isinstance(rows, list):
        return False
    for row in rows:
        for key, value in row.items():
            if str(key).lower() == "privilege" and str(value).strip().upper() == (
                "MANAGE WAREHOUSES"
            ):
                return True
    return False


def _escape_role_identifier(role_name: str) -> str:
    if not isinstance(role_name, str) or not role_name.strip():
        raise ValueError("role_name must be a non-empty string")
    if not _VALID_ROLE_NAME.fullmatch(role_name):
        raise ValueError(f"role_name is not a valid Snowflake identifier: {role_name!r}")
    return role_name.replace('"', '""')


def join_warehouse_view(
    live: list[dict[str, Any]],
    enrollments: list[Any],
    *,
    now: datetime | None = None,
) -> list[WarehouseView]:
    # `now` is injectable for deterministic tests; default to wall-clock UTC.
    now = now or datetime.now(timezone.utc)
    enrollment_by_name = {
        str(getattr(row, "warehouse_name", "")).upper(): row for row in enrollments
    }
    views: list[WarehouseView] = []
    for row in live:
        name = str(row.get("name", ""))
        warehouse_type = row.get("type")
        supported = str(warehouse_type).upper() == "STANDARD"
        auto_resume_raw = row.get("auto_resume")
        auto_resume_ok = str(auto_resume_raw).strip().lower() == "true"
        enrollment = enrollment_by_name.get(name.upper())

        # `min_cluster_count`/`started_clusters`/`max_cluster_count` are Enterprise+-only
        # columns; SHOW WAREHOUSES omits them on Standard edition (Task 0 spike,
        # 2026-07-12), which would otherwise render the UI's "# clusters" column blank.
        # A Standard-edition warehouse is always single-cluster, so default the absent
        # started/min fields to 1 while preserving real values when present.
        min_cluster_count = row.get("min_cluster_count")
        if min_cluster_count is None:
            min_cluster_count = 1
        started_clusters = row.get("started_clusters")
        if started_clusters is None:
            started_clusters = 1

        enabled = bool(getattr(enrollment, "enabled", False))
        managed_default = getattr(enrollment, "managed_auto_suspend", None)
        stored_default = getattr(enrollment, "stored_default_auto_suspend", None)
        drift_state = getattr(enrollment, "drift_state", "ok")
        drifted_value = getattr(enrollment, "drifted_value", None)
        cooldown_ts = getattr(enrollment, "cooldown_ts", None)
        state = row.get("state")

        views.append(
            WarehouseView(
                name=name,
                size=row.get("size"),
                state=state,
                type=warehouse_type,
                supported=supported,
                min_cluster_count=min_cluster_count,
                max_cluster_count=row.get("max_cluster_count"),
                started_clusters=started_clusters,
                auto_resume_ok=auto_resume_ok,
                managed_default=managed_default,
                stored_default=stored_default,
                enabled=enabled,
                drift_state=drift_state,
                drifted_value=drifted_value,
                cooldown_ts=cooldown_ts,
                status=_compute_status(
                    supported=supported,
                    drift_state=drift_state,
                    cooldown_ts=cooldown_ts,
                    state=state,
                    now=now,
                ),
            )
        )
    return views


def _compute_status(
    *,
    supported: bool,
    drift_state: str,
    cooldown_ts: str | None,
    state: str | None,
    now: datetime,
) -> str:
    if not supported:
        return "unsupported"
    if drift_state == "drifted":
        return "drifted"
    if _cooldown_active(cooldown_ts, now):
        return "in_cooldown"
    if str(state or "").upper() in ("SUSPENDING", "RESUMING"):
        return "mid_suspend"
    return "idle"


def _cooldown_active(cooldown_ts: str | None, now: datetime) -> bool:
    """True only while the anti-thrash cooldown is still in the future.

    The worker writes `cooldown_ts = restore_time + cooldown_seconds` and never
    nulls it, so a bare truthiness check would pin the status to `in_cooldown`
    forever after the first restore. Mirror the worker's own expiry check
    (engine.py: `cooldown_ts is not None and cooldown_ts > now`): parse the
    stored timestamp and compare it to `now`. An unparseable value is treated as
    inactive rather than sticking the badge on."""
    if not cooldown_ts:
        return False
    parsed = _coerce_utc(cooldown_ts)
    if parsed is None:
        return False
    return parsed > now


def _coerce_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
