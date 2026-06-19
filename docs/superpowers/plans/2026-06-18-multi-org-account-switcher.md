# Multi-Org Account Switcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user own/switch between multiple organizations via a header dropdown, with "Add Account" opening the existing onboarding wizard in a modal, while blocking duplicate Snowflake accounts.

**Architecture:** The data model is already many-to-many (`organization_memberships`). Backend work is a small policy migration (drop the one-owner cap, add a partial unique index on the Snowflake account) plus repurposing the existing `23505` error path. Frontend work lifts active-org selection into `OrgShell`, persists it in `localStorage`, exposes it through the existing account context, and renders an `AccountSwitcher` dropdown in the dashboard header. "Add Account" reuses `ConnectWizard` inside a modal.

**Tech Stack:** Next.js + React (TypeScript, Vitest + Testing Library), FastAPI (Python, pytest via `uv`), Supabase Postgres (SQL migrations).

## Global Constraints

- **Keep it dead simple.** Smallest diff that works. No speculative abstractions. No tests for unchanged behavior, trivial getters, or inert UI.
- **Immutability:** never mutate state objects; build new ones (spread).
- **Persistence:** selected org lives in `localStorage` only. No backend column, no new endpoint.
- **Normalization:** Snowflake account dedup uses `upper(account)` only. The account is already validated by `validate_account_identifier` (regex forbids whitespace), so there is nothing to trim.
- **Duplicate-account message (verbatim):** `This Snowflake account is already connected to an organization. Ask its owner to invite you.`
- **Web tests:** `cd apps/web && npm test` (Vitest). Target one file: `npm test -- src/<path>`.
- **API tests:** `cd apps/api && uv run pytest` (target: `uv run pytest tests/<file>::<test> -v`).
- **Out of scope:** invites/joining an existing org, server-side active-org persistence, auto-join on duplicate, role-management UI.

## File Structure

**Backend**
- Create: `supabase/migrations/202606180001_multi_org_account_switcher.sql` — drops the one-owner index + advisory lock, redefines the create RPC without the owner guard, adds the partial unique index on `upper(account)`.
- Modify: `apps/api/app/services/org_provisioning.py` — rename `OrgAlreadyExistsError` → `DuplicateSnowflakeAccountError`; the `23505` detector now means "duplicate account".
- Modify: `apps/api/app/routes/onboarding.py` — import/catch the renamed error; change the 409 message.
- Modify: `apps/api/tests/test_supabase_migration.py` — update the guard test to assert the lifted cap + new index.
- Modify: `apps/api/tests/test_org_provisioning.py` — rename the two conflict tests to the new error.

**Frontend**
- Create: `apps/web/src/lib/active-organization.ts` — `localStorage` read/write/clear for the active org id (SSR-safe). Created in Task 4 (its sole consumer); no dedicated test — a trivial getter/setter exercised end-to-end through OrgShell's reconcile tests.
- Create: `apps/web/src/components/dashboard/account-switcher.tsx` — the dropdown.
- Create: `apps/web/src/components/dashboard/account-switcher.test.tsx`
- Modify: `apps/web/src/lib/account-context.tsx` — extend `AccountChrome` with org list + active org + setter + `openAddAccount`.
- Modify: `apps/web/src/components/org/org-shell.tsx` — own active-org state, provide the new context fields, host the Add-Account modal.
- Modify: `apps/web/src/components/dashboard/dashboard-header.tsx` — replace the static `Account:` span with `<AccountSwitcher />`.
- Modify: `apps/web/src/components/dashboard/dashboard-header.test.tsx` — drop the header-rendered-locator assertions (the locator moved into `AccountSwitcher`).

---

## Task 1: Backend migration — lift the one-owner cap, add account unique index

**Files:**
- Create: `supabase/migrations/202606180001_multi_org_account_switcher.sql`
- Modify: `apps/api/tests/test_supabase_migration.py:198-211`

