import threading
import time
from datetime import date

import pytest

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


def test_real_query_error_propagates():
    """A non-object-unavailable SnowflakeQueryError must PROPAGATE, not skip.

    Connection failures, timeouts, and SQL regressions raise the base
    SnowflakeQueryError. These are real failures of the deferred AI source and
    must surface to the caller (which fails the source) rather than masquerading
    as "this account doesn't use that AI feature".
    """

    def execute(sql, params):
        raise SnowflakeQueryError("boom")

    with pytest.raises(SnowflakeQueryError):
        fetch_ai_consumption_daily(execute, window_days=30)


def test_ai_branches_run_in_parallel_and_skip_unavailable():
    # Peak-concurrency proof instead of a tight wall-clock bound (latent CI
    # flake): track how many branches are simultaneously in-flight. A sequential
    # runner would never exceed peak==1; true parallelism drives it above 1.
    lock = threading.Lock()
    active = 0
    peak = 0

    def execute(sql, params):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)  # widen the overlap window without racing a deadline
        finally:
            with lock:
                active -= 1
        if "cortex_search" in sql:  # one unavailable branch
            raise SnowflakeObjectUnavailableError("nope")
        return [{"row": 1}]

    rows, skipped = fetch_ai_consumption_daily(execute, window_days=30)
    assert peak > 1  # branches genuinely overlapped (ran concurrently)
    assert any("cortex_search" in s for s in skipped)
    assert len(rows) > 0


def test_every_branch_sql_has_window_filter():
    for branch in AI_CONSUMPTION_BRANCHES:
        assert "%(window_days)s" in branch.sql
        assert "usage_date" in branch.sql.lower()
