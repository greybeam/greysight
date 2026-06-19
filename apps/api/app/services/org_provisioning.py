from __future__ import annotations

import httpx


class OrgProvisioningError(RuntimeError):
    """Raised when org provisioning fails."""


class DuplicateSnowflakeAccountError(OrgProvisioningError):
    """Raised when the Snowflake account is already connected to an org."""


def _is_duplicate_account_conflict(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("code") == "23505"


class SupabaseOrgProvisioner:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = f"{supabase_url.rstrip('/')}/rest/v1/rpc/create_org_with_snowflake_connection"
        self._service_role_key = service_role_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def __call__(self, **params: str) -> str:
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.post(
                    self._url,
                    json=params,
                    headers={
                        "apikey": self._service_role_key,
                        "authorization": f"Bearer {self._service_role_key}",
                        "content-type": "application/json",
                    },
                )
        except httpx.HTTPError:
            # Use `from None` so the httpx exception chain — whose traceback
            # frames hold `params` (p_private_key_pem / p_passphrase) — is not
            # re-raised alongside this error.
            raise OrgProvisioningError("Could not create the organization.") from None
        if _is_duplicate_account_conflict(response):
            raise DuplicateSnowflakeAccountError(
                "This Snowflake account is already connected to an organization. "
                "Ask its owner to invite you."
            )
        if response.status_code not in (200, 201):
            raise OrgProvisioningError("Could not create the organization.")
        try:
            return str(response.json())
        except ValueError:
            raise OrgProvisioningError("Could not create the organization.") from None


class SupabaseOrgDisconnector:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = (
            f"{supabase_url.rstrip('/')}/rest/v1/rpc/disconnect_organization_snowflake"
        )
        self._service_role_key = service_role_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def __call__(self, organization_id: str) -> None:
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.post(
                    self._url,
                    json={"target_organization_id": organization_id},
                    headers={
                        "apikey": self._service_role_key,
                        "authorization": f"Bearer {self._service_role_key}",
                        "content-type": "application/json",
                    },
                )
        except httpx.HTTPError:
            raise OrgProvisioningError(
                "Could not disconnect the organization."
            ) from None
        # The RPC is an idempotent no-op when there is no connection row, so any
        # success status (incl. 204) is acceptable; anything else is a failure.
        if response.status_code not in (200, 204):
            raise OrgProvisioningError("Could not disconnect the organization.")


_provisioner: SupabaseOrgProvisioner | None = None
_disconnector: SupabaseOrgDisconnector | None = None


def configure_org_provisioner(provisioner: SupabaseOrgProvisioner | None) -> None:
    global _provisioner
    _provisioner = provisioner


def configure_org_disconnector(disconnector: SupabaseOrgDisconnector | None) -> None:
    global _disconnector
    _disconnector = disconnector


def create_org_with_connection(**params: str) -> str:
    if _provisioner is None:
        raise OrgProvisioningError("Org provisioning is not configured.")
    return _provisioner(**params)


def disconnect_org_connection(organization_id: str) -> None:
    if _disconnector is None:
        raise OrgProvisioningError("Org disconnect is not configured.")
    _disconnector(organization_id)
