from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import httpx

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
    private_key_pem: str = field(repr=False)
    passphrase: str | None = field(repr=False)
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


class SupabaseConnectionFetcher:
    """Reads a per-org connection row + decrypted Vault secret via service role."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = supabase_url.rstrip("/")
        self._table_url = f"{base}/rest/v1/organization_snowflake_connections"
        self._secret_rpc_url = f"{base}/rest/v1/rpc/get_organization_snowflake_secret"
        self._service_role_key = service_role_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_role_key,
            "authorization": f"Bearer {self._service_role_key}",
            "content-type": "application/json",
        }

    def __call__(self, organization_id: str) -> OrgConnectionRow | None:
        with httpx.Client(
            timeout=self._timeout_seconds, transport=self._transport
        ) as client:
            meta_response = client.get(
                self._table_url,
                params={
                    "organization_id": f"eq.{organization_id}",
                    "select": "account,snowflake_user,role,warehouse,database,schema,status,secret_id",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            meta_response.raise_for_status()
            rows = meta_response.json()
            if not isinstance(rows, list):
                raise OrgConnectionNotConfiguredError(
                    "Malformed Snowflake connection metadata for org."
                )
            if not rows:
                return None
            if len(rows) > 1:
                raise OrgConnectionNotConfiguredError(
                    "Multiple Snowflake connection rows for org."
                )
            meta = rows[0]
            if not meta.get("secret_id"):
                return None

            secret_response = client.post(
                self._secret_rpc_url,
                json={"target_organization_id": organization_id},
                headers=self._headers(),
            )
            secret_response.raise_for_status()
            secret_rows = secret_response.json()
            if not isinstance(secret_rows, list) or len(secret_rows) != 1:
                raise OrgConnectionNotConfiguredError(
                    "Snowflake secret missing for org."
                )
            secret = secret_rows[0]
            pem = secret.get("private_key_pem")
            if not pem:
                raise OrgConnectionNotConfiguredError(
                    "Snowflake secret missing for org."
                )

        return OrgConnectionRow(
            account=str(meta["account"]),
            snowflake_user=str(meta["snowflake_user"]),
            role=str(meta["role"]),
            warehouse=str(meta["warehouse"]),
            database=meta.get("database"),
            schema=meta.get("schema"),
            private_key_pem=str(pem),
            passphrase=secret.get("passphrase"),
            status=str(meta.get("status") or "invalid"),
        )
