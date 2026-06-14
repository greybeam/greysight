# Magic-Link Authentication (Spec A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a usable, secure passcode magic-link login: email → 6-digit code → Supabase session → token attached to API calls, with the API enforcing auth and live-checked org membership.

**Architecture:** Supabase Auth issues the session (passcode OTP, no link). The FastAPI API is the trust boundary: it authenticates the bearer token via `GET /auth/v1/user`, then reads `organization_memberships` **live** via the Supabase service-role REST API on every request (immediate revocation, single source of truth = the table). The frontend reads its memberships from a new `GET /api/session/memberships` endpoint instead of the JWT. A signed-in user with no org sees an interim screen; org creation is Spec B.

**Tech Stack:** Next.js 16 + React 18 + Vitest/Testing Library (web); FastAPI + httpx + pytest (api); Supabase Auth + Postgres/RLS. No new runtime deps.

**Reference spec:** `docs/superpowers/specs/2026-06-13-magic-link-auth-design.md`

---

## File Structure

**Backend (`apps/api/`)**
- `app/services/membership_directory.py` — **NEW**. Service-role REST lookup of a user's orgs (`Organization{id,name}`, `MembershipLookup` type, `SupabaseServiceRoleMembershipLookup`, `MembershipLookupError`). FastAPI-free.
- `app/config.py` — **MODIFY**. Add `supabase_service_role_key` setting.
- `app/auth.py` — **MODIFY**. `AuthContext.organizations`; live membership lookup in `validate_supabase_session` (fail-closed); `membership_lookup` global + `configure_membership_lookup`; drop JWT-claim membership extraction.
- `app/routes/session.py` — **NEW**. `GET /api/session/memberships` → `{ organizations: [{id,name}] }`.
- `app/main.py` — **MODIFY**. Configure the lookup, fail-closed startup check, mount the session router.
- Tests: `tests/test_membership_directory.py` (new), `tests/test_config.py`, `tests/test_auth.py`, `tests/test_session_route.py` (new), `tests/test_snowflake_validation.py` (mock-compat touch-up only).

**Frontend (`apps/web/`)**
- `src/lib/supabase-client.ts` — **MODIFY**. Add `verifyOtp`; passcode-mode `signInWithOtp` (drop `emailRedirectTo`).
- `src/lib/session-memberships.ts` — **NEW**. `fetchSessionMemberships(accessToken)` → `MembershipOrganization[]`.
- `src/components/auth/login-form.tsx` — **MODIFY**. Two-step email → code form.
- `src/components/org/org-shell.tsx` — **MODIFY**. API-driven memberships (loading/error/resolved), interim no-org screen, sign-out; remove dead create-org form.
- Tests: colocated `*.test.ts(x)` for each.

**Docs/config**
- `.env.example`, `docs/` — **MODIFY**. Web/API var split, deployment checklist, first-user bootstrap.

---

## Task 1: Add `supabase_service_role_key` setting + fail-closed startup check

**Files:**
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_config.py`, `apps/api/tests/test_auth.py`

- [ ] **Step 1: Write the failing config test**

Add to `apps/api/tests/test_config.py`:

```python
def test_supabase_service_role_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")

    settings = Settings()

    assert settings.supabase_service_role_key == "service-role-key"


def test_supabase_service_role_key_defaults_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    settings = Settings()

    assert settings.supabase_service_role_key == ""
```

- [ ] **Step 2: Run it to confirm failure**

Run: `cd apps/api && uv run pytest tests/test_config.py -k service_role -v`
Expected: FAIL — `Settings` has no attribute `supabase_service_role_key`.

- [ ] **Step 3: Add the setting**

In `apps/api/app/config.py`, after the `supabase_anon_key` field (line ~18):

```python
    supabase_service_role_key: str = Field(
        default="", validation_alias=AliasChoices("SUPABASE_SERVICE_ROLE_KEY")
    )
```

- [ ] **Step 4: Run it to confirm pass**

Run: `cd apps/api && uv run pytest tests/test_config.py -k service_role -v`
Expected: PASS.

- [ ] **Step 5: Write the failing startup-check test**

Add to `apps/api/tests/test_auth.py` (it already imports from `app.main`):

```python
def test_startup_requires_service_role_key_when_auth_required() -> None:
    from app.main import require_membership_lookup_when_auth_required

    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        require_membership_lookup_when_auth_required(Settings(auth_required=True))


def test_startup_allows_service_role_key_present() -> None:
    from app.main import require_membership_lookup_when_auth_required

    require_membership_lookup_when_auth_required(
        Settings(auth_required=True, supabase_service_role_key="service-role-key")
    )
```

- [ ] **Step 6: Run it to confirm failure**

Run: `cd apps/api && uv run pytest tests/test_auth.py -k startup_requires -v`
Expected: FAIL — `require_membership_lookup_when_auth_required` does not exist.

- [ ] **Step 7: Implement the startup check**

In `apps/api/app/main.py`, add after `warn_when_auth_required_without_verifier` (line ~22) and call it at module load after the existing `warn_...` call:

```python
def require_membership_lookup_when_auth_required(settings: Settings) -> None:
    if settings.auth_required and not settings.supabase_service_role_key.strip():
        raise RuntimeError(
            "AUTH_REQUIRED=true requires SUPABASE_SERVICE_ROLE_KEY for live "
            "organization membership lookups."
        )
