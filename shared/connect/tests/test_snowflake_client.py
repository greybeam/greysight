import logging
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from greysight_connect.snowflake_client import (
    SnowflakeConfigurationError,
    SnowflakeConnectionConfig,
    SnowflakeQueryError,
    SnowflakeValidationError,
    execute_source_query,
    validate_snowflake_connection,
)


class _FakeSnowflakeConnectionError(Exception):
    errno = 250001
    sqlstate = "08001"


class _FakeSnowflakeQueryError(Exception):
    errno = 3001
    sqlstate = "42501"


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
            "greysight_connect.snowflake_client.snowflake.connector.connect",
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
    with patch("greysight_connect.snowflake_client.snowflake.connector.connect") as connect:
        with pytest.raises(ValueError, match="window_days"):
            execute_source_query(
                "select %(window_days)s as window_days",
                {"window_days": 0},
            )

    connect.assert_not_called()


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
            (
                "Could not access required Snowflake Account Usage views. Verify "
                "that the configured role has Account Usage access."
            ),
        ),
    ],
)
def test_validation_maps_known_failure_modes_to_safe_messages(
    raw_error: Exception, safe_message: str
) -> None:
    with (
        patch(
            "greysight_connect.snowflake_client.snowflake.connector.connect",
            side_effect=raw_error,
        ),
        patch.object(SnowflakeConnectionConfig, "connector_kwargs", return_value={}),
    ):
        with pytest.raises(SnowflakeValidationError) as exc_info:
            validate_snowflake_connection()

    assert str(exc_info.value) == safe_message
    assert str(raw_error) not in str(exc_info.value)


def test_validation_reports_safe_network_policy_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reference = "0ce9eb56-821d-4ca9-a774-04ae89a0cf5a"
    raw_error = _FakeSnowflakeConnectionError(
        "Incoming request with IP/Token PEMSECRETMARKER is not allowed to "
        f"access Snowflake. [{reference}]"
    )

    with (
        patch(
            "greysight_connect.snowflake_client.snowflake.connector.connect",
            side_effect=raw_error,
        ),
        patch.object(SnowflakeConnectionConfig, "connector_kwargs", return_value={}),
        caplog.at_level(
            logging.WARNING, logger="greysight_connect.snowflake_client"
        ),
        pytest.raises(SnowflakeValidationError) as exc_info,
    ):
        validate_snowflake_connection()

    user_message = str(exc_info.value)
    assert "network policy" in user_message.lower()
    assert "250001" in user_message
    assert "08001" in user_message
    assert reference in user_message
    assert "PEMSECRETMARKER" not in user_message

    assert "phase=connect" in caplog.text
    assert "exception=_FakeSnowflakeConnectionError" in caplog.text
    assert "errno=250001" in caplog.text
    assert "sqlstate=08001" in caplog.text
    assert f"reference={reference}" in caplog.text
    assert "PEMSECRETMARKER" not in caplog.text


