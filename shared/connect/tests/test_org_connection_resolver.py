from dataclasses import dataclass

import httpx
import pytest

from greysight_connect.org_connection_resolver import (
    OrgConnectionNotConfiguredError,
    OrgConnectionRow,
    OrgConnectionUnavailableError,
    SupabaseConnectionFetcher,
    resolve_snowflake_config,
)


@dataclass
class Settings:
    auth_required: bool = False
    query_timeout_seconds: int = 120


def _row() -> OrgConnectionRow:
    return OrgConnectionRow(
        account="acct",
        snowflake_user="u",
        role="r",
        warehouse="w",
        database=None,
        schema=None,
        private_key_pem="-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----",
        passphrase=None,
    )


def test_uses_per_org_row_when_present() -> None:
    settings = Settings(auth_required=True)
    config = resolve_snowflake_config(
        "org-1", settings, fetch_connection=lambda _org_id: _row()
    )
    assert config.account == "acct"
    assert config.private_key_pem is not None


def test_fails_closed_when_no_row_and_auth_required() -> None:
    settings = Settings(auth_required=True)
    with pytest.raises(OrgConnectionNotConfiguredError):
        resolve_snowflake_config(
            "org-1", settings, fetch_connection=lambda _org_id: None
        )


def test_falls_back_to_env_when_auth_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "env-acct")
    settings = Settings(auth_required=False)
    config = resolve_snowflake_config(
        "org-1", settings, fetch_connection=lambda _org_id: None
    )
    assert config.account == "env-acct"


def test_unexpected_fetcher_error_is_not_reclassified_as_transient() -> None:
    settings = Settings(auth_required=True)

    def _boom(_org_id: str) -> OrgConnectionRow | None:
        raise RuntimeError("vault down")

    with pytest.raises(RuntimeError, match="vault down"):
        resolve_snowflake_config("org-1", settings, fetch_connection=_boom)


def test_classified_transient_error_propagates_but_stays_fail_closed() -> None:
    # The fetcher boundary classifies retryable transport/HTTP failures so the
    # worker can KEEP a warm session, while the API's broader parent catch remains
    # fail closed.
    settings = Settings(auth_required=True)

    def _boom(_org_id: str) -> OrgConnectionRow | None:
        raise OrgConnectionUnavailableError("supabase timed out")

    with pytest.raises(OrgConnectionUnavailableError):
        resolve_snowflake_config("org-1", settings, fetch_connection=_boom)
    # Subclass relationship preserves the API's fail-closed catch.
    assert issubclass(OrgConnectionUnavailableError, OrgConnectionNotConfiguredError)


def test_definitive_not_configured_from_fetcher_is_not_reclassified() -> None:
    # A DEFINITIVE verdict the fetcher already made (e.g. malformed/duplicate)
    # must propagate as-is (genuinely not configured), NOT as a transient error,
    # so the worker drops it instead of keeping a dead session.
    settings = Settings(auth_required=True)

    def _misconfigured(_org_id: str) -> OrgConnectionRow | None:
        raise OrgConnectionNotConfiguredError("multiple rows")

    with pytest.raises(OrgConnectionNotConfiguredError) as excinfo:
        resolve_snowflake_config("org-1", settings, fetch_connection=_misconfigured)
    assert not isinstance(excinfo.value, OrgConnectionUnavailableError)


def test_fails_closed_when_row_status_not_active() -> None:
    settings = Settings(auth_required=True)

    def _invalid(_org_id: str) -> OrgConnectionRow:
        return OrgConnectionRow(
            account="acct",
            snowflake_user="u",
            role="r",
            warehouse="w",
            database=None,
            schema=None,
            private_key_pem="-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----",
            passphrase=None,
            status="invalid",
        )

    with pytest.raises(OrgConnectionNotConfiguredError):
        resolve_snowflake_config("org-1", settings, fetch_connection=_invalid)


def _transport(handler):
    return httpx.MockTransport(handler)


def test_fetcher_combines_row_metadata_and_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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
                json=[
                    {
                        "private_key_pem": "PEMDATA",
                        "passphrase": None,
                    }
                ],
            )
        return httpx.Response(404)

    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(handler),
    )
    row = fetcher("org-1")
    assert row is not None
    assert row.account == "acct"
    assert row.private_key_pem == "PEMDATA"
    assert row.status == "active"


def test_fetcher_returns_none_when_metadata_is_empty() -> None:
    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(lambda _request: httpx.Response(200, json=[])),
    )

    assert fetcher("org-1") is None


def test_org_connection_row_repr_redacts_secrets() -> None:
    row = OrgConnectionRow(
        account="acct",
        snowflake_user="u",
        role="r",
        warehouse="w",
        database=None,
        schema=None,
        private_key_pem="-----BEGIN PRIVATE KEY-----\nSECRETKEYBODY\n-----END PRIVATE KEY-----",
        passphrase="hunter2",
    )
    rendered = repr(row)
    assert "SECRETKEYBODY" not in rendered
    assert "hunter2" not in rendered


def test_fetcher_raises_on_multiple_metadata_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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
                    },
                    {
                        "account": "acct2",
                        "snowflake_user": "u2",
                        "role": "r2",
                        "warehouse": "w2",
                        "database": None,
                        "schema": None,
                        "status": "active",
                        "secret_id": "sec-2",
                    },
                ],
            )
        return httpx.Response(404)

    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(handler),
    )
    with pytest.raises(OrgConnectionNotConfiguredError):
        fetcher("org-1")


