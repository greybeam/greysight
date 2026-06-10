from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.services.snowflake_client import (
    SnowflakeConfigurationError,
    SnowflakeConnectionConfig,
    SnowflakeValidationError,
    execute_source_query,
    validate_snowflake_connection,
)


def test_execute_source_query_uses_named_bind_params() -> None:
    cursor = Mock()
    cursor.description = [("WINDOW_DAYS",)]
    cursor.fetchall.return_value = [(30,)]
    connection = Mock()
    connection.cursor.return_value = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    config = SnowflakeConnectionConfig(
        account="account",
        user="user",
        role="role",
        warehouse="warehouse",
        database="SNOWFLAKE",
        schema="ACCOUNT_USAGE",
        private_key_path=Path("unused.p8"),
    )
    with (
        patch(
            "app.services.snowflake_client.snowflake.connector.connect",
            return_value=connection,
        ) as connect,
        patch.object(
            SnowflakeConnectionConfig, "_load_private_key_der", return_value=b"key"
        ),
    ):
        rows = execute_source_query(
            "select %(window_days)s as window_days",
            {"window_days": 30},
            config,
        )

    assert connect.call_args.kwargs["private_key"] == b"key"
    cursor.execute.assert_called_once_with(
        "select %(window_days)s as window_days",
        {"window_days": 30},
    )
    assert rows == [{"window_days": 30}]
    connection.close.assert_called_once_with()


def test_execute_source_query_rejects_invalid_window_before_connecting() -> None:
    with patch("app.services.snowflake_client.snowflake.connector.connect") as connect:
        with pytest.raises(ValueError, match="window_days"):
            execute_source_query(
                "select %(window_days)s as window_days",
                {"window_days": 0},
            )

    connect.assert_not_called()


def test_validation_maps_raw_snowflake_errors_to_user_safe_messages() -> None:
    with (
        patch(
            "app.services.snowflake_client.snowflake.connector.connect",
            side_effect=PermissionError("raw private backend detail"),
        ),
        patch.object(SnowflakeConnectionConfig, "connector_kwargs", return_value={}),
    ):
        with pytest.raises(SnowflakeValidationError) as exc_info:
            validate_snowflake_connection()

    assert "raw private backend detail" not in str(exc_info.value)
    assert (
        str(exc_info.value)
        == "Could not access required Snowflake Account Usage views."
    )


@pytest.mark.parametrize(
    ("raw_error", "safe_message"),
    [
        (
            RuntimeError("incorrect username or private key"),
            "Could not authenticate to Snowflake. Check the configured user and private key.",
        ),
        (
            RuntimeError("role ANALYST does not exist or not authorized"),
            "Could not use the configured Snowflake role.",
        ),
        (
            RuntimeError("warehouse LOAD_WH is unavailable"),
            "Could not use the configured Snowflake warehouse.",
        ),
        (
            PermissionError("insufficient privileges on account usage"),
            "Could not access required Snowflake Account Usage views.",
        ),
    ],
)
def test_validation_maps_known_failure_modes_to_safe_messages(
    raw_error: Exception, safe_message: str
) -> None:
    with (
        patch(
            "app.services.snowflake_client.snowflake.connector.connect",
            side_effect=raw_error,
        ),
        patch.object(SnowflakeConnectionConfig, "connector_kwargs", return_value={}),
    ):
        with pytest.raises(SnowflakeValidationError) as exc_info:
            validate_snowflake_connection()

    assert str(exc_info.value) == safe_message
    assert str(raw_error) not in str(exc_info.value)


def test_default_connection_uses_environment_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "account")
    monkeypatch.setenv("SNOWFLAKE_USER", "user")
    monkeypatch.setenv("SNOWFLAKE_ROLE", "role")
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "warehouse")
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "SNOWFLAKE")
    monkeypatch.setenv("SNOWFLAKE_SCHEMA", "ACCOUNT_USAGE")
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", "/secret/key.p8")

    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with (
        patch(
            "app.services.snowflake_client.snowflake.connector.connect",
            return_value=connection,
        ) as connect,
        patch.object(
            SnowflakeConnectionConfig, "_load_private_key_der", return_value=b"key"
        ),
    ):
        validate_snowflake_connection()

    assert connect.call_args.kwargs["account"] == "account"
    assert connect.call_args.kwargs["private_key"] == b"key"
    assert "/secret/key.p8" not in str(connect.call_args.kwargs)


def test_config_from_environment_expands_user_key_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", "~/snowflake_key.p8")

    config = SnowflakeConnectionConfig.from_environment()

    assert config.private_key_path == Path("~/snowflake_key.p8").expanduser()


def test_config_from_environment_loads_private_key_path_without_exposing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key_path = tmp_path / "snowflake_key.p8"
    key_path.write_text("not a valid key", encoding="utf-8")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "account")
    monkeypatch.setenv("SNOWFLAKE_USER", "user")
    monkeypatch.setenv("SNOWFLAKE_ROLE", "role")
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "warehouse")
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "SNOWFLAKE")
    monkeypatch.setenv("SNOWFLAKE_SCHEMA", "ACCOUNT_USAGE")
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", str(key_path))

    config = SnowflakeConnectionConfig.from_environment()

    assert config.private_key_path == key_path
    with pytest.raises(SnowflakeConfigurationError) as exc_info:
        config.connector_kwargs()
    assert str(key_path) not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
