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
    import pytest
    with pytest.raises(ValueError):
        WorkerConfig(supabase_url="u", supabase_service_role_key="k",
                     socket_timeout_seconds=20, poll_timeout_seconds=20)
