from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

import httpx

from greysight_connect.snowflake_client import SnowflakeConnectionConfig


class ResolverSettings(Protocol):
    auth_required: bool
    query_timeout_seconds: int


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
    account_locator: str | None = None


class OrgConnectionNotConfiguredError(RuntimeError):
    """Raised when an authenticated org has no usable Snowflake connection.

    This is a *definitive* verdict: the lookup succeeded and there is genuinely
    no active connection row (or a malformed/duplicate one). Callers may treat it
    as "the connection is gone" and drop any warm state.
    """


class OrgConnectionUnavailableError(OrgConnectionNotConfiguredError):
    """Raised when the connection lookup itself failed *transiently*.

    A network/timeout/5xx error means we could not determine whether the org has
    a connection — NOT that it lacks one. It subclasses
    ``OrgConnectionNotConfiguredError`` so existing callers (the API) keep their
    fail-closed behavior unchanged, while callers that care (the worker's
    supervisor) can catch this first and KEEP a still-configured warm session
    instead of dropping it on a blip.
    """


FetchConnection = Callable[[str], OrgConnectionRow | None]


def resolve_snowflake_config(
    organization_id: str,
    settings: ResolverSettings,
    *,
    fetch_connection: FetchConnection,
) -> SnowflakeConnectionConfig:
    try:
        row = fetch_connection(organization_id)
    except OrgConnectionNotConfiguredError:
        # The fetcher already made a definitive verdict (malformed / duplicate /
        # missing secret) — propagate it as-is (genuinely not configured).
        raise

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
            account_locator=row.account_locator,
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
        client: httpx.Client | None = None,
    ) -> None:
        base = supabase_url.rstrip("/")
        self._table_url = f"{base}/rest/v1/organization_snowflake_connections"
        self._secret_rpc_url = f"{base}/rest/v1/rpc/get_organization_snowflake_secret"
        self._service_role_key = service_role_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_role_key,
            "authorization": f"Bearer {self._service_role_key}",
            "content-type": "application/json",
        }

    def _lookup(
        self, client: httpx.Client, organization_id: str
    ) -> tuple[dict[str, str | None] | None, dict[str, str | None] | None]:
        # A per-call client already carries the timeout on the client; a shared
        # injected client must not have its pool-wide timeout mutated, so pass the
        # per-request timeout only on the injected path.
        timeout_kwargs = (
            {"timeout": self._timeout_seconds} if self._client is not None else {}
        )
        meta_response = client.get(
            self._table_url,
            params={
                "organization_id": f"eq.{organization_id}",
                "select": "account,account_locator,snowflake_user,role,warehouse,database,schema,status,secret_id",
                "limit": "1",
            },
            headers=self._headers(),
            **timeout_kwargs,
        )
        rows = _response_json(meta_response, subject="metadata")
        meta = _single_metadata_row(rows)
        if meta is None:
            return None, None

        secret_response = client.post(
            self._secret_rpc_url,
            json={"target_organization_id": organization_id},
            headers=self._headers(),
            **timeout_kwargs,
        )
        secret_rows = _response_json(secret_response, subject="secret")
        secret = _single_secret_row(secret_rows)
        return meta, secret

    def __call__(self, organization_id: str) -> OrgConnectionRow | None:
        try:
            if self._client is not None:
                # Injected pooled client (API path): never open/close it here.
                meta, secret = self._lookup(self._client, organization_id)
            else:
                # Short-lived per-call client (worker path / transport seam).
                with httpx.Client(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    meta, secret = self._lookup(client, organization_id)
        except (OrgConnectionNotConfiguredError, OrgConnectionUnavailableError):
            raise
        except httpx.TransportError as exc:
            raise OrgConnectionUnavailableError(
                "Could not load this organization's Snowflake connection."
            ) from exc

        if meta is None:
            return None

        return OrgConnectionRow(
            account=meta["account"],
            snowflake_user=meta["snowflake_user"],
            role=meta["role"],
            warehouse=meta["warehouse"],
            database=meta.get("database"),
            schema=meta.get("schema"),
            private_key_pem=secret["private_key_pem"],
            passphrase=secret.get("passphrase"),
            status=str(meta.get("status") or "invalid"),
            account_locator=meta.get("account_locator"),
        )


def _response_json(response: httpx.Response, *, subject: str) -> object:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = response.status_code
        if status in (408, 429) or status >= 500:
            raise OrgConnectionUnavailableError(
                "Could not load this organization's Snowflake connection."
            ) from exc
        raise OrgConnectionNotConfiguredError(
            f"Could not load Snowflake connection {subject} for org."
        ) from exc

    try:
        return response.json()
    except ValueError as exc:
        raise OrgConnectionNotConfiguredError(
            f"Malformed Snowflake connection {subject} for org."
        ) from exc


def _single_metadata_row(rows: object) -> dict[str, str | None] | None:
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
    required = ("account", "snowflake_user", "role", "warehouse", "secret_id")
    if not isinstance(meta, dict) or any(
        not isinstance(meta.get(key), str) or not meta[key].strip() for key in required
    ):
        raise OrgConnectionNotConfiguredError(
            "Malformed Snowflake connection metadata for org."
        )
    optional = ("database", "schema", "account_locator")
    if any(
        meta.get(key) is not None and not isinstance(meta[key], str) for key in optional
    ):
        raise OrgConnectionNotConfiguredError(
            "Malformed Snowflake connection metadata for org."
        )
    return meta


def _single_secret_row(rows: object) -> dict[str, str | None]:
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise OrgConnectionNotConfiguredError("Snowflake secret missing for org.")
    secret = rows[0]
    pem = secret.get("private_key_pem")
    passphrase = secret.get("passphrase")
    if (
        not isinstance(pem, str)
        or not pem.strip()
        or (passphrase is not None and not isinstance(passphrase, str))
    ):
        raise OrgConnectionNotConfiguredError("Snowflake secret missing for org.")
    return secret
