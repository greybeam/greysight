from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class WarehouseSnapshot:
    name: str
    state: str
    type: str
    size: str | None
    started_clusters: int
    min_cluster_count: int
    max_cluster_count: int
    running: int
    queued: int
    auto_suspend: int | None
    auto_resume: bool
    resumed_on: datetime | None
    created_on: datetime | None


def _ci_get(row: dict, key: str, default=None):
    if key in row:
        return row[key]
    lowered = {str(k).lower(): v for k, v in row.items()}
    return lowered.get(key.lower(), default)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1", "y")


def _coerce_ts(value) -> datetime | None:
    """Coerce a SHOW WAREHOUSES timestamp (str | datetime | None) to tz-aware UTC.
    The connector may return these as strings or tz-naive datetimes (finding #3)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    text = str(value).strip()
    # Snowflake SHOW timestamps look like "2026-07-12 11:58:30.000 -0000"
    # (exact format confirmed by the Task 0 spike). Try fromisoformat first, then fallback formats.
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unparseable SHOW WAREHOUSES timestamp: {value!r}")


def parse_warehouses(rows: list[dict], *, now: datetime) -> list[WarehouseSnapshot]:
    snapshots: list[WarehouseSnapshot] = []
    for row in rows:
        auto_suspend_raw = _ci_get(row, "auto_suspend")
        # `started_clusters`/`min_cluster_count`/`max_cluster_count` only exist on
        # Enterprise+ editions; SHOW WAREHOUSES omits them entirely on Standard edition
        # (Task 0 spike, 2026-07-12). A Standard-edition warehouse is always single-cluster
        # and safe to suspend, so when the column is ABSENT default started_clusters to the
        # resolved min_cluster_count (not a hardcoded 0) so should_force_suspend's
        # `started_clusters == min_cluster_count` gate can pass. When the column IS present
        # (Enterprise+), use its real value so a scaled-up multi-cluster warehouse stays
        # protected.
        min_cluster_count = _as_int(_ci_get(row, "min_cluster_count"), 1)
        started_clusters_raw = _ci_get(row, "started_clusters")
        started_clusters = (
            min_cluster_count
            if started_clusters_raw is None
            else _as_int(started_clusters_raw, min_cluster_count)
        )
        snapshots.append(
            WarehouseSnapshot(
                name=str(_ci_get(row, "name", "")),
                state=str(_ci_get(row, "state", "")),
                type=str(_ci_get(row, "type", "")),
                size=_ci_get(row, "size"),
                started_clusters=started_clusters,
                min_cluster_count=min_cluster_count,
                max_cluster_count=_as_int(_ci_get(row, "max_cluster_count"), 1),
                running=_as_int(_ci_get(row, "running"), 0),
                queued=_as_int(_ci_get(row, "queued"), 0),
                auto_suspend=None if auto_suspend_raw in (None, "") else _as_int(auto_suspend_raw, 0),
                auto_resume=_as_bool(_ci_get(row, "auto_resume", False)),
                resumed_on=_coerce_ts(_ci_get(row, "resumed_on")),
                created_on=_coerce_ts(_ci_get(row, "created_on")),
            )
        )
    return snapshots


def uptime_seconds(snapshot: WarehouseSnapshot, *, now: datetime) -> float | None:
    if snapshot.resumed_on is None:
        return None
    return (now - snapshot.resumed_on).total_seconds()
