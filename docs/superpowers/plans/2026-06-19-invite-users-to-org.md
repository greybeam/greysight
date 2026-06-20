# Invite Users to the Active Org — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an owner/admin of the currently-selected org invite a user by work email from the dashboard header; new users get a real Supabase invite email, existing users are attached directly, all as `member`.

**Architecture:** A header icon button opens a popover that POSTs to a new FastAPI endpoint. The endpoint authorizes via `require_org_admin`, validates the work email, and orchestrates a `SECURITY DEFINER` Postgres RPC (`add_org_member_by_email`) plus Supabase GoTrue admin calls (invite / generate-link). Membership creation is idempotent. The frontend learns the caller's per-org `role` (already on the backend `Organization`, currently dropped by the session route) to gate button visibility — the real guard is server-side.

**Tech Stack:** FastAPI + httpx (apps/api), Supabase Postgres + GoTrue, Next.js 16 / React 18 / Tremor / Tailwind (apps/web), pytest (api), vitest + testing-library (web).

## Global Constraints

- New-user handling: **Approach A** — real GoTrue invite email creates the account.
- Invitee role: `member` (no role picker).
- Authorization: owners and admins only; server-side `require_org_admin` is the real guard, client role-gating is UX only.
- Cross-org existing user: silent membership insert, **no** email.
- "Already a member": explicit **409** with message `"{email} is already a member."`.
- Success copy (web): `Invited: {email} to {name}`. Popover heading: `Add user to {name} ({accountLocator})` (drop the parens when no locator).
- Email validation reuses the existing work-email blocklist; TS and Python lists are kept in lockstep by a shared fixture at `shared/free-email-domains.json`.
- Endpoint is resource-shaped: `POST /api/organizations/{organization_id}/invitations`.
- RPC is locked down: `SECURITY DEFINER`, `set search_path = ''`, fully-qualified tables, `execute` granted **only** to `service_role` (revoked from `public`/`anon`/`authenticated`).
- Membership insert is idempotent: `ON CONFLICT (organization_id, user_id) DO NOTHING`.
- Service-role key stays backend-only.
- Immutability, small focused files, no secrets in error messages (mirror `org_provisioning.py` neutral errors).

## File Structure

**Create**
- `shared/free-email-domains.json` — canonical free-email-provider blocklist.
- `apps/api/app/services/work_email.py` — Python work-email validator (loads the shared JSON).
- `apps/api/tests/test_work_email.py` — validator + parity tests.
- `supabase/migrations/202606190001_org_member_invitations.sql` — `add_org_member_by_email` RPC.
- `apps/api/app/services/org_invitations.py` — GoTrue + RPC clients, orchestration, startup config.
- `apps/api/tests/test_org_invitations.py` — client + orchestration unit tests.
- `apps/api/app/routes/organizations.py` — the invite endpoint.
- `apps/api/tests/test_organizations_route.py` — endpoint tests.
- `apps/web/src/lib/org-invitations-api.ts` — web invite API client.
- `apps/web/src/lib/org-invitations-api.test.ts` — client tests.
- `apps/web/src/lib/work-email.test.ts` — TS↔fixture parity test.
- `apps/web/src/components/dashboard/invite-user.tsx` — icon button + popover.
- `apps/web/src/components/dashboard/invite-user.test.tsx` — component tests.

**Modify**
- `apps/api/app/services/connect_rate_limit.py` — add `get_invite_limiter()`.
- `apps/api/app/routes/session.py` — add `role` to `SessionOrganization`.
- `apps/api/tests/test_session_route.py` — expect `role`.
- `apps/api/tests/test_supabase_migration.py` — assert the new RPC's shape/grants.
- `apps/api/app/main.py` — configure invitations clients + mount the router.
- `apps/web/src/lib/session-memberships.ts` — add `role` to `MembershipOrganization`.
- `apps/web/src/lib/session-memberships.test.ts` — expect `role`.
- `apps/web/src/lib/account-context.tsx` — add `accessToken` to `AccountChrome`.
- `apps/web/src/components/org/org-shell.tsx` — pass `accessToken` into the provider.
- `apps/web/src/components/dashboard/account-switcher.test.tsx` — add `accessToken` to AccountChrome literals.
- `apps/web/src/components/dashboard/dashboard-header.tsx` — mount `<InviteUser />`.
- `apps/web/src/components/dashboard/dashboard-header.test.tsx` — add `accessToken` to any AccountChrome literal.

**Commands** (run from the stated dir)
- API tests: `cd apps/api && uv run pytest <path> -v` (fallback `python -m pytest` if `uv` unavailable).
- Web tests: `cd apps/web && npm test -- <path>` (vitest).
- Web typecheck: `cd apps/web && npm run typecheck`.

---

### Task 1: Shared free-email fixture + Python work-email validator

**Files:**
- Create: `shared/free-email-domains.json`
- Create: `apps/api/app/services/work_email.py`
- Test: `apps/api/tests/test_work_email.py`
- Create (later-used): `apps/web/src/lib/work-email.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `is_work_email(email: str) -> bool` (Python); canonical JSON at repo-root `shared/free-email-domains.json` (a JSON array of lowercase domains).

- [ ] **Step 1: Create the canonical fixture**

`shared/free-email-domains.json` — exactly the 20 domains currently in `apps/web/src/lib/work-email.ts`:

```json
[
  "gmail.com",
  "googlemail.com",
  "yahoo.com",
  "yahoo.co.uk",
  "outlook.com",
  "hotmail.com",
  "live.com",
  "msn.com",
  "icloud.com",
  "me.com",
  "mac.com",
  "aol.com",
  "proton.me",
  "protonmail.com",
  "gmx.com",
  "mail.com",
  "zoho.com",
  "yandex.com",
  "qq.com",
  "163.com"
]
```

- [ ] **Step 2: Write the failing Python test**

`apps/api/tests/test_work_email.py`:

```python
import json
from pathlib import Path

from app.services.work_email import FREE_EMAIL_DOMAINS, is_work_email

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "shared" / "free-email-domains.json"
)


def test_accepts_work_email() -> None:
    assert is_work_email("kyle@greybeam.ai") is True


def test_rejects_free_provider() -> None:
    assert is_work_email("kyle@gmail.com") is False


