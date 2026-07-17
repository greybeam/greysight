"""Process-wide, credential-neutral httpx client pools.

The FastAPI lifespan owns three shared clients — an async client for auth
requests, a general async client, and a sync client — so services reuse
connections instead of opening a fresh pool per request. The clients carry no
credentials (no default ``headers``, ``auth``, ``cookies``, or ``params``);
per-request auth is supplied by callers.
"""

from __future__ import annotations

import http.cookiejar
from typing import TypeVar

import httpx

POOL_TIMEOUT_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 10.0
HTTP_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)

_auth_client: httpx.AsyncClient | None = None
_async_client: httpx.AsyncClient | None = None
_sync_client: httpx.Client | None = None


class _NoStoreCookieJar(http.cookiejar.CookieJar):
    """A cookie jar that silently drops every response ``Set-Cookie``.

    The pooled clients are shared across credentials, so persisting a
    ``Set-Cookie`` would replay it as a ``Cookie:`` header on later requests to
    the same host — cross-credential leakage (worst on the auth client, which
    hits one Supabase host with per-user bearer tokens).
    """

    def set_cookie(self, cookie: http.cookiejar.Cookie) -> None:  # noqa: D102
        pass


_C = TypeVar("_C", httpx.Client, httpx.AsyncClient)


def disable_cookie_persistence(client: _C) -> _C:
    """Stop ``client`` from storing/replaying response cookies; returns it.

    httpx re-wraps a ``cookies=`` constructor argument into a plain
    ``httpx.Cookies`` (dropping any custom jar), so the no-op jar must be
    installed *after* construction via the private ``_cookies`` attribute —
    there is no public post-construction setter as of httpx 0.28.
    """
    client._cookies = httpx.Cookies(_NoStoreCookieJar())
    return client


def client_timeout() -> httpx.Timeout:
    return httpx.Timeout(DEFAULT_TIMEOUT_SECONDS, pool=POOL_TIMEOUT_SECONDS)


def request_timeout(timeout_seconds: float) -> httpx.Timeout:
    return httpx.Timeout(timeout_seconds, pool=POOL_TIMEOUT_SECONDS)


def install_clients(
    *,
    auth: httpx.AsyncClient,
    async_client: httpx.AsyncClient,
    sync_client: httpx.Client,
) -> None:
    global _auth_client, _async_client, _sync_client
    if any(
        client is not None for client in (_auth_client, _async_client, _sync_client)
    ):
        if (_auth_client, _async_client, _sync_client) != (
            auth,
            async_client,
            sync_client,
        ):
            raise RuntimeError("HTTP pool is already initialized")
        return
    _auth_client, _async_client, _sync_client = auth, async_client, sync_client


def clear_clients() -> None:
    global _auth_client, _async_client, _sync_client
    _auth_client = _async_client = _sync_client = None


def get_auth_client() -> httpx.AsyncClient:
    if _auth_client is None:
        raise RuntimeError("HTTP pool is not initialized")
    return _auth_client


def get_async_client() -> httpx.AsyncClient:
    if _async_client is None:
        raise RuntimeError("HTTP pool is not initialized")
    return _async_client


def get_sync_client() -> httpx.Client:
    if _sync_client is None:
        raise RuntimeError("HTTP pool is not initialized")
    return _sync_client