**Interfaces:**
- Consumes: existing RPC `create_org_with_snowflake_connection(p_user_id, p_org_name, p_account, p_user, p_role, p_warehouse, p_database, p_schema, p_private_key_pem, p_passphrase) returns uuid` and `set_organization_snowflake_secret(uuid, text, text) returns uuid` (defined in `202606160001`).
- Produces: same RPC signature, now without the owner guard/lock; a partial unique index `org_active_account_unique` that raises `unique_violation` (`23505`) when a second **active** connection reuses an account (case-insensitive).

- [ ] **Step 1: Update the migration test to the new expected SQL**

In `apps/api/tests/test_supabase_migration.py`, replace `test_atomic_create_rpc_and_one_org_guard` (lines 198-211) with:

```python
def test_create_rpc_lifts_one_org_cap_and_dedupes_account() -> None:
    sql = read_migration_sql()
    assert "create or replace function create_org_with_snowflake_connection" in sql
    # The one-owner cap is lifted: the new migration drops the index and the
    # create RPC no longer guards on existing ownership.
    assert "drop index if exists one_owner_membership_per_user" in sql
    # Duplicate Snowflake accounts are blocked by a partial unique index on the
    # upper-cased account, scoped to active connections so a disconnected
    # account can be re-onboarded.
    assert "create unique index org_active_account_unique" in sql
    assert "(upper(account))" in sql
    assert "where status = 'active'" in sql
    # service-role only
    block = sql.split(
        "grant execute on function create_org_with_snowflake_connection", 1
    )[1].split(";", 1)[0]
    assert "to service_role" in block
    assert "authenticated" not in block
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py::test_create_rpc_lifts_one_org_cap_and_dedupes_account -v`
Expected: FAIL — the new migration file does not exist yet, so the asserted strings are absent.

- [ ] **Step 3: Write the migration**

Create `supabase/migrations/202606180001_multi_org_account_switcher.sql`:

```sql
-- Multi-org account switcher (#16).
--
-- 1. Lift the v1 one-owner cap so a user can own multiple orgs (each "Add
--    Account" creates a new owned org).
-- 2. Block two orgs from holding the SAME Snowflake account *concurrently* via a
--    partial unique index on upper(account), scoped to active connections.
--    Disconnect sets status='invalid' and keeps the row, so scoping to 'active'
--    lets a disconnected account be re-onboarded elsewhere.

drop index if exists one_owner_membership_per_user;

-- Preflight: a live project with two orgs already sharing an active account
-- would fail the unique-index build with an opaque error. Fail loudly instead.
do $$
begin
  if exists (
    select 1 from organization_snowflake_connections
    where status = 'active'
    group by upper(account)
    having count(*) > 1
  ) then
    raise exception 'Cannot create org_active_account_unique: an account is already active on >1 org. Resolve duplicates before applying this migration.';
  end if;
end $$;

create unique index org_active_account_unique
  on organization_snowflake_connections (upper(account))
  where status = 'active';

-- Redefine the create RPC: no owner guard, no per-user advisory lock (the unique
-- index now enforces account dedup atomically). Body otherwise unchanged from
-- 202606160001.
create or replace function create_org_with_snowflake_connection(
  p_user_id uuid,
  p_org_name text,
  p_account text,
  p_user text,
  p_role text,
  p_warehouse text,
  p_database text,
  p_schema text,
  p_private_key_pem text,
  p_passphrase text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  new_org_id uuid;
  new_secret_id uuid;
begin
  insert into organizations (name, created_by_user_id)
  values (p_org_name, p_user_id)
  returning id into new_org_id;
  -- organizations_create_owner_membership trigger inserts the owner membership.

  insert into organization_snowflake_connections (
    organization_id, account, snowflake_user, role, warehouse,
    database, schema, has_passphrase, status, last_validated_at, created_by_user_id
  )
  values (
    new_org_id, p_account, p_user, p_role, p_warehouse,
    nullif(p_database, ''), nullif(p_schema, ''),
    p_passphrase is not null and p_passphrase <> '',
    'active', now(), p_user_id
  );

  new_secret_id := set_organization_snowflake_secret(new_org_id, p_private_key_pem, p_passphrase);
  update organization_snowflake_connections
    set secret_id = new_secret_id
    where organization_id = new_org_id;

  return new_org_id;
end;
$$;

revoke all on function create_org_with_snowflake_connection(uuid, text, text, text, text, text, text, text, text, text) from public;
grant execute on function create_org_with_snowflake_connection(uuid, text, text, text, text, text, text, text, text, text) to service_role;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py -v`
Expected: PASS (the whole file, to confirm no other migration assertion regressed).

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/202606180001_multi_org_account_switcher.sql apps/api/tests/test_supabase_migration.py
git commit -m "feat(db): lift one-owner cap, dedupe snowflake account (#16)"
```

---

## Task 2: Backend error mapping — duplicate account → 409

**Files:**
- Modify: `apps/api/app/services/org_provisioning.py:10-11,14,57-58`
- Modify: `apps/api/app/routes/onboarding.py:126-147`
- Modify: `apps/api/tests/test_org_provisioning.py:56,114` (rename two tests + the imported symbol)

**Interfaces:**
- Consumes: the `23505` unique-violation now originates only from `org_active_account_unique` (the owner guard is gone), so a `23505` from this RPC means "duplicate account".
- Produces: `DuplicateSnowflakeAccountError(OrgProvisioningError)` raised by `SupabaseOrgProvisioner` when the RPC response carries `code == "23505"`; mapped to HTTP 409 with the verbatim duplicate-account message.

> **Codex review fix:** the old `_is_one_org_conflict` treated *any* HTTP 409 as a conflict, which could misclassify unrelated 409s. The detector is narrowed to match on the JSON `code == "23505"` only — more precise, and it collapses the two existing conflict tests into one (the HTTP status no longer matters), so we delete the redundant one.

- [ ] **Step 1: Update the provisioning unit tests to the new error + narrowed detector**

In `apps/api/tests/test_org_provisioning.py`:

1. Change the import (top of file) from `OrgAlreadyExistsError` to `DuplicateSnowflakeAccountError`.
2. **Delete** `test_raises_on_one_org_guard_conflict` (lines 56-79) — it differs from the JSON-code test only by HTTP status, which the narrowed detector ignores. (Keep `test_success_body_not_misread_as_conflict` and the transport/non-JSON tests.)
3. Rename `test_one_org_conflict_detected_by_json_code` (line 114) → `test_duplicate_account_detected_by_json_code` and change `pytest.raises(OrgAlreadyExistsError)` → `pytest.raises(DuplicateSnowflakeAccountError)`:

```python
def test_duplicate_account_detected_by_json_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"code": "23505", "message": "unique_violation"}
        )

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(DuplicateSnowflakeAccountError):
        _provision(provisioner)
```

4. In `test_raises_provisioning_error_on_transport_failure` (line 95) change `assert not isinstance(excinfo.value, OrgAlreadyExistsError)` → `DuplicateSnowflakeAccountError`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_org_provisioning.py -v`
Expected: FAIL with `ImportError`/`NameError` for `DuplicateSnowflakeAccountError` (not yet defined).

- [ ] **Step 3: Rename the error class + narrow the detector in the service**

In `apps/api/app/services/org_provisioning.py`, replace lines 10-11:

```python
class DuplicateSnowflakeAccountError(OrgProvisioningError):
    """Raised when the Snowflake account is already connected to an org."""
```

Replace the detector (lines 14-21) — drop the bare `status_code == 409` shortcut so only a `23505` code counts:

```python
def _is_duplicate_account_conflict(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("code") == "23505"
```

And the raise (lines 57-58):

```python
        if _is_duplicate_account_conflict(response):
            raise DuplicateSnowflakeAccountError(
                "This Snowflake account is already connected to an organization. "
                "Ask its owner to invite you."
            )
```

