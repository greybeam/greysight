import time
from app.services.parallel_source_runner import SourceJob, run_sources_parallel
from app.services.snowflake_client import SnowflakeQueryError
from app.services import query_concurrency


def test_runs_all_jobs_and_collects_rows():
    query_concurrency.configure(8)
    jobs = [SourceJob(f"k{i}", f"sql{i}", {"window_days": 100}) for i in range(4)]

    def execute(sql, params):
        return [{"sql": sql}]

    outcomes = run_sources_parallel(jobs, execute)
    assert set(outcomes) == {"k0", "k1", "k2", "k3"}
    assert outcomes["k0"].available is True
    assert outcomes["k0"].rows == [{"sql": "sql0"}]


def test_unavailable_source_does_not_fail_run():
    query_concurrency.configure(8)
    jobs = [SourceJob("ok", "s", {}), SourceJob("bad", "s", {})]

    def execute(sql, params):  # noqa: F811
        if params.get("fail"):
            raise SnowflakeQueryError("boom")
        return []

    jobs = [SourceJob("ok", "s", {}), SourceJob("bad", "s", {"fail": True})]
    outcomes = run_sources_parallel(jobs, execute)
    assert outcomes["ok"].available is True
    assert outcomes["ok"].rows == []          # zero rows != unavailable
    assert outcomes["bad"].available is False
    assert outcomes["bad"].rows is None


def test_runs_concurrently():
    query_concurrency.configure(8)
    jobs = [SourceJob(f"k{i}", "s", {}) for i in range(4)]

    def execute(sql, params):
        time.sleep(0.2)
        return []

    start = time.monotonic()
    run_sources_parallel(jobs, execute)
    # 4 jobs * 0.2s sequential = 0.8s; parallel should be well under 0.5s
    assert time.monotonic() - start < 0.5


def test_on_complete_called_per_job():
    query_concurrency.configure(8)
    seen = []
    jobs = [SourceJob("a", "s", {}), SourceJob("b", "s", {})]
    run_sources_parallel(jobs, lambda s, p: [], on_complete=lambda o: seen.append(o.key))
    assert sorted(seen) == ["a", "b"]
