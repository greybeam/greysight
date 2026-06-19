import time
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


def test_real_query_error_skips_branch():
    """Any SnowflakeQueryError (not just object-unavailable) skips that branch.

    The parallel runner collapses all SnowflakeQueryError subclasses to
    available=False rather than propagating them, so a hard query error on one
    branch should skip that branch rather than failing the entire AI source.
    """

    def execute(sql, params):
        raise SnowflakeQueryError("boom")

    rows, skipped = fetch_ai_consumption_daily(execute, window_days=30)

    assert len(skipped) == len(AI_CONSUMPTION_BRANCHES)
    assert rows == []


def test_ai_branches_run_in_parallel_and_skip_unavailable():
    def execute(sql, params):
        time.sleep(0.1)
        if "cortex_search" in sql:  # one unavailable branch
            raise SnowflakeObjectUnavailableError("nope")
        return [{"row": 1}]

    start = time.monotonic()
    rows, skipped = fetch_ai_consumption_daily(execute, window_days=30)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5  # 10 branches * 0.1 serial = 1s
    assert any("cortex_search" in s for s in skipped)
    assert len(rows) > 0


def test_every_branch_sql_has_window_filter():
    for branch in AI_CONSUMPTION_BRANCHES:
        assert "%(window_days)s" in branch.sql
        assert "usage_date" in branch.sql.lower()
