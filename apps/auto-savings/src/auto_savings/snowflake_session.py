"""Warm, persistent per-tenant Snowflake session.

The wedge-escape mechanism here is connector-level socket timeouts, not
`close()`. A blocking C-level `recv()` inside the snowflake connector cannot
be interrupted by calling `close()` from another thread; it can only be
freed by the OS-level read timing out on its own. `socket_timeout_seconds`
MUST be strictly less than the caller's poll timeout so the socket read
fails before the watchdog trips (see WorkerConfig.__post_init__).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
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
        # connector_kwargs() defaults login_timeout to the shared 120s query
        # timeout, so a hung initial connect could outlive the poll watchdog even
        # though socket/network reads time out in socket_timeout_seconds. Bound
        # the connect the same way (socket_timeout_seconds is already validated
        # to be < poll_timeout_seconds) so a wedged connect can't escape it.
        kwargs["login_timeout"] = self.socket_timeout_seconds
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
        try:
            cursor.execute("SHOW WAREHOUSES")
            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        finally:
            cursor.close()

    def alter_auto_suspend(self, name: str, value: int) -> None:
        self.ensure_connected()
        cursor = self._connection.cursor()
        try:
            quoted = _quote_identifier(name)
            cursor.execute(f"ALTER WAREHOUSE {quoted} SET AUTO_SUSPEND = {value}")
        except BaseException:
            try:
                cursor.close()
            except Exception:
                pass
            raise
        else:
            try:
                cursor.close()
            except Exception:
                # The ALTER succeeded. Cleanup failure must not trigger a retry or
                # cause the successful mutation to be recorded as failed.
                pass

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
    max_attempt: int = 32,
) -> float:
    """Jittered exponential backoff, bounded by `cap`.

    ``attempt`` is saturated to ``max_attempt`` BEFORE exponentiation. A long
    outage can push ``attempt`` arbitrarily high, and ``2**attempt`` on a huge
    integer is a real (unbounded) computation even though the result is capped;
    clamping the exponent keeps ``2**attempt`` a small, bounded number.
    """
    safe_attempt = min(max(attempt, 0), max_attempt)
    raw = min(cap, base * (2**safe_attempt))
    return min(cap, raw * jitter())


def connection_fingerprint(config: Any) -> str:
    """Stable hash of all fields that define a Snowflake connection session.

    Used to detect when an org rotates its account, user, or private key: the
    fingerprint changes, so a warm session bound to the OLD identity can be
    recycled instead of continuing to ALTER a disconnected/rotated account.
    """
    parts = (
        str(getattr(config, "account", None) or ""),
        str(getattr(config, "account_locator", None) or ""),
        str(getattr(config, "user", None) or ""),
        str(getattr(config, "role", None) or ""),
        str(getattr(config, "warehouse", None) or ""),
        str(getattr(config, "database", None) or ""),
        str(getattr(config, "schema", None) or ""),
        str(getattr(config, "private_key_pem", None) or ""),
        str(getattr(config, "private_key_path", None) or ""),
        _private_key_path_digest(config),
        str(getattr(config, "private_key_passphrase", None) or ""),
        str(getattr(config, "query_timeout_seconds", None) or ""),
    )
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _private_key_path_digest(config: Any) -> str:
    key_path = getattr(config, "private_key_path", None)
    if not key_path:
        return ""
    try:
        key_bytes = Path(key_path).read_bytes()
    except (OSError, TypeError, ValueError):
        # The path itself remains in the fingerprint. This deterministic marker
        # keeps routine revalidation stable if the file is temporarily unreadable.
        return "unreadable"
    return hashlib.sha256(key_bytes).hexdigest()
