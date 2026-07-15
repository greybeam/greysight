import logging
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import adbc_driver_manager
import adbc_driver_manager.dbapi as adbc_dbapi
import adbc_driver_snowflake
import pytest

from greysight_connect.snowflake_client import (
    SnowflakeConfigurationError,
    SnowflakeConnectionConfig,
    SnowflakeObjectUnavailableError,
    SnowflakeQueryError,
    SnowflakeValidationError,
    execute_metadata_query,
    execute_source_query,
    validate_snowflake_connection,
)

_ADBC_CONNECT = "greysight_connect.snowflake_client.adbc_driver_snowflake.dbapi.connect"


def _adbc_error(
    message: str,
    *,
    vendor_code: int | None = None,
    sqlstate: str | None = None,
    status_code: adbc_driver_manager.AdbcStatusCode = (
        adbc_driver_manager.AdbcStatusCode.INVALID_ARGUMENT
    ),
) -> adbc_dbapi.ProgrammingError:
    return adbc_dbapi.ProgrammingError(
        message,
        status_code=status_code,
        vendor_code=vendor_code,
        sqlstate=sqlstate,
    )


class _RecordingCursor:
    def __init__(self, *, description, rows):
        self.description = description
        self._rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params) if params is not None else (sql,))

    def fetchall(self):
        return self._rows

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.cursor_kwargs = []

    def cursor(self, **kwargs):
        self.cursor_kwargs.append(kwargs)
        return self._cursor

    def close(self):
        return None


def test_execute_source_query_translates_named_binds_in_occurrence_order() -> None:
    cursor = _RecordingCursor(
        description=[("WINDOW_DAYS",), ("ACCOUNT_LOCATOR",)],
        rows=[(30, "XY12345")],
    )
    connection = _Connection(cursor)

    rows = execute_source_query(
        "select %(window_days)s, %(account_locator)s, %(window_days)s",
        {"window_days": 30, "account_locator": "XY12345"},
        connect=lambda _config: connection,
    )

    assert cursor.executed == [("select ?, ?, ?", (30, "XY12345", 30))]
    assert rows == [{"window_days": 30, "account_locator": "XY12345"}]


def test_execute_source_query_leaves_literal_percent_untouched() -> None:
    cursor = _RecordingCursor(description=[("LITERAL",)], rows=[("50%",)])
    connection = _Connection(cursor)

    rows = execute_source_query(
        "select '50%'",
        {},
        connect=lambda _config: connection,
    )

    assert cursor.executed == [("select '50%'",)]
    assert rows == [{"literal": "50%"}]


def test_source_query_tags_cursor_with_query_tag() -> None:
    cursor = _RecordingCursor(description=[("ONE",)], rows=[(1,)])
    connection = _Connection(cursor)

    execute_source_query("select 1", {}, connect=lambda _config: connection)

    assert connection.cursor_kwargs == [
        {
            "adbc_stmt_kwargs": {
                adbc_driver_snowflake.StatementOptions.QUERY_TAG.value: "greysight"
            }
        }
    ]


def test_execute_source_query_rejects_invalid_window_before_connecting() -> None:
    with patch(_ADBC_CONNECT) as connect:
        with pytest.raises(ValueError, match="window_days"):
            execute_source_query(
                "select %(window_days)s as window_days",
                {"window_days": 0},
            )

    connect.assert_not_called()


def test_execute_source_query_rejects_missing_bind_before_connecting() -> None:
    with patch(_ADBC_CONNECT) as connect:
        with pytest.raises(
            ValueError, match="Missing Snowflake bind param: account_locator"
        ):
            execute_source_query(
                "select %(account_locator)s",
                {"window_days": 30},
            )

    connect.assert_not_called()


