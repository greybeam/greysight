from pathlib import Path

import pytest

from auto_savings.config import WorkerConfig


def test_from_environment_reads_cadence_and_sharding(monkeypatch):
    monkeypatch.setenv("AUTO_SAVINGS_POLL_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("AUTO_SAVINGS_NUM_REPLICAS", "3")
    monkeypatch.setenv("AUTO_SAVINGS_REPLICA_INDEX", "2")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    config = WorkerConfig.from_environment()

    assert config.poll_interval_seconds == 5.0
    assert config.num_replicas == 3
    assert config.replica_index == 2
    assert config.uptime_floor_seconds == 62  # hardcoded guardrail default


def test_socket_timeout_must_be_below_poll_timeout():
    with pytest.raises(ValueError):
        WorkerConfig(
            supabase_url="u",
            supabase_service_role_key="k",
            socket_timeout_seconds=20,
            poll_timeout_seconds=20,
        )


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("poll_interval_seconds", 0),
        ("poll_interval_seconds", -1),
        ("poll_interval_seconds", float("nan")),
        ("poll_interval_seconds", float("inf")),
        ("uptime_floor_seconds", -1),
        ("tenant_refresh_seconds", 0),
        ("query_timeout_seconds", 0),
        ("socket_timeout_seconds", 0),
    ],
)
def test_non_finite_or_non_positive_intervals_raise(field, bad_value):
    kwargs = dict(supabase_url="u", supabase_service_role_key="k")
    kwargs[field] = bad_value
    with pytest.raises(ValueError):
        WorkerConfig(**kwargs)


def test_num_replicas_must_be_at_least_one():
    with pytest.raises(ValueError):
        WorkerConfig(supabase_url="u", supabase_service_role_key="k", num_replicas=0)


def test_max_workers_must_be_at_least_one():
    with pytest.raises(ValueError, match="max_workers"):
        WorkerConfig(supabase_url="u", supabase_service_role_key="k", max_workers=0)


def test_replica_index_must_be_within_num_replicas():
    with pytest.raises(ValueError):
        WorkerConfig(
            supabase_url="u",
            supabase_service_role_key="k",
            num_replicas=3,
            replica_index=3,
        )
    with pytest.raises(ValueError):
        WorkerConfig(
            supabase_url="u",
            supabase_service_role_key="k",
            num_replicas=3,
            replica_index=-1,
        )


def test_duckdb_backend_forbids_supabase_env() -> None:
    with pytest.raises(ValueError, match="forbids SUPABASE"):
        WorkerConfig(
            supabase_url="https://x.supabase.co",
            supabase_service_role_key="svc",
            backend="duckdb",
            duckdb_path=Path("/tmp/x.duckdb"),
        )


def test_duckdb_backend_requires_path() -> None:
    with pytest.raises(ValueError, match="AUTO_SAVINGS_DUCKDB_PATH"):
        WorkerConfig(
            supabase_url="",
            supabase_service_role_key="",
            backend="duckdb",
            duckdb_path=None,
        )


def test_supabase_backend_forbids_duckdb_path() -> None:
    with pytest.raises(ValueError, match="only valid when"):
        WorkerConfig(
            supabase_url="https://x.supabase.co",
            supabase_service_role_key="svc",
            backend="supabase",
            duckdb_path=Path("/tmp/x.duckdb"),
        )
