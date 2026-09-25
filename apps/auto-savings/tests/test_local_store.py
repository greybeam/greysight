from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from auto_savings.local_store import LOCAL_ORG_ID, LocalDuckDBStore
from auto_savings.store import SavingsEvent, StoreError

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
CREATED_ON = NOW - timedelta(days=1)


def _event() -> SavingsEvent:
    return SavingsEvent(
        organization_id=LOCAL_ORG_ID, warehouse_name="WH1",
        action="suspend", reason="idle",
        observed_state="STARTED",
        observed_running=0, observed_queued=0, observed_quiescing=0,
        observed_resumed_on=CREATED_ON,
        observed_started_clusters=1,
        observed_min_cluster_count=1, observed_max_cluster_count=1,
        observed_at=NOW,
    )


def _store(tmp_path: Path) -> LocalDuckDBStore:
    return LocalDuckDBStore(tmp_path / "auto.duckdb")


def test_bootstrap_creates_settings_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # worker_tenants is [] until global + a warehouse are both enabled
    assert store.worker_tenants() == []
    store.close()


def test_authorize_suspend_requires_versioned_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_agreed()
    store.set_global_enabled(True)
    store.upsert_warehouse("WH1", CREATED_ON)
    store.set_warehouse_enabled("WH1", True)
    row = store.list_enrollments(LOCAL_ORG_ID)[0]

    assert store.authorize_suspend(
        LOCAL_ORG_ID, "WH1",
        warehouse_created_on=row.warehouse_created_on,
        enrollment_updated_at=row.updated_at,
    )
    # A single microsecond drift on either identity component denies suspend.
    assert not store.authorize_suspend(
        LOCAL_ORG_ID, "WH1",
        warehouse_created_on=CREATED_ON + timedelta(microseconds=1),
        enrollment_updated_at=row.updated_at,
    )
    assert not store.authorize_suspend(
        LOCAL_ORG_ID, "WH1",
        warehouse_created_on=row.warehouse_created_on,
        enrollment_updated_at=row.updated_at + timedelta(microseconds=1),
    )
    store.close()


def test_authorize_suspend_denied_when_global_off(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_warehouse("WH1", CREATED_ON)
    store.set_warehouse_enabled("WH1", True)
    row = store.list_enrollments(LOCAL_ORG_ID)[0]
    # Global switch is off — always denied even with matching identity.
    assert not store.authorize_suspend(
        LOCAL_ORG_ID, "WH1",
        warehouse_created_on=row.warehouse_created_on,
        enrollment_updated_at=row.updated_at,
    )
    store.close()


def test_authorize_suspend_denied_when_warehouse_disabled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_global_enabled(True)
    store.upsert_warehouse("WH1", CREATED_ON)
    row = store.list_enrollments(LOCAL_ORG_ID)[0]
    assert not store.authorize_suspend(
        LOCAL_ORG_ID, "WH1",
        warehouse_created_on=row.warehouse_created_on,
        enrollment_updated_at=row.updated_at,
    )
    store.close()


def test_delete_stale_enrollment_only_when_identity_matches(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_warehouse("WH1", CREATED_ON)
    row = store.list_enrollments(LOCAL_ORG_ID)[0]
    assert not store.delete_stale_enrollment(
        LOCAL_ORG_ID, "WH1",
        warehouse_created_on=CREATED_ON + timedelta(seconds=1),
        enrollment_updated_at=row.updated_at,
    )
    assert store.list_enrollments(LOCAL_ORG_ID)  # untouched
    assert store.delete_stale_enrollment(
        LOCAL_ORG_ID, "WH1",
        warehouse_created_on=row.warehouse_created_on,
        enrollment_updated_at=row.updated_at,
    )
    assert store.list_enrollments(LOCAL_ORG_ID) == []
    store.close()


def test_worker_tenants_reflects_state_machine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.worker_tenants() == []
    store.set_global_enabled(True)
    assert store.worker_tenants() == []  # no enrolled warehouse yet
    store.upsert_warehouse("WH1", CREATED_ON)
    assert store.worker_tenants() == []  # enrollment exists but disabled
    store.set_warehouse_enabled("WH1", True)
    assert store.worker_tenants() == [LOCAL_ORG_ID]
    store.set_global_enabled(False)
    assert store.worker_tenants() == []
    store.close()


def test_record_event_is_append_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_event(_event())
    store.record_event(_event())
    conn = duckdb.connect(str(tmp_path / "auto.duckdb"), read_only=True)
    try:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM automated_savings_events"
        ).fetchone()
    assert count == 2
    store.close()


def test_naive_datetime_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    naive = datetime(2026, 6, 1, 0, 0, 0)  # no tzinfo
    with pytest.raises(StoreError, match="timezone-aware"):
        store.upsert_warehouse("WH1", naive)
    store.close()


def test_upsert_updates_versioned_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_warehouse("WH1", CREATED_ON)
    row_before = store.list_enrollments(LOCAL_ORG_ID)[0]
    later = CREATED_ON + timedelta(hours=1)
    store.upsert_warehouse("WH1", later)
    row_after = store.list_enrollments(LOCAL_ORG_ID)[0]
    assert row_after.warehouse_created_on == later
    assert row_after.updated_at > row_before.updated_at
    store.close()


def test_concurrent_reads_serialize_safely(tmp_path: Path) -> None:
    # Not a race test — DuckDB's own tests cover that. This asserts the
    # per-instance lock does not deadlock under threaded read pressure.
    store = _store(tmp_path)
    store.set_global_enabled(True)
    store.upsert_warehouse("WH1", CREATED_ON)
    store.set_warehouse_enabled("WH1", True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: store.worker_tenants(), range(32),
        ))
    assert all(r == [LOCAL_ORG_ID] for r in results)
    store.close()
