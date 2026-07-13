from unittest.mock import Mock

from auto_savings.snowflake_session import (
    TenantSession,
    connection_fingerprint,
    next_backoff,
)


def test_default_connect_passes_socket_timeouts_to_connector(monkeypatch):
    # D2 regression: the DEFAULT (non-injected) connect path must forward the
    # socket/network timeouts to the real snowflake.connector.connect, or the
    # wedge-escape never applies in production.
    import snowflake.connector as sf

    class Cfg:
        def connector_kwargs(self):
            return {"account": "ab12345", "user": "svc"}

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


def test_default_connect_bounds_login_timeout_below_poll_watchdog(monkeypatch):
    # #2b: a hung initial connect must not outlive the poll watchdog. login_timeout
    # (defaulted to the 120s query timeout by connector_kwargs) is overridden down
    # to socket_timeout_seconds, which config validation keeps < poll_timeout.
    import snowflake.connector as sf

    class Cfg:
        def connector_kwargs(self):
            return {"account": "ab12345", "user": "svc", "login_timeout": 120}

    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return Mock()

    monkeypatch.setattr(sf, "connect", fake_connect)

    session = TenantSession(config=Cfg(), socket_timeout_seconds=15)
    session.ensure_connected()

    assert "login_timeout" in captured
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


def test_alter_quotes_identifier():
    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(config=object(), socket_timeout_seconds=15, connect=lambda c: connection)
    session.alter_auto_suspend('weird"name', 1)
    sql = cursor.execute.call_args[0][0]
    assert '"weird""name"' in sql
    assert "SET AUTO_SUSPEND = 1" in sql


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
        def __init__(self, account, user, pem):
            self.account = account
            self.account_locator = None
            self.user = user
            self.private_key_pem = pem
            self.private_key_path = None

    base = connection_fingerprint(Cfg("acct1", "svc", "PEM-A"))
    assert base == connection_fingerprint(Cfg("acct1", "svc", "PEM-A"))  # stable
    assert base != connection_fingerprint(Cfg("acct2", "svc", "PEM-A"))  # account
    assert base != connection_fingerprint(Cfg("acct1", "other", "PEM-A"))  # user
    assert base != connection_fingerprint(Cfg("acct1", "svc", "PEM-B"))  # key
