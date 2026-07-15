"""Warm, persistent per-tenant Snowflake session.

The wedge-escape mechanism is ADBC's ``client_timeout``, which bounds network
round trips and HTTP response reads inside the Go Snowflake driver. The worker
sets login, request, and client timeouts to ``socket_timeout_seconds``.
``socket_timeout_seconds`` MUST remain strictly less than the caller's poll
timeout so the driver call returns before the watchdog trips (see
WorkerConfig.__post_init__).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional

import adbc_driver_manager  # noqa: F401  (ensures the ADBC manager package is importable)
import adbc_driver_manager.dbapi as adbc_dbapi
import adbc_driver_snowflake.dbapi

from greysight_connect import snowflake_cursor

logger = logging.getLogger(__name__)


class SuspendOutcome(Enum):
    ACCEPTED = auto()
    UNKNOWN_IDEMPOTENT = auto()


@dataclass(frozen=True)
class ConnectorErrorMetadata:
    """Sanitized connector fields retained for correlated engine telemetry."""

    error_type: str
    errno: int | None
    sqlstate: str | None
    message: str | None


@dataclass(frozen=True)
class SuspendResult:
    outcome: SuspendOutcome
    connector_error: ConnectorErrorMetadata | None = None


_REDACTED = "[REDACTED]"

# ADBC/Go-driver error text can echo connection options or key material. Redact
# any PEM block and the value of any key/passphrase connection option so secrets
# can never reach log telemetry. Applied AFTER whitespace normalization (PEM
# newlines are already collapsed to spaces) and BEFORE truncation, so a
# truncated PEM can't slip a partial secret through.
# A BEGIN header with no matching END marker (e.g. the driver truncated the
# message itself) fails safe: redact from the header to the end of the message.
_PEM_BLOCK = re.compile(
    r"-----BEGIN[^-]*PRIVATE KEY-----(?:.*?-----END[^-]*PRIVATE KEY-----|.*$)",
    re.IGNORECASE,
)
_SECRET_OPTION = re.compile(
    r"(jwt_private_key_pkcs8_value"
    r"|jwt_private_key_pkcs8_password"
    r"|private_key_pem"
    r"|private_key_passphrase"
    r"|password)"
    # The key may be quoted (JSON-style '"password": "..."') and the separator
    # may be '=' or ':'. Quoted values may contain whitespace and
    # backslash-escaped characters (including escaped quotes); redact through
    # the true closing quote. Anything else — an unterminated quote or an
    # unquoted value — has no reliable terminator, so fail safe by redacting
    # through the end of the message.
    r"[\"']?\s*[:=]\s*(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|.*$)",
    re.IGNORECASE,
)


def _redact_secrets(message: str) -> str:
    message = _PEM_BLOCK.sub(_REDACTED, message)
    message = _SECRET_OPTION.sub(lambda m: f"{m.group(1)}={_REDACTED}", message)
    return message


def _sanitize_connector_message(value: object) -> str | None:
    if value is None:
        return None
    message = " ".join(str(value).split())
    if not message:
        return None
    message = _redact_secrets(message)
    return message[:512]


def connector_error_metadata(exc: Any) -> ConnectorErrorMetadata:
    is_adbc = isinstance(exc, adbc_dbapi.Error)
    message = _sanitize_connector_message(str(exc)) if is_adbc else None
    return ConnectorErrorMetadata(
        error_type=type(exc).__name__,
        errno=exc.vendor_code if is_adbc else None,
        sqlstate=exc.sqlstate if is_adbc else None,
        message=message,
    )


def _connect_adbc(config: Any, *, timeout_seconds: int) -> Any:
    options = config.adbc_db_kwargs(
        timeout_seconds=timeout_seconds,
        keep_session_alive=True,
    )
    return adbc_driver_snowflake.dbapi.connect(
        db_kwargs=options,
        autocommit=True,
    )


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
        # connection. The default path builds the ADBC connection itself so it
        # can bound the login/request/client timeouts — the load-bearing
        # wedge-escape (see the module docstring).
        self._connect = connect
        self._connection: Any = None

    def ensure_connected(self) -> None:
        if self._connection is None:
            if self._connect is not None:
                self._connection = self._connect(self._config)
            else:
                # Default path: build the ADBC connection with all timeouts
                # bounded to socket_timeout_seconds so a wedged driver call
                # returns before the poll watchdog trips.
                self._connection = _connect_adbc(
                    self._config,
                    timeout_seconds=self.socket_timeout_seconds,
                )

    def show_warehouses(self) -> list[dict]:
        self.ensure_connected()
        cursor = snowflake_cursor(self._connection)
        try:
            cursor.execute("SHOW WAREHOUSES")
            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        finally:
            cursor.close()

    def suspend_warehouse(self, name: str) -> SuspendResult:
        self.ensure_connected()
        cursor = snowflake_cursor(self._connection)
        try:
            quoted = _quote_identifier(name)
            cursor.execute(f"ALTER WAREHOUSE {quoted} SUSPEND")
        except BaseException as exc:
            try:
                cursor.close()
            except Exception:
                pass

            if isinstance(exc, adbc_dbapi.Error) and exc.vendor_code == 90064:
                metadata = connector_error_metadata(exc)
                logger.warning(
                    "Snowflake suspend outcome is unknown but idempotent",
                    extra={
                        "connector_error_type": metadata.error_type,
                        "connector_errno": metadata.errno,
                        "connector_sqlstate": metadata.sqlstate,
                        "connector_message": metadata.message,
                    },
                )
                return SuspendResult(
                    outcome=SuspendOutcome.UNKNOWN_IDEMPOTENT,
                    connector_error=metadata,
                )
            raise
        else:
            try:
                cursor.close()
            except Exception:
                # The ALTER succeeded. Cleanup failure must not trigger a retry or
                # cause the successful mutation to be recorded as failed.
                pass
            return SuspendResult(outcome=SuspendOutcome.ACCEPTED)

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
