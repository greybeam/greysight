"""Re-export shim — implementation moved to the greysight_connect package."""
from greysight_connect.snowflake_account import (  # noqa: F401
    InvalidSnowflakeAccountError,
    validate_account_identifier,
)
