from unittest.mock import Mock

from auto_savings.snowflake_session import TenantSession, next_backoff


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


def test_connector_kwargs_include_socket_timeouts():
    # The real wedge-escape: connector network/socket timeouts, not close() (finding #6).
    class Cfg:
        def connector_kwargs(self):
            return {"account": "ab12345", "user": "svc"}

    session = TenantSession(config=Cfg(), socket_timeout_seconds=15, connect=lambda c: Mock())
    kwargs = session._connector_kwargs()  # the dict the default connect passes to snowflake.connector.connect
    assert kwargs["network_timeout"] == 15
    assert kwargs["socket_timeout"] == 15
    assert kwargs["client_session_keep_alive"] is True


def test_alter_quotes_identifier():
    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor
    session = TenantSession(config=object(), socket_timeout_seconds=15, connect=lambda c: connection)
    session.alter_auto_suspend('weird"name', 1)
    sql = cursor.execute.call_args[0][0]
    assert '"weird""name"' in sql
    assert "SET AUTO_SUSPEND = 1" in sql


def test_close_hard_closes_connection():
    connection = Mock()
    session = TenantSession(config=object(), socket_timeout_seconds=15, connect=lambda c: connection)
    session.ensure_connected()
    session.close_hard()
    connection.close.assert_called_once()


def test_backoff_is_bounded_and_jittered():
    assert next_backoff(0, base=0.5, cap=30.0, jitter=lambda: 1.0) == 0.5
    assert next_backoff(10, base=0.5, cap=30.0, jitter=lambda: 1.0) == 30.0  # capped
