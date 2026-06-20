import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_SOURCE", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("GREYSIGHT_DEFAULT_WINDOW_DAYS", raising=False)
    monkeypatch.delenv("GREYSIGHT_QUERY_TIMEOUT_SECONDS", raising=False)

    settings = Settings()

    assert settings.data_source == "demo"
    assert settings.auth_required is False
    assert settings.default_window_days == 30
    assert settings.query_timeout_seconds == 120


def test_greysight_window_and_timeout_env_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GREYSIGHT_DEFAULT_WINDOW_DAYS", "45")
    monkeypatch.setenv("GREYSIGHT_QUERY_TIMEOUT_SECONDS", "90")

    settings = Settings()

    assert settings.default_window_days == 45
    assert settings.query_timeout_seconds == 90


def test_supabase_env_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    settings = Settings()

    assert settings.supabase_url == "https://project.supabase.co"
    assert settings.supabase_anon_key == "anon-key"


def test_supabase_falls_back_to_next_public_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", "https://public.supabase.co")
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "public-anon-key")

    settings = Settings()

    assert settings.supabase_url == "https://public.supabase.co"
    assert settings.supabase_anon_key == "public-anon-key"


def test_supabase_server_vars_take_precedence_over_next_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://server.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "server-anon-key")
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", "https://public.supabase.co")
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "public-anon-key")

    settings = Settings()

    assert settings.supabase_url == "https://server.supabase.co"
    assert settings.supabase_anon_key == "server-anon-key"


def test_supabase_service_role_key_has_no_next_public_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY", "should-not-be-read")

    settings = Settings()

    assert settings.supabase_service_role_key == ""


def test_supabase_service_role_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")

    settings = Settings()

    assert settings.supabase_service_role_key == "service-role-key"


def test_supabase_service_role_key_defaults_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    settings = Settings()

    assert settings.supabase_service_role_key == ""


def test_empty_storage_price_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PRICE_USD_PER_TB_MONTH", "")

    settings = Settings()

    assert settings.storage_price_usd_per_tb_month == 23.0


def test_invalid_data_source_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_SOURCE", "postgres")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["0", "366", "-1"])
def test_invalid_default_window_days_is_rejected(monkeypatch, value):
    monkeypatch.setenv("GREYSIGHT_DEFAULT_WINDOW_DAYS", value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_invalid_query_timeout_seconds_is_rejected(monkeypatch, value):
    monkeypatch.setenv("GREYSIGHT_QUERY_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings()


def test_estimated_credit_price_defaults_to_three_usd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ESTIMATED_CREDIT_PRICE_USD", raising=False)

    settings = Settings()

    assert settings.estimated_credit_price_usd == 3.0


def test_estimated_credit_price_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESTIMATED_CREDIT_PRICE_USD", "2.25")

    settings = Settings()

    assert settings.estimated_credit_price_usd == 2.25


def test_estimated_credit_price_empty_env_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ESTIMATED_CREDIT_PRICE_USD", "")

    settings = Settings()

    assert settings.estimated_credit_price_usd == 3.0