def test_rejects_malformed() -> None:
    for bad in ["", "a", "a@", "@b.com", "a@b", "a@.com", "a@b.", "a@b..com"]:
        assert is_work_email(bad) is False


def test_is_case_and_whitespace_insensitive() -> None:
    assert is_work_email("  Kyle@GREYBEAM.ai ") is True
    assert is_work_email("X@GMAIL.COM") is False


def test_python_list_matches_shared_fixture() -> None:
    fixture = set(json.loads(_FIXTURE.read_text()))
    assert FREE_EMAIL_DOMAINS == fixture
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_work_email.py -v`
Expected: FAIL (`ModuleNotFoundError: app.services.work_email`).

- [ ] **Step 4: Implement the validator**

`apps/api/app/services/work_email.py`:

```python
"""Server-side work-email gate. The free-provider list is the shared canonical
fixture so it cannot drift from the web client's copy."""

from __future__ import annotations

import json
import re
from pathlib import Path

_FIXTURE = (
    Path(__file__).resolve().parents[4] / "shared" / "free-email-domains.json"
)

FREE_EMAIL_DOMAINS: frozenset[str] = frozenset(
    json.loads(_FIXTURE.read_text())
)

# Mirrors EMAIL_PATTERN in apps/web/src/lib/work-email.ts: non-empty local part,
# exactly one "@", dotted domain with each label non-empty.
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")


def is_work_email(email: str) -> bool:
    normalized = email.strip().lower()
    if not _EMAIL_PATTERN.fullmatch(normalized):
        return False
    domain = normalized[normalized.index("@") + 1 :]
    return domain not in FREE_EMAIL_DOMAINS
```

- [ ] **Step 5: Run the Python test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_work_email.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Add the TS parity test**

`apps/web/src/lib/work-email.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { FREE_EMAIL_DOMAINS, isWorkEmail } from "./work-email";

describe("isWorkEmail", () => {
  it("accepts a work email and rejects a free provider", () => {
    expect(isWorkEmail("kyle@greybeam.ai")).toBe(true);
    expect(isWorkEmail("kyle@gmail.com")).toBe(false);
  });

  it("stays in lockstep with the shared fixture", () => {
    const fixture = JSON.parse(
      readFileSync(
        resolve(__dirname, "../../../../shared/free-email-domains.json"),
        "utf8",
      ),
    ) as string[];
    expect(new Set(FREE_EMAIL_DOMAINS)).toEqual(new Set(fixture));
  });
});
```

- [ ] **Step 7: Run the TS parity test**

Run: `cd apps/web && npm test -- src/lib/work-email.test.ts`
Expected: PASS. (If the path `../../../../shared/...` does not resolve, adjust to the repo root — `src/lib` is 2 dirs below `apps/web`, which is 2 below the repo root.)

- [ ] **Step 8: Commit**

```bash
git add shared/free-email-domains.json apps/api/app/services/work_email.py apps/api/tests/test_work_email.py apps/web/src/lib/work-email.test.ts
git commit -m "feat: shared work-email fixture + python validator"
```

---

### Task 2: `add_org_member_by_email` migration

**Files:**
- Create: `supabase/migrations/202606190001_org_member_invitations.sql`
- Test: `apps/api/tests/test_supabase_migration.py` (add assertions)

**Interfaces:**
- Consumes: existing `public.organization_memberships`, `auth.users`.
- Produces: Postgres RPC `add_org_member_by_email(p_actor_user_id uuid, p_org_id uuid, p_email text) returns text` returning one of `'unauthorized' | 'invite_needed' | 'pending_resend' | 'already_member' | 'added'`.

- [ ] **Step 1: Write the failing migration assertions**

Append to `apps/api/tests/test_supabase_migration.py`:

```python
def test_migration_defines_invite_rpc_locked_down() -> None:
    sql = read_migration_sql()

    assert "create or replace function add_org_member_by_email" in sql
    assert "security definer" in sql
    assert "set search_path = ''" in sql
    # Idempotent membership insert.
    assert "on conflict (organization_id, user_id) do nothing" in sql
    # Locked-down grants.
    assert (
        "revoke all on function add_org_member_by_email(uuid, uuid, text) "
        "from public" in sql
    )
    assert (
        "revoke all on function add_org_member_by_email(uuid, uuid, text) "
        "from authenticated" in sql
    )
    assert (
        "grant execute on function add_org_member_by_email(uuid, uuid, text) "
        "to service_role" in sql
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py::test_migration_defines_invite_rpc_locked_down -v`
Expected: FAIL (string not found).

- [ ] **Step 3: Write the migration**

`supabase/migrations/202606190001_org_member_invitations.sql`:

```sql
-- Invite/attach a user to an org by email. Returns a status the API maps to an
-- action (send invite, resend link, attach silently) and HTTP code. SECURITY
-- DEFINER so it can read auth.users; search_path='' + fully-qualified names +
-- service_role-only execute keep the privilege surface minimal.
create or replace function add_org_member_by_email(
  p_actor_user_id uuid,
  p_org_id uuid,
  p_email text
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid;
  v_confirmed timestamptz;
  v_is_admin boolean;
begin
  -- Defense in depth behind the API's require_org_admin: the actor must be an
  -- owner/admin of the target org.
  select exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = p_org_id
      and m.user_id = p_actor_user_id
      and m.role in ('owner', 'admin')
  ) into v_is_admin;

  if not v_is_admin then
    return 'unauthorized';
  end if;

  select u.id, u.email_confirmed_at
    into v_user_id, v_confirmed
  from auth.users u
  where lower(u.email) = lower(p_email)
  limit 1;

  if v_user_id is null then
    return 'invite_needed';
  end if;

  if exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = p_org_id
      and m.user_id = v_user_id
  ) then
    if v_confirmed is null then
      return 'pending_resend';
    end if;
    return 'already_member';
  end if;

  insert into public.organization_memberships (organization_id, user_id, role)
  values (p_org_id, v_user_id, 'member')
  on conflict (organization_id, user_id) do nothing;

  return 'added';
end;
$$;

