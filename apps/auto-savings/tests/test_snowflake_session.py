from unittest.mock import Mock

import adbc_driver_manager
import adbc_driver_manager.dbapi as adbc_dbapi
import adbc_driver_snowflake
import pytest

from auto_savings.snowflake_session import (
    ConnectorErrorMetadata,
    SuspendOutcome,
    SuspendResult,
    TenantSession,
    _sanitize_connector_message,
    connection_fingerprint,
    connector_error_metadata,
    next_backoff,
)


def _adbc_error(
    error_class: type[adbc_dbapi.Error],
    message: str,
    *,
    vendor_code: int | None = None,
    sqlstate: str | None = None,
    status_code: adbc_driver_manager.AdbcStatusCode = (
        adbc_driver_manager.AdbcStatusCode.INVALID_ARGUMENT
    ),
) -> adbc_dbapi.Error:
    return error_class(
        message,
        status_code=status_code,
        vendor_code=vendor_code,
        sqlstate=sqlstate,
    )


def test_sanitize_normalizes_whitespace_and_truncates():
    assert _sanitize_connector_message("  a\n\tb   c  ") == "a b c"
    assert _sanitize_connector_message(None) is None
    assert _sanitize_connector_message("   ") is None
    long = "x" * 1000
    assert _sanitize_connector_message(long) == "x" * 512


def test_sanitize_ordinary_message_unchanged():
    assert (
        _sanitize_connector_message("warehouse suspend failed: object does not exist")
        == "warehouse suspend failed: object does not exist"
    )


def test_sanitize_redacts_unencrypted_pem_block():
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ\n"
        "secretsecretsecretsecretsecretsecretsecretsecret\n"
        "-----END PRIVATE KEY-----"
    )
    result = _sanitize_connector_message(f"connect failed: {pem} for user svc")
    assert result is not None
    assert "[REDACTED]" in result
    assert "MIIEvQIBADAN" not in result
    assert "secretsecret" not in result
    assert "PRIVATE KEY" not in result
    assert result.startswith("connect failed:")
    assert result.endswith("for user svc")


def test_sanitize_redacts_encrypted_pem_block():
    pem = (
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
        "MIIFHzBJBgkqhkiG9w0BBQ0wPDAbBgkqhkiG9w0BBQwwDgQI\n"
        "-----END ENCRYPTED PRIVATE KEY-----"
    )
    result = _sanitize_connector_message(f"bad key: {pem}")
    assert result is not None
    assert "MIIFHzBJ" not in result
    assert "ENCRYPTED PRIVATE KEY" not in result
    assert "[REDACTED]" in result


def test_sanitize_redacts_adbc_key_and_passphrase_option_values():
    msg = (
        "failed opening connection: "
        "jwt_private_key_pkcs8_value=MIIEvQIBADANsecretkeymaterial;"
        "jwt_private_key_pkcs8_password=hunter2passphrase;"
        "username=svc"
    )
    result = _sanitize_connector_message(msg)
    assert result is not None
    assert "MIIEvQIBADANsecretkeymaterial" not in result
    assert "hunter2passphrase" not in result
    assert "[REDACTED]" in result
    # Unquoted values have no reliable terminator, so redaction fails safe
    # through the end of the message: the trailing option is swallowed too.
    assert result == "failed opening connection: jwt_private_key_pkcs8_value=[REDACTED]"


# Value-end detection across quoting conventions (whitespace, `=>`, backslash
# escapes, doubled quotes, unterminated quotes) proved unreliable, so a secret
# key redacts unconditionally from the value start to the END of the message.
@pytest.mark.parametrize(
    "value",
    [
        "correct horse battery",  # bare, whitespace-separated
        "=> secret",  # odd separator
        "'correct horse battery staple'",  # single-quoted
        '"correct horse battery staple"',  # double-quoted
        '"correct\\"horse"',  # backslash-escaped quote
        '"correct""horse"',  # doubled-quote escape
        "'correct horse battery staple",  # unterminated quote
    ],
)
@pytest.mark.parametrize(
    "option",
    ["password", "private_key_passphrase", "jwt_private_key_pkcs8_password"],
)
def test_sanitize_redacts_secret_values_to_end_of_message(option, value):
    msg = f"connect failed: {option}={value}; username=svc"
    result = _sanitize_connector_message(msg)
    assert result == f"connect failed: {option}=[REDACTED]"
    for fragment in ("correct", "horse", "battery", "secret", "username=svc"):
        assert fragment not in result


