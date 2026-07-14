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
    started_clusters: int | None
    min_cluster_count: int | None
    max_cluster_count: int | None
    running: int
    queued: int
    auto_suspend: int | None
    auto_resume: bool
    resumed_on: datetime | None
    created_on: datetime | None
    quiescing: int = 0
    activity_valid: bool = True
    quiescing_valid: bool = False


def _ci_get(row: dict, key: str, default=None):
    if key in row:
        return row[key]
    lowered = {str(k).lower(): v for k, v in row.items()}
    return lowered.get(key.lower(), default)


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


def _optional_int(value, *, minimum: int) -> int | None:
    parsed, valid = _validated_int(value, minimum=minimum)
    return parsed if valid else None


def _validated_quiescing(value) -> tuple[int, bool]:
    # Observed SHOW WAREHOUSES contract: Snowflake encodes idle quiescing as the
    # exact empty string, while a draining warehouse returns a numeric value.
    if value == "":
        return 0, True
    return _validated_int(value, minimum=0)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1", "y")


def _coerce_ts(value) -> datetime | None:
    """Coerce a SHOW WAREHOUSES timestamp (str | datetime | None) to tz-aware UTC.
    Values without an explicit timezone fail closed."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    # Snowflake SHOW timestamps look like "2026-07-12 11:58:30.000 -0000"
    # (exact format confirmed by the Task 0 spike). Try fromisoformat first, then fallback formats.
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f %z",
        "%Y-%m-%d %H:%M:%S %z",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def parse_warehouses(rows: list[dict], *, now: datetime) -> list[WarehouseSnapshot]:
    snapshots: list[WarehouseSnapshot] = []
    for row in rows:
        auto_suspend_raw = _ci_get(row, "auto_suspend")
        running, running_valid = _validated_int(_ci_get(row, "running"), minimum=0)
        queued, queued_valid = _validated_int(_ci_get(row, "queued"), minimum=0)
        quiescing, quiescing_valid = _validated_quiescing(_ci_get(row, "quiescing"))
        snapshots.append(
            WarehouseSnapshot(
                name=str(_ci_get(row, "name", "")),
                state=str(_ci_get(row, "state", "")),
                type=str(_ci_get(row, "type", "")),
                size=_ci_get(row, "size"),
                started_clusters=_optional_int(
                    _ci_get(row, "started_clusters"), minimum=0
                ),
                min_cluster_count=_optional_int(
                    _ci_get(row, "min_cluster_count"), minimum=1
                ),
                max_cluster_count=_optional_int(
                    _ci_get(row, "max_cluster_count"), minimum=1
                ),
                running=running,
                queued=queued,
                quiescing=quiescing,
                auto_suspend=_optional_int(auto_suspend_raw, minimum=0),
                auto_resume=_as_bool(_ci_get(row, "auto_resume", False)),
                resumed_on=_coerce_ts(_ci_get(row, "resumed_on")),
                created_on=_coerce_ts(_ci_get(row, "created_on")),
                activity_valid=running_valid and queued_valid,
                quiescing_valid=quiescing_valid,
            )
        )
    return snapshots


def uptime_seconds(snapshot: WarehouseSnapshot, *, now: datetime) -> float | None:
    if snapshot.resumed_on is None:
        return None
    return (now - snapshot.resumed_on).total_seconds()
