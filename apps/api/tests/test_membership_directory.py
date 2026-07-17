import anyio
import httpx
import pytest

from app.services.http_pool import (
    clear_clients,
    get_async_client,
    install_clients,
)
from app.services.membership_directory import (
    MembershipLookupError,
    MembershipLookupUnavailable,
    Organization,
    SupabaseServiceRoleMembershipLookup,
)


def _lookup(handler: "callable") -> SupabaseServiceRoleMembershipLookup:
    return SupabaseServiceRoleMembershipLookup(
        supabase_url="https://project.supabase.co",
        service_role_key="service-role-key",
        transport=httpx.MockTransport(handler),
    )


def test_returns_organizations_for_user() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "organization_id": "org-1",
                    "organizations": {"id": "org-1", "name": "Acme"},
                },
                {
                    "organization_id": "org-2",
                    "organizations": {"id": "org-2", "name": "Beta"},
                },
            ],
        )

    orgs = anyio.run(_lookup(handler), "user-123")

    assert orgs == (
        Organization(id="org-1", name="Acme"),
        Organization(id="org-2", name="Beta"),
    )
    assert requests[0].url.params["user_id"] == "eq.user-123"
    # A deterministic order is required: PostgREST returns rows in arbitrary
    # physical order without it, which makes the frontend's implicit active-org
    # fallback (organizations[0]) flip between refreshes.
    assert requests[0].url.params["order"] == "organization_id.asc"
    assert (
        "organizations(id,name,organization_snowflake_connections(account,account_locator,status))"
        in requests[0].url.params["select"]
    )
    assert requests[0].headers["apikey"] == "service-role-key"
    assert requests[0].headers["authorization"] == "Bearer service-role-key"


@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
def test_upstream_5xx_or_429_raises_unavailable(status_code: int) -> None:
    with pytest.raises(MembershipLookupUnavailable):
        anyio.run(
            _lookup(lambda _r: httpx.Response(status_code, json={})), "user-123"
        )


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_upstream_4xx_raises_lookup_error(status_code: int) -> None:
    with pytest.raises(MembershipLookupError) as exc_info:
        anyio.run(
            _lookup(lambda _r: httpx.Response(status_code, json={})), "user-123"
        )
    # Client-side rejections stay the base lookup error (auth maps to 401),
    # not the unavailable subclass (which maps to 503).
    assert not isinstance(exc_info.value, MembershipLookupUnavailable)


def test_transport_error_raises_lookup_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private detail")

    with pytest.raises(MembershipLookupUnavailable):
        anyio.run(_lookup(handler), "user-123")


def test_membership_reuses_pooled_client() -> None:
    clear_clients()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "organization_id": "org-1",
                    "organizations": {"id": "org-1", "name": "Acme"},
                }
            ],
        )

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = httpx.AsyncClient()
    sync_client = httpx.Client()
    install_clients(auth=auth, async_client=async_client, sync_client=sync_client)
    try:
        lookup = SupabaseServiceRoleMembershipLookup(
            supabase_url="https://project.supabase.co",
            service_role_key="service-role-key",
        )
        anyio.run(lookup, "user-1")
        anyio.run(lookup, "user-2")

        assert get_async_client() is async_client
        assert len(requests) == 2
        assert requests[0].url.params["user_id"] == "eq.user-1"
        assert requests[1].url.params["user_id"] == "eq.user-2"
        assert requests[0].headers["apikey"] == "service-role-key"
        # Pooled client stays credential-neutral after per-request auth.
        assert "authorization" not in async_client.headers
        assert "apikey" not in async_client.headers
    finally:
        clear_clients()
        anyio.run(auth.aclose)
        anyio.run(async_client.aclose)
        sync_client.close()


def test_membership_pool_timeout_raises_unavailable() -> None:
    clear_clients()

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.PoolTimeout("pool exhausted")

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = httpx.AsyncClient()
    sync_client = httpx.Client()
    install_clients(auth=auth, async_client=async_client, sync_client=sync_client)
    try:
        lookup = SupabaseServiceRoleMembershipLookup(
            supabase_url="https://project.supabase.co",
            service_role_key="service-role-key",
        )
        with pytest.raises(MembershipLookupUnavailable):
            anyio.run(lookup, "user-1")
    finally:
        clear_clients()
        anyio.run(auth.aclose)
        anyio.run(async_client.aclose)
        sync_client.close()


def test_truncated_result_raises_lookup_error() -> None:
    rows = [
        {
            "organization_id": f"org-{i}",
            "organizations": {"id": f"org-{i}", "name": f"O{i}"},
        }
        for i in range(201)
    ]
    with pytest.raises(MembershipLookupError):
        anyio.run(_lookup(lambda _r: httpx.Response(200, json=rows)), "user-123")


def test_malformed_row_raises_lookup_error() -> None:
    rows = [{"organization_id": "org-1", "organizations": {"id": "", "name": "Acme"}}]
    with pytest.raises(MembershipLookupError):
        anyio.run(_lookup(lambda _r: httpx.Response(200, json=rows)), "user-123")


def test_parses_membership_role() -> None:
    from app.services.membership_directory import _parse_organization

    org = _parse_organization(
        {
            "role": "admin",
            "organizations": {"id": "org-1", "name": "Acme"},
        }
    )
    assert org.id == "org-1"
    assert org.role == "admin"
    assert org.account_locator is None


def test_parses_account_locator_from_embedded_object() -> None:
    from app.services.membership_directory import _parse_organization

    org = _parse_organization(
        {
            "organizations": {
                "id": "org-1",
                "name": "Acme",
                "organization_snowflake_connections": {
                    "account_locator": "IJ42635",
                    "status": "active",
                },
            },
        }
    )
    assert org.account_locator == "IJ42635"
    assert org.connection_status == "active"


def test_parses_account_locator_from_embedded_list() -> None:
    from app.services.membership_directory import _parse_organization

    org = _parse_organization(
        {
            "organizations": {
                "id": "org-1",
                "name": "Acme",
                "organization_snowflake_connections": [
                    {"account_locator": "TU24199", "status": "invalid"}
                ],
            },
        }
    )
    assert org.account_locator == "TU24199"
    assert org.connection_status == "invalid"


def test_missing_connection_yields_null_account_locator() -> None:
    from app.services.membership_directory import _parse_organization

    org = _parse_organization(
        {
            "organizations": {
                "id": "org-1",
                "name": "Acme",
                "organization_snowflake_connections": None,
            },
        }
    )
    assert org.account_locator is None
