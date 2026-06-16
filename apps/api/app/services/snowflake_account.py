import re

# Snowflake account identifiers are alphanumerics plus dots, hyphens, and
# underscores (e.g. "ORG-ACCOUNT", "abc12345.us-east-1.aws"). Reject anything
# that could redirect the connector to an attacker-controlled host.
_ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


class InvalidSnowflakeAccountError(ValueError):
    """Raised when a submitted Snowflake account identifier is unsafe."""


def validate_account_identifier(value: str) -> str:
    if not isinstance(value, str) or not _ACCOUNT_PATTERN.fullmatch(value):
        raise InvalidSnowflakeAccountError(
            "Snowflake account must be 1-255 letters, digits, dots, hyphens, or underscores."
        )
    return value
