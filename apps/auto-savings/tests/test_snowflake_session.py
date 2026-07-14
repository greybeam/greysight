from unittest.mock import Mock

import pytest
import snowflake.connector

from auto_savings.snowflake_session import (
    ConnectorErrorMetadata,
    SuspendOutcome,
    SuspendResult,
    TenantSession,
    connection_fingerprint,
    next_backoff,
)


def test_default_connect_forwards_socket_timeouts_and_bounds_login_timeout(monkeypatch):
    # The DEFAULT (non-injected) connect path must forward the socket/network
    # timeouts to the real snowflake.connector.connect (D2), and a hung initial
    # connect must not outlive the poll watchdog: login_timeout (defaulted to the
    # 120s query timeout by connector_kwargs) is overridden down to
    # socket_timeout_seconds, which config validation keeps < poll_timeout (#2b).
    import snowflake.connector as sf

    class Cfg:
        def connector_kwargs(self):
            return {"account": "ab12345", "user": "svc", "login_timeout": 120}

    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return Mock()

    monkeypatch.setattr(sf, "connect", fake_connect)

    session = TenantSession(config=Cfg(), socket_timeout_seconds=15)  # no `connect` injected
    session.ensure_connected()

    assert captured["socket_timeout"] == 15
    assert captured["network_timeout"] == 15
    assert captured["client_session_keep_alive"] is True
    assert captured["account"] == "ab12345"
    assert captured["login_timeout"] <= 15  # <= socket_timeout_seconds (< poll_timeout)


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

    session = TenantSession(config=object(), socket_timeout_seconds=15, connect=fake_connect)
    session.show_warehouses()
    session.show_warehouses()
    assert len(connects) == 1  # warm reuse, not reconnect-per-poll


def test_suspend_quotes_identifier_and_returns_accepted():
    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(config=object(), socket_timeout_seconds=15, connect=lambda c: connection)

    assert session.suspend_warehouse('weird"name') == SuspendResult(
        outcome=SuspendOutcome.ACCEPTED,
        connector_error=None,
    )

    cursor.execute.assert_called_once_with('ALTER WAREHOUSE "weird""name" SUSPEND')


def test_suspend_90064_is_unknown_without_claiming_success(caplog):
    cursor = Mock()
    cursor.execute.side_effect = snowflake.connector.errors.ProgrammingError(
        msg="observed\nrace",
        errno=90064,
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


def test_suspend_connection_failure_propagates_as_ambiguous():
    cursor = Mock()
    cursor.execute.side_effect = snowflake.connector.errors.OperationalError("lost")
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(
        config=object(),
        socket_timeout_seconds=15,
        connect=lambda _config: connection,
    )

    with pytest.raises(snowflake.connector.errors.OperationalError, match="lost"):
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

    session = TenantSession(config=object(), socket_timeout_seconds=15, connect=fake_connect)
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
    assert base != connection_fingerprint(
        Cfg("acct1", "svc", "PEM-A", role="ROLE_B")
    )
    assert base != connection_fingerprint(
        Cfg("acct1", "svc", "PEM-A", passphrase="PASS_B")
    )


def test_connection_fingerprint_tracks_path_key_contents_and_handles_unreadable(tmp_path):
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