```

And after `warn_when_auth_required_without_verifier(settings)` (line ~25):

```python
require_membership_lookup_when_auth_required(settings)
```

- [ ] **Step 8: Run it to confirm pass**

Run: `cd apps/api && uv run pytest tests/test_auth.py -k startup -v && uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/config.py apps/api/app/main.py apps/api/tests/test_config.py apps/api/tests/test_auth.py
git commit -m "feat(api): add supabase_service_role_key setting and fail-closed startup check"
```

---

## Task 2: Membership directory service (live service-role lookup)

**Files:**
- Create: `apps/api/app/services/membership_directory.py`
- Test: `apps/api/tests/test_membership_directory.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_membership_directory.py`:

```python
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
                {"organization_id": "org-1", "organizations": {"id": "org-1", "name": "Acme"}},
                {"organization_id": "org-2", "organizations": {"id": "org-2", "name": "Beta"}},
            ],
        )

    orgs = anyio.run(_lookup(handler), "user-123")

    assert orgs == (
        Organization(id="org-1", name="Acme"),
        Organization(id="org-2", name="Beta"),
    )
    assert requests[0].url.params["user_id"] == "eq.user-123"
    assert "organizations(id,name)" in requests[0].url.params["select"]
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
        {"organization_id": f"org-{i}", "organizations": {"id": f"org-{i}", "name": f"O{i}"}}
        for i in range(201)
    ]
    with pytest.raises(MembershipLookupError):
        anyio.run(_lookup(lambda _r: httpx.Response(200, json=rows)), "user-123")


def test_malformed_row_raises_lookup_error() -> None:
    rows = [{"organization_id": "org-1", "organizations": {"id": "", "name": "Acme"}}]
    with pytest.raises(MembershipLookupError):
        anyio.run(_lookup(lambda _r: httpx.Response(200, json=rows)), "user-123")
```

- [ ] **Step 2: Run it to confirm failure**

Run: `cd apps/api && uv run pytest tests/test_membership_directory.py -v`
Expected: FAIL — module `app.services.membership_directory` does not exist.

- [ ] **Step 3: Implement the service**

Create `apps/api/app/services/membership_directory.py`:

```python
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
```

- [ ] **Step 4: Run it to confirm pass**

Run: `cd apps/api && uv run pytest tests/test_membership_directory.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/membership_directory.py apps/api/tests/test_membership_directory.py
git commit -m "feat(api): add service-role organization membership directory"
```

---

## Task 3: Wire live membership lookup into auth (immediate revocation)

**Files:**
- Modify: `apps/api/app/auth.py`
- Test: `apps/api/tests/test_auth.py`

- [ ] **Step 1: Replace the claim-based membership tests with lookup tests**

In `apps/api/tests/test_auth.py`: **delete** `test_supabase_validation_derives_memberships_from_verified_claims` and `test_supabase_validation_ignores_malformed_membership_items` (they test the removed JWT-claim path). Add:

```python
def test_validation_populates_memberships_from_live_lookup(monkeypatch) -> None:
    from app.services.membership_directory import Organization

    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    async def lookup(user_id: str) -> tuple[Organization, ...]:
        assert user_id == "user_123"
        return (Organization(id="org-1", name="Acme"),)

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)

    context = anyio.run(validate_supabase_session, "opaque-token", None, lookup)

    assert context.memberships == frozenset({"org-1"})
    assert tuple(context.organizations) == (Organization(id="org-1", name="Acme"),)


def test_validation_fails_closed_when_lookup_errors(monkeypatch) -> None:
    from app.services.membership_directory import MembershipLookupError

    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    async def lookup(user_id: str):
        raise MembershipLookupError()

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(validate_supabase_session, "opaque-token", None, lookup)

    assert exc_info.value.status_code == 401


def test_validation_without_lookup_yields_empty_memberships(monkeypatch) -> None:
    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)
    monkeypatch.setattr("app.auth.membership_lookup", None)

    context = anyio.run(validate_supabase_session, "opaque-token")

    assert context.memberships == frozenset()
    assert tuple(context.organizations) == ()
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd apps/api && uv run pytest tests/test_auth.py -k "live_lookup or fails_closed or without_lookup" -v`
Expected: FAIL — `validate_supabase_session` takes no `lookup` arg / `AuthContext` has no `organizations`.

- [ ] **Step 3: Update `auth.py`**

In `apps/api/app/auth.py`:

(a) Update imports near the top:

```python
from app.services.membership_directory import (
    MembershipLookup,
    MembershipLookupError,
    Organization,
    SupabaseServiceRoleMembershipLookup,
)
```

(b) Add the module global next to `supabase_session_verifier`:

```python
membership_lookup: MembershipLookup | None = None
```

(c) Add `organizations` to `AuthContext`:

```python
@dataclass(frozen=True)
class AuthContext:
    user_id: str | None
    auth_required: bool
    memberships: Collection[str] = field(default_factory=frozenset)
    organizations: Collection[Organization] = field(default_factory=tuple)
