"""Re-export shim — implementation moved to the greysight_connect package."""
from greysight_connect import snowflake_client as _impl
from greysight_connect.snowflake_client import (  # noqa: F401  explicit for name-based patching
    SnowflakeConfigurationError,
    SnowflakeConnectionConfig,
    SnowflakeObjectUnavailableError,
    SnowflakeQueryError,
    SnowflakeValidationError,
    execute_source_query,
    validate_snowflake_connection,
)

# `execute_metadata_query` is added to the package in Task 2; add it to this import list then.
# Re-export the `snowflake` module attribute so `patch("app.services.snowflake_client.snowflake...")`
# targets the same object the implementation uses.
snowflake = _impl.snowflake
