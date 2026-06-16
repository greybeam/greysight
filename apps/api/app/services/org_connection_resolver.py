from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.config import Settings
from app.services.snowflake_client import SnowflakeConnectionConfig


@dataclass(frozen=True)
class OrgConnectionRow:
    account: str
    snowflake_user: str
    role: str
    warehouse: str
    database: str | None
    schema: str | None
    private_key_pem: str
    passphrase: str | None
    status: str = "active"


class OrgConnectionNotConfiguredError(RuntimeError):
    """Raised when an authenticated org has no usable Snowflake connection."""


FetchConnection = Callable[[str], OrgConnectionRow | None]


def resolve_snowflake_config(
    organization_id: str,
    settings: Settings,
    *,
    fetch_connection: FetchConnection,
) -> SnowflakeConnectionConfig:
    try:
        row = fetch_connection(organization_id)
    except Exception as exc:  # fail closed: never fall through on a lookup error
        raise OrgConnectionNotConfiguredError(
            "Could not load this organization's Snowflake connection."
        ) from exc

    if row is not None:
        if row.status != "active":
            # Fail closed: an invalidated connection must not run dashboards.
            raise OrgConnectionNotConfiguredError(
                "This organization's Snowflake connection is not active."
            )
        return SnowflakeConnectionConfig(
            account=row.account,
            user=row.snowflake_user,
            role=row.role,
            warehouse=row.warehouse,
            database=row.database,
            schema=row.schema,
            private_key_pem=row.private_key_pem,
            private_key_passphrase=row.passphrase,
            query_timeout_seconds=settings.query_timeout_seconds,
        )

    if settings.auth_required:
        # No per-org connection in multi-tenant mode → fail closed. Never serve
        # the deployment .env credentials under another org's identity.
        raise OrgConnectionNotConfiguredError(
            "This organization has no Snowflake connection configured."
        )

    return SnowflakeConnectionConfig.from_environment()
