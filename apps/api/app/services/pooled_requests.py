"""Shared helper for issuing HTTP requests via the pooled sync client.

Service clients that talk to Supabase share one behavior: when a test supplies
a ``transport`` override they must use a short-lived ``httpx.Client`` bound to
that transport; otherwise they reuse the process-wide pooled sync client. This
module centralizes that logic so each service's ``_send`` can simply delegate.
"""

import httpx

from app.services.http_pool import get_sync_client, request_timeout


def send_pooled_request(
    method: str,
    url: str,
    *,
    transport: httpx.BaseTransport | None,
    timeout_seconds: float,
    **kwargs: object,
) -> httpx.Response:
    """Issue an HTTP request via a transport override or the pooled client."""
    timeout = request_timeout(timeout_seconds)
    if transport is not None:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            return client.request(method, url, timeout=timeout, **kwargs)
    return get_sync_client().request(method, url, timeout=timeout, **kwargs)
