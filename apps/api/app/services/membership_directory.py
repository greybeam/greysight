from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx

MAX_MEMBERSHIPS = 200


@dataclass(frozen=True)
class Organization:
    id: str
    name: str


class MembershipLookupError(Exception):
    """Raised when org memberships cannot be determined; callers fail closed."""


MembershipLookup = Callable[
    [str], tuple[Organization, ...] | Awaitable[tuple[Organization, ...]]
]


class SupabaseServiceRoleMembershipLookup:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_memberships: int = MAX_MEMBERSHIPS,
    ) -> None:
        self._url = f"{supabase_url.rstrip('/')}/rest/v1/organization_memberships"
        self._service_role_key = service_role_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._max_memberships = max_memberships

    async def __call__(self, user_id: str) -> tuple[Organization, ...]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._url,
                    params={
                        "user_id": f"eq.{user_id}",
                        "select": "organization_id,organizations(id,name)",
                        "limit": str(self._max_memberships + 1),
                    },
                    headers={
                        "apikey": self._service_role_key,
                        "authorization": f"Bearer {self._service_role_key}",
                    },
                )
        except httpx.HTTPError as exc:
            raise MembershipLookupError() from exc

        if response.status_code != 200:
            raise MembershipLookupError()

        try:
            payload = response.json()
        except ValueError as exc:
            raise MembershipLookupError() from exc

        if not isinstance(payload, list) or len(payload) > self._max_memberships:
            raise MembershipLookupError()

        organizations: list[Organization] = []
        for row in payload:
            organizations.append(_parse_organization(row))
        return tuple(organizations)


def _parse_organization(row: object) -> Organization:
    if not isinstance(row, Mapping):
        raise MembershipLookupError()
    embedded = row.get("organizations")
    if not isinstance(embedded, Mapping):
        raise MembershipLookupError()
    org_id = embedded.get("id")
    org_name = embedded.get("name")
    if not isinstance(org_id, str) or not org_id.strip():
        raise MembershipLookupError()
    if not isinstance(org_name, str):
        raise MembershipLookupError()
    return Organization(id=org_id.strip(), name=org_name)