- [ ] **Step 4: Update the onboarding route mapping**

In `apps/api/app/routes/onboarding.py`, change the import block (lines 126-129):

```python
    from app.services.org_provisioning import (
        DuplicateSnowflakeAccountError,
        OrgProvisioningError,
    )
```

And the except block (lines 144-147):

```python
    except DuplicateSnowflakeAccountError:
        raise HTTPException(
            status_code=409,
            detail=(
                "This Snowflake account is already connected to an organization. "
                "Ask its owner to invite you."
            ),
        ) from None
```

- [ ] **Step 5: Run the affected backend tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_org_provisioning.py tests/test_onboarding_route.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/org_provisioning.py apps/api/app/routes/onboarding.py apps/api/tests/test_org_provisioning.py
git commit -m "feat(api): map duplicate snowflake account to 409 (#16)"
```

---

## Task 3: Frontend — extend the account context

**Files:**
- Modify: `apps/web/src/lib/account-context.tsx:10-14`

**Interfaces:**
- Consumes: `MembershipOrganization` from `apps/web/src/lib/session-memberships.ts` (`{ id: string; name: string; accountLocator: string | null }`).
- Produces: extended `AccountChrome` type used by `OrgShell` (provider) and `AccountSwitcher` (consumer):
  - `organizations: MembershipOrganization[]`
  - `activeOrganizationId: string | null`
  - `setActiveOrganization: (id: string) => void`
  - `openAddAccount: () => void`

- [ ] **Step 1: Extend the type**

In `apps/web/src/lib/account-context.tsx`, add the import and extend the type (replace lines 1-14):

```tsx
"use client";

import { createContext, useContext } from "react";

import type { MembershipOrganization } from "./session-memberships";

// Account-level chrome (signed-in identity + sign-out) lifted out of OrgShell so
// the dashboard's own app bar can render it as a single unified header instead
// of OrgShell stacking a second bar above the dashboard. Consumers read it via
// useAccountChrome(); it is null in unauthenticated/demo contexts where no
// provider wraps the tree, so the dashboard header simply omits the user menu.
export type AccountChrome = {
  email: string;
  onSignOut: () => void;
  signOutError: string | null;
  // Org switcher: the user's orgs, the active selection, and the actions the
  // header dropdown drives. Empty list / null active in the single-org demo
  // contexts where the switcher simply shows the lone (or no) account.
  organizations: MembershipOrganization[];
  activeOrganizationId: string | null;
  setActiveOrganization: (id: string) => void;
  openAddAccount: () => void;
};
```

- [ ] **Step 2: Typecheck (no test yet — consumers come next)**

Run: `cd apps/web && npm run typecheck`
Expected: FAIL — `OrgShell`'s provider value no longer satisfies `AccountChrome` (missing new fields). This is expected; Task 4 fixes it. Do not commit yet — commit at the end of Task 4 together, since the type and its sole provider must change atomically to keep the tree compiling.

> Note: Task 3 has no standalone commit. It is folded into Task 4's commit because the type change and the provider that satisfies it cannot compile independently.

---

## Task 4: Frontend — OrgShell owns active org + Add-Account modal

**Files:**
- Create: `apps/web/src/lib/active-organization.ts`
- Modify: `apps/web/src/components/org/org-shell.tsx`
- Test: `apps/web/src/components/org/org-shell.test.tsx` (extend)

**Interfaces:**
- Consumes: the active-org localStorage helper (created in Step 1 below); extended `AccountChrome` (Task 3); existing `ConnectWizard`, `fetchSessionMemberships`.
- Produces: a provider value satisfying the extended `AccountChrome`; calls `onOrganizationChange` exactly once per active-org change so `DashboardRuntimeShell` rebuilds its runtime.

- [ ] **Step 1: Create the active-org localStorage helper**

`localStorage`-only persistence of the active org id (no backend column). SSR-safe via a `typeof window` guard; trivial enough to need no dedicated test — it is exercised end-to-end through the OrgShell reconcile tests below. Create `apps/web/src/lib/active-organization.ts`:

```ts
// Per-browser persistence of the dashboard's active organization. Kept in
// localStorage only (no backend column); SSR-safe via a typeof window guard.
const STORAGE_KEY = "greysight.activeOrganizationId";