revoke all on function add_org_member_by_email(uuid, uuid, text) from public;
revoke all on function add_org_member_by_email(uuid, uuid, text) from anon;
revoke all on function add_org_member_by_email(uuid, uuid, text) from authenticated;
grant execute on function add_org_member_by_email(uuid, uuid, text) to service_role;
```

- [ ] **Step 4: Run the migration assertions to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py -v`
Expected: PASS (all, including the new test).

> Note: the migration suite is static SQL-text assertions (no live Postgres). The RPC's runtime behavior is covered indirectly by the orchestration tests in Task 3, which stub the RPC's status outputs. `email_confirmed_at` is the GoTrue confirmation column; confirm it exists on the deployed `auth.users` during the manual smoke (final task).

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/202606190001_org_member_invitations.sql apps/api/tests/test_supabase_migration.py
git commit -m "feat: add_org_member_by_email RPC migration"
```

---

### Task 3: Invitations service (GoTrue + RPC clients, orchestration)

**Files:**
- Create: `apps/api/app/services/org_invitations.py`
- Test: `apps/api/tests/test_org_invitations.py`

**Interfaces:**
- Consumes: RPC statuses from Task 2.
- Produces:
  - `class InviteError(RuntimeError)`, `class AlreadyMemberError(InviteError)`, `class UnauthorizedInviteError(InviteError)`, `class InviteProvisioningError(InviteError)`.
  - `class SupabaseMemberRpc` — callable `(actor_user_id, organization_id, email) -> str` (status).
  - `class SupabaseUserInviter` — methods `invite(email: str) -> None`, `resend(email: str) -> None`.
  - `configure_invitations(rpc: SupabaseMemberRpc | None, inviter: SupabaseUserInviter | None) -> None`.
  - `invite_member_to_org(*, actor_user_id: str, organization_id: str, email: str, rpc=None, inviter=None) -> str` — returns the email on success; raises the typed errors above.

- [ ] **Step 1: Write the failing orchestration + client tests**

`apps/api/tests/test_org_invitations.py`:

```python
import httpx
import pytest

from app.services.org_invitations import (
    AlreadyMemberError,
    InviteProvisioningError,
    SupabaseMemberRpc,
    SupabaseUserInviter,
    UnauthorizedInviteError,
    invite_member_to_org,
)


class FakeInviter:
    def __init__(self) -> None:
        self.invited: list[str] = []
        self.resent: list[str] = []

    def invite(self, email: str) -> None:
        self.invited.append(email)

    def resend(self, email: str) -> None:
        self.resent.append(email)


def _rpc(*statuses: str):
    """Return a fake RPC callable yielding the given statuses in order."""
    calls = {"n": 0}

    def call(actor_user_id: str, organization_id: str, email: str) -> str:
        i = min(calls["n"], len(statuses) - 1)
        calls["n"] += 1
        return statuses[i]

    return call


def _invite(rpc, inviter):
    return invite_member_to_org(
        actor_user_id="actor-1",
        organization_id="org-1",
        email="new@acme.com",
        rpc=rpc,
        inviter=inviter,
    )


def test_added_existing_user_sends_no_email() -> None:
    inviter = FakeInviter()
    assert _invite(_rpc("added"), inviter) == "new@acme.com"
    assert inviter.invited == [] and inviter.resent == []


def test_invite_needed_invites_then_reconfirms() -> None:
    inviter = FakeInviter()
    assert _invite(_rpc("invite_needed", "added"), inviter) == "new@acme.com"
    assert inviter.invited == ["new@acme.com"]


def test_pending_resend_resends_link() -> None:
    inviter = FakeInviter()
    assert _invite(_rpc("pending_resend"), inviter) == "new@acme.com"
    assert inviter.resent == ["new@acme.com"]


def test_already_member_raises() -> None:
    with pytest.raises(AlreadyMemberError):
        _invite(_rpc("already_member"), FakeInviter())


def test_unauthorized_raises() -> None:
    with pytest.raises(UnauthorizedInviteError):
        _invite(_rpc("unauthorized"), FakeInviter())


def test_unexpected_status_raises_provisioning_error() -> None:
    with pytest.raises(InviteProvisioningError):
        _invite(_rpc("???"), FakeInviter())


