import math

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


def test_defaults_are_safe(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    config = WorkerConfig.from_environment()
    assert config.poll_interval_seconds == 3.0
    assert config.intent_poll_interval_seconds == 1.0
    assert config.cooldown_seconds == 60
    assert config.num_replicas == 1


def test_intent_poll_interval_env_override(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setenv("AUTO_SAVINGS_INTENT_POLL_INTERVAL_SECONDS", "0.5")
    config = WorkerConfig.from_environment()
    assert config.intent_poll_interval_seconds == 0.5


def test_socket_timeout_must_be_below_poll_timeout():
    with pytest.raises(ValueError):
        WorkerConfig(supabase_url="u", supabase_service_role_key="k",
                     socket_timeout_seconds=20, poll_timeout_seconds=20)


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("poll_interval_seconds", 0),
        ("poll_interval_seconds", -1),
        ("poll_interval_seconds", float("nan")),
        ("poll_interval_seconds", float("inf")),
        ("cooldown_seconds", 0),
        ("cooldown_seconds", -5),
        ("intent_poll_interval_seconds", 0),
        ("uptime_floor_seconds", -1),
        ("max_intent_hold_ticks", 0),
        ("orphan_grace_seconds", 0),
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


def test_replica_index_must_be_within_num_replicas():
    with pytest.raises(ValueError):
        WorkerConfig(
            supabase_url="u", supabase_service_role_key="k",
            num_replicas=3, replica_index=3,
        )
    with pytest.raises(ValueError):
        WorkerConfig(
            supabase_url="u", supabase_service_role_key="k",
            num_replicas=3, replica_index=-1,
        )
    # Sanity: a valid index does not raise.
    WorkerConfig(
        supabase_url="u", supabase_service_role_key="k",
        num_replicas=3, replica_index=2,
    )


def test_valid_defaults_pass_finiteness_check():
    config = WorkerConfig(supabase_url="u", supabase_service_role_key="k")
    assert math.isfinite(config.poll_interval_seconds)
