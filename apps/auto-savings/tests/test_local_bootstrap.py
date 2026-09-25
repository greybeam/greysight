from pathlib import Path

import duckdb
import pytest

from auto_savings.local_bootstrap import run
from auto_savings.local_store import LOCAL_ORG_ID, LocalDuckDBStore


@pytest.fixture
def env(monkeypatch, tmp_path: Path) -> Path:
    db = tmp_path / "auto.duckdb"
    monkeypatch.setenv("AUTO_SAVINGS_BACKEND", "duckdb")
    monkeypatch.setenv("AUTO_SAVINGS_DUCKDB_PATH", str(db))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    return db


def test_agree_persists(env: Path) -> None:
    assert run(["agree"]) == 0
    conn = duckdb.connect(str(env), read_only=True)
    try:
        agreed_at = conn.execute(
            "SELECT agreed_at FROM automated_savings_settings "
            "WHERE organization_id = ?",
            (LOCAL_ORG_ID,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert agreed_at is not None


def test_enable_before_enroll_exits_2(env: Path) -> None:
    assert run(["enable", "--warehouse", "WH1"]) == 2


def test_enroll_then_enable_promotes_tenant(env: Path) -> None:
    assert run([
        "enroll", "--warehouse", "WH1",
        "--warehouse-created-on", "2026-06-01T00:00:00Z",
    ]) == 0
    assert run(["enable", "--warehouse", "WH1"]) == 0
    assert run(["enable-global"]) == 0
    store = LocalDuckDBStore(env)
    try:
        assert store.worker_tenants() == [LOCAL_ORG_ID]
    finally:
        store.close()


def test_enroll_rejects_naive_timestamp(env: Path) -> None:
    with pytest.raises(SystemExit):
        run(["enroll", "--warehouse", "WH1",
             "--warehouse-created-on", "2026-06-01T00:00:00"])


def test_cli_refuses_when_backend_not_duckdb(monkeypatch) -> None:
    monkeypatch.setenv("AUTO_SAVINGS_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.delenv("AUTO_SAVINGS_DUCKDB_PATH", raising=False)
    with pytest.raises(SystemExit):
        run(["agree"])
