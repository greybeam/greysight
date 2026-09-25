"""DuckDB-backed implementation of the auto-savings ``Store`` protocol.

Single-tenant by design. Every row is keyed by ``LOCAL_ORG_ID`` (a stable
literal), because in local mode there is exactly one user and one Snowflake
account. Multi-tenancy stays with ``SupabaseStore`` — RLS lives there.

Threading model
---------------
The worker runs blocking store calls on a ``ThreadPoolExecutor``. A DuckDB
``DuckDBPyConnection`` is NOT thread-safe on its own; the documented
patterns are (a) call ``.cursor()`` per thread on a shared connection, or
(b) serialize calls with a lock. We take (b): a per-instance
``threading.Lock`` guards every store method. Serialization is fine here —
the auto-savings store is off the hot path (a few writes per tenant per
minute, not per query). Correctness matters more than throughput.

Concurrency semantics match ``SupabaseStore``: authorization is a strict
equality check on the versioned ``(warehouse_created_on, updated_at)``
tuple executed inside one transaction. Two concurrent enrollments cannot
cause a suspend to be authorized against stale identity — the row either
matches or it does not.

Why DuckDB, not SQLite
----------------------
The codebase commits to a "DuckDB refactor" in four separate places
(``section-filters.ts``, ``currency-format.ts``). Landing DuckDB as the
worker's local backend puts the first stake in that roadmap and shares
one embedded file with the follow-up local dashboard-mode PR.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from auto_savings.store import (
    EnrollmentRow,
    SavingsEvent,
    StoreError,
)

LOCAL_ORG_ID = "local"

_SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS automated_savings_events_id_seq START 1;

CREATE TABLE IF NOT EXISTS automated_savings_settings (
    organization_id TEXT PRIMARY KEY,
    agreed_at       TIMESTAMPTZ,
    global_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS automated_savings_warehouses (
    organization_id      TEXT NOT NULL,
    warehouse_name       TEXT NOT NULL,
    enabled              BOOLEAN NOT NULL DEFAULT FALSE,
    warehouse_created_on TIMESTAMPTZ NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, warehouse_name)
);

CREATE TABLE IF NOT EXISTS automated_savings_events (
    id BIGINT PRIMARY KEY
       DEFAULT nextval('automated_savings_events_id_seq'),
    organization_id             TEXT NOT NULL,
    warehouse_name              TEXT NOT NULL,
    action                      TEXT NOT NULL CHECK (action = 'suspend'),
    reason                      TEXT NOT NULL CHECK (reason = 'idle'),
    observed_state              TEXT NOT NULL,
    observed_running            INTEGER NOT NULL CHECK (observed_running >= 0),
    observed_queued             INTEGER NOT NULL CHECK (observed_queued >= 0),
    observed_quiescing          INTEGER NOT NULL CHECK (observed_quiescing >= 0),
    observed_resumed_on         TIMESTAMPTZ NOT NULL,
    observed_started_clusters   INTEGER,
    observed_min_cluster_count  INTEGER,
    observed_max_cluster_count  INTEGER,
    observed_at                 TIMESTAMPTZ NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS automated_savings_events_org_created_idx
    ON automated_savings_events (organization_id, created_at);
"""