def test_rpc_client_posts_and_returns_status() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(200, json="added")

    rpc = SupabaseMemberRpc(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    assert rpc("actor-1", "org-1", "new@acme.com") == "added"
    assert seen["path"].endswith("/rpc/add_org_member_by_email")
    assert "actor-1" in seen["body"] and "org-1" in seen["body"]


def test_rpc_client_raises_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    rpc = SupabaseMemberRpc(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(InviteProvisioningError):
        rpc("a", "o", "e@x.com")


def test_inviter_invite_hits_invite_endpoint() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": "u1"})

    inviter = SupabaseUserInviter(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    inviter.invite("new@acme.com")
    assert seen["path"].endswith("/auth/v1/invite")


def test_inviter_invite_tolerates_already_registered() -> None:
    # Recoverable TOCTOU: user created between RPC calls. invite() must not raise.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422, json={"error_code": "email_exists", "msg": "already registered"}
        )

    inviter = SupabaseUserInviter(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    inviter.invite("new@acme.com")  # no raise


def test_inviter_resend_hits_generate_link() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"action_link": "https://x"})

    inviter = SupabaseUserInviter(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    inviter.resend("new@acme.com")
    assert seen["path"].endswith("/auth/v1/admin/generate_link")
    assert '"invite"' in seen["body"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_org_invitations.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the service**

`apps/api/app/services/org_invitations.py`:

```python
from __future__ import annotations

import httpx

STATUS_UNAUTHORIZED = "unauthorized"
STATUS_INVITE_NEEDED = "invite_needed"
STATUS_PENDING_RESEND = "pending_resend"
STATUS_ALREADY_MEMBER = "already_member"
STATUS_ADDED = "added"

_TERMINAL_OK = {STATUS_ADDED, STATUS_ALREADY_MEMBER, STATUS_PENDING_RESEND}


class InviteError(RuntimeError):
    """Base class for invite failures."""


class AlreadyMemberError(InviteError):
    """The email is already a member of the org."""


class UnauthorizedInviteError(InviteError):
    """The actor is not an owner/admin of the org."""


class InviteProvisioningError(InviteError):
    """An upstream (RPC or GoTrue) failure; never leaks upstream detail."""


class SupabaseMemberRpc:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = (
            f"{supabase_url.rstrip('/')}/rest/v1/rpc/add_org_member_by_email"
        )
        self._key = service_role_key
        self._timeout = timeout_seconds
        self._transport = transport

    def __call__(
        self, actor_user_id: str, organization_id: str, email: str
    ) -> str:
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(
                    self._url,
                    json={
                        "p_actor_user_id": actor_user_id,
                        "p_org_id": organization_id,
                        "p_email": email,
                    },
                    headers={
                        "apikey": self._key,
                        "authorization": f"Bearer {self._key}",
                        "content-type": "application/json",
                    },
                )
        except httpx.HTTPError:
            raise InviteProvisioningError("Could not send the invite.") from None
        if response.status_code not in (200, 201):
            raise InviteProvisioningError("Could not send the invite.")
        try:
            return str(response.json())
        except ValueError:
            raise InviteProvisioningError("Could not send the invite.") from None


class SupabaseUserInviter:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = supabase_url.rstrip("/")
        self._invite_url = f"{base}/auth/v1/invite"
        self._generate_link_url = f"{base}/auth/v1/admin/generate_link"
        self._key = service_role_key
        self._timeout = timeout_seconds
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._key,
            "authorization": f"Bearer {self._key}",
            "content-type": "application/json",
        }

    def invite(self, email: str) -> None:
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(
                    self._invite_url, json={"email": email}, headers=self._headers()
                )
        except httpx.HTTPError:
            raise InviteProvisioningError("Could not send the invite.") from None
        if response.status_code in (200, 201):
            return
        # Recoverable: the user already exists (created between RPC calls). The
        # caller re-runs the RPC to attach membership, so this is not an error.
        if response.status_code == 422 and _is_user_exists(response):
            return
        raise InviteProvisioningError("Could not send the invite.")

    def resend(self, email: str) -> None:
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(
                    self._generate_link_url,
                    json={"type": "invite", "email": email},
                    headers=self._headers(),
                )
        except httpx.HTTPError:
            raise InviteProvisioningError("Could not send the invite.") from None
        if response.status_code not in (200, 201):
            raise InviteProvisioningError("Could not send the invite.")


def _is_user_exists(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    blob = f"{body.get('error_code') or ''} {body.get('msg') or ''}".lower()
    return "exist" in blob or "registered" in blob


_rpc: SupabaseMemberRpc | None = None
_inviter: SupabaseUserInviter | None = None


def configure_invitations(
    rpc: SupabaseMemberRpc | None, inviter: SupabaseUserInviter | None
) -> None:
    global _rpc, _inviter
    _rpc = rpc
    _inviter = inviter


def invite_member_to_org(
    *,
    actor_user_id: str,
    organization_id: str,
    email: str,
    rpc: object | None = None,
    inviter: object | None = None,
) -> str:
    selected_rpc = rpc if rpc is not None else _rpc
    selected_inviter = inviter if inviter is not None else _inviter
    if selected_rpc is None or selected_inviter is None:
        raise InviteProvisioningError("Invitations are not configured.")

    status = selected_rpc(actor_user_id, organization_id, email)
    if status == STATUS_ALREADY_MEMBER:
        raise AlreadyMemberError(f"{email} is already a member.")
    if status == STATUS_UNAUTHORIZED:
        raise UnauthorizedInviteError("Organization admin access required")
    if status == STATUS_ADDED:
        return email
    if status == STATUS_PENDING_RESEND:
        selected_inviter.resend(email)
        return email
    if status == STATUS_INVITE_NEEDED:
        selected_inviter.invite(email)
        status2 = selected_rpc(actor_user_id, organization_id, email)
        if status2 not in _TERMINAL_OK:
            raise InviteProvisioningError("Could not send the invite.")
        return email
    raise InviteProvisioningError("Could not send the invite.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_org_invitations.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/org_invitations.py apps/api/tests/test_org_invitations.py
git commit -m "feat: invitations service (gotrue + rpc orchestration)"
```

---

### Task 4: Invite endpoint + rate limiter + main wiring

**Files:**
- Modify: `apps/api/app/services/connect_rate_limit.py` (add `get_invite_limiter()`)
- Create: `apps/api/app/routes/organizations.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_organizations_route.py`

**Interfaces:**
- Consumes: `invite_member_to_org`, the invite errors (Task 3); `require_org_admin`, `require_auth_context`, `AuthContext` (auth.py); `Organization` (membership_directory); `get_invite_limiter`, `ConnectInFlightError`, `ConnectRateLimitedError` (connect_rate_limit).
- Produces: `POST /api/organizations/{organization_id}/invitations` returning `200 {"email": ...}`; the route-level seam `organizations.invite_member_to_org(**kwargs)` for monkeypatching.

- [ ] **Step 1: Add the invite limiter (no new test needed — covered by route tests)**

Append to `apps/api/app/services/connect_rate_limit.py`:

```python
_invite_limiter: InMemoryConnectLimiter | None = None

DEFAULT_INVITE_MAX_ATTEMPTS = 20


def get_invite_limiter() -> InMemoryConnectLimiter:
    """Separate limiter so invites and Snowflake connects don't share in-flight
    state for the same user."""
    global _invite_limiter
    if _invite_limiter is None:
        _invite_limiter = InMemoryConnectLimiter(
            max_attempts=DEFAULT_INVITE_MAX_ATTEMPTS,
            window_seconds=DEFAULT_WINDOW_SECONDS,
        )
    return _invite_limiter
```

- [ ] **Step 2: Write the failing route tests**

`apps/api/tests/test_organizations_route.py`:

```python
import pytest
from fastapi.testclient import TestClient

import app.services.connect_rate_limit as connect_rate_limit
from app.auth import AuthContext, require_auth_context
from app.main import app
from app.routes import organizations
from app.services.connect_rate_limit import InMemoryConnectLimiter
from app.services.membership_directory import Organization
from app.services.org_invitations import AlreadyMemberError, InviteProvisioningError


@pytest.fixture(autouse=True)
def fresh_invite_limiter(monkeypatch):
    limiter = InMemoryConnectLimiter(max_attempts=100, window_seconds=300)
    monkeypatch.setattr(connect_rate_limit, "_invite_limiter", limiter)
    return limiter


def _admin_ctx() -> AuthContext:
    return AuthContext(
        user_id="actor-1",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="owner"),),
    )


def _member_ctx() -> AuthContext:
    return AuthContext(
        user_id="actor-2",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="member"),),
    )


def test_invite_succeeds_for_admin(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx
    seen = {}
    monkeypatch.setattr(
        organizations,
        "invite_member_to_org",
        lambda **kw: seen.update(kw) or kw["email"],
    )
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"email": "new@acme.com"}
    assert seen["actor_user_id"] == "actor-1"
    assert seen["organization_id"] == "org-1"


def test_invite_forbidden_for_member(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _member_ctx
    monkeypatch.setattr(
        organizations, "invite_member_to_org", lambda **kw: kw["email"]
    )
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 403


def test_invite_rejects_free_email(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx
    calls = []
    monkeypatch.setattr(
        organizations, "invite_member_to_org", lambda **kw: calls.append(kw)
    )
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "x@gmail.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 422
    assert calls == []


def test_invite_already_member_returns_409(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx

    def _raise(**kw):
        raise AlreadyMemberError("new@acme.com is already a member.")

    monkeypatch.setattr(organizations, "invite_member_to_org", _raise)
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 409
    assert "already a member" in response.json()["detail"]


def test_invite_upstream_failure_returns_502(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx

    def _raise(**kw):
        raise InviteProvisioningError("Could not send the invite.")

    monkeypatch.setattr(organizations, "invite_member_to_org", _raise)
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 502


def test_invite_rate_limited_returns_429(monkeypatch) -> None:
    monkeypatch.setattr(
        connect_rate_limit,
        "_invite_limiter",
        InMemoryConnectLimiter(max_attempts=1, window_seconds=300),
    )
    app.dependency_overrides[require_auth_context] = _admin_ctx
    monkeypatch.setattr(
        organizations, "invite_member_to_org", lambda **kw: kw["email"]
    )
    client = TestClient(app)
    statuses = [
        client.post(
            "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
        ).status_code
        for _ in range(2)
    ]
    app.dependency_overrides.clear()
    assert statuses[0] == 200
    assert statuses[1] == 429
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_organizations_route.py -v`
Expected: FAIL (`ModuleNotFoundError: app.routes.organizations`).

- [ ] **Step 4: Implement the route**

`apps/api/app/routes/organizations.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import AuthContext, require_auth_context, require_org_admin

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


class InviteRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)


class InviteResponse(BaseModel):
    email: str


def invite_member_to_org(**kwargs: object) -> str:
    """Indirection seam so tests can stub the service-role orchestration."""
    from app.services.org_invitations import invite_member_to_org as impl

    return impl(**kwargs)  # type: ignore[arg-type]


@router.post("/{organization_id}/invitations", response_model=InviteResponse)
def invite_user(
    organization_id: str,
    request: InviteRequest,
    auth_context: AuthContext = Depends(require_auth_context),
) -> InviteResponse:
    if not auth_context.auth_required or not auth_context.user_id:
        raise HTTPException(status_code=403, detail="Authentication required")
    require_org_admin(auth_context, organization_id)

    from app.services.work_email import is_work_email

    email = request.email.strip()
    if not is_work_email(email):
        raise HTTPException(status_code=422, detail="Please use your work email.")

    from app.services.connect_rate_limit import (
        ConnectInFlightError,
        ConnectRateLimitedError,
        get_invite_limiter,
    )
    from app.services.org_invitations import (
        AlreadyMemberError,
        InviteProvisioningError,
        UnauthorizedInviteError,
    )

    try:
        with get_invite_limiter().guard(auth_context.user_id):
            invite_member_to_org(
                actor_user_id=auth_context.user_id,
                organization_id=organization_id,
                email=email,
            )
    except ConnectInFlightError:
        raise HTTPException(
            status_code=409, detail="An invite is already in progress."
        ) from None
    except ConnectRateLimitedError:
        raise HTTPException(
            status_code=429, detail="Too many invites. Try again shortly."
        ) from None
    except AlreadyMemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except UnauthorizedInviteError:
        raise HTTPException(
            status_code=403, detail="Organization admin access required"
        ) from None
    except InviteProvisioningError:
        raise HTTPException(
            status_code=502, detail="Could not send the invite."
        ) from None

    return InviteResponse(email=email)
```

- [ ] **Step 5: Wire startup config + mount the router in `main.py`**

In `apps/api/app/main.py`, add the import near the other service imports:

```python
from app.services.org_invitations import (
    SupabaseMemberRpc,
    SupabaseUserInviter,
    configure_invitations,
)
from app.routes.organizations import router as organizations_router
```

Add a configure helper next to `_configure_org_disconnector`:

```python
def _configure_invitations(settings: Settings) -> None:
    if settings.supabase_url.strip() and settings.supabase_service_role_key.strip():
        configure_invitations(
            SupabaseMemberRpc(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            ),
            SupabaseUserInviter(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            ),
        )
    else:
        configure_invitations(None, None)
```

Call it in the startup block (after `_configure_org_disconnector(settings)`):

```python
_configure_invitations(settings)
```

Mount the router (after `app.include_router(onboarding_router)`):

```python
app.include_router(organizations_router)
```

- [ ] **Step 6: Run the route tests + full api suite**

Run: `cd apps/api && uv run pytest tests/test_organizations_route.py -v`
Expected: PASS (all).
Run: `cd apps/api && uv run pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/connect_rate_limit.py apps/api/app/routes/organizations.py apps/api/app/main.py apps/api/tests/test_organizations_route.py
git commit -m "feat: POST /api/organizations/{id}/invitations endpoint"
```

---

### Task 5: Expose `role` from the session memberships endpoint

**Files:**
- Modify: `apps/api/app/routes/session.py`
- Test: `apps/api/tests/test_session_route.py`

**Interfaces:**
- Consumes: `Organization.role` (already present).
- Produces: `/api/session/memberships` items now include `"role"`.

- [ ] **Step 1: Update the failing test**

In `apps/api/tests/test_session_route.py`, change `test_returns_caller_memberships`'s lookup to set a role and update the expected payload:

```python
    async def lookup(user_id: str) -> tuple[Organization, ...]:
        assert user_id == "user_123"
        return (
            Organization(
                id="org-1", name="Acme", role="owner", account_locator="IJ42635"
            ),
        )
```

```python
    assert response.json() == {
        "organizations": [
            {
                "id": "org-1",
                "name": "Acme",
                "role": "owner",
                "account_locator": "IJ42635",
            }
        ]
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_session_route.py::test_returns_caller_memberships -v`
Expected: FAIL (response missing `role`).

- [ ] **Step 3: Add `role` to the response model + mapping**

In `apps/api/app/routes/session.py`:

```python
class SessionOrganization(BaseModel):
    id: str
    name: str
    role: str = "member"
    account_locator: str | None = None
```

```python
            SessionOrganization(
                id=org.id,
                name=org.name,
                role=org.role,
                account_locator=org.account_locator,
            )
```

- [ ] **Step 4: Run the session tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_session_route.py -v`
Expected: PASS (all 3).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routes/session.py apps/api/tests/test_session_route.py
git commit -m "feat: include role in session memberships response"
```

---

### Task 6: Parse `role` on the web membership type

**Files:**
- Modify: `apps/web/src/lib/session-memberships.ts`
- Test: `apps/web/src/lib/session-memberships.test.ts`

**Interfaces:**
- Consumes: the `role` field from Task 5.
- Produces: `MembershipOrganization.role: "owner" | "admin" | "member"` (default `"member"` when absent/invalid).

- [ ] **Step 1: Update the failing tests**

In `apps/web/src/lib/session-memberships.test.ts`, update every expected object to include `role`, defaulting to `"member"`, and add one explicit role test. Examples — the first test's expectation becomes:

```ts
    expect(organizations).toEqual([
      { id: "org-1", name: "Acme", role: "member", accountLocator: null },
    ]);
```

The "parses the account locator when present" expectation becomes:

```ts
    expect(organizations).toEqual([
      { id: "org-1", name: "Acme", role: "member", accountLocator: "IJ42635" },
    ]);
```

The " org-1 " trim test expectation becomes:

```ts
    expect(organizations).toEqual([
      { id: "org-1", name: " Acme ", role: "member", accountLocator: null },
    ]);
```

Add a new test:

```ts
  it("parses an explicit role", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          organizations: [{ id: "org-1", name: "Acme", role: "owner" }],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    const organizations = await fetchSessionMemberships("access-token");
    expect(organizations[0].role).toBe("owner");
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/web && npm test -- src/lib/session-memberships.test.ts`
Expected: FAIL (objects lack `role`).

- [ ] **Step 3: Add the type + parsing**

In `apps/web/src/lib/session-memberships.ts`:

```ts
export type OrgRole = "owner" | "admin" | "member";

export type MembershipOrganization = {
  id: string;
  name: string;
  role: OrgRole;
  accountLocator: string | null;
};
```

Inside `parseOrganizations`'s `.map`, after computing `accountLocator`, add:

```ts
    const rawRole = (item as { role?: unknown }).role;
    const role: OrgRole =
      rawRole === "owner" || rawRole === "admin" ? rawRole : "member";
    return { id, name: entry.name, role, accountLocator };
```

(Update the `entry` cast to also allow `role?: unknown` if needed for TS.)

- [ ] **Step 4: Run to verify pass + typecheck**

Run: `cd apps/web && npm test -- src/lib/session-memberships.test.ts`
Expected: PASS.
Run: `cd apps/web && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/session-memberships.ts apps/web/src/lib/session-memberships.test.ts
git commit -m "feat: parse per-org role on the web membership type"
```

---

### Task 7: Add `accessToken` to AccountChrome context

**Files:**
- Modify: `apps/web/src/lib/account-context.tsx`
- Modify: `apps/web/src/components/org/org-shell.tsx`
- Modify: `apps/web/src/components/dashboard/account-switcher.test.tsx`
- Modify: `apps/web/src/components/dashboard/dashboard-header.test.tsx` (only if it constructs an AccountChrome literal)

**Interfaces:**
- Consumes: `accessToken` already held in `org-shell.tsx`.
- Produces: `AccountChrome.accessToken: string | null`.

- [ ] **Step 1: Extend the type**

In `apps/web/src/lib/account-context.tsx`, add to the `AccountChrome` type:

```ts
  // Bearer token for authenticated calls the header makes (e.g. inviting users).
  accessToken: string | null;
```

- [ ] **Step 2: Provide it from org-shell**

In `apps/web/src/components/org/org-shell.tsx`, add `accessToken` to the `AccountChromeProvider` value object:

```tsx
        accessToken,
```

(`accessToken` is already in scope at line ~75.)

- [ ] **Step 3: Fix existing AccountChrome literals in tests**

In `apps/web/src/components/dashboard/account-switcher.test.tsx`, add `accessToken: null,` to BOTH places that construct a full `AccountChrome` (the `renderWith` default value and the inline literal in "renders nothing when organizations is empty"). Check `dashboard-header.test.tsx` for any `AccountChrome` literal and add `accessToken: null,` there too.

- [ ] **Step 4: Typecheck + run affected tests**

Run: `cd apps/web && npm run typecheck`
Expected: PASS (no missing-property errors).
Run: `cd apps/web && npm test -- src/components/dashboard/account-switcher.test.tsx src/components/org/org-shell.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/account-context.tsx apps/web/src/components/org/org-shell.tsx apps/web/src/components/dashboard/account-switcher.test.tsx apps/web/src/components/dashboard/dashboard-header.test.tsx
git commit -m "feat: expose accessToken on AccountChrome context"
```

---

### Task 8: Web invite API client

**Files:**
- Create: `apps/web/src/lib/org-invitations-api.ts`
- Test: `apps/web/src/lib/org-invitations-api.test.ts`

**Interfaces:**
- Consumes: `resolveApiUrl` (api-client.ts).
- Produces: `inviteUser(input: InviteUserInput, options?: { accessToken?: string | null }): Promise<string>` returning the invited email; throws `InviteValidationError` (422), `InviteConflictError` (409), or generic `Error`.

- [ ] **Step 1: Write the failing test**

`apps/web/src/lib/org-invitations-api.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  inviteUser,
  InviteConflictError,
  InviteValidationError,
} from "./org-invitations-api";

afterEach(() => vi.restoreAllMocks());

describe("inviteUser", () => {
  it("posts the email with the bearer token and returns it", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ email: "new@acme.com" }), { status: 200 }),
      );

    const email = await inviteUser(
      { organizationId: "org-1", email: "new@acme.com" },
      { accessToken: "tok" },
    );

    expect(email).toBe("new@acme.com");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/organizations/org-1/invitations");
    expect(JSON.parse(String(init?.body))).toEqual({ email: "new@acme.com" });
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer tok");
  });

  it("throws InviteValidationError on 422", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Please use your work email." }), {
        status: 422,
      }),
    );
    await expect(
      inviteUser({ organizationId: "org-1", email: "x@gmail.com" }, {}),
    ).rejects.toBeInstanceOf(InviteValidationError);
  });

  it("throws InviteConflictError on 409", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "new@acme.com is already a member." }),
        { status: 409 },
      ),
    );
    await expect(
      inviteUser({ organizationId: "org-1", email: "new@acme.com" }, {}),
    ).rejects.toBeInstanceOf(InviteConflictError);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/web && npm test -- src/lib/org-invitations-api.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the client**

`apps/web/src/lib/org-invitations-api.ts`:

```ts
import resolveApiUrl from "./api-client";

export interface InviteUserInput {
  organizationId: string;
  email: string;
}

interface InviteOptions {
  accessToken?: string | null;
}

export class InviteValidationError extends Error {}
export class InviteConflictError extends Error {}

export async function inviteUser(
  input: InviteUserInput,
  options: InviteOptions = {},
): Promise<string> {
  const headers = new Headers({ "content-type": "application/json" });
  const accessToken = options.accessToken?.trim();
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  const response = await fetch(
    resolveApiUrl(
      `/api/organizations/${encodeURIComponent(input.organizationId)}/invitations`,
    ),
    {
      method: "POST",
      headers,
      cache: "no-store",
      body: JSON.stringify({ email: input.email }),
    },
  );

  if (response.status === 200) {
    const payload = (await response.json()) as { email: string };
    return payload.email;
  }

  const detail = await safeDetail(response);
  if (response.status === 422) throw new InviteValidationError(detail);
  if (response.status === 409) throw new InviteConflictError(detail);
  throw new Error(detail || `Invite failed with ${response.status}`);
}

async function safeDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : "";
  } catch {
    return "";
  }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/web && npm test -- src/lib/org-invitations-api.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/org-invitations-api.ts apps/web/src/lib/org-invitations-api.test.ts
git commit -m "feat: web invite API client"
```

---

### Task 9: InviteUser popover + header mount

**Files:**
- Create: `apps/web/src/components/dashboard/invite-user.tsx`
- Test: `apps/web/src/components/dashboard/invite-user.test.tsx`
- Modify: `apps/web/src/components/dashboard/dashboard-header.tsx`

**Interfaces:**
- Consumes: `useAccountChrome()` (`organizations`, `activeOrganizationId`, `accessToken`), `isWorkEmail` (work-email.ts), `inviteUser` + `InviteConflictError` + `InviteValidationError` (org-invitations-api.ts).
- Produces: `<InviteUser />` default export.

- [ ] **Step 1: Write the failing component tests**

`apps/web/src/components/dashboard/invite-user.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider, type AccountChrome } from "../../lib/account-context";
import * as api from "../../lib/org-invitations-api";
import InviteUser from "./invite-user";

function renderWith(overrides: Partial<AccountChrome>) {
  const value: AccountChrome = {
    email: "user@example.com",
    onSignOut: vi.fn(),
    signOutError: null,
    organizations: [
      { id: "org-1", name: "Acme", role: "owner", accountLocator: "AAA-111" },
    ],
    activeOrganizationId: "org-1",
    setActiveOrganization: vi.fn(),
    openAddAccount: vi.fn(),
    accessToken: "tok",
    ...overrides,
  };
  render(
    <AccountChromeProvider value={value}>
      <InviteUser />
    </AccountChromeProvider>,
  );
  return value;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InviteUser", () => {
  it("renders nothing for a member", () => {
    const { container } = render(
      <AccountChromeProvider
        value={{
          email: "u@e.com",
          onSignOut: vi.fn(),
          signOutError: null,
          organizations: [
            { id: "org-1", name: "Acme", role: "member", accountLocator: null },
          ],
          activeOrganizationId: "org-1",
          setActiveOrganization: vi.fn(),
          openAddAccount: vi.fn(),
          accessToken: "tok",
        }}
      >
        <InviteUser />
      </AccountChromeProvider>,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the org name and locator in the popover heading", () => {
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    expect(screen.getByText(/Add user to Acme \(AAA-111\)/)).toBeInTheDocument();
  });

  it("rejects a non-work email without calling the API", () => {
    const spy = vi.spyOn(api, "inviteUser");
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    fireEvent.change(screen.getByPlaceholderText(/work-email/i), {
      target: { value: "x@gmail.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));
    expect(screen.getByText("Please use your work email.")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("shows success after a successful invite", async () => {
    vi.spyOn(api, "inviteUser").mockResolvedValue("new@acme.com");
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    fireEvent.change(screen.getByPlaceholderText(/work-email/i), {
      target: { value: "new@acme.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));
    await waitFor(() =>
      expect(
        screen.getByText("Invited: new@acme.com to Acme"),
      ).toBeInTheDocument(),
    );
  });

  it("shows the already-a-member message on conflict", async () => {
    vi.spyOn(api, "inviteUser").mockRejectedValue(
      new api.InviteConflictError("new@acme.com is already a member."),
    );
    renderWith({});
    fireEvent.click(screen.getByRole("button", { name: /invite/i }));
    fireEvent.change(screen.getByPlaceholderText(/work-email/i), {
      target: { value: "new@acme.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^invite$/i }));
    await waitFor(() =>
      expect(
        screen.getByText("new@acme.com is already a member."),
      ).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/web && npm test -- src/components/dashboard/invite-user.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the component**

`apps/web/src/components/dashboard/invite-user.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";

import { useAccountChrome } from "../../lib/account-context";
import {
  inviteUser,
  InviteConflictError,
  InviteValidationError,
} from "../../lib/org-invitations-api";
import { isWorkEmail } from "../../lib/work-email";

const WORK_EMAIL_ERROR = "Please use your work email.";
const GENERIC_ERROR = "Something went wrong. Please try again.";

export default function InviteUser() {
  const account = useAccountChrome();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  if (!account) return null;
  const active =
    account.organizations.find((o) => o.id === account.activeOrganizationId) ??
    account.organizations[0];
  if (!active || (active.role !== "owner" && active.role !== "admin")) {
    return null;
  }

  const heading = active.accountLocator
    ? `Add user to ${active.name} (${active.accountLocator})`
    : `Add user to ${active.name}`;

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    const trimmed = email.trim();
    if (!isWorkEmail(trimmed)) {
      setError(WORK_EMAIL_ERROR);
      return;
    }
    setPending(true);
    try {
      const invited = await inviteUser(
        { organizationId: active.id, email: trimmed },
        { accessToken: account.accessToken },
      );
      setSuccess(`Invited: ${invited} to ${active.name}`);
      setEmail("");
    } catch (err: unknown) {
      if (err instanceof InviteConflictError || err instanceof InviteValidationError) {
        setError(err.message || GENERIC_ERROR);
      } else {
        setError(GENERIC_ERROR);
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Invite user"
        className="flex h-9 w-9 items-center justify-center rounded-md border border-hairline text-slate-300 hover:bg-white/5"
        onClick={() => setOpen((v) => !v)}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-5 w-5"
        >
          <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M19 8v6M22 11h-6" />
        </svg>
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label={heading}
          className="absolute right-0 z-50 mt-2 w-80 rounded-md border border-hairline bg-surface p-3 shadow-lg"
        >
          <p className="mb-2 text-sm font-medium text-slate-200">{heading}</p>
          <form className="flex gap-2" onSubmit={submit}>
            <input
              autoComplete="email"
              type="email"
              required
              disabled={pending}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@work-email.com"
              className="flex-1 rounded-md border border-slate-600 bg-canvas px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-chart-purple focus:outline-none focus:ring-1 focus:ring-chart-purple"
            />
            <button
              type="submit"
              disabled={pending}
              className="shrink-0 rounded-md bg-chart-purple px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            >
              {pending ? "Inviting" : "Invite"}
            </button>
          </form>
          {error ? (
            <p className="mt-2 text-sm font-medium text-red-400" role="alert">
              {error}
            </p>
          ) : null}
          {success ? (
            <p className="mt-2 text-sm font-medium text-emerald-400" role="status">
              {success}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Mount it in the header**

In `apps/web/src/components/dashboard/dashboard-header.tsx`, add the import:

```tsx
import InviteUser from "./invite-user";
```

In the right cluster `<div className="flex flex-wrap items-center justify-end gap-3">`, add `<InviteUser />` immediately before the "Run analysis" `<button>`:

```tsx
          <InviteUser />
          <button
            aria-busy={running}
```

- [ ] **Step 5: Run the component tests + header tests + typecheck**

Run: `cd apps/web && npm test -- src/components/dashboard/invite-user.test.tsx src/components/dashboard/dashboard-header.test.tsx`
Expected: PASS.
Run: `cd apps/web && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/dashboard/invite-user.tsx apps/web/src/components/dashboard/invite-user.test.tsx apps/web/src/components/dashboard/dashboard-header.tsx
git commit -m "feat: invite-user popover in dashboard header"
```

---

### Task 10: Full-suite verification + manual smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the full API suite**

Run: `cd apps/api && uv run pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 2: Run the full web suite + lint + typecheck**

Run: `cd apps/web && npm test`
Expected: PASS.
Run: `cd apps/web && npm run lint && npm run typecheck`
Expected: PASS.

- [ ] **Step 3: Manual smoke (against deployed Supabase) — record results**

Verify the two items the static tests can't cover:
1. `auth.users.email_confirmed_at` exists on the deployed GoTrue version (else adjust the migration's `select`).
2. `POST /auth/v1/admin/generate_link` with `{"type":"invite", ...}` returns 200 for an existing unconfirmed user (the resend path). If the deployed version differs, adjust `SupabaseUserInviter.resend`.

Smoke the happy path: as an org owner, invite a brand-new work email → invitee receives an email and, after accepting, appears in the org; invite an existing user from another org → added silently; invite an existing member → 409 "already a member".

- [ ] **Step 4: Final commit (if any smoke fixes were needed)**

```bash
git add -A && git commit -m "fix: invite flow adjustments from smoke test"
```

---

## Self-Review

**Spec coverage:**
- UI popover + icon + role gating → Task 9. ✅
- Heading/success/error copy → Task 9 (matches Global Constraints). ✅
- `role` to frontend (session route + web type + context) → Tasks 5, 6, 7. ✅
- `accessToken` to context → Task 7. ✅
- Web API client → Task 8. ✅
- Endpoint + authz + work-email + rate-limit + error mapping → Tasks 1, 4. ✅
- RPC migration (locked-down, idempotent, search_path='') → Task 2. ✅
- GoTrue invite/generateLink orchestration + idempotency + recoverable "exists" → Task 3. ✅
- Shared work-email fixture parity (TS + Python) → Task 1. ✅
- 409 vs 200 contract → Tasks 3, 4, 9. ✅
- Implementation-time verifications (confirmation column, resend primitive) → Task 10. ✅

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step has full code. ✅

**Type consistency:** RPC statuses (`unauthorized`/`invite_needed`/`pending_resend`/`already_member`/`added`) match between Task 2 SQL, Task 3 constants/tests. `invite_member_to_org(actor_user_id, organization_id, email)` signature matches between Task 3 (def), Task 4 (route call + seam), and tests. `MembershipOrganization.role` (`owner|admin|member`) matches Tasks 5/6/9. `AccountChrome.accessToken` matches Tasks 7/9. Endpoint path `/api/organizations/{id}/invitations` matches Tasks 4/8. ✅
