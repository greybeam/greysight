from datetime import date

from app.services.ai_consumption import (
    AI_CONSUMPTION_BRANCHES,
    fetch_ai_consumption_daily,
)
from app.services.snowflake_client import (
    SnowflakeObjectUnavailableError,
    SnowflakeQueryError,
)


def _ok_rows(branch_id):
    return [
        {
            "usage_date": date(2026, 6, 1),
            "service_type": "CORTEX_AGENTS",
            "consumption_type": branch_id,
            "credits_used": 1.0,
        }
    ]


def test_runs_every_branch_and_unions_rows():
    calls = []

    def execute(sql, params):
        calls.append(params)
        return _ok_rows("B")

    rows, skipped = fetch_ai_consumption_daily(execute, window_days=30)

    assert skipped == []
    assert len(calls) == len(AI_CONSUMPTION_BRANCHES)
    # window_days is threaded to every branch
    assert all(p == {"window_days": 30} for p in calls)
    assert len(rows) == len(AI_CONSUMPTION_BRANCHES)


def test_skips_branch_when_table_unavailable():
    missing = AI_CONSUMPTION_BRANCHES[0].consumption_type

    def execute(sql, params):
        if AI_CONSUMPTION_BRANCHES[0].table in sql:
            raise SnowflakeObjectUnavailableError("missing")
        return _ok_rows("OK")

    rows, skipped = fetch_ai_consumption_daily(execute, window_days=30)

    assert AI_CONSUMPTION_BRANCHES[0].id in skipped
    assert all(row["consumption_type"] != missing for row in rows)


def test_real_error_propagates():
    def execute(sql, params):
        raise SnowflakeQueryError("boom")

    try:
        fetch_ai_consumption_daily(execute, window_days=30)
        assert False, "expected SnowflakeQueryError"
    except SnowflakeQueryError:
        pass


def test_every_branch_sql_has_window_filter():
    for branch in AI_CONSUMPTION_BRANCHES:
        assert "%(window_days)s" in branch.sql
        assert "usage_date" in branch.sql.lower()
