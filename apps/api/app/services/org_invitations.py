from __future__ import annotations

import httpx

STATUS_UNAUTHORIZED = "unauthorized"
STATUS_INVITE_NEEDED = "invite_needed"
STATUS_PENDING_RESEND = "pending_resend"
STATUS_ALREADY_MEMBER = "already_member"
STATUS_ADDED = "added"

_TERMINAL_OK = {STATUS_ADDED, STATUS_ALREADY_MEMBER, STATUS_PENDING_RESEND}


class InviteError(RuntimeError):
    """Base class for invite failures."""


class AlreadyMemberError(InviteError):
    """The email is already a member of the org."""


class UnauthorizedInviteError(InviteError):
    """The actor is not an owner/admin of the org."""


class InviteProvisioningError(InviteError):
    """An upstream (RPC or GoTrue) failure; never leaks upstream detail."""


class SupabaseMemberRpc:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = f"{supabase_url.rstrip('/')}/rest/v1/rpc/add_org_member_by_email"
        self._key = service_role_key
        self._timeout = timeout_seconds
        self._transport = transport

    def __call__(self, actor_user_id: str, organization_id: str, email: str) -> str:
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(
                    self._url,
                    json={
                        "p_actor_user_id": actor_user_id,
                        "p_org_id": organization_id,
                        "p_email": email,
                    },
                    headers={
                        "apikey": self._key,
                        "authorization": f"Bearer {self._key}",
                        "content-type": "application/json",
                    },
                )
        except httpx.HTTPError:
            raise InviteProvisioningError("Could not send the invite.") from None
        if response.status_code not in (200, 201):
            raise InviteProvisioningError("Could not send the invite.")
        try:
            return str(response.json())
        except ValueError:
            raise InviteProvisioningError("Could not send the invite.") from None


class SupabaseUserInviter:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = supabase_url.rstrip("/")
        self._invite_url = f"{base}/auth/v1/invite"
        self._generate_link_url = f"{base}/auth/v1/admin/generate_link"
        self._key = service_role_key
        self._timeout = timeout_seconds
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._key,
            "authorization": f"Bearer {self._key}",
            "content-type": "application/json",
        }

    def invite(self, email: str, *, data: dict | None = None) -> None:
        body = {"email": email, **({"data": data} if data else {})}
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(
                    self._invite_url, json=body, headers=self._headers()
                )
        except httpx.HTTPError:
            raise InviteProvisioningError("Could not send the invite.") from None
        if response.status_code in (200, 201):
            return
        # Recoverable: the user already exists (created between RPC calls). The
        # caller re-runs the RPC to attach membership, so this is not an error.
        if response.status_code == 422 and _is_user_exists(response):
            return
        raise InviteProvisioningError("Could not send the invite.")

    def resend(self, email: str, *, data: dict | None = None) -> None:
        body = {"type": "invite", "email": email, **({"data": data} if data else {})}
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(
                    self._generate_link_url,
                    json=body,
                    headers=self._headers(),
                )
        except httpx.HTTPError:
            raise InviteProvisioningError("Could not send the invite.") from None
        if response.status_code not in (200, 201):
            raise InviteProvisioningError("Could not send the invite.")


def _is_user_exists(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    blob = f"{body.get('error_code') or ''} {body.get('msg') or ''}".lower()
    return "exist" in blob or "registered" in blob


_rpc: SupabaseMemberRpc | None = None
_inviter: SupabaseUserInviter | None = None


def configure_invitations(
    rpc: SupabaseMemberRpc | None, inviter: SupabaseUserInviter | None
) -> None:
    global _rpc, _inviter
    _rpc = rpc
    _inviter = inviter


def invite_member_to_org(
    *,
    actor_user_id: str,
    organization_id: str,
    email: str,
    org_name: str | None = None,
    account_locator: str | None = None,
    rpc: object | None = None,
    inviter: object | None = None,
) -> str:
    selected_rpc = rpc if rpc is not None else _rpc
    selected_inviter = inviter if inviter is not None else _inviter
    if selected_rpc is None or selected_inviter is None:
        raise InviteProvisioningError("Invitations are not configured.")

    metadata = {
        k: v
        for k, v in (("org_name", org_name), ("account_locator", account_locator))
        if v is not None
    }
    data = metadata or None

    status = selected_rpc(actor_user_id, organization_id, email)
    if status == STATUS_ALREADY_MEMBER:
        raise AlreadyMemberError(f"{email} is already a member.")
    if status == STATUS_UNAUTHORIZED:
        raise UnauthorizedInviteError("Organization admin access required")
    if status == STATUS_ADDED:
        return email
    if status == STATUS_PENDING_RESEND:
        selected_inviter.resend(email, data=data)
        return email
    if status == STATUS_INVITE_NEEDED:
        selected_inviter.invite(email, data=data)
        status2 = selected_rpc(actor_user_id, organization_id, email)
        if status2 not in _TERMINAL_OK:
            raise InviteProvisioningError("Could not send the invite.")
        return email
    raise InviteProvisioningError("Could not send the invite.")