```

(d) Add the configure function next to `configure_supabase_session_verifier`:

```python
def configure_membership_lookup(settings: Settings) -> None:
    global membership_lookup
    if settings.supabase_url.strip() and settings.supabase_service_role_key.strip():
        membership_lookup = SupabaseServiceRoleMembershipLookup(
            supabase_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
        )
    else:
        membership_lookup = None
```

(e) Replace the body of `validate_supabase_session` after `user_id` is validated. The new signature and tail:

```python
async def validate_supabase_session(
    token: str,
    verifier: SupabaseSessionVerifier | None = None,
    lookup: MembershipLookup | None = None,
) -> AuthContext:
    stripped_token = token.strip()
    if not stripped_token:
        raise _authentication_required()

    selected_verifier = verifier or supabase_session_verifier
    if selected_verifier is None:
        raise _authentication_required()

    claims_result = selected_verifier(stripped_token)
    claims = (
        await claims_result if inspect.isawaitable(claims_result) else claims_result
    )
    if not isinstance(claims, Mapping):
        raise _authentication_required()

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise _authentication_required()

    normalized_user_id = user_id.strip()
    organizations = await _fetch_organizations(normalized_user_id, lookup)

    return AuthContext(
        user_id=normalized_user_id,
        auth_required=True,
        memberships=frozenset(org.id for org in organizations),
        organizations=organizations,
    )


async def _fetch_organizations(
    user_id: str,
    lookup: MembershipLookup | None,
) -> tuple[Organization, ...]:
    selected_lookup = lookup if lookup is not None else membership_lookup
    if selected_lookup is None:
        return ()
    try:
        lookup_result = selected_lookup(user_id)
        organizations = (
            await lookup_result
            if inspect.isawaitable(lookup_result)
            else lookup_result
        )
    except MembershipLookupError as exc:
        raise _authentication_required() from exc
    return tuple(organizations)
```

(f) **Delete** the now-unused `_extract_memberships` and `_string_list_claim` functions. Keep `_normalize_membership_id` (still used by `require_org_membership`).

- [ ] **Step 4: Run the full auth suite**

Run: `cd apps/api && uv run pytest tests/test_auth.py -v`
Expected: PASS. (The existing `test_supabase_validation_uses_verified_claims` still passes: no lookup → empty memberships.)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/auth.py apps/api/tests/test_auth.py
git commit -m "feat(api): derive org membership from live service-role lookup (immediate revocation)"
```

---

## Task 4: `GET /api/session/memberships` endpoint

**Files:**
- Create: `apps/api/app/routes/session.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_session_route.py`

- [ ] **Step 1: Write the failing route test**

Create `apps/api/tests/test_session_route.py`:

```python
from fastapi.testclient import TestClient

from app.main import app
from app.services.membership_directory import Organization


def test_returns_caller_memberships(monkeypatch) -> None:
    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    async def lookup(user_id: str) -> tuple[Organization, ...]:
        assert user_id == "user_123"
        return (Organization(id="org-1", name="Acme"),)

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)
    monkeypatch.setattr("app.auth.membership_lookup", lookup)

    response = TestClient(app).get(
        "/api/session/memberships", headers={"Authorization": "Bearer x"}
    )

    assert response.status_code == 200
    assert response.json() == {"organizations": [{"id": "org-1", "name": "Acme"}]}


def test_requires_authentication(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRED", "true")

    response = TestClient(app).get("/api/session/memberships")

    assert response.status_code in {401, 403}


def test_empty_when_no_memberships(monkeypatch) -> None:
    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    async def lookup(user_id: str) -> tuple[Organization, ...]:
        return ()

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)
    monkeypatch.setattr("app.auth.membership_lookup", lookup)

    response = TestClient(app).get(
        "/api/session/memberships", headers={"Authorization": "Bearer x"}
    )

    assert response.status_code == 200
    assert response.json() == {"organizations": []}
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd apps/api && uv run pytest tests/test_session_route.py -v`
Expected: FAIL — route returns 404 (not mounted / does not exist).

- [ ] **Step 3: Create the route**

Create `apps/api/app/routes/session.py`:

```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import AuthContext, require_auth_context

router = APIRouter(prefix="/api/session", tags=["session"])


class SessionOrganization(BaseModel):
    id: str
    name: str


class SessionMembershipsResponse(BaseModel):
    organizations: list[SessionOrganization]


@router.get("/memberships", response_model=SessionMembershipsResponse)
def get_session_memberships(
    context: AuthContext = Depends(require_auth_context),
) -> SessionMembershipsResponse:
    return SessionMembershipsResponse(
        organizations=[
            SessionOrganization(id=org.id, name=org.name)
            for org in context.organizations
        ]
    )
```

- [ ] **Step 4: Mount it + configure the lookup in `main.py`**

In `apps/api/app/main.py`:

Add the import (after the snowflake router import, line ~10):

```python
from app.routes.session import router as session_router
```

