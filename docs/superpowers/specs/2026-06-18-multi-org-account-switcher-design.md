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

New migration that drops `one_owner_membership_per_user` and removes the matching
API guard in the onboarding path. No data changes. Multi-org *membership* was
already allowed; this only lifts the *ownership* cap.

### 2. Block duplicate Snowflake accounts

Today `create_org_with_snowflake_connection` always creates a new org. Add a
uniqueness guard on the **normalized** account:

- Normalize the account locator (trim + uppercase) and use it for comparison.
- Enforce at the DB level with a unique index on the normalized account in
  `organization_snowflake_connections`. The RPC raises a distinct error on
  collision (atomic — two simultaneous onboardings can't both win).
- The API maps that error to **409 Conflict**:
  *"This Snowflake account is already connected to an organization. Ask its owner
  to invite you."*

**Known limitation:** Snowflake account identifiers can be written multiple ways
(account locator vs. `ORG-ACCOUNT` form) for the same account. Trim+uppercase
normalization catches the common cases but is not bulletproof. Acceptable for now;
documented here so it isn't a surprise.

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
- **Add cancelled:** closing the modal is a no-op; stays on the current account.

## Testing (only what changed)

- **Backend:** second owned-org creation now succeeds; duplicate normalized account
  → 409; normalization collapses case/format variants.
- **Frontend:** switcher renders for 1 and 2+ orgs; selecting switches active org +
  writes `localStorage`; stale-id fallback; add-account success auto-switches and
  closes; cancel is a no-op.

Do **not** add tests for unchanged behavior or trivial getters. Keep the suite lean.

## Out of scope

- Inviting users / joining an existing org (the real "add me to that account" path).
- Server-side persistence of the active org (cross-device).
- Auto-join on duplicate account.
- Per-org role management UI.
