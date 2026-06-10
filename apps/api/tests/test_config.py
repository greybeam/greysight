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
    assert settings.query_timeout_seconds == 60


def test_greysight_window_and_timeout_env_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GREYSIGHT_DEFAULT_WINDOW_DAYS", "45")
    monkeypatch.setenv("GREYSIGHT_QUERY_TIMEOUT_SECONDS", "90")

    settings = Settings()

    assert settings.default_window_days == 45
    assert settings.query_timeout_seconds == 90


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