Add the lookup configuration (after `auth.configure_supabase_session_verifier(settings)`, line ~14):

```python
auth.configure_membership_lookup(settings)
```

Add the router include (after `app.include_router(dashboard_runs_router)`, line ~36):

```python
app.include_router(session_router)
```

- [ ] **Step 5: Run to confirm pass**

Run: `cd apps/api && uv run pytest tests/test_session_route.py -v`
Expected: PASS (all 3).

- [ ] **Step 6: Run the whole API suite (no regressions)**

Run: `cd apps/api && uv run pytest`
Expected: PASS. If `tests/test_snowflake_validation.py::test_snowflake_validation_accepts_verified_bearer` fails, confirm it sets only a verifier (no lookup) → memberships empty → auth-only route still 200; no change needed. If any test constructs `Settings(auth_required=True)` and now fails, it is unrelated to this task (the setting has a safe `""` default and no cross-field validator was added).

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/routes/session.py apps/api/app/main.py apps/api/tests/test_session_route.py
git commit -m "feat(api): add GET /api/session/memberships with live membership lookup"
```

---

## Task 5: Browser auth client — passcode `verifyOtp`

**Files:**
- Modify: `apps/web/src/lib/supabase-client.ts`
- Test: `apps/web/src/lib/supabase-client.test.ts`

- [ ] **Step 1: Update the failing tests**

In `apps/web/src/lib/supabase-client.test.ts`:

In the two inline mock client objects (the `authClient` in "preserves injectable auth clients" and the one in `supabaseClient.auth`), add `verifyOtp: vi.fn()` / `verifyOtp: vi.fn().mockResolvedValue({ error: null })` so they satisfy the type.

Change the "maps missing Supabase sessions and auth errors" assertion for `signInWithOtp` to the passcode shape and add a `verifyOtp` assertion. Replace the `signInWithOtp` block there with:

```javascript
    await expect(
      authClient.signInWithOtp({ email: "owner@example.com" }),
    ).resolves.toEqual({ error: { message: "Email rejected" } });
    await expect(
      authClient.verifyOtp({ email: "owner@example.com", token: "123456" }),
    ).resolves.toEqual({ error: { message: "Invalid code" } });
```

And in that test's `supabaseClient.auth`, add:

```javascript
        verifyOtp: vi
          .fn()
          .mockResolvedValue({ error: { message: "Invalid code" } }),
```

Add a new test:

```javascript
  it("verifies an email OTP code", async () => {
    const verifyOtp = vi.fn().mockResolvedValue({ data: {}, error: null });
    const supabaseClient = {
      auth: {
        getSession: vi.fn(),
        onAuthStateChange: vi.fn(),
        signInWithOtp: vi.fn().mockResolvedValue({ error: null }),
        signOut: vi.fn(),
        verifyOtp,
      },
    };
    createClient.mockReturnValue(supabaseClient);

    const authClient = createSupabaseBrowserAuthClient({
      supabaseUrl: "https://project.supabase.co",
      supabaseAnonKey: "anon-key",
    });

    await authClient.verifyOtp({ email: "owner@example.com", token: "123456" });

    expect(verifyOtp).toHaveBeenCalledWith({
      email: "owner@example.com",
      token: "123456",
      type: "email",
    });
  });
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd apps/web && npx vitest run src/lib/supabase-client.test.ts`
Expected: FAIL — `verifyOtp` not on `BrowserAuthClient`; passcode `signInWithOtp` type mismatch.

- [ ] **Step 3: Update the client**

In `apps/web/src/lib/supabase-client.ts`:

Change the `signInWithOtp` member of `BrowserAuthClient` and add `verifyOtp`:

```typescript
  signInWithOtp(input: { email: string }): Promise<{
    error?: { message: string } | null;
  }>;
  verifyOtp(input: { email: string; token: string }): Promise<{
    error?: { message: string } | null;
  }>;
```

In `createSupabaseBrowserAuthClient`, replace the `signInWithOtp` impl and add `verifyOtp`:

```typescript
    async signInWithOtp(input) {
      const { error } = await supabase.auth.signInWithOtp({ email: input.email });
      return { error: error ? { message: error.message } : null };
    },
    async verifyOtp(input) {
      const { error } = await supabase.auth.verifyOtp({
        email: input.email,
        token: input.token,
        type: "email",
      });
      return { error: error ? { message: error.message } : null };
    },
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd apps/web && npx vitest run src/lib/supabase-client.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/supabase-client.ts apps/web/src/lib/supabase-client.test.ts
git commit -m "feat(web): add passcode verifyOtp to browser auth client"
```

---

## Task 6: Two-step login form (email → code)

**Files:**
- Modify: `apps/web/src/components/auth/login-form.tsx`
- Test: `apps/web/src/components/auth/login-form.test.tsx`

- [ ] **Step 1: Rewrite the tests**

Replace the body of `apps/web/src/components/auth/login-form.test.tsx` with:

```javascript
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import LoginForm from "./login-form";
import type { BrowserAuthClient } from "../../lib/supabase-client";

