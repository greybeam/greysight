from pathlib import Path


_SUPABASE_DIR = Path(__file__).resolve().parents[3] / "supabase"
MIGRATIONS_DIR = _SUPABASE_DIR / "migrations"
DIRECT_SQL = _SUPABASE_DIR / "tests" / "automated_savings_direct.sql"


def test_unreleased_sentinel_followups_are_folded_away():
    assert not (
        MIGRATIONS_DIR / "20260713223505_automated_savings_sentinel_confirmed.sql"
    ).exists()
    assert not (
        MIGRATIONS_DIR / "20260714002106_automated_savings_intent_safety.sql"
    ).exists()


def test_direct_sql_test_exercises_the_worker_rpcs():
    assert DIRECT_SQL.exists()
    body = DIRECT_SQL.read_text()
    for rpc in (
        "automated_savings_upsert_enrollment",
        "automated_savings_disable_enrollment",
        "automated_savings_authorize_suspend",
        "automated_savings_delete_stale_enrollment",
    ):
        assert rpc in body, f"direct SQL test no longer references {rpc}"
