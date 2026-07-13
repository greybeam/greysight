from __future__ import annotations

import re
from dataclasses import dataclass
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
) -> list[WarehouseView]:
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
                min_cluster_count=row.get("min_cluster_count"),
                max_cluster_count=row.get("max_cluster_count"),
                started_clusters=row.get("started_clusters"),
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
) -> str:
    if not supported:
        return "unsupported"
    if drift_state == "drifted":
        return "drifted"
    if cooldown_ts:
        return "in_cooldown"
    if str(state or "").upper() in ("SUSPENDING", "RESUMING"):
        return "mid_suspend"
    return "idle"
