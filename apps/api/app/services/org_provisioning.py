from __future__ import annotations

import httpx


class OrgProvisioningError(RuntimeError):
    """Raised when org provisioning fails."""


class OrgAlreadyExistsError(OrgProvisioningError):
    """Raised when the one-org guard rejects a second org for the user."""


def _is_one_org_conflict(response: httpx.Response) -> bool:
    if response.status_code == 409:
        return True
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
        if _is_one_org_conflict(response):
            raise OrgAlreadyExistsError("You already have an organization.")
        if response.status_code not in (200, 201):
            raise OrgProvisioningError("Could not create the organization.")
        try:
            return str(response.json())
        except ValueError:
            raise OrgProvisioningError("Could not create the organization.") from None


_provisioner: SupabaseOrgProvisioner | None = None


def configure_org_provisioner(provisioner: SupabaseOrgProvisioner | None) -> None:
    global _provisioner
    _provisioner = provisioner


def create_org_with_connection(**params: str) -> str:
    if _provisioner is None:
        raise OrgProvisioningError("Org provisioning is not configured.")
    return _provisioner(**params)
