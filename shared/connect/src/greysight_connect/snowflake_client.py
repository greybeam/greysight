from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import adbc_driver_snowflake
from cryptography.hazmat.primitives import serialization

from greysight_connect.snowflake_account import validate_account_identifier

logger = logging.getLogger(__name__)

_LOGIN_FAILURE_REFERENCE = re.compile(
    r"\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]",
    re.IGNORECASE,
)
_SQLSTATE_PATTERN = re.compile(r"[A-Z0-9]{5}")


class SnowflakeConfigurationError(RuntimeError):
    """Raised when Snowflake backend configuration is missing or invalid."""


class SnowflakeValidationError(RuntimeError):
    """Raised with a user-safe Snowflake validation message."""

    def __init__(self, message: str, *, user_safe_message: str | None = None) -> None:
        super().__init__(message)
        self.user_safe_message = user_safe_message


class SnowflakeQueryError(RuntimeError):
    """Raised with a user-safe Snowflake query message."""

    def __init__(self, message: str, *, user_safe_message: str | None = None) -> None:
        super().__init__(message)
        self.user_safe_message = user_safe_message


class SnowflakeObjectUnavailableError(SnowflakeQueryError):
    """Raised when a queried object does not exist or is not authorized.

    A specialization of SnowflakeQueryError so resilient per-branch fetches can
    catch it explicitly to skip a missing/unauthorized table, while broader
    ``except SnowflakeQueryError`` fallbacks (e.g. main-run source groups) still
    treat it as a query failure and degrade gracefully to estimated data.
    """


class _SnowflakeConnectorProxy:
    def connect(self, **kwargs: Any) -> Any:
        import snowflake.connector as connector

        return connector.connect(**kwargs)


class _SnowflakeProxy:
    connector = _SnowflakeConnectorProxy()


snowflake = _SnowflakeProxy()


@dataclass(frozen=True)
class _SafeFailureMetadata:
    errno: int | None
    sqlstate: str | None
    reference: str | None


