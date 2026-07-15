"""Re-export shim — implementation moved to the greysight_connect package."""

from greysight_connect.snowflake_client import (  # noqa: F401  explicit for name-based patching
    SnowflakeConfigurationError,
    SnowflakeConnectionConfig,
    SnowflakeObjectUnavailableError,
    SnowflakeQueryError,
    SnowflakeValidationError,
    execute_metadata_query,
    execute_source_query,
    validate_snowflake_connection,
)
