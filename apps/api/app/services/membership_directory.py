from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx

MAX_MEMBERSHIPS = 200


@dataclass(frozen=True)
class Organization:
    id: str
    name: str
    role: str = "member"
    # Snowflake account locator from the org's persisted connection, if any.
    # Lets callers surface the account before any analysis run.
    account_locator: str | None = None
    connection_status: str | None = None


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
                        "select": (
                            "role,organization_id,organizations"
                            "(id,name,organization_snowflake_connections"
                            "(account,account_locator,status))"
                        ),
                        # Deterministic order: without it PostgREST returns rows
                        # in arbitrary physical order, so the frontend's implicit
                        # active-org fallback (organizations[0]) flips between
                        # refreshes — bouncing users between orgs (and back to
                        # the automated-savings opt-in gate for an un-agreed org).
                        "order": "organization_id.asc",
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
    role = row.get("role")
    role_value = role if isinstance(role, str) and role else "member"
    connection = embedded.get("organization_snowflake_connections")
    account_locator = _extract_account_locator(connection)
    connection_status = _extract_connection_status(connection)
    return Organization(
        id=org_id.strip(),
        name=org_name,
        role=role_value,
        account_locator=account_locator,
        connection_status=connection_status,
    )


def _extract_account_locator(connection: object) -> str | None:
    # PostgREST returns an embedded one-to-one relation as an object (or null),
    # but can return a single-element list depending on relationship detection;
    # handle both so the locator survives either shape.
    if isinstance(connection, Mapping):
        return _account_from_row(connection)
    if isinstance(connection, list):
        for entry in connection:
            account = _account_from_row(entry)
            if account is not None:
                return account
    return None


def _account_from_row(row: object) -> str | None:
    if not isinstance(row, Mapping):
        return None
    account_locator = row.get("account_locator")
    if isinstance(account_locator, str) and account_locator.strip():
        return account_locator.strip()
    # Fall back to the user-entered account identifier for legacy rows that
    # pre-date the account_locator column (populated on re-validation).
    account = row.get("account")
    if isinstance(account, str) and account.strip():
        return account.strip()
    return None


def _extract_connection_status(connection: object) -> str | None:
    if isinstance(connection, Mapping):
        return _connection_status_from_row(connection)
    if isinstance(connection, list):
        for entry in connection:
            status = _connection_status_from_row(entry)
            if status is not None:
                return status
    return None


def _connection_status_from_row(row: object) -> str | None:
    if not isinstance(row, Mapping):
        return None
    status = row.get("status")
    if isinstance(status, str) and status.strip():
        return status.strip()
    return None