@dataclass(frozen=True)
class SnowflakeConnectionConfig:
    account: str | None = None
    user: str | None = None
    role: str | None = None
    warehouse: str | None = None
    database: str | None = None
    schema: str | None = None
    private_key_path: Path | None = None
    private_key_pem: str | None = field(default=None, repr=False)
    private_key_passphrase: str | None = field(default=None, repr=False)
    query_timeout_seconds: int = 120
    account_locator: str | None = None

    @classmethod
    def from_environment(cls) -> SnowflakeConnectionConfig:
        private_key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
        return cls(
            account=os.environ.get("SNOWFLAKE_ACCOUNT"),
            user=os.environ.get("SNOWFLAKE_USER"),
            role=os.environ.get("SNOWFLAKE_ROLE"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
            database=os.environ.get("SNOWFLAKE_DATABASE"),
            schema=os.environ.get("SNOWFLAKE_SCHEMA"),
            private_key_path=Path(private_key_path).expanduser()
            if private_key_path
            else None,
            private_key_passphrase=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
            query_timeout_seconds=int(
                os.environ.get("GREYSIGHT_QUERY_TIMEOUT_SECONDS", "120")
            ),
            account_locator=os.environ.get("SNOWFLAKE_ACCOUNT_LOCATOR"),
        )

    def connector_kwargs(self) -> dict[str, Any]:
        database = self.database or "SNOWFLAKE"
        schema = self.schema or "ACCOUNT_USAGE"
        required_values = {
            "SNOWFLAKE_ACCOUNT": self.account,
            "SNOWFLAKE_USER": self.user,
            "SNOWFLAKE_ROLE": self.role,
            "SNOWFLAKE_WAREHOUSE": self.warehouse,
            "SNOWFLAKE_PRIVATE_KEY": self.private_key_pem or self.private_key_path,
        }
        missing = [name for name, value in required_values.items() if not value]
        if missing:
            raise SnowflakeConfigurationError(
                "Snowflake connection is not configured. Missing: " + ", ".join(missing)
            )

        validate_account_identifier(self.account)

        return {
            "account": self.account,
            "user": self.user,
            "role": self.role,
            "warehouse": self.warehouse,
            "database": database,
            "schema": schema,
            "private_key": self._load_private_key_der(),
            "login_timeout": self.query_timeout_seconds,
            "network_timeout": self.query_timeout_seconds,
            "session_parameters": {"QUERY_TAG": "greysight"},
        }

    def adbc_db_kwargs(
        self,
        *,
        timeout_seconds: int | None = None,
        keep_session_alive: bool = False,
    ) -> dict[str, str]:
        required_values = {
            "SNOWFLAKE_ACCOUNT": self.account,
            "SNOWFLAKE_USER": self.user,
            "SNOWFLAKE_ROLE": self.role,
            "SNOWFLAKE_WAREHOUSE": self.warehouse,
            "SNOWFLAKE_PRIVATE_KEY": self.private_key_pem or self.private_key_path,
        }
        missing = [name for name, value in required_values.items() if not value]
        if missing:
            raise SnowflakeConfigurationError(
                "Snowflake connection is not configured. Missing: " + ", ".join(missing)
            )
        validate_account_identifier(self.account)
        timeout = timeout_seconds or self.query_timeout_seconds
        duration = f"{timeout}s"
        options = {
            adbc_driver_snowflake.DatabaseOptions.ACCOUNT.value: self.account,
            "username": self.user,
            adbc_driver_snowflake.DatabaseOptions.ROLE.value: self.role,
            adbc_driver_snowflake.DatabaseOptions.WAREHOUSE.value: self.warehouse,
            adbc_driver_snowflake.DatabaseOptions.DATABASE.value: self.database or "SNOWFLAKE",
            adbc_driver_snowflake.DatabaseOptions.SCHEMA.value: self.schema or "ACCOUNT_USAGE",
            adbc_driver_snowflake.DatabaseOptions.AUTH_TYPE.value: "auth_jwt",
            adbc_driver_snowflake.DatabaseOptions.JWT_PRIVATE_KEY_VALUE.value: self._load_private_key_pem(),
            adbc_driver_snowflake.DatabaseOptions.LOGIN_TIMEOUT.value: duration,
            adbc_driver_snowflake.DatabaseOptions.REQUEST_TIMEOUT.value: duration,
            adbc_driver_snowflake.DatabaseOptions.CLIENT_TIMEOUT.value: duration,
        }
        if self.private_key_passphrase:
            options[adbc_driver_snowflake.DatabaseOptions.JWT_PRIVATE_KEY_PASSWORD.value] = self.private_key_passphrase
        if keep_session_alive:
            options[adbc_driver_snowflake.DatabaseOptions.KEEP_SESSION_ALIVE.value] = "true"
        return options

    def _load_private_key_pem(self) -> str:
        if self.private_key_pem is None and self.private_key_path is None:
            raise SnowflakeConfigurationError("Snowflake connection is not configured.")
        try:
            if self.private_key_pem is not None:
                pem = self.private_key_pem
            else:
                assert self.private_key_path is not None
                pem = self.private_key_path.read_text(encoding="utf-8")
            password = (
                self.private_key_passphrase.encode("utf-8")
                if self.private_key_passphrase
                else None
            )
            serialization.load_pem_private_key(pem.encode("utf-8"), password=password)
            return pem
        except (OSError, TypeError, ValueError):
            raise SnowflakeConfigurationError(
                "Snowflake private key could not be loaded."
            ) from None


def execute_source_query(
    sql: str,
    bind_params: dict[str, Any],
    config: SnowflakeConnectionConfig | None = None,
    *,
    connect: Callable[[SnowflakeConnectionConfig | None], Any] | None = None,
) -> list[dict[str, Any]]:
    _validate_window_params(bind_params)
    try:
        connection = (connect or _connect)(config)
    except SnowflakeValidationError as exc:
        raise SnowflakeQueryError(
            str(exc),
            user_safe_message=exc.user_safe_message,
        ) from None
    except Exception:
        raise SnowflakeQueryError("Could not query Snowflake.") from None
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, bind_params)
            columns = [_column_name(column) for column in cursor.description or ()]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    except Exception as exc:
        if _is_object_unavailable(exc):
            raise SnowflakeObjectUnavailableError(
                "Snowflake object is unavailable for this account."
            ) from None
        known_message = _known_base_user_safe_message(exc)
        message = _user_safe_message(exc) if known_message else "Could not query Snowflake."
        raise SnowflakeQueryError(
            message,
            user_safe_message=message if known_message else None,
        ) from None
    finally:
        connection.close()