def test_sanitize_redacts_colon_separated_secret_to_end_of_message():
    msg = 'connect failed: PASSWORD : "correct horse battery"; username=svc'
    result = _sanitize_connector_message(msg)
    assert result == "connect failed: PASSWORD=[REDACTED]"


def test_sanitize_redacts_json_style_secret_values():
    msg = 'options {"user": "svc", "password": "hunter two three"} rejected'
    result = _sanitize_connector_message(msg)
    assert result is not None
    for fragment in ("hunter", "two", "three", "rejected"):
        assert fragment not in result
    assert "[REDACTED]" in result
    assert '"user": "svc"' in result  # text BEFORE the secret key preserved


def test_connector_error_metadata_never_leaks_quoted_passphrase_fragments():
    exc = _adbc_error(
        adbc_dbapi.OperationalError,
        'auth failed: private_key_passphrase="hunter two three four" rejected',
        vendor_code=250001,
    )
    metadata = connector_error_metadata(exc)
    assert metadata.message is not None
    for fragment in ("hunter", "two", "three", "four"):
        assert fragment not in metadata.message
    assert "[REDACTED]" in metadata.message


def test_sanitize_truncated_pem_without_end_marker_fails_safe():
    # A PEM whose END marker was cut off (e.g. by the driver's own truncation)
    # must not leak its header or key fragment; redact through end of message.
    msg = (
        "connect failed with -----BEGIN ENCRYPTED PRIVATE KEY----- "
        "MIIFHzBJBgkqhkiG9w0BBQ0wPDAbBgkq trailing text"
    )
    result = _sanitize_connector_message(msg)
    assert result is not None
    assert "MIIFHzBJ" not in result
    assert "PRIVATE KEY" not in result
    assert "trailing text" not in result
    assert "[REDACTED]" in result
    assert result.startswith("connect failed with")


def test_sanitize_redacts_before_truncation():
    # A PEM near the 512 boundary must not leak a partial secret through the cut.
    pem = "-----BEGIN PRIVATE KEY-----" + "A" * 2000 + "-----END PRIVATE KEY-----"
    result = _sanitize_connector_message(pem)
    assert result is not None
    assert "AAAA" not in result
    assert result == "[REDACTED]"


def test_connector_error_metadata_message_never_carries_key_material():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANsecret\n-----END PRIVATE KEY-----"
    exc = _adbc_error(
        adbc_dbapi.OperationalError,
        f"auth failed with {pem} and jwt_private_key_pkcs8_password=topsecret",
        vendor_code=250001,
    )
    metadata = connector_error_metadata(exc)
    assert metadata.message is not None
    assert "MIIEvQIBADANsecret" not in metadata.message
    assert "topsecret" not in metadata.message
    assert "PRIVATE KEY" not in metadata.message
    assert "[REDACTED]" in metadata.message


def test_default_connect_bounds_all_adbc_timeouts_and_keeps_session_alive(monkeypatch):
    config = Mock()
    config.adbc_db_kwargs.return_value = {"username": "svc"}
    connection = Mock()
    connect = Mock(return_value=connection)
    monkeypatch.setattr(adbc_driver_snowflake.dbapi, "connect", connect)

    session = TenantSession(config=config, socket_timeout_seconds=15)
    session.ensure_connected()

    config.adbc_db_kwargs.assert_called_once_with(
        timeout_seconds=15,
        keep_session_alive=True,
    )
    connect.assert_called_once_with(
        db_kwargs={"username": "svc"},
        autocommit=True,
    )


