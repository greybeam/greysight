from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
    activity_valid: bool = True
    cluster_counts_valid: bool = True


def _ci_get(row: dict, key: str, default=None):
    if key in row:
        return row[key]
    lowered = {str(k).lower(): v for k, v in row.items()}
    return lowered.get(key.lower(), default)


def _ci_present(row: dict, key: str) -> bool:
    """True if ``key`` exists as a column in ``row`` (case-insensitive),
    regardless of whether its value is null. Distinguishes an ABSENT column
    (Standard-edition SHOW WAREHOUSES omits cluster columns entirely) from a
    PRESENT-but-null/malformed one (finding #4) — only the former is safe to
    default."""
    if key in row:
        return True
    return key.lower() in {str(k).lower() for k in row}


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _validated_int(value, *, minimum: int) -> tuple[int, bool]:
    if isinstance(value, bool):
        return minimum, False
    try:
        decimal_value = Decimal(str(value).strip())
        if not decimal_value.is_finite():
            return minimum, False
        integral = decimal_value.to_integral_value()
        if decimal_value != integral:
            return minimum, False
        parsed = int(integral)
    except (InvalidOperation, OverflowError, TypeError, ValueError):
        return minimum, False
    return parsed, parsed >= minimum


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
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    text = str(value).strip()
    # Snowflake SHOW timestamps look like "2026-07-12 11:58:30.000 -0000"
    # (exact format confirmed by the Task 0 spike). Try fromisoformat first, then fallback formats.
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
            return (
                parsed.replace(tzinfo=timezone.utc)
                if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc)
            )
        except ValueError:
            continue
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f %z",
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return (
                parsed.replace(tzinfo=timezone.utc)
                if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc)
            )
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
        # and safe to suspend, so when ALL THREE cluster columns are ABSENT default
        # started_clusters to the resolved min_cluster_count (not a hardcoded 0) so
        # should_force_suspend's `started_clusters == min_cluster_count` gate can pass.
        #
        # A cluster column that is PRESENT but null/unparseable (e.g. an Enterprise+
        # warehouse with a malformed row) must NOT be treated the same as absent —
        # doing so would default started_clusters to min_cluster_count and could pass
        # the safety gate for a scaled-up multi-cluster warehouse (finding #4). In that
        # case fail closed with cluster_counts_valid=False. Activity fields use the
        # same validity approach so missing/malformed values cannot look idle.
        cluster_cols_absent = (
            not _ci_present(row, "started_clusters")
            and not _ci_present(row, "min_cluster_count")
            and not _ci_present(row, "max_cluster_count")
        )
        if cluster_cols_absent:
            started_clusters = min_cluster_count = max_cluster_count = 1
            cluster_counts_valid = True
        else:
            started_clusters, started_valid = _validated_int(
                _ci_get(row, "started_clusters"), minimum=0
            )
            min_cluster_count, min_valid = _validated_int(
                _ci_get(row, "min_cluster_count"), minimum=1
            )
            max_cluster_count, max_valid = _validated_int(
                _ci_get(row, "max_cluster_count"), minimum=1
            )
            cluster_counts_valid = (
                started_valid
                and min_valid
                and max_valid
                and min_cluster_count <= max_cluster_count
                and started_clusters <= max_cluster_count
            )
        running, running_valid = _validated_int(_ci_get(row, "running"), minimum=0)
        queued, queued_valid = _validated_int(_ci_get(row, "queued"), minimum=0)
        snapshots.append(
            WarehouseSnapshot(
                name=str(_ci_get(row, "name", "")),
                state=str(_ci_get(row, "state", "")),
                type=str(_ci_get(row, "type", "")),
                size=_ci_get(row, "size"),
                started_clusters=started_clusters,
                min_cluster_count=min_cluster_count,
                max_cluster_count=max_cluster_count,
                running=running,
                queued=queued,
                auto_suspend=None
                if auto_suspend_raw in (None, "")
                else _as_int(auto_suspend_raw, 0),
                auto_resume=_as_bool(_ci_get(row, "auto_resume", False)),
                resumed_on=_coerce_ts(_ci_get(row, "resumed_on")),
                created_on=_coerce_ts(_ci_get(row, "created_on")),
                activity_valid=running_valid and queued_valid,
                cluster_counts_valid=cluster_counts_valid,
            )
        )
    return snapshots


def uptime_seconds(snapshot: WarehouseSnapshot, *, now: datetime) -> float | None:
    if snapshot.resumed_on is None:
        return None
    return (now - snapshot.resumed_on).total_seconds()
