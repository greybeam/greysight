import pytest

from app.config import Settings
from app.services.org_connection_resolver import (
    OrgConnectionNotConfiguredError,
    OrgConnectionRow,
    resolve_snowflake_config,
)


def _row() -> OrgConnectionRow:
    return OrgConnectionRow(
        account="acct", snowflake_user="u", role="r", warehouse="w",
        database=None, schema=None,
        private_key_pem="-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----",
        passphrase=None,
    )


def test_uses_per_org_row_when_present() -> None:
    settings = Settings(auth_required=True)
    config = resolve_snowflake_config(
        "org-1", settings, fetch_connection=lambda _org_id: _row()
    )
    assert config.account == "acct"
    assert config.private_key_pem is not None


def test_fails_closed_when_no_row_and_auth_required() -> None:
    settings = Settings(auth_required=True)
    with pytest.raises(OrgConnectionNotConfiguredError):
        resolve_snowflake_config("org-1", settings, fetch_connection=lambda _org_id: None)


def test_falls_back_to_env_when_auth_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "env-acct")
    settings = Settings(auth_required=False)
    config = resolve_snowflake_config("org-1", settings, fetch_connection=lambda _org_id: None)
    assert config.account == "env-acct"


def test_fails_closed_when_lookup_errors_and_auth_required() -> None:
    settings = Settings(auth_required=True)

    def _boom(_org_id: str) -> OrgConnectionRow | None:
        raise RuntimeError("vault down")

    with pytest.raises(OrgConnectionNotConfiguredError):
        resolve_snowflake_config("org-1", settings, fetch_connection=_boom)


def test_fails_closed_when_row_status_not_active() -> None:
    settings = Settings(auth_required=True)

    def _invalid(_org_id: str) -> OrgConnectionRow:
        return OrgConnectionRow(
            account="acct", snowflake_user="u", role="r", warehouse="w",
            database=None, schema=None,
            private_key_pem="-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----",
            passphrase=None, status="invalid",
        )

    with pytest.raises(OrgConnectionNotConfiguredError):
        resolve_snowflake_config("org-1", settings, fetch_connection=_invalid)