export function readActiveOrganizationId(): string | null {
  if (typeof window === "undefined") return null;
  const value = window.localStorage.getItem(STORAGE_KEY);
  return value && value.length > 0 ? value : null;
}

export function writeActiveOrganizationId(id: string | null): void {
  if (typeof window === "undefined") return;
  if (id && id.length > 0) {
    window.localStorage.setItem(STORAGE_KEY, id);
  } else {
    window.localStorage.removeItem(STORAGE_KEY);
  }
}
```

- [ ] **Step 2: Write the failing tests**

Add to `apps/web/src/components/org/org-shell.test.tsx`. These assume the file's existing helpers for rendering `OrgShell` with a fake auth client + `fetchMemberships`. Mirror the existing test setup in that file; the two new behaviors to assert:

```tsx
it("selects the persisted org from localStorage when still a member", async () => {
  window.localStorage.setItem("greysight.activeOrganizationId", "org-2");
  const onOrganizationChange = vi.fn();
  renderSignedIn({
    onOrganizationChange,
    memberships: [
      { id: "org-1", name: "Alpha", accountLocator: "AAA" },
      { id: "org-2", name: "Beta", accountLocator: "BBB" },
    ],
  });
  await waitFor(() =>
    expect(onOrganizationChange).toHaveBeenCalledWith(
      expect.objectContaining({ id: "org-2" }),
    ),
  );
});

it("falls back to the first org and clears a stale persisted id", async () => {
  window.localStorage.setItem("greysight.activeOrganizationId", "gone");
  const onOrganizationChange = vi.fn();
  renderSignedIn({
    onOrganizationChange,
    memberships: [{ id: "org-1", name: "Alpha", accountLocator: "AAA" }],
  });
  await waitFor(() =>
    expect(onOrganizationChange).toHaveBeenCalledWith(
      expect.objectContaining({ id: "org-1" }),
    ),
  );
  expect(window.localStorage.getItem("greysight.activeOrganizationId")).toBeNull();
});
```

> If the existing test file does not already expose a `renderSignedIn`-style helper with `memberships`, adapt these to the file's actual harness (a fake `authClient` that yields a session + a `fetchMemberships` returning the array). Keep to two tests — persisted-selection and stale-fallback — do not add more.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd apps/web && npm test -- src/components/org/org-shell.test.tsx`
Expected: FAIL — selection still hardcodes `organizations[0]`, so the persisted-org test fails.

- [ ] **Step 4: Replace the selection effect with reconcile-and-notify**

In `apps/web/src/components/org/org-shell.tsx`:

Add imports near the top (after the existing `session-memberships` import):

```tsx
import {
  readActiveOrganizationId,
  writeActiveOrganizationId,
} from "../../lib/active-organization";
```

Add state next to the other `useState` hooks (after `signOutError`, ~line 66):

```tsx
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [addAccountOpen, setAddAccountOpen] = useState(false);
```

Replace the existing selection effect (lines 156-159) with the derived active org + a single reconcile-and-notify effect + the switcher actions:

