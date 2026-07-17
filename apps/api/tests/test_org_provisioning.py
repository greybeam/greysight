import contextlib

import anyio
import httpx
import pytest

from app.services.http_pool import clear_clients, get_sync_client, install_clients
from app.services.org_provisioning import (
    DuplicateSnowflakeAccountError,
    OrgProvisioningError,
    SupabaseOrgDisconnector,
    SupabaseOrgProvisioner,
)


@contextlib.contextmanager
def _installed_sync_pool(handler):
    clear_clients()
    sync_client = httpx.Client(transport=httpx.MockTransport(handler))
    auth = httpx.AsyncClient()
    async_client = httpx.AsyncClient()
    install_clients(auth=auth, async_client=async_client, sync_client=sync_client)
    try:
        yield sync_client
    finally:
        clear_clients()
        sync_client.close()
        anyio.run(auth.aclose)
        anyio.run(async_client.aclose)


def test_provisioner_reuses_pooled_sync_client_without_closing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json="org-123")

    with _installed_sync_pool(handler) as sync_client:
        provisioner = SupabaseOrgProvisioner(
            supabase_url="https://example.supabase.co",
            service_role_key="svc",
        )
        assert _provision(provisioner) == "org-123"
        assert get_sync_client() is sync_client
        assert not sync_client.is_closed
        assert len(requests) == 1


def test_disconnector_reuses_pooled_sync_client_without_closing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    with _installed_sync_pool(handler) as sync_client:
        disconnector = SupabaseOrgDisconnector(
            supabase_url="https://example.supabase.co",
            service_role_key="svc",
        )
        disconnector("org-1")
        assert get_sync_client() is sync_client
        assert not sync_client.is_closed
        assert len(requests) == 1


def _provision(provisioner: SupabaseOrgProvisioner) -> str:
    return provisioner(
        p_user_id="user-1",
        p_org_name="Acme",
        p_account="acct",
        p_user="u",
        p_role="r",
        p_warehouse="w",
        p_database="",
        p_schema="",
        p_private_key_pem="PEMSECRET",
        p_passphrase="PASSSECRET",
    )


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


def test_raises_provisioning_error_on_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OrgProvisioningError) as excinfo:
        _provision(provisioner)

    # Must surface a neutral OrgProvisioningError, never a raw httpx error.
    assert not isinstance(excinfo.value, DuplicateSnowflakeAccountError)
    message = str(excinfo.value)
    assert "PEMSECRET" not in message
    assert "PASSSECRET" not in message


def test_raises_provisioning_error_on_non_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OrgProvisioningError):
        _provision(provisioner)


def test_duplicate_account_detected_by_account_constraint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "23505",
                "message": (
                    "duplicate key value violates unique constraint "
                    '"org_active_account_unique"'
                ),
                "details": "Key (upper(account))=(ABC123) already exists.",
            },
        )

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(DuplicateSnowflakeAccountError):
        _provision(provisioner)


def test_other_unique_violation_not_treated_as_duplicate_account() -> None:
    # A 23505 from the legacy one-owner-cap index (raised when a user who
    # already owns an org adds a second org on a DB missing migration
    # 202606180001) must NOT be mislabeled as a duplicate Snowflake account.
    # It should fall through to the generic OrgProvisioningError -> 502.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "23505",
                "message": (
                    "duplicate key value violates unique constraint "
                    '"one_owner_membership_per_user"'
                ),
                "details": "Key (user_id)=(user-1) already exists.",
            },
        )

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OrgProvisioningError) as excinfo:
        _provision(provisioner)
    assert not isinstance(excinfo.value, DuplicateSnowflakeAccountError)


def test_generic_unique_violation_message_not_treated_as_duplicate() -> None:
    # A bare 23505 with no recognizable constraint name should NOT be treated
    # as a duplicate account; it falls through to the generic error.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"code": "23505", "message": "unique_violation"}
        )

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OrgProvisioningError) as excinfo:
        _provision(provisioner)
    assert not isinstance(excinfo.value, DuplicateSnowflakeAccountError)


def test_success_body_not_misread_as_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="org-123")

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    assert _provision(provisioner) == "org-123"
