from datetime import date

from app.services.dataset_bounds import OTHER_USER_NAME, bound_user_compute_rows


def _row(
    day: int,
    user: str,
    warehouse: str,
    credits: float,
) -> dict[str, object]:
    return {
        "usage_date": date(2026, 6, day),
        "user_name": user,
        "warehouse_name": warehouse,
        "credits_attributed_compute": credits,
    }


def test_bound_user_compute_rows_keeps_all_rows_within_limit() -> None:
    rows = [_row(1, "A", "WH1", 5.0), _row(2, "B", "WH1", 3.0)]

    assert bound_user_compute_rows(rows, top_n=100) == rows


def test_bound_user_compute_rows_rolls_tail_into_daily_warehouse_other_rows() -> None:
    rows = [
        _row(1, "HEAVY", "WH1", 100.0),
        _row(1, "TAIL_1", "WH1", 2.0),
        _row(2, "TAIL_1", "WH2", 4.0),
        _row(1, "TAIL_2", "WH1", 3.0),
        _row(2, "HEAVY", "WH2", 1.0),
    ]

    bounded = bound_user_compute_rows(rows, top_n=1)

    assert bounded == [
        _row(1, "HEAVY", "WH1", 100.0),
        _row(2, "HEAVY", "WH2", 1.0),
        _row(1, OTHER_USER_NAME, "WH1", 5.0),
        _row(2, OTHER_USER_NAME, "WH2", 4.0),
    ]


def test_bound_user_compute_rows_breaks_top_user_ties_by_name() -> None:
    rows = [
        _row(1, "B", "WH1", 5.0),
        _row(1, "A", "WH1", 5.0),
    ]

    assert bound_user_compute_rows(rows, top_n=1) == [
        _row(1, "A", "WH1", 5.0),
        _row(1, OTHER_USER_NAME, "WH1", 5.0),
    ]