class LocalDuckDBStore:
    """A ``Store`` backed by a single DuckDB file.

    Structural implementation of the ``Store`` Protocol. The tests use it
    against the ``Store`` type alias directly, so no ``class
    LocalDuckDBStore(Store)`` inheritance is needed.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # One long-lived connection per store instance; every method guards
        # its access with ``_lock``. Opening/closing a DuckDB connection is
        # cheaper than a Snowflake session but not free, and a shared
        # connection keeps the query planner cache warm.
        self._conn: duckdb.DuckDBPyConnection = duckdb.connect(
            str(self._path)
        )
        self._lock = threading.Lock()
        self._initialize()

    # ---- schema bootstrap ------------------------------------------------

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                # DuckDB's ``execute`` accepts one statement per call; run
                # each with ``executemany``-like iteration via ``execute``.
                for statement in _iter_statements(_SCHEMA):
                    self._conn.execute(statement)
                # Ensure a settings row exists so ``authorize_suspend``
                # never has to distinguish "no row" from "not enabled".
                now = _utcnow()
                self._conn.execute(
                    """
                    INSERT INTO automated_savings_settings
                      (organization_id, global_enabled, created_at, updated_at)
                    VALUES (?, FALSE, ?, ?)
                    ON CONFLICT (organization_id) DO NOTHING
                    """,
                    (LOCAL_ORG_ID, now, now),
                )
                self._conn.execute("COMMIT")
            except duckdb.Error as exc:
                self._conn.execute("ROLLBACK")
                raise StoreError("store initialization failed") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- Store protocol --------------------------------------------------

    def list_enrollments(self, organization_id: str) -> list[EnrollmentRow]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT organization_id, warehouse_name, enabled,
                           warehouse_created_on, updated_at
                      FROM automated_savings_warehouses
                     WHERE organization_id = ?
                    """,
                    (organization_id,),
                ).fetchall()
            except duckdb.Error as exc:
                raise StoreError("list enrollments failed") from exc
        return [_row_to_enrollment(row) for row in rows]

    def authorize_suspend(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool:
        _require_aware(warehouse_created_on, "warehouse_created_on")
        _require_aware(enrollment_updated_at, "enrollment_updated_at")
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                row = self._conn.execute(
                    """
                    SELECT 1
                      FROM automated_savings_warehouses w
                      JOIN automated_savings_settings s
                        ON s.organization_id = w.organization_id
                     WHERE w.organization_id = ?
                       AND w.warehouse_name = ?
                       AND s.global_enabled = TRUE
                       AND w.enabled = TRUE
                       AND w.warehouse_created_on = ?
                       AND w.updated_at = ?
                    """,
                    (
                        organization_id,
                        warehouse_name,
                        warehouse_created_on,
                        enrollment_updated_at,
                    ),
                ).fetchone()
                self._conn.execute("COMMIT")
            except duckdb.Error as exc:
                self._conn.execute("ROLLBACK")
                raise StoreError("authorize suspend failed") from exc
        return row is not None

    def delete_stale_enrollment(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool:
        _require_aware(warehouse_created_on, "warehouse_created_on")
        _require_aware(enrollment_updated_at, "enrollment_updated_at")
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                matched = self._conn.execute(
                    """
                    SELECT 1
                      FROM automated_savings_warehouses
                     WHERE organization_id = ?
                       AND warehouse_name = ?
                       AND warehouse_created_on = ?
                       AND updated_at = ?
                    """,
                    (
                        organization_id,
                        warehouse_name,
                        warehouse_created_on,
                        enrollment_updated_at,
                    ),
                ).fetchall()
                if not matched:
                    self._conn.execute("COMMIT")
                    return False
                if len(matched) > 1:
                    self._conn.execute("ROLLBACK")
                    raise StoreError(
                        "delete stale enrollment matched more than one row"
                    )
                self._conn.execute(
                    """
                    DELETE FROM automated_savings_warehouses
                     WHERE organization_id = ?
                       AND warehouse_name = ?
                       AND warehouse_created_on = ?
                       AND updated_at = ?
                    """,
                    (
                        organization_id,
                        warehouse_name,
                        warehouse_created_on,
                        enrollment_updated_at,
                    ),
                )
                self._conn.execute("COMMIT")
            except duckdb.Error as exc:
                self._conn.execute("ROLLBACK")
                raise StoreError("delete stale enrollment failed") from exc
        return True

    def record_event(self, event: SavingsEvent) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO automated_savings_events (
                        organization_id, warehouse_name, action, reason,
                        observed_state, observed_running, observed_queued,
                        observed_quiescing, observed_resumed_on,
                        observed_started_clusters, observed_min_cluster_count,
                        observed_max_cluster_count, observed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.organization_id,
                        event.warehouse_name,
                        event.action,
                        event.reason,
                        event.observed_state,
                        event.observed_running,
                        event.observed_queued,
                        event.observed_quiescing,
                        event.observed_resumed_on,
                        event.observed_started_clusters,
                        event.observed_min_cluster_count,
                        event.observed_max_cluster_count,
                        event.observed_at,
                        _utcnow(),
                    ),
                )
            except duckdb.Error as exc:
                raise StoreError("record event failed") from exc

    def worker_tenants(self) -> list[str]:
        # Local mode is single-tenant. Return [LOCAL_ORG_ID] only when
        # global + at least one warehouse are enabled — the exact predicate
        # ``automated_savings_worker_tenants`` evaluates for Supabase.
        with self._lock:
            try:
                row = self._conn.execute(
                    """
                    SELECT 1
                      FROM automated_savings_settings s
                      JOIN automated_savings_warehouses w
                        ON w.organization_id = s.organization_id
                     WHERE s.organization_id = ?
                       AND s.global_enabled = TRUE
                       AND w.enabled = TRUE
                     LIMIT 1
                    """,
                    (LOCAL_ORG_ID,),
                ).fetchone()
            except duckdb.Error as exc:
                raise StoreError("worker tenants failed") from exc
        return [LOCAL_ORG_ID] if row is not None else []

    # ---- CLI helpers (used by local_bootstrap.py) ------------------------

    def set_agreed(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE automated_savings_settings
                   SET agreed_at = COALESCE(agreed_at, ?),
                       updated_at = ?
                 WHERE organization_id = ?
                """,
                (_utcnow(), _utcnow(), LOCAL_ORG_ID),
            )

    def set_global_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE automated_savings_settings
                   SET global_enabled = ?, updated_at = ?
                 WHERE organization_id = ?
                """,
                (enabled, _utcnow(), LOCAL_ORG_ID),
            )

    def upsert_warehouse(
        self, warehouse_name: str, warehouse_created_on: datetime
    ) -> None:
        _require_aware(warehouse_created_on, "warehouse_created_on")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO automated_savings_warehouses (
                    organization_id, warehouse_name, enabled,
                    warehouse_created_on, updated_at
                ) VALUES (?, ?, FALSE, ?, ?)
                ON CONFLICT (organization_id, warehouse_name) DO UPDATE SET
                    warehouse_created_on = excluded.warehouse_created_on,
                    updated_at = excluded.updated_at
                """,
                (LOCAL_ORG_ID, warehouse_name, warehouse_created_on, _utcnow()),
            )

    def set_warehouse_enabled(self, warehouse_name: str, enabled: bool) -> bool:
        with self._lock:
            # DuckDB has a known limitation where UPDATE ... RETURNING on a
            # table with a PRIMARY KEY spuriously reports a duplicate-key
            # violation even when the PK columns are unchanged (see
            # https://duckdb.org/docs/sql/indexes). Do the update, then
            # verify by re-selecting the row.
            exists = self._conn.execute(
                """
                SELECT 1
                  FROM automated_savings_warehouses
                 WHERE organization_id = ? AND warehouse_name = ?
                """,
                (LOCAL_ORG_ID, warehouse_name),
            ).fetchone()
            if exists is None:
                return False
            self._conn.execute(
                """
                UPDATE automated_savings_warehouses
                   SET enabled = ?, updated_at = ?
                 WHERE organization_id = ? AND warehouse_name = ?
                """,
                (enabled, _utcnow(), LOCAL_ORG_ID, warehouse_name),
            )
            return True


# ---- helpers -------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StoreError(f"{field} must be timezone-aware")


def _iter_statements(script: str) -> list[str]:
    # DuckDB's Python ``execute`` accepts one statement per call. Split the
    # bootstrap script on ';' semicolons while skipping empty tails.
    return [stmt.strip() for stmt in script.split(";") if stmt.strip()]


def _row_to_enrollment(row: tuple[object, ...]) -> EnrollmentRow:
    try:
        organization_id, warehouse_name, enabled, warehouse_created_on, updated_at = row
        assert isinstance(organization_id, str)
        assert isinstance(warehouse_name, str)
        assert isinstance(enabled, bool)
        assert isinstance(warehouse_created_on, datetime)
        assert isinstance(updated_at, datetime)
    except (AssertionError, ValueError) as exc:
        raise StoreError("malformed enrollment row") from exc
    # DuckDB returns TIMESTAMPTZ as tz-aware datetime; assert loudly if a
    # future version regresses to naive.
    _require_aware(warehouse_created_on, "warehouse_created_on")
    _require_aware(updated_at, "updated_at")
    return EnrollmentRow(
        organization_id=organization_id,
        warehouse_name=warehouse_name,
        enabled=enabled,
        warehouse_created_on=warehouse_created_on,
        updated_at=updated_at,
    )