```tsx
  const organizations =
    membership.status === "resolved" ? membership.organizations : [];
  const activeOrganization =
    organizations.find((org) => org.id === activeOrgId) ??
    organizations[0] ??
    null;

  // Reconcile the active org from localStorage AND notify the parent in ONE
  // effect, so the dashboard runtime rebuilds exactly once with the correct org.
  //   1. Keep the persisted id if it is still a member; otherwise fall back to
  //      the first org and CLEAR the stale key (write null — we never persist the
  //      implicit first-org fallback; persistence happens only on an explicit
  //      selection via setActiveOrganization).
  //   2. Settle activeOrgId first (return), then notify on the next pass once
  //      activeOrgId equals the resolved selection. This avoids the transient
  //      wrong-org notify that a separate [activeOrganization] effect would emit
  //      (first org, then the persisted org) on the first resolved render.
  useEffect(() => {
    if (membership.status !== "resolved") return;
    const orgs = membership.organizations;
    const stored = readActiveOrganizationId();
    const valid =
      stored && orgs.some((org) => org.id === stored) ? stored : null;
    if (stored && !valid) writeActiveOrganizationId(null);
    const resolvedId = valid ?? orgs[0]?.id ?? null;
    if (resolvedId !== activeOrgId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActiveOrgId(resolvedId);
      return;
    }
    onOrganizationChangeRef.current?.(
      orgs.find((org) => org.id === resolvedId) ?? null,
    );
  }, [membership, activeOrgId]);

  const setActiveOrganization = useCallback((id: string) => {
    setActiveOrgId(id);
    writeActiveOrganizationId(id);
  }, []);

  const openAddAccount = useCallback(() => setAddAccountOpen(true), []);
```

> This single effect replaces both the old selection effect and the would-be separate notify effect. The stale-id branch writes `null` (clears the key) rather than persisting the fallback id, matching the "clears a stale persisted id" test. Because `activeOrgId` is never reset on sign-out, a sign-out → sign-in cycle lands directly on the equal-id pass and re-notifies the persisted org.

- [ ] **Step 5: Extend the provider value and host the modal**

Replace the final signed-in `return` (lines 300-310) with:

```tsx
  return (
    <AccountChromeProvider
      value={{
        email: session.user?.email ?? "Authenticated user",
        onSignOut: handleSignOut,
        signOutError,
        organizations,
        activeOrganizationId: activeOrganization?.id ?? null,
        setActiveOrganization,
        openAddAccount,
      }}
    >
      {children}
      {addAccountOpen ? (
        <div
          className="dark fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-6 [color-scheme:dark]"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-4xl">
            <div className="mb-3 flex justify-end">
              <button
                className="h-9 rounded-md border border-hairline px-3 text-sm font-medium text-slate-300 hover:bg-white/5"
                onClick={() => setAddAccountOpen(false)}
                type="button"
              >
                Cancel
              </button>
            </div>
            <ConnectWizard
              accessToken={accessToken}
              onConnected={(newOrgId) => {
                // Persist the new org and reload memberships; once the reload
                // includes it, the reconcile effect selects it (now a valid
                // member) and notifies the dashboard. No setActiveOrganization
                // call needed here.
                writeActiveOrganizationId(newOrgId);
                setAddAccountOpen(false);
                if (accessToken) void loadMemberships(accessToken);
              }}
            />
          </div>
        </div>
      ) : null}
    </AccountChromeProvider>
  );
```

- [ ] **Step 6: Run tests + typecheck to verify pass**

Run: `cd apps/web && npm test -- src/components/org/org-shell.test.tsx && npm run typecheck`
Expected: PASS, and typecheck clean (the provider now satisfies the extended `AccountChrome` from Task 3).

- [ ] **Step 7: Commit (includes Task 3)**

```bash
git add apps/web/src/lib/active-organization.ts apps/web/src/lib/account-context.tsx apps/web/src/components/org/org-shell.tsx apps/web/src/components/org/org-shell.test.tsx
git commit -m "feat(web): active-org selection, persistence, and add-account modal (#16)"
```

---

## Task 5: Frontend — AccountSwitcher dropdown in the header

**Files:**
- Create: `apps/web/src/components/dashboard/account-switcher.tsx`
- Create: `apps/web/src/components/dashboard/account-switcher.test.tsx`
- Modify: `apps/web/src/components/dashboard/dashboard-header.tsx:32-33,56-61`
- Modify: `apps/web/src/components/dashboard/dashboard-header.test.tsx`

