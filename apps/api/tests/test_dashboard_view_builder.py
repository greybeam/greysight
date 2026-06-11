from datetime import date
from uuid import UUID

import pytest

from app.models import DashboardRun
from app.services.dashboard_view_builder import (
    DEFAULT_VIEW_WINDOW_DAYS,
    DashboardRangeOutOfBoundsError,
    resolve_dashboard_view_range,
)
from app.services.dashboard_view_models import DashboardViewRange


RUN = DashboardRun(
    id="00000000-0000-4000-8000-000000000001",
    source="snowflake",
    status="completed",
    window_days=100,
    organization_id=UUID("00000000-0000-4000-8000-000000000001"),
)


def test_resolves_default_relative_range_from_through_date() -> None:
    view_range = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
    )

    assert DEFAULT_VIEW_WINDOW_DAYS == 30
    assert view_range == DashboardViewRange(
        mode="relative",
        window_days=30,
        start_date=date(2026, 5, 10),
        end_date=date(2026, 6, 8),
    )


def test_resolves_supported_relative_windows_from_through_date() -> None:
    seven = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
        window_days=7,
    )
    ninety = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
        window_days=90,
    )

    assert seven.start_date == date(2026, 6, 2)
    assert seven.end_date == date(2026, 6, 8)
    assert ninety.start_date == date(2026, 3, 11)
    assert ninety.end_date == date(2026, 6, 8)


def test_clamps_custom_end_date_to_through_date() -> None:
    view_range = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 11),
    )

    assert view_range == DashboardViewRange(
        mode="custom",
        window_days=None,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 8),
    )


def test_rejects_ranges_older_than_stored_source_bounds() -> None:
    with pytest.raises(DashboardRangeOutOfBoundsError) as exc_info:
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            start_date=date(2026, 2, 28),
            end_date=date(2026, 3, 5),
        )

    assert exc_info.value.source_start_date == date(2026, 3, 1)
    assert exc_info.value.source_end_date == date(2026, 6, 8)


def test_rejects_ambiguous_relative_and_custom_range() -> None:
    with pytest.raises(ValueError, match="exactly one range mode"):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            window_days=7,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 8),
        )