function authClient(overrides: Partial<BrowserAuthClient> = {}): BrowserAuthClient {
  return {
    getSession: vi.fn(),
    onAuthStateChange: vi.fn(),
    signInWithOtp: vi.fn().mockResolvedValue({ error: null }),
    verifyOtp: vi.fn().mockResolvedValue({ error: null }),
    signOut: vi.fn(),
    ...overrides,
  };
}

describe("LoginForm", () => {
  afterEach(() => cleanup());

  it("requests a passcode then advances to the code step", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));

    await waitFor(() => {
      expect(signInWithOtp).toHaveBeenCalledWith({ email: "owner@example.com" });
    });
    expect(screen.getByLabelText("6-digit code")).toBeInTheDocument();
  });

  it("verifies the entered code", async () => {
    const verifyOtp = vi.fn().mockResolvedValue({ error: null });
    render(<LoginForm authClient={authClient({ verifyOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    await screen.findByLabelText("6-digit code");

    fireEvent.change(screen.getByLabelText("6-digit code"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));

    await waitFor(() => {
      expect(verifyOtp).toHaveBeenCalledWith({
        email: "owner@example.com",
        token: "123456",
      });
    });
  });

  it("shows the send error in the alert region", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({
      error: { message: "Email login is unavailable" },
    });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Email login is unavailable",
    );
  });

  it("shows the verify error in the alert region", async () => {
    const verifyOtp = vi.fn().mockResolvedValue({
      error: { message: "Invalid or expired code" },
    });
    render(<LoginForm authClient={authClient({ verifyOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    await screen.findByLabelText("6-digit code");

    fireEvent.change(screen.getByLabelText("6-digit code"), {
      target: { value: "000000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid or expired code",
    );
  });

  it("can reset to a different email", async () => {
    render(<LoginForm authClient={authClient()} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    await screen.findByLabelText("6-digit code");

    fireEvent.click(screen.getByRole("button", { name: "Use a different email" }));

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd apps/web && npx vitest run src/components/auth/login-form.test.tsx`
Expected: FAIL — old single-step form; "Email me a code" / "6-digit code" not found.

- [ ] **Step 3: Rewrite the component**

Replace `apps/web/src/components/auth/login-form.tsx` with:

```tsx
"use client";

import { useState } from "react";
import type { BrowserAuthClient } from "../../lib/supabase-client";

type LoginFormProps = {
  authClient: BrowserAuthClient | null;
};

const CODE_PATTERN = /^\d{6}$/;

export default function LoginForm({ authClient }: LoginFormProps) {
  const [step, setStep] = useState<"request" | "verify">("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function requestCode(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!authClient) {
      setError("Authentication is not configured.");
      return;
    }
    setPending(true);
    const result = await authClient.signInWithOtp({ email: email.trim() });
    setPending(false);
    if (result.error) {
      setError(result.error.message);
      return;
    }
    setCode("");
    setStep("verify");
  }

  async function verifyCode(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!authClient) {
      setError("Authentication is not configured.");
      return;
    }
    if (!CODE_PATTERN.test(code.trim())) {
      setError("Enter the 6-digit code from your email.");
      return;
    }
    setPending(true);
    const result = await authClient.verifyOtp({
      email: email.trim(),
      token: code.trim(),
    });
    setPending(false);
    if (result.error) {
      setError(result.error.message);
    }
  }

  function resetEmail() {
    setStep("request");
    setError(null);
    setCode("");
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      {step === "request" ? (
        <form className="space-y-4" onSubmit={requestCode}>
          <div className="space-y-2">
            <label className="text-sm font-medium text-slate-700" htmlFor="email">
              Email
            </label>
            <input
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-950 shadow-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200"
              id="email"
              name="email"
              onChange={(event) => setEmail(event.target.value)}
              required
              type="email"
              value={email}
            />
          </div>
          <button
            className="rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800 disabled:bg-slate-400"
            disabled={pending}
            type="submit"
          >
            {pending ? "Sending code" : "Email me a code"}
          </button>
        </form>
      ) : (
        <form className="space-y-4" onSubmit={verifyCode}>
          <p className="text-sm text-slate-600">
            Enter the 6-digit code we emailed to{" "}
            <span className="font-medium text-slate-950">{email.trim()}</span>.
          </p>
          <div className="space-y-2">
            <label className="text-sm font-medium text-slate-700" htmlFor="code">
              6-digit code
            </label>
            <input
              autoComplete="one-time-code"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm tracking-widest text-slate-950 shadow-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200"
              id="code"
              inputMode="numeric"
              name="code"
              onChange={(event) => setCode(event.target.value)}
              required
              value={code}
            />
          </div>
          <div className="flex items-center gap-3">
            <button
              className="rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800 disabled:bg-slate-400"
              disabled={pending}
              type="submit"
            >
              {pending ? "Verifying" : "Verify code"}
            </button>
            <button
              className="text-sm font-medium text-slate-600 hover:text-slate-950"
              onClick={resetEmail}
              type="button"
            >
              Use a different email
            </button>
          </div>
        </form>
      )}
      {error ? (
        <p className="mt-3 text-sm font-medium text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd apps/web && npx vitest run src/components/auth/login-form.test.tsx`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/auth/login-form.tsx apps/web/src/components/auth/login-form.test.tsx
git commit -m "feat(web): two-step passcode login form"
```

---

## Task 7: Frontend session-memberships fetch helper

**Files:**
- Create: `apps/web/src/lib/session-memberships.ts`
- Test: `apps/web/src/lib/session-memberships.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/lib/session-memberships.test.ts`:

```javascript
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSessionMemberships } from "./session-memberships";

describe("fetchSessionMemberships", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns organizations and sends the bearer token", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ organizations: [{ id: "org-1", name: "Acme" }] }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const organizations = await fetchSessionMemberships("access-token");

    expect(organizations).toEqual([{ id: "org-1", name: "Acme" }]);
    const [, init] = fetchSpy.mock.calls[0];
    expect((init?.headers as Record<string, string>).authorization).toBe(
      "Bearer access-token",
    );
  });

  it("throws when the request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("nope", { status: 500 }),
    );

    await expect(fetchSessionMemberships("access-token")).rejects.toThrow();
  });

  it("throws on a malformed payload", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ organizations: [{ id: 1 }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(fetchSessionMemberships("access-token")).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd apps/web && npx vitest run src/lib/session-memberships.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the helper**

Create `apps/web/src/lib/session-memberships.ts`:

```typescript
import resolveApiUrl from "./api-client";

export type MembershipOrganization = {
  id: string;
  name: string;
};

function parseOrganizations(payload: unknown): MembershipOrganization[] {
  if (typeof payload !== "object" || payload === null) {
    throw new Error("Malformed memberships response");
  }
  const organizations = (payload as { organizations?: unknown }).organizations;
  if (!Array.isArray(organizations)) {
    throw new Error("Malformed memberships response");
  }
  return organizations.map((item) => {
    if (
      typeof item !== "object" ||
      item === null ||
      typeof (item as { id?: unknown }).id !== "string" ||
      typeof (item as { name?: unknown }).name !== "string"
    ) {
      throw new Error("Malformed membership entry");
    }
    const entry = item as { id: string; name: string };
    return { id: entry.id, name: entry.name };
  });
}

export async function fetchSessionMemberships(
  accessToken: string,
): Promise<MembershipOrganization[]> {
  const response = await fetch(resolveApiUrl("/api/session/memberships"), {
    headers: { authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Membership lookup failed with ${response.status}`);
  }
  return parseOrganizations(await response.json());
}

export default fetchSessionMemberships;
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd apps/web && npx vitest run src/lib/session-memberships.test.ts`
Expected: PASS (all 3).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/session-memberships.ts apps/web/src/lib/session-memberships.test.ts
git commit -m "feat(web): add session memberships fetch helper"
```

---

## Task 8: Org shell — API-driven memberships, interim screen, sign-out

**Files:**
- Modify: `apps/web/src/components/org/org-shell.tsx`
- Test: `apps/web/src/components/org/org-shell.test.tsx`

- [ ] **Step 1: Rewrite the tests**

Replace `apps/web/src/components/org/org-shell.test.tsx` with:

```javascript
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import OrgShell from "./org-shell";
import type { AuthSession, BrowserAuthClient } from "../../lib/supabase-client";

function authClient(
  session: AuthSession | null,
  overrides: Partial<BrowserAuthClient> = {},
): BrowserAuthClient {
  return {
    getSession: vi.fn().mockResolvedValue({ session, error: null }),
    onAuthStateChange: vi.fn(() => ({ unsubscribe: vi.fn() })),
    signInWithOtp: vi.fn(),
    verifyOtp: vi.fn(),
    signOut: vi.fn().mockResolvedValue({ error: null }),
    ...overrides,
  };
}

const session: AuthSession = {
  accessToken: "access-token",
  user: { email: "owner@example.com", appMetadata: null },
};

afterEach(() => cleanup());

describe("OrgShell", () => {
  it("renders children with the demo banner when auth is not required", () => {
    render(
      <OrgShell authRequired={false}>
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(screen.getByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByText("dashboard")).toBeInTheDocument();
  });

  it("renders the login form when there is no session", async () => {
    render(
      <OrgShell authRequired authClient={authClient(null)}>
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByText("dashboard")).not.toBeInTheDocument();
  });

  it("shows the interim screen when the user has no organization", async () => {
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi.fn().mockResolvedValue([])}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(
      await screen.findByText(/Connecting your Snowflake account is coming soon/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("dashboard")).not.toBeInTheDocument();
  });

  it("renders the dashboard and selects the org when membership resolves", async () => {
    const onOrganizationChange = vi.fn();
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme" }])}
        onOrganizationChange={onOrganizationChange}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
    await waitFor(() =>
      expect(onOrganizationChange).toHaveBeenCalledWith({
        id: "org-1",
        name: "Acme",
      }),
    );
  });

  it("shows an error state (not the no-org screen) when the lookup fails", async () => {
    render(
      <OrgShell
        authRequired
        authClient={authClient(session)}
        fetchMemberships={vi.fn().mockRejectedValue(new Error("boom"))}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(
      await screen.findByText(/couldn’t load your organizations/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/coming soon/i),
    ).not.toBeInTheDocument();
  });

  it("signs out and clears the selected organization", async () => {
    const signOut = vi.fn().mockResolvedValue({ error: null });
    const onOrganizationChange = vi.fn();
    render(
      <OrgShell
        authRequired
        authClient={authClient(session, { signOut })}
        fetchMemberships={vi
          .fn()
          .mockResolvedValue([{ id: "org-1", name: "Acme" }])}
        onOrganizationChange={onOrganizationChange}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    await screen.findByText("dashboard");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(signOut).toHaveBeenCalled());
    expect(onOrganizationChange).toHaveBeenLastCalledWith(null);
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd apps/web && npx vitest run src/components/org/org-shell.test.tsx`
Expected: FAIL — current shell reads memberships from `appMetadata`, has no `fetchMemberships` prop, no interim/error/sign-out behavior.

- [ ] **Step 3: Rewrite the component**

Replace `apps/web/src/components/org/org-shell.tsx` with:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";

import { getAuthMode } from "../../lib/auth-mode";
import createBrowserAuthClient, {
  type AuthSession,
  type BrowserAuthClient,
} from "../../lib/supabase-client";
import {
  fetchSessionMemberships,
  type MembershipOrganization,
} from "../../lib/session-memberships";
import LoginForm from "../auth/login-form";

export type SelectedOrganization = {
  id: string;
  name: string;
};

type MembershipState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error" }
  | { status: "resolved"; organizations: MembershipOrganization[] };

type OrgShellProps = {
  authClient?: BrowserAuthClient | null;
  authRequired?: boolean;
  bypassModeLabel?: string;
  children: React.ReactNode;
  fetchMemberships?: (accessToken: string) => Promise<MembershipOrganization[]>;
  onAccessTokenChange?: (accessToken: string | null) => void;
  onOrganizationChange?: (organization: SelectedOrganization | null) => void;
};

export default function OrgShell({
  authClient: providedAuthClient,
  authRequired = getAuthMode().authRequired,
  bypassModeLabel = "Demo mode",
  children,
  fetchMemberships = fetchSessionMemberships,
  onAccessTokenChange,
  onOrganizationChange,
}: OrgShellProps) {
  const [authClient] = useState<BrowserAuthClient | null>(() => {
    if (!authRequired) return null;
    if (providedAuthClient !== undefined) return providedAuthClient;
    return createBrowserAuthClient();
  });
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loadingSession, setLoadingSession] = useState(
    authRequired && Boolean(authClient),
  );
  const [membership, setMembership] = useState<MembershipState>({
    status: "idle",
  });
  const accessToken = session?.accessToken ?? null;

  useEffect(() => {
    if (!authRequired || !authClient) return;

    let active = true;
    void authClient.getSession().then((result) => {
      if (!active) return;
      setSession(result.session);
      setLoadingSession(false);
    });

    const subscription = authClient.onAuthStateChange((nextSession) => {
      if (!active) return;
      setSession(nextSession);
      setLoadingSession(false);
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, [authClient, authRequired]);

  useEffect(() => {
    onAccessTokenChange?.(accessToken);
  }, [accessToken, onAccessTokenChange]);

  const loadMemberships = useCallback(
    async (token: string) => {
      setMembership({ status: "loading" });
      try {
        const organizations = await fetchMemberships(token);
        setMembership({ status: "resolved", organizations });
      } catch {
        setMembership({ status: "error" });
      }
    },
    [fetchMemberships],
  );

  useEffect(() => {
    if (!authRequired) return;
    if (!accessToken) {
      setMembership({ status: "idle" });
      onOrganizationChange?.(null);
      return;
    }
    void loadMemberships(accessToken);
  }, [accessToken, authRequired, loadMemberships, onOrganizationChange]);

  useEffect(() => {
    if (membership.status !== "resolved") return;
    onOrganizationChange?.(membership.organizations[0] ?? null);
  }, [membership, onOrganizationChange]);

  const handleSignOut = useCallback(async () => {
    await authClient?.signOut();
    onOrganizationChange?.(null);
  }, [authClient, onOrganizationChange]);

  if (!authRequired) {
    return (
      <div className="min-h-screen bg-slate-50">
        <div className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-900">
          {bypassModeLabel}
        </div>
        {children}
      </div>
    );
  }

  if (!authClient) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold text-slate-950">
            Authentication is not configured
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            Set public Supabase URL and anon key to enable login.
          </p>
        </section>
      </main>
    );
  }

  if (loadingSession) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <p className="text-sm text-slate-600">Loading authentication</p>
      </main>
    );
  }

  if (!session) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <LoginForm authClient={authClient} />
      </main>
    );
  }

  const signedInHeader = (
    <div className="flex items-center justify-between">
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Signed in
        </p>
        <p className="text-sm font-semibold text-slate-950">
          {session.user?.email ?? "Authenticated user"}
        </p>
      </div>
      <button
        className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100"
        onClick={handleSignOut}
        type="button"
      >
        Sign out
      </button>
    </div>
  );

  if (membership.status === "idle" || membership.status === "loading") {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <p className="text-sm text-slate-600">Loading your workspace</p>
      </main>
    );
  }

  if (membership.status === "error") {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          {signedInHeader}
          <p className="text-sm text-red-700" role="alert">
            We couldn’t load your organizations. Please try again.
          </p>
          <button
            className="rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
            onClick={() => accessToken && void loadMemberships(accessToken)}
            type="button"
          >
            Retry
          </button>
        </section>
      </main>
    );
  }

  if (membership.organizations.length === 0) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          {signedInHeader}
          <p className="text-sm text-slate-700">
            You’re signed in. Connecting your Snowflake account is coming soon.
          </p>
        </section>
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        {signedInHeader}
      </section>
      {children}
    </div>
  );
}
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd apps/web && npx vitest run src/components/org/org-shell.test.tsx`
Expected: PASS (all 6).

- [ ] **Step 5: Run the web suite + typecheck (catch fallout in integration tests)**

Run: `cd apps/web && npx vitest run && npm run typecheck`
Expected: PASS. If `dashboard-runtime-shell.integration.test.tsx` references the removed create-org form or `organizationIdGenerator`, update it to drive membership via the new `fetchMemberships` prop (mock returning one org) — keep its dashboard-runtime assertions intact.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/org/org-shell.tsx apps/web/src/components/org/org-shell.test.tsx
git commit -m "feat(web): API-driven org membership with interim screen and sign-out"
```

---

## Task 9: Env + docs (deployment checklist, first-user bootstrap)

**Files:**
- Modify: `.env.example`
- Modify/Create: `docs/` (extend an existing setup/deployment doc, or create `docs/auth-and-deployment.md`)

- [ ] **Step 1: Annotate `.env.example`**

Edit `.env.example` to group and comment the auth vars (no key removal). Above the Supabase block add:

```bash
# --- Auth (Supabase) ---
# AUTH_REQUIRED=true turns on magic-link login. Web vars are public (browser);
# server vars stay on the API host only.
# Web (public):  NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY
# API (server):  SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
# SUPABASE_SERVICE_ROLE_KEY is REQUIRED when AUTH_REQUIRED=true (live membership lookup).
```

Add the new key if absent:

```bash
SUPABASE_SERVICE_ROLE_KEY=
```

- [ ] **Step 2: Write the deployment + bootstrap doc**

Create `docs/auth-and-deployment.md` with: (a) the web vs API env-var split table from the spec; (b) the Supabase deployment checklist — enable email OTP, set the email template to send `{{ .Token }}` (6-digit code), set a short access-token lifetime, set `GREYSIGHT_CORS_ALLOWED_ORIGINS` to the web origin; (c) the first-user bootstrap SQL from the spec (insert an `organizations` row for the operator's `auth.users.id`; the `organizations_create_owner_membership` trigger grants owner membership; the next request's live lookup picks it up); (d) a note that Vercel can host the FastAPI backend as Python serverless functions, with the serverless time-limit/pooling caveat.

- [ ] **Step 3: Verify the docs reference real identifiers**

Run: `git grep -n "GREYSIGHT_CORS_ALLOWED_ORIGINS\|SUPABASE_SERVICE_ROLE_KEY\|organizations_create_owner_membership" docs/auth-and-deployment.md .env.example`
Expected: matches in both files; identifiers match `config.py` / the migration.

- [ ] **Step 4: Commit**

```bash
git add .env.example docs/auth-and-deployment.md
git commit -m "docs: auth env split, deployment checklist, and first-user bootstrap"
```

---

## Final verification

- [ ] **Run the full suite, lint, and typecheck**

Run from repo root:

```bash
npm run test
npm run lint
npm run typecheck
```

Expected: all green. This satisfies the AGENTS.md invariant that every behavior change ships with a test that fails without it and passes with it.

- [ ] **Manual smoke (optional, requires real Supabase creds)**

With `AUTH_REQUIRED=true` and Supabase configured: load the web app → enter email → receive 6-digit code → enter it → land on the interim screen (no org) or dashboard (seeded org). Remove the membership row in Supabase → the next dashboard API call is denied immediately. (Kyle verifies UI changes in his own browser.)

---

## Notes for the implementer

- **TDD, every task:** the failing test comes first; never write implementation before seeing red.
- **Demo mode is sacred:** `AUTH_REQUIRED=false` must continue to bypass all Supabase calls. Several existing API tests assert this — do not loosen them.
- **Fail closed:** a membership-lookup failure is a 401 (API) / error screen (web), never silently "zero orgs."
- **Service role is server-only:** it is read from `Settings`, used only in `membership_directory.py`, scoped strictly to the authenticated `sub`. Never expose it to the browser or accept a client-supplied user id.
- **No new runtime dependencies** are required (httpx, pydantic, FastAPI, supabase-js are already present).
- **Deferred (LOW, by decision):** the spec suggests sharing a single `httpx.AsyncClient` across the auth + membership calls. This plan opens a client per call to match the existing `SupabaseAuthServerVerifier` pattern and keep the change small. Revisit only if per-request latency on a long-lived API host becomes a measured problem.
