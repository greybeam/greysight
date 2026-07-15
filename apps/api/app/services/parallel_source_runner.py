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
    user_safe_message: str | None = None


def run_sources_parallel(
    jobs: list[SourceJob],
    execute: ExecuteFn,
    *,
    on_complete: Callable[[SourceOutcome], None] | None = None,
    unavailable_exc: type[BaseException]
    | tuple[type[BaseException], ...] = SnowflakeQueryError,
) -> dict[str, SourceOutcome]:
    """Execute each job concurrently on the process-wide query executor.

    Only exceptions matching ``unavailable_exc`` mark that single source
    unavailable (``available=False``); they never abort the others. Availability
    comes from the exception type, not row count. ANY OTHER exception PROPAGATES
    out of this function — it surfaces when the worker results are consumed
    (``list(get_query_executor().map(...))``), so the caller must be prepared to
    handle (or deliberately fail on) a propagated error.

    ``unavailable_exc`` defaults to the base ``SnowflakeQueryError`` (so a
    queried object that is missing/unauthorized OR a real query failure both
    degrade to unavailable — the right behavior for main-run source groups).
    Callers that must distinguish a legitimately-missing object from a real
    failure can narrow it (e.g. ``SnowflakeObjectUnavailableError``) so that only
    the missing-object case is skipped and real failures propagate.

    ``on_complete`` is invoked from WORKER THREADS (one per job, as each job
    settles) — it MUST be thread-safe. Callers should only touch lock-guarded
    state inside it and avoid assuming any particular invocation order. It is
    NOT invoked for a job whose exception propagates.
    """

    def _run(job: SourceJob) -> SourceOutcome:
        try:
            rows = execute(job.sql, job.bind_params)
            outcome = SourceOutcome(key=job.key, rows=rows, available=True)
        except unavailable_exc as exc:
            outcome = SourceOutcome(
                key=job.key,
                rows=None,
                available=False,
                user_safe_message=getattr(exc, "user_safe_message", None),
            )
        if on_complete is not None:
            on_complete(outcome)
        return outcome

    if not jobs:
        return {}
    outcomes = list(get_query_executor().map(_run, jobs))
    return {o.key: o for o in outcomes}
