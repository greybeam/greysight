# Multi-Org Account Switcher — Design

**Issue:** [#16 — Allow multiple orgs per user](https://github.com/greybeam/greysight/issues/16)
**Date:** 2026-06-18

## Guiding principle

Keep this **dead simple**. The data model already supports multi-org membership;
this is mostly a frontend switcher plus one small backend policy change. Favor
the smallest diff that works. No speculative abstractions, no redundant tests —
test the behavior that actually changed, nothing more.

## Background

The data model is already many-to-many: `organization_memberships` has primary
key `(organization_id, user_id)`, so a user can belong to many orgs today. What
blocks the feature:

1. A unique index `one_owner_membership_per_user` (and a matching API guard) caps
   each user to **owning** one org. The onboarding wizard makes you the owner, so
   it can't run twice.
2. The frontend just picks `organizations[0]`; there is no switcher (TODO already
   noted in `org-shell.tsx`).
3. The header account indicator (`dashboard-header.tsx`) is static text.
4. The `ConnectWizard` renders only when you have **zero** orgs — unreachable once
   you have one.

## Decisions

- **Persistence:** selected org stored in `localStorage` (per-browser). No backend
  column, no new endpoint.
- **Add Account UI:** opens the existing `ConnectWizard` in a **modal**.
- **After add:** **auto-switch** to the newly created org.
- **Switcher visibility:** **always a dropdown**, even with one org, so "Add
  Account" is always reachable.
- **Duplicate Snowflake account:** **block** with a friendly error. No auto-join.
- **Invites / joining someone else's org:** **out of scope** (separate feature).

## Backend

### 1. Remove the one-owner cap

The ownership cap lives in **two** places (not a Python API guard):
- the partial unique index `one_owner_membership_per_user`
  (`202606160001_org_snowflake_connections.sql:218`), and
- an `if exists (... role = 'owner') then raise unique_violation` block **inside
  the `create_org_with_snowflake_connection` RPC** (same file, ~line 247).

New migration drops the index and removes that guard block. The RPC's per-user
`pg_advisory_xact_lock` (line 245) existed only to serialize that guard against
itself — once the account unique index below enforces dedup atomically, the lock
is dead weight, so remove it too. No data changes.

### 2. Block duplicate Snowflake accounts

Today `create_org_with_snowflake_connection` always creates a new org. Add a
uniqueness guard on the account:

- **Normalization is just `upper(account)`.** The account is already validated by
  `validate_account_identifier` (`snowflake_account.py:14`), whose regex forbids
  whitespace and any other punctuation outside `[A-Za-z0-9._-]`, so there is
  nothing to trim — only case to fold.
- Enforce with a **functional unique index** `(upper(account))` on
  `organization_snowflake_connections`. This is atomic — two simultaneous
  onboardings of the same account can't both win — and needs no advisory lock.
- **Error mapping reuses the existing path.** `org_provisioning.py:14`
  (`_is_one_org_conflict`) currently maps any `23505` → `OrgAlreadyExistsError` →
  "You already have an organization." Since we are removing the owner guard (the
  only current `23505` source from this RPC), the *only* remaining `23505` will be
  the new account index. So just change that error's message to:
  *"This Snowflake account is already connected to an organization. Ask its owner
  to invite you."* — no new error class, no constraint-name branching. (Rename the
  class to `DuplicateSnowflakeAccountError` for clarity if cheap; optional.)

**Implementation checks:**
- The migration creates a unique index; it will fail if duplicate accounts already
  exist. Verify there are none first (early stage — almost certainly none).
- Confirm what `disconnect_organization_snowflake` does to the connection row. If
  it **deletes** the row, re-onboarding the same account works. If it leaves the
  row, the unique index would block re-onboarding — handle only if that's the
  actual behavior.

**Known limitation:** Snowflake account identifiers can be written multiple ways
(account locator vs. `ORG-ACCOUNT` form) for the same account. `upper()` folding
catches case variants but not format variants. Acceptable for now; documented so
it isn't a surprise.

## Frontend

### 1. Active-org selection moves up

In `org-shell.tsx`, replace the hardcoded `organizations[0]` with:

- On load, read the persisted org id from `localStorage`.
- If it is still in the membership list, use it; otherwise fall back to the first
  org and clear the stale key.
- A setter updates React state **and** `localStorage`.

### 2. Expose what the header needs

Extend the existing account context (currently `{email, onSignOut, signOutError}`)
to also carry: `organizations`, `activeOrganization`, `setActiveOrganization(id)`,
and `openAddAccount()`. Reuse the existing context — do not introduce a second one.

### 3. `AccountSwitcher` component

Replaces the static `Account: …` text in `dashboard-header.tsx`:

- Always a dropdown (even with one org).
- Lists each org by name + account locator, with a check on the active one.
- Divider, then **"+ Add Account"** at the bottom.
- Selecting an org calls `setActiveOrganization(id)`; the dashboard reloads its data.
- Built from Tremor primitives already used in the project, matching current styling.

### 4. Add-account modal

`openAddAccount()` opens a dialog hosting the **existing** `ConnectWizard`. On
`onConnected(newOrgId)`: reload memberships → `setActiveOrganization(newOrgId)` →
close modal → dashboard reloads on the new account. The zero-org case keeps its
current full-page inline wizard; only "add another" uses the modal.

## Edge cases

- **Stale `localStorage`:** id you no longer have access to → fall back to first
  org, clear the key.
- **Single org:** dropdown still renders so "Add Account" stays reachable; the lone
  org shows checked.
- **Duplicate account on add:** wizard surfaces the 409 inline (same error channel
  it already uses for 422/409); modal stays open to correct.
- **Add cancelled:** closing the modal returns to the current account. No test
  needed — the wizard has no cancel logic and the modal close is inert.

## Testing (only what changed)

- **Backend:** `test_supabase_migration.py` already asserts the old one-org guard
  (~line 198) — **update that existing assertion** to reflect the lifted cap rather
  than adding a new "second org succeeds" test. Add **one** service test:
  duplicate `upper(account)` → 409 with the new message. That's it.
- **Frontend:** switcher renders for 1 and 2+ orgs; selecting switches active org +
  writes `localStorage`; stale-id fallback; add-account success auto-switches and
  closes.

Do **not** add tests for unchanged behavior, trivial getters, or the inert modal
cancel. Keep the suite lean.

## Out of scope

- Inviting users / joining an existing org (the real "add me to that account" path).
- Server-side persistence of the active org (cross-device).
- Auto-join on duplicate account.
- Per-org role management UI.