def test_validation_probe_identifies_unavailable_account_usage_view(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_error = _FakeSnowflakeQueryError(
        "Insufficient privileges on QUERY_ATTRIBUTION_HISTORY SECRETQUERYMARKER"
    )
    cursor = Mock()

    def execute(sql: str) -> None:
        if "QUERY_ATTRIBUTION_HISTORY" in sql:
            raise raw_error

    cursor.execute.side_effect = execute
    connection = Mock()
    connection.cursor.return_value = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with (
        patch(
            "greysight_connect.snowflake_client.snowflake.connector.connect",
            return_value=connection,
        ),
        patch.object(SnowflakeConnectionConfig, "connector_kwargs", return_value={}),
        caplog.at_level(
            logging.WARNING, logger="greysight_connect.snowflake_client"
        ),
        pytest.raises(SnowflakeValidationError) as exc_info,
    ):
        validate_snowflake_connection()

    user_message = str(exc_info.value)
    assert "QUERY_ATTRIBUTION_HISTORY" in user_message
    assert "configured role has Account Usage access" in user_message
    assert "SECRETQUERYMARKER" not in user_message
    assert "phase=query_attribution_history" in caplog.text
    assert "SECRETQUERYMARKER" not in caplog.text


def test_validation_probe_preserves_timeout_category_without_exposing_uuid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    unrelated_reference = "11111111-2222-4333-8444-555555555555"
    raw_error = TimeoutError(
        f"SECRETQUERYMARKER timed out [{unrelated_reference}]"
    )
    cursor = Mock()

    def execute(sql: str) -> None:
        if "QUERY_ATTRIBUTION_HISTORY" in sql:
            raise raw_error

    cursor.execute.side_effect = execute
    connection = Mock()
    connection.cursor.return_value = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with (
        patch(
            "greysight_connect.snowflake_client.snowflake.connector.connect",
            return_value=connection,
        ),
        patch.object(SnowflakeConnectionConfig, "connector_kwargs", return_value={}),
        caplog.at_level(
            logging.WARNING, logger="greysight_connect.snowflake_client"
        ),
        pytest.raises(SnowflakeValidationError) as exc_info,
    ):
        validate_snowflake_connection()

    user_message = str(exc_info.value)
    assert "timed out" in user_message
    assert "QUERY_ATTRIBUTION_HISTORY" in user_message
    assert "role has Snowflake Account Usage access" not in user_message
    assert unrelated_reference not in user_message
    assert unrelated_reference not in caplog.text
    assert "SECRETQUERYMARKER" not in user_message
    assert "SECRETQUERYMARKER" not in caplog.text


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
    cursor.fetchone.return_value = ("ACCOUNT123",)
    connection = Mock()
    connection.cursor.return_value = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with (
        patch(
            "greysight_connect.snowflake_client.snowflake.connector.connect",
            return_value=connection,
        ) as connect,
        patch.object(
            SnowflakeConnectionConfig, "_load_private_key_der", return_value=b"key"
        ),
    ):
        locator = validate_snowflake_connection()

    assert connect.call_args.kwargs["account"] == "account"
    assert connect.call_args.kwargs["private_key"] == b"key"
    assert "/secret/key.p8" not in str(connect.call_args.kwargs)
    assert locator == "ACCOUNT123"


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


def test_validation_logs_configuration_failure_without_raw_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_error = SnowflakeConfigurationError(
        "SECRETCONFIGMARKER private key could not be loaded"
    )

    with (
        patch.object(
            SnowflakeConnectionConfig,
            "connector_kwargs",
            side_effect=raw_error,
        ),
        caplog.at_level(
            logging.WARNING, logger="greysight_connect.snowflake_client"
        ),
        pytest.raises(SnowflakeConfigurationError),
    ):
        validate_snowflake_connection()

    assert "phase=configuration" in caplog.text
    assert "exception=SnowflakeConfigurationError" in caplog.text
    assert "SECRETCONFIGMARKER" not in caplog.text


def test_execute_source_query_allows_empty_bind_params() -> None:
    captured: dict[str, object] = {}

    class _Cursor:
        description = [("ONE",)]

        def execute(self, sql: str, params: dict[str, object]) -> None:
            captured.update(params)

        def fetchall(self) -> list[tuple[int]]:
            return [(1,)]

        def __enter__(self) -> "_Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

        def close(self) -> None:
            return None

    rows = execute_source_query(
        "select 1 as one",
        {},
        config=SnowflakeConnectionConfig(),
        connect=lambda _config: _Connection(),
    )

    assert rows == [{"one": 1}]
    assert captured == {}


def test_execute_source_query_accepts_account_locator_bind() -> None:
    captured: dict[str, object] = {}

    class _Cursor:
        description = [("ACCOUNT_LOCATOR",)]

        def execute(self, sql: str, params: dict[str, object]) -> None:
            captured.update(params)

        def fetchall(self) -> list[tuple[str]]:
            return [("TU24199",)]

        def __enter__(self) -> "_Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

        def close(self) -> None:
            return None

    rows = execute_source_query(
        "select current_account() as account_locator",
        {"window_days": 100, "account_locator": "TU24199"},
        config=SnowflakeConnectionConfig(),
        connect=lambda _config: _Connection(),
    )

    assert rows == [{"account_locator": "TU24199"}]
    assert captured == {"window_days": 100, "account_locator": "TU24199"}


def test_execute_source_query_rejects_malformed_account_locator() -> None:
    with pytest.raises(ValueError, match="account_locator"):
        execute_source_query(
            "select 1",
            {"window_days": 100, "account_locator": "BAD;DROP"},
        )


def test_execute_source_query_rejects_unknown_bind_keys() -> None:
    with pytest.raises(ValueError, match="bind"):
        execute_source_query("select 1", {"window_days": 100, "foo": 1})


def test_execute_source_query_maps_query_errors_to_neutral_message() -> None:
    class _Cursor:
        def execute(self, sql: str, params: dict[str, object]) -> None:
            raise RuntimeError("raw account usage failure")

        def __enter__(self) -> "_Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

        def close(self) -> None:
            return None

    with pytest.raises(SnowflakeQueryError) as exc_info:
        execute_source_query(
            "select 1",
            {},
            config=SnowflakeConnectionConfig(),
            connect=lambda _config: _Connection(),
        )

    assert str(exc_info.value) == "Could not query Snowflake."
    assert "raw account usage failure" not in str(exc_info.value)


def _generate_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def test_loads_private_key_from_pem_content() -> None:
    pem = _generate_pem()
    config = SnowflakeConnectionConfig(
        account="acct",
        user="u",
        role="r",
        warehouse="w",
        database="SNOWFLAKE",
        schema="ACCOUNT_USAGE",
        private_key_pem=pem,
    )
    kwargs = config.connector_kwargs()
    assert isinstance(kwargs["private_key"], bytes) and len(kwargs["private_key"]) > 0


def test_repr_does_not_leak_key_material() -> None:
    pem = _generate_pem()
    config = SnowflakeConnectionConfig(
        account="acct",
        user="u",
        role="r",
        warehouse="w",
        private_key_pem=pem,
        private_key_passphrase="hunter2",
    )
    text = repr(config)
    assert "BEGIN PRIVATE KEY" not in text
    assert "hunter2" not in text


def test_connector_kwargs_rejects_malformed_account() -> None:
    from greysight_connect.snowflake_account import InvalidSnowflakeAccountError

    pem = _generate_pem()
    config = SnowflakeConnectionConfig(
        account="http://evil.example.com",
        user="u",
        role="r",
        warehouse="w",
        private_key_pem=pem,
    )
    with pytest.raises(InvalidSnowflakeAccountError):
        config.connector_kwargs()


def test_execute_source_query_normalizes_connection_failure() -> None:
    def boom(_config: object) -> object:
        raise RuntimeError("connector auth failed")

    with pytest.raises(SnowflakeQueryError) as exc_info:
        execute_source_query("select 1", {}, None, connect=boom)

    assert str(exc_info.value) == "Could not query Snowflake."
    assert "connector auth failed" not in str(exc_info.value)


def test_execute_source_query_normalizes_invalid_account() -> None:
    config = SnowflakeConnectionConfig(
        account="http://evil",
        user="u",
        role="r",
        warehouse="w",
        private_key_pem=_generate_pem(),
    )

    with pytest.raises(SnowflakeQueryError):
        execute_source_query("select 1", {}, config)


# ---------------------------------------------------------------------------
# SnowflakeObjectUnavailableError classification tests
# ---------------------------------------------------------------------------


class _FakeProgrammingError(Exception):
    def __init__(self, msg: str, errno: int) -> None:
        super().__init__(msg)
        self.errno = errno


class _Cursor:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        raise self._exc

    @property
    def description(self):
        return ()

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def cursor(self):
        return _Cursor(self._exc)

    def close(self):
        pass


def test_object_does_not_exist_raises_object_unavailable():
    from greysight_connect.snowflake_client import SnowflakeObjectUnavailableError

    exc = _FakeProgrammingError("Object 'X' does not exist", errno=2003)
    with pytest.raises(SnowflakeObjectUnavailableError):
        execute_source_query(
            "select 1", {"window_days": 30}, connect=lambda _cfg: _Conn(exc)
        )


def test_insufficient_privileges_raises_object_unavailable():
    from greysight_connect.snowflake_client import SnowflakeObjectUnavailableError

    exc = _FakeProgrammingError("Insufficient privileges to operate", errno=3001)
    with pytest.raises(SnowflakeObjectUnavailableError):
        execute_source_query(
            "select 1", {"window_days": 30}, connect=lambda _cfg: _Conn(exc)
        )


def test_other_error_still_raises_generic_query_error():
    exc = _FakeProgrammingError("syntax error", errno=1003)
    with pytest.raises(SnowflakeQueryError):
        execute_source_query(
            "select 1", {"window_days": 30}, connect=lambda _cfg: _Conn(exc)
        )


def test_column_does_not_exist_raises_generic_query_error_not_object_unavailable():
    # errno 904 = "invalid identifier" in Snowflake — not an object-unavailable code.
    # The message contains "does not exist" but NOT "object", so it must NOT be
    # misclassified as SnowflakeObjectUnavailableError.
    exc = _FakeProgrammingError(
        "Column 'foo' does not exist in result set", errno=904
    )
    with pytest.raises(SnowflakeQueryError):
        execute_source_query(
            "select foo from bar", {"window_days": 30}, connect=lambda _cfg: _Conn(exc)
        )
