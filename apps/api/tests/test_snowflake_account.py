import pytest

from app.services.snowflake_account import (
    InvalidSnowflakeAccountError,
    validate_account_identifier,
)


@pytest.mark.parametrize(
    "value", ["GOPGUKF-JO19546", "abc12345.us-east-1", "org-account_1"]
)
def test_accepts_valid_account_identifiers(value: str) -> None:
    assert validate_account_identifier(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "http://evil.example.com",
        "acct/../x",
        "acct:5432",
        "acct account",
        "a" * 300,
        "",
    ],
)
def test_rejects_unsafe_account_identifiers(value: str) -> None:
    with pytest.raises(InvalidSnowflakeAccountError):
        validate_account_identifier(value)
