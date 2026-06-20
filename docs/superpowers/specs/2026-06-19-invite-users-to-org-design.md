# Invite Users to the Active Org — Design

**Date:** 2026-06-19
**Status:** Approved (design); ready for implementation plan
**Branch:** `featuser-invite`

## Summary

Add a Notion-style "invite a user" affordance to the dashboard header. An
owner/admin of the **currently-selected org** clicks a subtle square icon, a
popover opens beneath it with an email field, they type a work email and click
**Invite**, and the invitee is added to that org — receiving a real invite email
(Supabase GoTrue) if they don't yet have an account. New users default to the
`member` role.

This is "Approach A" (real invite emails) hardened per a cross-model (Codex)
design review.

## Goals

- Owner/admins can invite a user to the active org from the header.
- Invitee gets a real account + email if they don't have one (GoTrue invite).
- Existing users are attached to the org directly (silently, no new email).
- Minimal, pattern-consistent change; reuse existing service-role RPC, popover,
  rate-limit, and API-client patterns.

## Non-Goals (v1)

- No role picker — everyone invited becomes `member`.
- No "you've been added to <org>" email for existing users (cross-org case is
  silent). Noted as a future nicety.
- No invitations management UI (list/revoke/expire pending invites).
- No dedicated `organization_invitations` table — memberships are created
  directly. (Revisit if/when audit, revoke, or expiry are required.)

## Decisions (from brainstorming + review)

| Decision | Choice |
|---|---|
| New-user handling | **A** — real GoTrue invite email creates the account |
| Invitee role | `member`, no picker |
| Who can invite | Owners and admins only |
| Cross-org existing user | Silent membership insert, no email |
| "Already a member" | Explicit **409** + inline message (admins inviting to their own org; not a meaningful enumeration leak) |

## Architecture

### Data flow

```
[Header icon button]  (visible only if active org role ∈ {owner, admin})
   └─ click → InviteUser popover
        └─ Invite → lib/org-invitations-api.ts: POST /api/organizations/{id}/invitations
             └─ FastAPI route: require_org_admin → validate work email → orchestrate
                  ├─ RPC add_org_member_by_email(actor, org, email)  [SECURITY DEFINER, service_role only]
                  └─ GoTrue admin invite / generateLink (service-role key) when needed
```

### Component / file inventory

**Backend (`apps/api`)**

- `app/routes/organizations.py` *(new)* — `POST /api/organizations/{organization_id}/invitations`,
  body `{ email }`. Mounted in `main.py` as a new `organizations` router. Mirrors
  the `onboarding.py` structure: thin route, lazy import of the service seam,
  rate-limit guard, typed-error → HTTP mapping.
- `app/services/org_invitations.py` *(new)* — orchestration + service-role
  clients, configured at startup exactly like `SupabaseOrgProvisioner`
  (`configure_*` + module-global + indirection seam). Contains:
  - a GoTrue admin client (service-role key) for invite / generate-link;
  - a PostgREST RPC caller for `add_org_member_by_email`;
  - `invite_member_to_org(actor_user_id, organization_id, email)` that drives the
    status machine and raises typed errors (`AlreadyMemberError`,
    `InviteProvisioningError`).
- `app/services/work_email.py` *(new)* — Python port of the TS work-email
  blocklist + format check. Source of truth for the domain list lives in a
  **shared fixture** consumed by both TS and Python tests so they cannot drift.
- `app/services/connect_rate_limit.py` *(reuse)* — wrap the invite in the same
  in-flight/rate-limit guard pattern, keyed per actor (and ideally per org).
- `app/routes/session.py` *(edit)* — `SessionOrganization` gains `role`.

**Database (`supabase/migrations`)**

- `202606190001_org_member_invitations.sql` *(new)* — `add_org_member_by_email`
  RPC (details below).

**Frontend (`apps/web`)**

- `src/components/dashboard/invite-user.tsx` *(new)* — the icon button + popover.
  Reuses the `AccountSwitcher` outside-click pattern (`useRef` +
  `document.mousedown` + `absolute … z-50 … rounded-md border border-hairline
  bg-surface shadow-lg`). Reads `useAccountChrome()` for active org, role, and
  access token. Inline SVG "user-plus" icon, styled
  `h-9 w-9 rounded-md border border-hairline text-slate-300 hover:bg-white/5`.
- `src/components/dashboard/dashboard-header.tsx` *(edit)* — mount `<InviteUser />`
  in the right cluster, immediately **left of "Run analysis"**.
- `src/lib/org-invitations-api.ts` *(new)* — `inviteUser({ organizationId, email },
  { accessToken })`, mirroring `onboarding-api.ts` with typed
  `InviteValidationError` / `InviteConflictError`.
- `src/lib/session-memberships.ts` *(edit)* — `MembershipOrganization` gains
  `role: 'owner' | 'admin' | 'member'`; `parseOrganizations` parses it
  (default `'member'` if absent, for resilience).
