from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.query_concurrency import get_query_executor
from app.services.snowflake_client import SnowflakeQueryError

ExecuteFn = Callable[[str, dict[str, Any]], list[dict[str, Any]]]


@dataclass(frozen=True)
class SourceJob:
    key: str
    sql: str
    bind_params: dict[str, Any]


@dataclass(frozen=True)
class SourceOutcome:
    key: str
    rows: list[dict[str, Any]] | None
    available: bool


def run_sources_parallel(
    jobs: list[SourceJob],
    execute: ExecuteFn,
    *,
    on_complete: Callable[[SourceOutcome], None] | None = None,
) -> dict[str, SourceOutcome]:
    """Execute each job concurrently on the process-wide query executor.

    A SnowflakeQueryError (incl. the object-unavailable subclass) marks that
    single source unavailable; it never aborts the others. Availability comes
    from the exception type, not row count.

    ``on_complete`` is invoked from WORKER THREADS (one per job, as each job
    settles) — it MUST be thread-safe. Callers should only touch lock-guarded
    state inside it and avoid assuming any particular invocation order.
    """

    def _run(job: SourceJob) -> SourceOutcome:
        try:
            rows = execute(job.sql, job.bind_params)
            outcome = SourceOutcome(key=job.key, rows=rows, available=True)
        except SnowflakeQueryError:
            outcome = SourceOutcome(key=job.key, rows=None, available=False)
        if on_complete is not None:
            on_complete(outcome)
        return outcome

    if not jobs:
        return {}
    outcomes = list(get_query_executor().map(_run, jobs))
    return {o.key: o for o in outcomes}
