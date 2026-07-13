"""Warm, persistent per-tenant Snowflake session.

The wedge-escape mechanism here is connector-level socket timeouts, not
`close()`. A blocking C-level `recv()` inside the snowflake connector cannot
be interrupted by calling `close()` from another thread; it can only be
freed by the OS-level read timing out on its own. `socket_timeout_seconds`
MUST be strictly less than the caller's poll timeout so the socket read
fails before the watchdog trips (see WorkerConfig.__post_init__).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

try:
    import snowflake.connector as _snowflake_connector
except ImportError:  # pragma: no cover - snowflake connector optional at import time
    _snowflake_connector = None


def _connect_with_kwargs(kwargs: dict) -> Any:
    if _snowflake_connector is None:  # pragma: no cover
        raise RuntimeError("snowflake-connector-python is not installed")
    return _snowflake_connector.connect(**kwargs)


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class TenantSession:
    """One persistent Snowflake connection reused across polls for a tenant."""

    def __init__(
        self,
        config: Any,
        *,
        socket_timeout_seconds: int,
        connect: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        self._config = config
        self.socket_timeout_seconds = socket_timeout_seconds
        # An injected `connect` (tests) receives the config and returns a
        # connection. The default path builds the connection itself so it can
        # pass the socket/network timeouts — the load-bearing wedge-escape.
        self._connect = connect
        self._connection: Any = None

    def _connector_kwargs(self) -> dict:
        kwargs = dict(self._config.connector_kwargs())
        kwargs["client_session_keep_alive"] = True
        kwargs["network_timeout"] = self.socket_timeout_seconds
        kwargs["socket_timeout"] = self.socket_timeout_seconds
        return kwargs

    def ensure_connected(self) -> None:
        if self._connection is None:
            if self._connect is not None:
                self._connection = self._connect(self._config)
            else:
                # Default path: pass the socket/network timeouts through to the
                # real Snowflake connection so a wedged recv() times out on its own.
                self._connection = _connect_with_kwargs(self._connector_kwargs())

    def show_warehouses(self) -> list[dict]:
        self.ensure_connected()
        cursor = self._connection.cursor()
        cursor.execute("SHOW WAREHOUSES")
        columns = [col[0].lower() for col in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def alter_auto_suspend(self, name: str, value: int) -> None:
        self.ensure_connected()
        cursor = self._connection.cursor()
        quoted = _quote_identifier(name)
        cursor.execute(f"ALTER WAREHOUSE {quoted} SET AUTO_SUSPEND = {value}")

    def close_hard(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            connection.close()
        except Exception:
            pass


def next_backoff(
    attempt: int,
    *,
    base: float = 0.5,
    cap: float = 30.0,
    jitter: Callable[[], float],
) -> float:
    """Jittered exponential backoff, bounded by `cap`."""
    raw = min(cap, base * (2**attempt))
    return min(cap, raw * jitter())