def test_show_warehouses_reuses_one_connection():
    cursor = Mock()
    cursor.description = [("name",), ("state",)]
    cursor.fetchall.return_value = [("WH1", "STARTED")]
    connection = Mock()
    connection.cursor.return_value = cursor
    connects = []

    def fake_connect(_config):
        connects.append(1)
        return connection

    session = TenantSession(
        config=object(), socket_timeout_seconds=15, connect=fake_connect
    )
    session.show_warehouses()
    session.show_warehouses()
    assert len(connects) == 1  # warm reuse, not reconnect-per-poll


def test_suspend_quotes_identifier_and_returns_accepted():
    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(
        config=object(), socket_timeout_seconds=15, connect=lambda c: connection
    )

    assert session.suspend_warehouse('weird"name') == SuspendResult(
        outcome=SuspendOutcome.ACCEPTED,
        connector_error=None,
    )

    cursor.execute.assert_called_once_with('ALTER WAREHOUSE "weird""name" SUSPEND')


def test_suspend_90064_is_unknown_without_claiming_success(caplog):
    cursor = Mock()
    cursor.execute.side_effect = _adbc_error(
        adbc_dbapi.ProgrammingError,
        "observed\nrace",
        vendor_code=90064,
        sqlstate="57014",
    )
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(
        config=object(),
        socket_timeout_seconds=15,
        connect=lambda _config: connection,
    )

    with caplog.at_level("WARNING"):
        outcome = session.suspend_warehouse("WH1")

    assert outcome == SuspendResult(
        outcome=SuspendOutcome.UNKNOWN_IDEMPOTENT,
        connector_error=ConnectorErrorMetadata(
            error_type="ProgrammingError",
            errno=90064,
            sqlstate="57014",
            message="observed race",
        ),
    )
    record = caplog.records[-1]
    assert record.connector_error_type == "ProgrammingError"
    assert record.connector_errno == 90064
    assert record.connector_sqlstate == "57014"
    assert record.connector_message == "observed race"


def test_suspend_90064_text_without_vendor_code_raises_normally():
    # Only a real vendor code 90064 is the unknown-but-idempotent signal. An
    # error whose text merely mentions the same code but carries no vendor code
    # must fail closed: it propagates so the engine reconnects and backs off.
    error = _adbc_error(
        adbc_dbapi.ProgrammingError,
        "observed race 90064",
        vendor_code=None,
        sqlstate="57014",
    )
    cursor = Mock()
    cursor.execute.side_effect = error
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(
        config=object(),
        socket_timeout_seconds=15,
        connect=lambda _config: connection,
    )

    with pytest.raises(adbc_dbapi.ProgrammingError):
        session.suspend_warehouse("WH1")


def test_suspend_connection_failure_propagates_as_ambiguous():
    cursor = Mock()
    cursor.execute.side_effect = _adbc_error(
        adbc_dbapi.OperationalError,
        "lost",
        status_code=adbc_driver_manager.AdbcStatusCode.IO,
    )
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(
        config=object(),
        socket_timeout_seconds=15,
        connect=lambda _config: connection,
    )

    with pytest.raises(adbc_dbapi.OperationalError, match="lost"):
        session.suspend_warehouse("WH1")


@pytest.mark.parametrize("execute_fails", [False, True])
def test_suspend_cursor_close_failure_never_masks_the_command_result(execute_fails):
    # A failing cursor.close() must never change the outcome: an accepted command
    # stays accepted, and an execute failure is preserved as the raised error.
    cursor = Mock()
    if execute_fails:
        cursor.execute.side_effect = ValueError("execute failed")
    cursor.close.side_effect = RuntimeError("close failed")
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(
        config=object(),
        socket_timeout_seconds=15,
        connect=lambda _config: connection,
    )

    if execute_fails:
        with pytest.raises(ValueError, match="execute failed"):
            session.suspend_warehouse("WH1")
    else:
        outcome = session.suspend_warehouse("WH1")
        assert outcome == SuspendResult(
            outcome=SuspendOutcome.ACCEPTED,
            connector_error=None,
        )
        cursor.execute.assert_called_once()


