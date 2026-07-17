import anyio
import httpx
import pytest

from app import main
from app.services.http_pool import (
    POOL_TIMEOUT_SECONDS,
    clear_clients,
    get_async_client,
    get_auth_client,
    get_sync_client,
    install_clients,
    request_timeout,
)


def test_getters_fail_before_install_and_after_clear() -> None:
    clear_clients()
    with pytest.raises(RuntimeError, match="not initialized"):
        get_sync_client()
    with pytest.raises(RuntimeError, match="not initialized"):
        get_auth_client()
    with pytest.raises(RuntimeError, match="not initialized"):
        get_async_client()


def test_install_returns_same_client_objects_on_every_get() -> None:
    clear_clients()
    auth = httpx.AsyncClient()
    async_client = httpx.AsyncClient()
    sync_client = httpx.Client()
    install_clients(auth=auth, async_client=async_client, sync_client=sync_client)
    try:
        assert get_auth_client() is auth
        assert get_async_client() is async_client
        assert get_sync_client() is sync_client
    finally:
        clear_clients()
        anyio.run(auth.aclose)
        anyio.run(async_client.aclose)
        sync_client.close()


def test_reinstall_same_objects_is_noop() -> None:
    clear_clients()
    auth = httpx.AsyncClient()
    async_client = httpx.AsyncClient()
    sync_client = httpx.Client()
    install_clients(auth=auth, async_client=async_client, sync_client=sync_client)
    try:
        install_clients(auth=auth, async_client=async_client, sync_client=sync_client)
        assert get_auth_client() is auth
    finally:
        clear_clients()
        anyio.run(auth.aclose)
        anyio.run(async_client.aclose)
        sync_client.close()


def test_reinstall_different_objects_raises() -> None:
    clear_clients()
    auth = httpx.AsyncClient()
    async_client = httpx.AsyncClient()
    sync_client = httpx.Client()
    other = httpx.AsyncClient()
    install_clients(auth=auth, async_client=async_client, sync_client=sync_client)
    try:
        with pytest.raises(RuntimeError, match="already initialized"):
            install_clients(
                auth=other, async_client=async_client, sync_client=sync_client
            )
    finally:
        clear_clients()
        anyio.run(auth.aclose)
        anyio.run(async_client.aclose)
        anyio.run(other.aclose)
        sync_client.close()


def test_request_timeout_preserves_pool_timeout() -> None:
    timeout = request_timeout(37.0)
    assert timeout.connect == 37.0
    assert timeout.read == 37.0
    assert timeout.write == 37.0
    assert timeout.pool == POOL_TIMEOUT_SECONDS


def test_lifespan_clients_have_no_credential_defaults() -> None:
    clear_clients()

    async def _check() -> None:
        async with main._lifespan(main.app):
            for client in (
                get_auth_client(),
                get_async_client(),
                get_sync_client(),
            ):
                assert "authorization" not in client.headers
                assert "apikey" not in client.headers
                assert not client.cookies
                assert client.auth is None
                assert not client.params

    anyio.run(_check)
    with pytest.raises(RuntimeError, match="not initialized"):
        get_sync_client()


def test_lifespan_installs_and_closes_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_clients()
    created: list[object] = []

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.closed = 0
            self.headers = httpx.Headers()
            self.cookies = httpx.Cookies()
            self.auth = None
            self.params = httpx.QueryParams()
            created.append(self)

        async def aclose(self) -> None:
            self.closed += 1

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.closed = 0
            self.headers = httpx.Headers()
            self.cookies = httpx.Cookies()
            self.auth = None
            self.params = httpx.QueryParams()
            created.append(self)

        def close(self) -> None:
            self.closed += 1

    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(main.httpx, "Client", _FakeClient)

    async def _run() -> None:
        async with main._lifespan(main.app):
            assert get_auth_client() is not None
            assert get_async_client() is not None
            assert get_sync_client() is not None

    anyio.run(_run)

    assert len(created) == 3
    for client in created:
        assert client.closed == 1  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="not initialized"):
        get_sync_client()
