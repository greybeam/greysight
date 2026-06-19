import threading

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

    def execute(sql, params):
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
    # Barrier-based concurrency proof (no wall-clock race): each of the 4 jobs
    # must enter execute() before any is allowed to proceed. A sequential runner
    # would run jobs one at a time, so the barrier would never trip and wait()
    # would raise BrokenBarrierError. The generous timeout keeps this robust on
    # slow CI while still guaranteeing true parallelism.
    query_concurrency.configure(8)
    jobs = [SourceJob(f"k{i}", "s", {}) for i in range(4)]
    barrier = threading.Barrier(len(jobs))

    def execute(sql, params):
        barrier.wait(timeout=5)
        return []

    outcomes = run_sources_parallel(jobs, execute)
    assert all(o.available for o in outcomes.values())
    assert not barrier.broken


def test_on_complete_called_per_job():
    query_concurrency.configure(8)
    seen = []
    jobs = [SourceJob("a", "s", {}), SourceJob("b", "s", {})]
    run_sources_parallel(jobs, lambda s, p: [], on_complete=lambda o: seen.append(o.key))
    assert sorted(seen) == ["a", "b"]
