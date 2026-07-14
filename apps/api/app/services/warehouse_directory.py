from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from greysight_connect.snowflake_client import (
    SnowflakeConnectionConfig,
    execute_metadata_query,
)

# Module-scope import above so `execute_metadata_query` is a module attribute
# that tests can monkeypatch (`warehouse_directory.execute_metadata_query`).

_VALID_ROLE_NAME = re.compile(r'^[A-Za-z0-9_$ "]+$')

WarehouseStatus = Literal["idle", "transitioning", "unsupported"]


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
    auto_suspend: int | None
    quiescing: int | None
    enabled: bool
    status: WarehouseStatus


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
        raise ValueError(
            f"role_name is not a valid Snowflake identifier: {role_name!r}"
        )
    return role_name.replace('"', '""')


def join_warehouse_view(
    live: list[dict[str, Any]],
    enrollments: list[Any],
) -> list[WarehouseView]:
    enrollment_by_name = {
        str(getattr(row, "warehouse_name", "")): row for row in enrollments
    }
    views: list[WarehouseView] = []
    for row in live:
        name = str(row.get("name", ""))
        warehouse_type = row.get("type")
        supported = str(warehouse_type).upper() == "STANDARD"
        auto_resume_raw = row.get("auto_resume")
        auto_resume_ok = str(auto_resume_raw).strip().lower() == "true"
        enrollment = enrollment_by_name.get(name)

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
        state = row.get("state")
        auto_suspend = _parse_nonnegative_int(row.get("auto_suspend"))
        quiescing = _parse_quiescing(row.get("quiescing"))

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
                auto_suspend=auto_suspend,
                quiescing=quiescing,
                enabled=enabled,
                status=_compute_status(
                    supported=supported,
                    state=state,
                    quiescing=quiescing,
                ),
            )
        )
    return views


def _compute_status(
    *,
    supported: bool,
    state: str | None,
    quiescing: int | None,
) -> WarehouseStatus:
    if not supported:
        return "unsupported"
    if str(state or "").upper() in ("SUSPENDING", "RESUMING") or (
        quiescing is not None and quiescing > 0
    ):
        return "transitioning"
    return "idle"


def _parse_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed < 0 or (not isinstance(value, str) and value != parsed):
        return None
    return parsed


def _parse_quiescing(value: Any) -> int | None:
    # Observed SHOW WAREHOUSES contract: Snowflake encodes idle quiescing as the
    # exact empty string, while a draining warehouse returns a numeric value.
    if value == "":
        return 0
    return _parse_nonnegative_int(value)
