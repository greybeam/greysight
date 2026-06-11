from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.services.dashboard_view_models import DashboardViewRange

DEFAULT_VIEW_WINDOW_DAYS = 30
SUPPORTED_VIEW_WINDOW_DAYS = frozenset({7, 30, 90})


@dataclass(frozen=True)
class DashboardRangeOutOfBoundsError(ValueError):
    source_start_date: date
    source_end_date: date

    def __str__(self) -> str:
        return "Requested dashboard range is outside stored source bounds."


def window_start_for(through_date: date, window_days: int) -> date:
    return through_date - timedelta(days=window_days - 1)


def projection_range_for(through_date: date) -> tuple[date, date]:
    return window_start_for(through_date, DEFAULT_VIEW_WINDOW_DAYS), through_date


def resolve_dashboard_view_range(
    *,
    through_date: date,
    source_start_date: date,
    source_end_date: date,
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DashboardViewRange:
    has_relative = window_days is not None
    has_custom = start_date is not None or end_date is not None
    if has_relative and has_custom:
        raise ValueError("Dashboard view accepts exactly one range mode.")
    if has_custom and (start_date is None or end_date is None):
        raise ValueError("Custom dashboard range requires start_date and end_date.")

    if not has_relative and not has_custom:
        window_days = DEFAULT_VIEW_WINDOW_DAYS
        has_relative = True

    if has_relative:
        if window_days not in SUPPORTED_VIEW_WINDOW_DAYS:
            raise ValueError("Unsupported dashboard window_days.")
        effective_start = window_start_for(through_date, int(window_days))
        effective_end = through_date
        mode = "relative"
        effective_window_days = int(window_days)
    else:
        assert start_date is not None
        assert end_date is not None
        if start_date > end_date:
            raise ValueError(
                "Custom dashboard range start_date must be before end_date."
            )
        effective_start = start_date
        effective_end = min(end_date, through_date)
        mode = "custom"
        effective_window_days = None

    if effective_start < source_start_date or effective_end > source_end_date:
        raise DashboardRangeOutOfBoundsError(
            source_start_date=source_start_date,
            source_end_date=source_end_date,
        )
    if effective_start > effective_end:
        raise DashboardRangeOutOfBoundsError(
            source_start_date=source_start_date,
            source_end_date=source_end_date,
        )

    return DashboardViewRange(
        mode=mode,
        window_days=effective_window_days,
        start_date=effective_start,
        end_date=effective_end,
    )