def execute_metadata_query(
    sql: str,
    *,
    config: SnowflakeConnectionConfig | None = None,
    connect: Callable[[SnowflakeConnectionConfig | None], Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a metadata SHOW statement (e.g. SHOW WAREHOUSES). No warehouse compute,
    no bind params, and — unlike a SELECT — never resumes a warehouse."""
    try:
        connection = (connect or _connect)(config)
    except Exception as exc:
        known_message = (
            exc.user_safe_message
            if isinstance(exc, SnowflakeValidationError)
            else _known_base_user_safe_message(exc)
        )
        message = str(exc) if known_message else "Could not query Snowflake."
        raise SnowflakeQueryError(
            message,
            user_safe_message=message if known_message else None,
        ) from None
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            columns = [_column_name(column) for column in cursor.description or ()]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except Exception as exc:
            known_message = _known_base_user_safe_message(exc)
            message = _user_safe_message(exc) if known_message else "Could not query Snowflake."
            raise SnowflakeQueryError(
                message,
                user_safe_message=message if known_message else None,
            ) from None
        finally:
            cursor.close()
    finally:
        connection.close()


def validate_snowflake_connection(
    config: SnowflakeConnectionConfig | None = None,
) -> str | None:
    """Validate access and return the account locator (current_account())."""
    connection = _connect(config)
    try:
        with connection.cursor() as cursor:
            for phase, view_name, sql in _validation_queries():
                try:
                    cursor.execute(sql)
                except Exception as exc:
                    user_message = _validation_probe_user_message(exc, view_name)
                    raise _validation_error(
                        exc, phase=phase, user_message=user_message
                    ) from None
            try:
                cursor.execute("select current_account()")
            except Exception as exc:
                raise _validation_error(exc, phase="current_account") from None
            row = cursor.fetchone()
            return str(row[0]) if row and row[0] is not None else None
    except SnowflakeValidationError:
        raise
    except Exception as exc:
        raise _validation_error(exc, phase="validation") from None
    finally:
        connection.close()


def _connect(config: SnowflakeConnectionConfig | None) -> Any:
    effective_config = config or SnowflakeConnectionConfig.from_environment()
    try:
        kwargs = effective_config.connector_kwargs()
    except SnowflakeConfigurationError as exc:
        _log_validation_failure(
            exc,
            phase="configuration",
            metadata=_safe_failure_metadata(exc),
        )
        raise
    try:
        return snowflake.connector.connect(**kwargs)
    except SnowflakeConfigurationError:
        raise
    except Exception as exc:
        raise _validation_error(exc, phase="connect") from None


def _validate_window_params(bind_params: dict[str, Any]) -> None:
    allowed_bind_keys = {"window_days", "account_locator"}
    unknown_bind_keys = set(bind_params) - allowed_bind_keys
    if unknown_bind_keys:
        raise ValueError(f"Unknown Snowflake bind params: {sorted(unknown_bind_keys)}")

    if not bind_params:
        return

    window_days = bind_params.get("window_days")
    if window_days is not None and (
        not isinstance(window_days, int) or not 1 <= window_days <= 365
    ):
        raise ValueError("window_days must be an integer between 1 and 365")

    account_locator = bind_params.get("account_locator")
    if account_locator is not None and (
        not isinstance(account_locator, str)
        or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", account_locator)
    ):
        raise ValueError("account_locator must be 1-64 letters, digits, or underscores")


def _column_name(column: Any) -> str:
    if hasattr(column, "name"):
        return str(column.name).lower()
    return str(column[0]).lower()


def _validation_queries() -> tuple[tuple[str, str, str], ...]:
    return (
        (
            "metering_daily_history",
            "METERING_DAILY_HISTORY",
            "select 1 from SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY limit 1",
        ),
        (
            "warehouse_metering_history",
            "WAREHOUSE_METERING_HISTORY",
            "select 1 from SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY limit 1",
        ),
        (
            "query_attribution_history",
            "QUERY_ATTRIBUTION_HISTORY",
            "select 1 from SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY limit 1",
        ),
        (
            "database_storage_usage_history",
            "DATABASE_STORAGE_USAGE_HISTORY",
            "select 1 from SNOWFLAKE.ACCOUNT_USAGE.DATABASE_STORAGE_USAGE_HISTORY limit 1",
        ),
    )


# Snowflake error codes for "object does not exist" (2003) and
# "insufficient privileges" (3001). These mean a branch table is unavailable
# for this account rather than a real query failure.
_OBJECT_UNAVAILABLE_ERRNOS = frozenset({2003, 3001})


def _is_object_unavailable(exc: Exception) -> bool:
    errno = getattr(exc, "errno", None)
    if errno in _OBJECT_UNAVAILABLE_ERRNOS:
        return True
    message = str(exc).lower()
    return (
        ("does not exist" in message and "object" in message)
        or "not authorized" in message
        or "insufficient privileges" in message
    )


def _user_safe_message(exc: Exception) -> str:
    return _base_user_safe_message(exc) + _user_diagnostic_suffix(
        _safe_failure_metadata(exc)
    )


def _base_user_safe_message(exc: Exception) -> str:
    return _known_base_user_safe_message(exc) or (
        "Could not validate Snowflake connection. Ask your Snowflake "
        "administrator to check LOGIN_HISTORY for this user and try again."
    )


def _known_base_user_safe_message(exc: Exception) -> str | None:
    message = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in message or "timeout" in message:
        safe_message = (
            "Snowflake connection timed out. Verify that the account is reachable "
            "and try again."
        )
    elif (
        "not allowed to access snowflake" in message
        or "incoming_request_blocked" in message
        or "network policy" in message
    ):
        safe_message = (
            "Snowflake blocked the connection under its network policy. Ask your "
            "Snowflake administrator to allow this deployment's API and worker "
            "egress IP addresses."
        )
    elif "warehouse" in message:
        safe_message = "Could not use the configured Snowflake warehouse."
    elif "role" in message:
        safe_message = "Could not use the configured Snowflake role."
    elif (
        "permission" in message
        or "privilege" in message
        or "not authorized" in message
        or isinstance(exc, PermissionError)
    ):
        safe_message = (
            "Could not access required Snowflake Account Usage views. Verify that "
            "the configured role has Account Usage access."
        )
    elif "private key" in message or "jwt" in message or "authenticate" in message:
        safe_message = (
            "Could not authenticate to Snowflake. Check the configured user and "
            "private key."
        )
    else:
        safe_message = None
    return safe_message


def _validation_probe_user_message(exc: Exception, view_name: str) -> str:
    return (
        f"{_base_user_safe_message(exc)} Validation stopped while checking "
        f"SNOWFLAKE.ACCOUNT_USAGE.{view_name}."
    )


def _validation_error(
    exc: Exception, *, phase: str, user_message: str | None = None
) -> SnowflakeValidationError:
    metadata = _safe_failure_metadata(
        exc, include_login_reference=phase == "connect"
    )
    _log_validation_failure(exc, phase=phase, metadata=metadata)
    known_message = _known_base_user_safe_message(exc)
    safe_message = (user_message or known_message or _base_user_safe_message(exc)) + (
        _user_diagnostic_suffix(metadata)
    )
    return SnowflakeValidationError(
        safe_message,
        user_safe_message=safe_message if known_message is not None else None,
    )


def _log_validation_failure(
    exc: Exception, *, phase: str, metadata: _SafeFailureMetadata
) -> None:
    logger.warning(
        "Snowflake validation failed phase=%s exception=%s errno=%s "
        "sqlstate=%s reference=%s",
        phase,
        type(exc).__name__,
        metadata.errno,
        metadata.sqlstate,
        metadata.reference,
    )


def _safe_failure_metadata(
    exc: Exception, *, include_login_reference: bool = False
) -> _SafeFailureMetadata:
    raw_errno = getattr(exc, "errno", None)
    errno = (
        raw_errno
        if isinstance(raw_errno, int) and not isinstance(raw_errno, bool)
        else None
    )

    raw_sqlstate = getattr(exc, "sqlstate", None)
    sqlstate = (
        raw_sqlstate.upper()
        if isinstance(raw_sqlstate, str)
        and _SQLSTATE_PATTERN.fullmatch(raw_sqlstate.upper())
        else None
    )

    match = _LOGIN_FAILURE_REFERENCE.search(str(exc)) if include_login_reference else None
    reference = match.group(1).lower() if match else None
    return _SafeFailureMetadata(errno=errno, sqlstate=sqlstate, reference=reference)


def _user_diagnostic_suffix(metadata: _SafeFailureMetadata) -> str:
    diagnostics: list[str] = []
    if metadata.errno is not None:
        diagnostics.append(f"error {metadata.errno}")
    if metadata.sqlstate is not None:
        diagnostics.append(f"SQL state {metadata.sqlstate}")
    if metadata.reference is not None:
        diagnostics.append(f"reference {metadata.reference}")
    if not diagnostics:
        return ""
    return " Snowflake diagnostics: " + "; ".join(diagnostics) + "."