**Interfaces:**
- Consumes: `useAccountChrome()` (extended `AccountChrome`).
- Produces: `<AccountSwitcher />` (no props) rendered in the header where the static `Account:` span was.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/components/dashboard/account-switcher.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChromeProvider, type AccountChrome } from "../../lib/account-context";
import AccountSwitcher from "./account-switcher";

function renderWith(overrides: Partial<AccountChrome>) {
  const value: AccountChrome = {
    email: "user@example.com",
    onSignOut: vi.fn(),
    signOutError: null,
    organizations: [],
    activeOrganizationId: null,
    setActiveOrganization: vi.fn(),
    openAddAccount: vi.fn(),
    ...overrides,
  };
  render(
    <AccountChromeProvider value={value}>
      <AccountSwitcher />
    </AccountChromeProvider>,
  );
  return value;
}

afterEach(() => vi.clearAllMocks());

describe("AccountSwitcher", () => {
  it("shows the active org locator on the trigger", () => {
    renderWith({
      organizations: [{ id: "org-1", name: "Alpha", accountLocator: "AAA-111" }],
      activeOrganizationId: "org-1",
    });
    expect(screen.getByRole("button", { name: /AAA-111/ })).toBeInTheDocument();
  });

  it("switches org on selection", () => {
    const value = renderWith({
      organizations: [
        { id: "org-1", name: "Alpha", accountLocator: "AAA-111" },
        { id: "org-2", name: "Beta", accountLocator: "BBB-222" },
      ],
      activeOrganizationId: "org-1",
    });
    fireEvent.click(screen.getByRole("button", { name: /AAA-111/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Beta/ }));
    expect(value.setActiveOrganization).toHaveBeenCalledWith("org-2");
  });

  it("invokes openAddAccount from the Add Account item", () => {
    const value = renderWith({
      organizations: [{ id: "org-1", name: "Alpha", accountLocator: "AAA-111" }],
      activeOrganizationId: "org-1",
    });
    fireEvent.click(screen.getByRole("button", { name: /AAA-111/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Add Account/ }));
    expect(value.openAddAccount).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web && npm test -- src/components/dashboard/account-switcher.test.tsx`
Expected: FAIL — module `./account-switcher` not found.

- [ ] **Step 3: Write the component**

Create `apps/web/src/components/dashboard/account-switcher.tsx`. A self-contained dropdown (click-toggled, closes on outside click) — no new dependency, matches the existing dark header styling:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";

import { useAccountChrome } from "../../lib/account-context";

export default function AccountSwitcher() {
  const account = useAccountChrome();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  // No provider (demo/unauthenticated) or no orgs yet: render nothing — the
  // zero-org case is handled by OrgShell's inline wizard.
  if (!account || account.organizations.length === 0) return null;

  const active =
    account.organizations.find((org) => org.id === account.activeOrganizationId) ??
    account.organizations[0];

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        Account:{" "}
        <span className="font-mono text-slate-200">
          {active.accountLocator ?? active.name}
        </span>
        <span aria-hidden="true">▾</span>
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute left-0 z-50 mt-2 min-w-56 rounded-md border border-hairline bg-surface py-1 shadow-lg"
        >
          {account.organizations.map((org) => (
            <button
              key={org.id}
              role="menuitem"
              type="button"
              className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm text-slate-200 hover:bg-white/5"
              onClick={() => {
                account.setActiveOrganization(org.id);
                setOpen(false);
              }}
            >
              <span className="min-w-0">
                <span className="block truncate">{org.name}</span>
                {org.accountLocator ? (
                  <span className="block truncate font-mono text-xs text-slate-400">
                    {org.accountLocator}
                  </span>
                ) : null}
              </span>
              {org.id === active.id ? (
                <span aria-hidden="true" className="text-chart-purple">
                  ✓
                </span>
              ) : null}
            </button>
          ))}
          <div className="my-1 border-t border-hairline" />
          <button
            role="menuitem"
            type="button"
            className="w-full px-3 py-2 text-left text-sm font-medium text-slate-200 hover:bg-white/5"
            onClick={() => {
              account.openAddAccount();
              setOpen(false);
            }}
          >
            + Add Account
          </button>
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Run the component test to verify it passes**

Run: `cd apps/web && npm test -- src/components/dashboard/account-switcher.test.tsx`
Expected: PASS.

- [ ] **Step 5: Wire it into the header**

In `apps/web/src/components/dashboard/dashboard-header.tsx`:

Add the import:

```tsx
import AccountSwitcher from "./account-switcher";
```

Remove the now-unused `locator` line (line 33) and the static `Account:` span (lines 56-61), replacing the span with the component. The brand block becomes:

```tsx
        <div className="flex flex-wrap items-center gap-3">
          {brandLogo ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              alt="Greybeam"
              className="h-7 w-7 rounded-md"
              height={28}
              src="/greybeam_assets/greybeam_logo.svg"
              width={28}
            />
          ) : null}
          <h1 className="font-display text-lg font-semibold text-slate-50">
            Greybeam
          </h1>
          <AccountSwitcher />
        </div>
```

> The `accountLocator` prop and `header?.accountLocator` fallback are no longer read by the header (the switcher sources the active org's locator from context). Leave the `DashboardHeaderProps.accountLocator` prop in place if other callers still pass it; just remove the unused `const locator = …` line to satisfy the linter. If `npm run typecheck`/`lint` flags `accountLocator` as unused in this file, prefix it `_accountLocator` or drop it from the destructure — do not chase removing it from callers in this task.

- [ ] **Step 6: Update the existing header test for the moved locator**

The header no longer renders the `Account:`/locator span itself — `AccountSwitcher` sources the active org's locator from context and renders `null` when no provider wraps it. Three tests in `apps/web/src/components/dashboard/dashboard-header.test.tsx` assert the header-rendered locator and now break; update them:

1. `shows the product and the account locator` (line 30) — the header still renders `Greybeam` and the Run button but no longer the locator. Drop the `screen.getByText("TU24199")` and `screen.getByText(/Account:/)` assertions (keep `Greybeam` + `Run analysis`) and rename it to `shows the product and run action`.
2. `prefers the connection account locator over the run's view model` (line 46) — **delete** it. Locator precedence moved out of the header; `AccountSwitcher` owns locator display (covered by `account-switcher.test.tsx`).
3. `shows the account locator before any run, without a view model` (line 60) — **delete** it, same reason.

Leave the other header tests untouched.

- [ ] **Step 7: Run header tests + full web suite + typecheck**

Run: `cd apps/web && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/components/dashboard/account-switcher.tsx apps/web/src/components/dashboard/account-switcher.test.tsx apps/web/src/components/dashboard/dashboard-header.tsx apps/web/src/components/dashboard/dashboard-header.test.tsx
git commit -m "feat(web): account switcher dropdown in dashboard header (#16)"
```

---

## Final verification

- [ ] **Backend:** `cd apps/api && uv run pytest` → all pass.
- [ ] **Frontend:** `cd apps/web && npm test && npm run typecheck && npm run lint` → all pass.
- [ ] **Manual (Kyle verifies visually in browser):** with two orgs, the header shows a dropdown; switching reloads the dashboard for the other account and survives reload; "+ Add Account" opens the wizard modal; completing it auto-switches to the new account; onboarding a duplicate Snowflake account shows the 409 message inline.

## Spec self-review notes

- **Coverage:** multi-org ownership (Task 1), duplicate blocking + 409 (Tasks 1-2), context plumbing (Task 3), localStorage persistence + active-org selection + stale fallback + modal + auto-switch (Task 4), dropdown + Add Account + header wiring (Task 5). All spec sections map to a task.
- **Disconnect edge (spec implementation-check):** resolved — disconnect keeps the row as `status='invalid'`, so the unique index is partial (`where status='active'`) to allow re-onboarding.
- **Pre-existing duplicates (spec implementation-check):** resolved — migration preflight raises a clear error before building the index.
