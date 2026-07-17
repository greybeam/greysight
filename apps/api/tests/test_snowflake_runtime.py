import httpx

from app.config import Settings
from app.services.http_pool import get_sync_client
from app.services.snowflake_runtime import get_connection_fetcher
from tests.conftest import installed_sync_pool


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/organization_snowflake_connections"):
        return httpx.Response(
            200,
            json=[
                {
                    "account": "acct",
                    "snowflake_user": "u",
                    "role": "r",
                    "warehouse": "w",
                    "database": None,
                    "schema": None,
                    "status": "active",
                    "secret_id": "sec-1",
                }
            ],
        )
    if request.url.path.endswith("/rpc/get_organization_snowflake_secret"):
        return httpx.Response(
            200,
            json=[{"private_key_pem": "PEMDATA", "passphrase": None}],
        )
    return httpx.Response(404)


def test_connection_fetcher_uses_pooled_sync_client() -> None:
    settings = Settings(
        auth_required=True,
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="svc",
    )

    with installed_sync_pool(_handler) as sync_client:
        fetcher = get_connection_fetcher(settings)
        # The fetcher must be bound to the lifespan-owned pooled sync client.
        assert fetcher._client is sync_client  # type: ignore[attr-defined]
        assert fetcher._client is get_sync_client()  # type: ignore[attr-defined]
        row = fetcher("org-1")
        assert row is not None
        assert row.account == "acct"
        # The pooled client was reused, not closed by the fetcher.
        assert sync_client.is_closed is False