- `src/lib/account-context.tsx` *(edit)* — `AccountChrome` gains
  `accessToken: string | null`.
- `src/components/org/org-shell.tsx` *(edit)* — pass `accessToken` into the
  `AccountChromeProvider` value (it already holds `accessToken`).

### The RPC: `add_org_member_by_email`

`SECURITY DEFINER`, `language plpgsql`, `set search_path = ''` (fully-qualified
table references: `auth.users`, `public.organization_memberships`).

Signature (conceptual): `add_org_member_by_email(p_actor_user_id uuid,
p_org_id uuid, p_email text) returns text`.

Logic:

1. Re-assert the actor is an owner/admin of `p_org_id` (defense in depth behind
   the API's `require_org_admin`); if not → raise / return an `'unauthorized'`
   status the route maps to 403.
2. Resolve `v_user := auth.users` row where `lower(email) = lower(p_email)`.
3. **Not found** → return `'invite_needed'`.
4. **Found, membership exists, email confirmed** → return `'already_member'`.
5. **Found, membership exists, email NOT confirmed** → return `'pending_resend'`.
6. **Found, no membership** → `insert … values (p_org_id, v_user.id, 'member')
   on conflict (organization_id, user_id) do nothing`, return `'added'`.

Grants: `revoke all on function … from public, anon, authenticated;
grant execute … to service_role;`

Confirmation column: use `email_confirmed_at` (prefer over the broader
`confirmed_at`); **verify the exact column on the deployed GoTrue version during
implementation.**

### API orchestration (`invite_member_to_org`)

| RPC status | Action | HTTP |
|---|---|---|
| `invite_needed` | GoTrue admin **invite** (create user + send email), then **re-call RPC** (now `added`) | 200 `{ email }` |
| `pending_resend` | Re-issue invite link via **`generateLink({ type: 'invite' })`** | 200 `{ email }` |
| `added` | Existing user attached silently (cross-org case), no email | 200 `{ email }` |
| `already_member` | raise `AlreadyMemberError` | **409** |
| `unauthorized` | raise | 403 |

- Membership creation is **idempotent** (`ON CONFLICT DO NOTHING` in the RPC) so
  the TOCTOU window between the two RPC calls / concurrent admins cannot produce
  a 500. Treat GoTrue "user already exists" as recoverable → re-call the RPC.
- The GoTrue resend primitive (`generateLink({ type: 'invite' })`) is the planned
  call; `/auth/v1/invite` resend semantics are unproven and `/auth/v1/resend`
  does not cover invites. **Confirm against the deployed Supabase during
  implementation.**
- Post-accept redirect relies on the Supabase project's configured Site URL
  (same as today's magic-link OTP). No explicit `redirect_to` in v1.
- Upstream GoTrue failures map to a **generic** 502/500 message — never passed
  through verbatim (consistent with `onboarding.py` secret-safe handling).

### Authorization & security

- **Real guard** = server-side `require_org_admin(auth_context, organization_id)`,
  which derives role from a fresh service-role membership lookup keyed by the
  authenticated user id — never from client-supplied data.
- Client-side button gating (via `role`) is **UX only**.
- Service-role key stays backend-only (already the case; configured in
  `Settings`).
- Rate-limit per actor/org to blunt invite-email spam.
- `409 "already a member"` is acceptable: only owner/admins of that same org can
  reach the endpoint, and they can already see their own member list.

## UI copy

- Popover heading: **Add user to {name} ({accountLocator})** — drop the parens if
  the org has no locator.
- Email input placeholder: `name@work-email.com`.
- Success (green, input clears): **Invited: {email} to {name}**.
- Inline errors (red): *"Please use your work email."* / *"{email} is already a
  member."* / generic fallback.

## Testing

- **Python**
  - Route: auth (403 for non-admin, 401 unauth), work-email rejection (422),
    rate-limit (409/429), success (200), already-member (409).
  - Service orchestration: each RPC status → correct action/HTTP, stubbing the
    GoTrue client + RPC seam (as `onboarding` tests stub `create_org_with_connection`).
  - `work_email.py` against the shared fixture.
  - RPC: SQL-level test (member insert, conflict no-op, status returns) if the
    migration test harness supports it.
- **Web (vitest/testing-library)**
  - Button visibility gated by role (hidden for `member`, shown for `owner`/`admin`).
  - Email validation, success state, each error state (stubbed fetch).
  - `session-memberships` parses `role`.
- Shared TS/Python work-email fixture keeps the two validators in lockstep.

## Open implementation-time verifications (not design risks)

1. Exact GoTrue resend primitive (`generateLink({ type: 'invite' })`) against the
   deployed Supabase version.
2. Exact `auth.users` confirmation column (`email_confirmed_at`).
3. Whether the migration test harness can run the RPC unit test.
