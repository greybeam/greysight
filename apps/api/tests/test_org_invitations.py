import httpx
import pytest

from app.services.org_invitations import (
    AlreadyMemberError,
    InviteProvisioningError,
    SupabaseMemberRpc,
    SupabaseUserInviter,
    UnauthorizedInviteError,
    invite_member_to_org,
)


class FakeInviter:
    def __init__(self) -> None:
        self.invited: list[str] = []
        self.resent: list[str] = []

    def invite(self, email: str) -> None:
        self.invited.append(email)

    def resend(self, email: str) -> None:
        self.resent.append(email)


def _rpc(*statuses: str):
    """Return a fake RPC callable yielding the given statuses in order."""
    calls = {"n": 0}

    def call(actor_user_id: str, organization_id: str, email: str) -> str:
        i = min(calls["n"], len(statuses) - 1)
        calls["n"] += 1
        return statuses[i]

    return call


def _invite(rpc, inviter):
    return invite_member_to_org(
        actor_user_id="actor-1",
        organization_id="org-1",
        email="new@acme.com",
        rpc=rpc,
        inviter=inviter,
    )


def test_added_existing_user_sends_no_email() -> None:
    inviter = FakeInviter()
    assert _invite(_rpc("added"), inviter) == "new@acme.com"
    assert inviter.invited == [] and inviter.resent == []


def test_invite_needed_invites_then_reconfirms() -> None:
    inviter = FakeInviter()
    assert _invite(_rpc("invite_needed", "added"), inviter) == "new@acme.com"
    assert inviter.invited == ["new@acme.com"]


def test_pending_resend_resends_link() -> None:
    inviter = FakeInviter()
    assert _invite(_rpc("pending_resend"), inviter) == "new@acme.com"
    assert inviter.resent == ["new@acme.com"]


def test_already_member_raises() -> None:
    with pytest.raises(AlreadyMemberError):
        _invite(_rpc("already_member"), FakeInviter())


def test_unauthorized_raises() -> None:
    with pytest.raises(UnauthorizedInviteError):
        _invite(_rpc("unauthorized"), FakeInviter())


def test_unexpected_status_raises_provisioning_error() -> None:
    with pytest.raises(InviteProvisioningError):
        _invite(_rpc("???"), FakeInviter())


def test_rpc_client_posts_and_returns_status() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(200, json="added")

    rpc = SupabaseMemberRpc(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    assert rpc("actor-1", "org-1", "new@acme.com") == "added"
    assert seen["path"].endswith("/rpc/add_org_member_by_email")
    assert "actor-1" in seen["body"] and "org-1" in seen["body"]


def test_rpc_client_raises_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    rpc = SupabaseMemberRpc(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(InviteProvisioningError):
        rpc("a", "o", "e@x.com")


def test_inviter_invite_hits_invite_endpoint() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": "u1"})

    inviter = SupabaseUserInviter(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    inviter.invite("new@acme.com")
    assert seen["path"].endswith("/auth/v1/invite")


def test_inviter_invite_tolerates_already_registered() -> None:
    # Recoverable TOCTOU: user created between RPC calls. invite() must not raise.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422, json={"error_code": "email_exists", "msg": "already registered"}
        )

    inviter = SupabaseUserInviter(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    inviter.invite("new@acme.com")  # no raise


def test_inviter_resend_hits_generate_link() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"action_link": "https://x"})

    inviter = SupabaseUserInviter(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    inviter.resend("new@acme.com")
    assert seen["path"].endswith("/auth/v1/admin/generate_link")
    assert '"invite"' in seen["body"]
