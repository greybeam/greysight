import anyio
import httpx
import pytest

from app.services.membership_directory import (
    MembershipLookupError,
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
    assert (
        "organizations(id,name,organization_snowflake_connections(account,account_locator,status))"
        in requests[0].url.params["select"]
    )
    assert requests[0].headers["apikey"] == "service-role-key"
    assert requests[0].headers["authorization"] == "Bearer service-role-key"


def test_empty_membership_returns_empty_tuple() -> None:
    orgs = anyio.run(_lookup(lambda _r: httpx.Response(200, json=[])), "user-123")
    assert orgs == ()


def test_non_200_raises_lookup_error() -> None:
    with pytest.raises(MembershipLookupError):
        anyio.run(_lookup(lambda _r: httpx.Response(500, json={})), "user-123")


def test_transport_error_raises_lookup_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private detail")

    with pytest.raises(MembershipLookupError):
        anyio.run(_lookup(handler), "user-123")


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