def test_close_hard_closes_old_connection_and_next_op_reconnects():
    # D-lifecycle regression: after close_hard(), the OLD connection's .close()
    # must have been called, and a subsequent operation must establish a NEW
    # connection (not silently reuse a stale/closed one) — the wedge-escape only
    # works if the next tick actually reconnects (finding #20).
    connections = []

    def fake_connect(_config):
        conn = Mock()
        connections.append(conn)
        return conn

    session = TenantSession(
        config=object(), socket_timeout_seconds=15, connect=fake_connect
    )
    session.ensure_connected()
    first_connection = connections[0]
    assert len(connections) == 1

    session.close_hard()
    assert first_connection.close.called is True

    session.ensure_connected()
    assert len(connections) == 2  # a NEW connection was established
    assert connections[1] is not first_connection

    cursor = Mock()
    cursor.description = [("name",), ("state",)]
    cursor.fetchall.return_value = [("WH1", "STARTED")]
    connections[1].cursor.return_value = cursor
    session.show_warehouses()
    assert len(connections) == 2  # reused the reconnected session, not a third connect


def test_backoff_is_bounded_and_jittered():
    assert next_backoff(0, base=0.5, cap=30.0, jitter=lambda: 1.0) == 0.5
    assert next_backoff(10, base=0.5, cap=30.0, jitter=lambda: 1.0) == 30.0  # capped


def test_backoff_saturates_huge_attempt_without_hanging():
    # #12a: a very large attempt must not compute 2**attempt on a huge int. The
    # exponent is clamped, so this returns the cap instantly.
    assert next_backoff(10_000_000, base=0.5, cap=30.0, jitter=lambda: 1.0) == 30.0


def test_connection_fingerprint_changes_when_identity_rotates():
    class Cfg:
        def __init__(self, account, user, pem, *, role="ROLE_A", passphrase="PASS_A"):
            self.account = account
            self.account_locator = None
            self.user = user
            self.role = role
            self.warehouse = "WH"
            self.database = "DB"
            self.schema = "SCHEMA"
            self.private_key_pem = pem
            self.private_key_path = None
            self.private_key_passphrase = passphrase
            self.query_timeout_seconds = 120

    base = connection_fingerprint(Cfg("acct1", "svc", "PEM-A"))
    assert base == connection_fingerprint(Cfg("acct1", "svc", "PEM-A"))  # stable
    assert base != connection_fingerprint(Cfg("acct2", "svc", "PEM-A"))  # account
    assert base != connection_fingerprint(Cfg("acct1", "other", "PEM-A"))  # user
    assert base != connection_fingerprint(Cfg("acct1", "svc", "PEM-B"))  # key
    assert base != connection_fingerprint(Cfg("acct1", "svc", "PEM-A", role="ROLE_B"))
    assert base != connection_fingerprint(
        Cfg("acct1", "svc", "PEM-A", passphrase="PASS_B")
    )


def test_connection_fingerprint_tracks_path_key_contents_and_handles_unreadable(
    tmp_path,
):
    key_path = tmp_path / "tenant-key.pem"
    key_path.write_text("KEY-A")

    class Cfg:
        account = "acct1"
        account_locator = None
        user = "svc"
        role = "ROLE_A"
        warehouse = "WH"
        database = "DB"
        schema = "SCHEMA"
        private_key_pem = None
        private_key_path = key_path
        private_key_passphrase = None
        query_timeout_seconds = 120

    # Rotating the key file contents rotates the fingerprint.
    before = connection_fingerprint(Cfg())
    key_path.write_text("KEY-B")
    assert connection_fingerprint(Cfg()) != before

    # An unreadable/missing key path yields a stable fingerprint that still
    # differs by path (so a rotated-but-missing key is not silently reused).
    class MissingCfg(Cfg):
        private_key_path = tmp_path / "missing.pem"

    missing_fingerprint = connection_fingerprint(MissingCfg())
    assert missing_fingerprint == connection_fingerprint(MissingCfg())

    class OtherMissingCfg(Cfg):
        private_key_path = tmp_path / "other-missing.pem"

    assert connection_fingerprint(OtherMissingCfg()) != missing_fingerprint
