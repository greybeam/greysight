from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization


class SnowflakeConfigurationError(RuntimeError):
    """Raised when Snowflake backend configuration is missing or invalid."""


class SnowflakeValidationError(RuntimeError):
    """Raised with a user-safe Snowflake validation message."""


class SnowflakeQueryError(RuntimeError):
    """Raised with a user-safe Snowflake query message."""


class _SnowflakeConnectorProxy:
    def connect(self, **kwargs: Any) -> Any:
        import snowflake.connector as connector

        return connector.connect(**kwargs)


class _SnowflakeProxy:
    connector = _SnowflakeConnectorProxy()


snowflake = _SnowflakeProxy()


@dataclass(frozen=True)
class SnowflakeConnectionConfig:
    account: str | None = None
    user: str | None = None
    role: str | None = None
    warehouse: str | None = None
    database: str | None = None
    schema: str | None = None
    private_key_path: Path | None = None
    private_key_passphrase: str | None = None
    query_timeout_seconds: int = 60

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
                os.environ.get("GREYSIGHT_QUERY_TIMEOUT_SECONDS", "60")
            ),
        )

    def connector_kwargs(self) -> dict[str, Any]:
        required_values = {
            "SNOWFLAKE_ACCOUNT": self.account,
            "SNOWFLAKE_USER": self.user,
            "SNOWFLAKE_ROLE": self.role,
            "SNOWFLAKE_WAREHOUSE": self.warehouse,
            "SNOWFLAKE_DATABASE": self.database,
            "SNOWFLAKE_SCHEMA": self.schema,
            "SNOWFLAKE_PRIVATE_KEY_PATH": self.private_key_path,
        }
        missing = [name for name, value in required_values.items() if not value]
        if missing:
            raise SnowflakeConfigurationError(
                "Snowflake connection is not configured. Missing: " + ", ".join(missing)
            )

        return {
            "account": self.account,
            "user": self.user,
            "role": self.role,
            "warehouse": self.warehouse,
            "database": self.database,
            "schema": self.schema,
            "private_key": self._load_private_key_der(),
            "login_timeout": self.query_timeout_seconds,
            "network_timeout": self.query_timeout_seconds,
            "session_parameters": {"QUERY_TAG": "greysight"},
        }

    def _load_private_key_der(self) -> bytes:
        if self.private_key_path is None:
            raise SnowflakeConfigurationError("Snowflake connection is not configured.")

        password = (
            self.private_key_passphrase.encode("utf-8")
            if self.private_key_passphrase
            else None
        )
        try:
            private_key = serialization.load_pem_private_key(
                self.private_key_path.read_bytes(),
                password=password,
            )
            return private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        except (OSError, TypeError, ValueError):
            raise SnowflakeConfigurationError(
                "Snowflake private key could not be loaded."
            ) from None


def execute_source_query(
    sql: str,
    bind_params: dict[str, Any],
    config: SnowflakeConnectionConfig | None = None,
) -> list[dict[str, Any]]:
    _validate_window_params(bind_params)
    connection = _connect(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, bind_params)
            columns = [_column_name(column) for column in cursor.description or ()]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    except Exception:
        raise SnowflakeQueryError("Could not query Snowflake Account Usage.") from None
    finally:
        connection.close()


def validate_snowflake_connection(
    config: SnowflakeConnectionConfig | None = None,
) -> None:
    connection = _connect(config)
    try:
        with connection.cursor() as cursor:
            for sql in _validation_queries():
                cursor.execute(sql)
    except Exception as exc:
        raise SnowflakeValidationError(_user_safe_message(exc)) from None
    finally:
        connection.close()


def _connect(config: SnowflakeConnectionConfig | None) -> Any:
    effective_config = config or SnowflakeConnectionConfig.from_environment()
    kwargs = effective_config.connector_kwargs()
    try:
        return snowflake.connector.connect(**kwargs)
    except SnowflakeConfigurationError:
        raise
    except Exception as exc:
        raise SnowflakeValidationError(_user_safe_message(exc)) from None


def _validate_window_params(bind_params: dict[str, Any]) -> None:
    window_days = bind_params.get("window_days")
    if not isinstance(window_days, int) or not 1 <= window_days <= 365:
        raise ValueError("window_days must be an integer between 1 and 365")


def _column_name(column: Any) -> str:
    if hasattr(column, "name"):
        return str(column.name).lower()
    return str(column[0]).lower()


def _validation_queries() -> tuple[str, ...]:
    return (
        "select 1 from SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY limit 1",
        "select 1 from SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY limit 1",
        "select 1 from SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY limit 1",
        "select 1 from SNOWFLAKE.ACCOUNT_USAGE.DATABASE_STORAGE_USAGE_HISTORY limit 1",
    )


def _user_safe_message(exc: Exception) -> str:
    message = str(exc).lower()
    if "warehouse" in message:
        return "Could not use the configured Snowflake warehouse."
    if "role" in message:
        return "Could not use the configured Snowflake role."
    if (
        "permission" in message
        or "privilege" in message
        or "not authorized" in message
        or isinstance(exc, PermissionError)
    ):
        return "Could not access required Snowflake Account Usage views."
    if "private key" in message or "jwt" in message or "authenticate" in message:
        return "Could not authenticate to Snowflake. Check the configured user and private key."
    return "Could not validate Snowflake connection."