@pytest.mark.parametrize(
    ("raw_error", "safe_message"),
    [
        (
            _adbc_error("incorrect username or private key"),
            "Could not authenticate to Snowflake. Check the configured user and private key.",
        ),
        (
            _adbc_error("role ANALYST does not exist or not authorized"),
            "Could not use the configured Snowflake role.",
        ),
        (
            _adbc_error("warehouse LOAD_WH is unavailable"),
            "Could not use the configured Snowflake warehouse.",
        ),
        (
            _adbc_error("insufficient privileges on account usage"),
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
        patch(_ADBC_CONNECT, side_effect=raw_error),
        patch.object(SnowflakeConnectionConfig, "adbc_db_kwargs", return_value={}),
    ):
        with pytest.raises(SnowflakeValidationError) as exc_info:
            validate_snowflake_connection()

    assert str(exc_info.value).startswith(safe_message)
    assert str(raw_error) not in str(exc_info.value)


def test_validation_reports_safe_network_policy_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reference = "0ce9eb56-821d-4ca9-a774-04ae89a0cf5a"
    raw_error = _adbc_error(
        "Incoming request with IP/Token PEMSECRETMARKER is not allowed to "
        f"access Snowflake. [{reference}]",
        vendor_code=250001,
        sqlstate="08001",
    )

    with (
        patch(_ADBC_CONNECT, side_effect=raw_error),
        patch.object(SnowflakeConnectionConfig, "adbc_db_kwargs", return_value={}),
        caplog.at_level(logging.WARNING, logger="greysight_connect.snowflake_client"),
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
    assert "exception=ProgrammingError" in caplog.text
    assert "errno=250001" in caplog.text
    assert "sqlstate=08001" in caplog.text
    assert f"reference={reference}" in caplog.text
    assert "PEMSECRETMARKER" not in caplog.text


def test_source_query_preserves_safe_network_policy_message() -> None:
    raw_error = _adbc_error(
        "Incoming request with IP/Token PEMSECRETMARKER is not allowed to "
        "access Snowflake."
    )

    with (
        patch(_ADBC_CONNECT, side_effect=raw_error),
        patch.object(SnowflakeConnectionConfig, "adbc_db_kwargs", return_value={}),
        pytest.raises(SnowflakeQueryError) as exc_info,
    ):
        execute_source_query("select 1", {})

    assert "network policy" in str(exc_info.value).lower()
    assert "PEMSECRETMARKER" not in str(exc_info.value)


def test_metadata_query_preserves_safe_network_policy_message() -> None:
    raw_error = _adbc_error(
        "Incoming request with IP/Token PEMSECRETMARKER is not allowed to "
        "access Snowflake."
    )

    with (
        patch(_ADBC_CONNECT, side_effect=raw_error),
        patch.object(SnowflakeConnectionConfig, "adbc_db_kwargs", return_value={}),
        pytest.raises(SnowflakeQueryError) as exc_info,
    ):
        execute_metadata_query("SHOW WAREHOUSES")

    assert exc_info.value.user_safe_message is not None
    assert "network policy" in exc_info.value.user_safe_message.lower()
    assert "PEMSECRETMARKER" not in str(exc_info.value)


def test_source_query_does_not_classify_fallback_validation_message() -> None:
    raw_error = _adbc_error("UNCLASSIFIEDSECRETMARKER")

    with (
        patch(_ADBC_CONNECT, side_effect=raw_error),
        patch.object(SnowflakeConnectionConfig, "adbc_db_kwargs", return_value={}),
        pytest.raises(SnowflakeQueryError) as exc_info,
    ):
        execute_source_query("select 1", {})

    assert exc_info.value.user_safe_message is None
    assert "UNCLASSIFIEDSECRETMARKER" not in str(exc_info.value)


def test_validation_probe_identifies_unavailable_account_usage_view(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_error = _adbc_error(
        "Insufficient privileges on QUERY_ATTRIBUTION_HISTORY SECRETQUERYMARKER",
        vendor_code=3001,
    )
    cursor = Mock()

    def execute(sql: str, params: object = None) -> None:
        if "QUERY_ATTRIBUTION_HISTORY" in sql:
            raise raw_error

    cursor.execute.side_effect = execute
    connection = Mock()
    connection.cursor.return_value = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with (
        patch(_ADBC_CONNECT, return_value=connection),
        patch.object(SnowflakeConnectionConfig, "adbc_db_kwargs", return_value={}),
        caplog.at_level(logging.WARNING, logger="greysight_connect.snowflake_client"),
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
    raw_error = _adbc_error(
        f"SECRETQUERYMARKER [{unrelated_reference}]",
        status_code=adbc_driver_manager.AdbcStatusCode.TIMEOUT,
    )
    cursor = Mock()

    def execute(sql: str, params: object = None) -> None:
        if "QUERY_ATTRIBUTION_HISTORY" in sql:
            raise raw_error

    cursor.execute.side_effect = execute
    connection = Mock()
    connection.cursor.return_value = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with (
        patch(_ADBC_CONNECT, return_value=connection),
        patch.object(SnowflakeConnectionConfig, "adbc_db_kwargs", return_value={}),
        caplog.at_level(logging.WARNING, logger="greysight_connect.snowflake_client"),
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
        patch(_ADBC_CONNECT, return_value=connection) as connect,
        patch.object(
            SnowflakeConnectionConfig,
            "adbc_db_kwargs",
            return_value={"adbc.snowflake.sql.account": "account"},
        ),
    ):
        locator = validate_snowflake_connection()

    assert connect.call_args.kwargs["db_kwargs"] == {
        "adbc.snowflake.sql.account": "account"
    }
    assert connect.call_args.kwargs["autocommit"] is True
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
        config.adbc_db_kwargs()
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
            "adbc_db_kwargs",
            side_effect=raw_error,
        ),
        caplog.at_level(logging.WARNING, logger="greysight_connect.snowflake_client"),
        pytest.raises(SnowflakeConfigurationError),
    ):
        validate_snowflake_connection()

    assert "phase=configuration" in caplog.text
    assert "exception=SnowflakeConfigurationError" in caplog.text
    assert "SECRETCONFIGMARKER" not in caplog.text


def test_execute_source_query_allows_empty_bind_params() -> None:
    cursor = _RecordingCursor(description=[("ONE",)], rows=[(1,)])
    connection = _Connection(cursor)

    rows = execute_source_query(
        "select 1 as one",
        {},
        config=SnowflakeConnectionConfig(),
        connect=lambda _config: connection,
    )

    assert rows == [{"one": 1}]
    assert cursor.executed == [("select 1 as one",)]


def test_execute_source_query_accepts_account_locator_bind() -> None:
    cursor = _RecordingCursor(description=[("ACCOUNT_LOCATOR",)], rows=[("TU24199",)])
    connection = _Connection(cursor)

    rows = execute_source_query(
        "select %(account_locator)s as account_locator",
        {"window_days": 100, "account_locator": "TU24199"},
        config=SnowflakeConnectionConfig(),
        connect=lambda _config: connection,
    )

    assert rows == [{"account_locator": "TU24199"}]
    assert cursor.executed == [("select ? as account_locator", ("TU24199",))]


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
        def execute(self, sql: str, params: object = None) -> None:
            raise RuntimeError("raw account usage failure")

        def __enter__(self) -> "_Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Conn:
        def cursor(self, **kwargs: object) -> _Cursor:
            return _Cursor()

        def close(self) -> None:
            return None

    with pytest.raises(SnowflakeQueryError) as exc_info:
        execute_source_query(
            "select 1",
            {},
            config=SnowflakeConnectionConfig(),
            connect=lambda _config: _Conn(),
        )

    assert str(exc_info.value) == "Could not query Snowflake."
    assert "raw account usage failure" not in str(exc_info.value)


def _generate_pem(*, passphrase: str | None = None) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode("utf-8"))
        if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    ).decode("utf-8")


def test_adbc_db_kwargs_preserves_existing_jwt_onboarding_contract() -> None:
    pem = _generate_pem(passphrase="hunter2")
    config = SnowflakeConnectionConfig(
        account="ORG-ACCOUNT",
        user="svc",
        role="GREYSIGHT_RL",
        warehouse="GREYSIGHT_WH",
        database=None,
        schema=None,
        private_key_pem=pem,
        private_key_passphrase="hunter2",
        query_timeout_seconds=120,
    )

    options = config.adbc_db_kwargs()

    assert options["adbc.snowflake.sql.account"] == "ORG-ACCOUNT"
    assert options["username"] == "svc"
    assert options["adbc.snowflake.sql.role"] == "GREYSIGHT_RL"
    assert options["adbc.snowflake.sql.warehouse"] == "GREYSIGHT_WH"
    assert options["adbc.snowflake.sql.db"] == "SNOWFLAKE"
    assert options["adbc.snowflake.sql.schema"] == "ACCOUNT_USAGE"
    assert options["adbc.snowflake.sql.auth_type"] == "auth_jwt"
    assert (
        options["adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_value"] == pem
    )
    assert (
        options["adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_password"]
        == "hunter2"
    )
    assert "password" not in options


def test_adbc_db_kwargs_reads_private_key_from_path(tmp_path: Path) -> None:
    pem = _generate_pem(passphrase="hunter2")
    key_path = tmp_path / "snowflake_key.p8"
    key_path.write_text(pem, encoding="utf-8")
    config = SnowflakeConnectionConfig(
        account="ORG-ACCOUNT",
        user="svc",
        role="GREYSIGHT_RL",
        warehouse="GREYSIGHT_WH",
        private_key_path=key_path,
        private_key_passphrase="hunter2",
    )

    options = config.adbc_db_kwargs()

    assert (
        options["adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_value"] == pem
    )
    assert (
        options["adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_password"]
        == "hunter2"
    )


def test_adbc_db_kwargs_rejects_missing_fields() -> None:
    config = SnowflakeConnectionConfig(account="acct")
    with pytest.raises(SnowflakeConfigurationError) as exc_info:
        config.adbc_db_kwargs()
    assert "Missing" in str(exc_info.value)


def test_adbc_db_kwargs_rejects_wrong_passphrase() -> None:
    pem = _generate_pem(passphrase="hunter2")
    config = SnowflakeConnectionConfig(
        account="acct",
        user="u",
        role="r",
        warehouse="w",
        private_key_pem=pem,
        private_key_passphrase="wrong",
    )
    with pytest.raises(SnowflakeConfigurationError):
        config.adbc_db_kwargs()


def test_adbc_db_kwargs_rejects_malformed_pem() -> None:
    config = SnowflakeConnectionConfig(
        account="acct",
        user="u",
        role="r",
        warehouse="w",
        private_key_pem="not a valid key",
    )
    with pytest.raises(SnowflakeConfigurationError):
        config.adbc_db_kwargs()


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


def test_adbc_db_kwargs_rejects_malformed_account() -> None:
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
        config.adbc_db_kwargs()


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
# SnowflakeObjectUnavailableError classification tests (ADBC vendor codes)
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        raise self._exc

    @property
    def description(self):
        return ()

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def cursor(self, **kwargs):
        return _Cursor(self._exc)

    def close(self):
        pass


def test_object_does_not_exist_raises_object_unavailable():
    exc = _adbc_error("Object 'X' does not exist", vendor_code=2003)
    with pytest.raises(SnowflakeObjectUnavailableError):
        execute_source_query(
            "select 1", {"window_days": 30}, connect=lambda _cfg: _Conn(exc)
        )


def test_insufficient_privileges_raises_object_unavailable():
    exc = _adbc_error("Insufficient privileges to operate", vendor_code=3001)
    with pytest.raises(SnowflakeObjectUnavailableError):
        execute_source_query(
            "select 1", {"window_days": 30}, connect=lambda _cfg: _Conn(exc)
        )


def test_unrelated_vendor_code_still_raises_generic_query_error():
    exc = _adbc_error("syntax error near foo", vendor_code=1003)
    with pytest.raises(SnowflakeQueryError) as exc_info:
        execute_source_query(
            "select 1", {"window_days": 30}, connect=lambda _cfg: _Conn(exc)
        )
    assert not isinstance(exc_info.value, SnowflakeObjectUnavailableError)


def test_missing_vendor_code_still_raises_generic_query_error():
    exc = _adbc_error("some backend failure with no vendor code")
    with pytest.raises(SnowflakeQueryError) as exc_info:
        execute_source_query(
            "select 1", {"window_days": 30}, connect=lambda _cfg: _Conn(exc)
        )
    assert not isinstance(exc_info.value, SnowflakeObjectUnavailableError)


def test_column_does_not_exist_raises_generic_query_error_not_object_unavailable():
    # vendor 904 = "invalid identifier" in Snowflake — not an object-unavailable code.
    # The message contains "does not exist" but NOT "object", so it must NOT be
    # misclassified as SnowflakeObjectUnavailableError.
    exc = _adbc_error("Column 'foo' does not exist in result set", vendor_code=904)
    with pytest.raises(SnowflakeQueryError) as exc_info:
        execute_source_query(
            "select foo from bar", {"window_days": 30}, connect=lambda _cfg: _Conn(exc)
        )
    assert not isinstance(exc_info.value, SnowflakeObjectUnavailableError)