def test_fetcher_raises_on_malformed_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/organization_snowflake_connections"):
            return httpx.Response(200, json={"unexpected": "shape"})
        return httpx.Response(404)

    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(handler),
    )
    with pytest.raises(OrgConnectionNotConfiguredError):
        fetcher("org-1")


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_fetcher_classifies_nonretryable_http_errors_as_not_configured(
    status_code: int,
) -> None:
    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(
            lambda _request: httpx.Response(status_code, json={"message": "bad"})
        ),
    )

    with pytest.raises(OrgConnectionNotConfiguredError) as exc_info:
        fetcher("org-1")

    assert not isinstance(exc_info.value, OrgConnectionUnavailableError)


@pytest.mark.parametrize("status_code", [408, 429, 500, 503])
def test_fetcher_classifies_retryable_http_errors_as_unavailable(
    status_code: int,
) -> None:
    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(
            lambda _request: httpx.Response(status_code, json={"message": "retry"})
        ),
    )

    with pytest.raises(OrgConnectionUnavailableError):
        fetcher("org-1")


def test_fetcher_classifies_transport_error_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(handler),
    )

    with pytest.raises(OrgConnectionUnavailableError):
        fetcher("org-1")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"{"),
        httpx.Response(200, json=[None]),
        httpx.Response(200, json=[{"secret_id": "sec-1"}]),
        httpx.Response(
            200,
            json=[
                {
                    "account": "acct",
                    "snowflake_user": "u",
                    "role": "r",
                    "warehouse": "w",
                }
            ],
        ),
    ],
)
def test_fetcher_classifies_invalid_metadata_as_not_configured(
    response: httpx.Response,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return response
        return httpx.Response(
            200,
            json=[{"private_key_pem": "PEMDATA", "passphrase": None}],
        )

    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(handler),
    )

    with pytest.raises(OrgConnectionNotConfiguredError) as exc_info:
        fetcher("org-1")

    assert not isinstance(exc_info.value, OrgConnectionUnavailableError)


@pytest.mark.parametrize(
    "secret_response",
    [
        httpx.Response(200, content=b"{"),
        httpx.Response(200, json=[None]),
        httpx.Response(200, json=[{}]),
    ],
)
def test_fetcher_classifies_invalid_secret_as_not_configured(
    secret_response: httpx.Response,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
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
        return secret_response

    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(handler),
    )

    with pytest.raises(OrgConnectionNotConfiguredError) as exc_info:
        fetcher("org-1")

    assert not isinstance(exc_info.value, OrgConnectionUnavailableError)


def _row_with_locator(**over):
    base = dict(
        account="myorg-acct",
        snowflake_user="u",
        role="r",
        warehouse="w",
        database=None,
        schema=None,
        private_key_pem="pem",
        passphrase=None,
        status="active",
        account_locator="XY12345",
    )
    base.update(over)
    return OrgConnectionRow(**base)


def test_resolver_threads_account_locator() -> None:
    config = resolve_snowflake_config(
        "org-1", Settings(), fetch_connection=lambda _id: _row_with_locator()
    )
    assert config.account == "myorg-acct"
    assert config.account_locator == "XY12345"


class _RecordingClient:
    """Wraps a real httpx.Client to record use and forbid close()."""

    def __init__(self, inner: httpx.Client) -> None:
        self._inner = inner
        self.get_calls = 0
        self.post_calls = 0
        self.closed = False

    def get(self, *args, **kwargs):
        self.get_calls += 1
        return self._inner.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        self.post_calls += 1
        return self._inner.post(*args, **kwargs)

    def close(self) -> None:
        self.closed = True
        self._inner.close()


def _connection_handler(request: httpx.Request) -> httpx.Response:
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


def test_fetcher_uses_injected_client_without_closing_it() -> None:
    inner = httpx.Client(transport=_transport(_connection_handler))
    recording = _RecordingClient(inner)
    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        client=recording,  # type: ignore[arg-type]
    )

    row = fetcher("org-1")

    assert row is not None
    assert row.account == "acct"
    assert row.private_key_pem == "PEMDATA"
    # The injected client did the work and was NOT closed by the fetcher.
    assert recording.get_calls == 1
    assert recording.post_calls == 1
    assert recording.closed is False

    inner.close()


def test_fetcher_reuses_injected_client_across_sequential_lookups() -> None:
    seen_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("authorization", ""))
        return _connection_handler(request)

    inner = httpx.Client(transport=_transport(handler))
    recording = _RecordingClient(inner)
    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        client=recording,  # type: ignore[arg-type]
    )

    assert fetcher("org-1") is not None
    assert fetcher("org-2") is not None

    # Same client object serviced both lookups (2 GET + 2 POST).
    assert recording.get_calls == 2
    assert recording.post_calls == 2
    assert recording.closed is False
    # Per-request auth headers are still supplied.
    assert seen_headers == ["Bearer svc"] * 4


def test_fetcher_raises_on_multiple_secret_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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
                json=[
                    {"private_key_pem": "PEMDATA", "passphrase": None},
                    {"private_key_pem": "PEMDATA2", "passphrase": None},
                ],
            )
        return httpx.Response(404)

    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(handler),
    )
    with pytest.raises(OrgConnectionNotConfiguredError):
        fetcher("org-1")
