import httpx

from app.services.org_provisioning import SupabaseOrgProvisioner


def test_calls_create_rpc_and_returns_org_id() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(200, json="org-123")

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    org_id = provisioner(
        p_user_id="user-1",
        p_org_name="Acme",
        p_account="acct",
        p_user="u",
        p_role="r",
        p_warehouse="w",
        p_database="",
        p_schema="",
        p_private_key_pem="PEM",
        p_passphrase="",
    )
    assert org_id == "org-123"
    assert seen["path"].endswith("/rpc/create_org_with_snowflake_connection")
    assert "user-1" in seen["body"]


def test_raises_on_one_org_guard_conflict() -> None:
    import pytest

    from app.services.org_provisioning import OrgAlreadyExistsError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"code": "23505", "message": "unique_violation"}
        )

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OrgAlreadyExistsError):
        provisioner(
            p_user_id="user-1",
            p_org_name="Acme",
            p_account="acct",
            p_user="u",
            p_role="r",
            p_warehouse="w",
            p_database="",
            p_schema="",
            p_private_key_pem="PEM",
            p_passphrase="",
        )
