# Auth Card — Dark Theme Login & Loading Screens

**Date:** 2026-06-19
**Status:** Approved (design)

## Problem

The login and loading screens are light-themed and visually disconnected from
the rest of the dashboard, which uses a dark theme. The login form also shows a
two-step "6-digit code" flow in the UI, but the backend actually sends a **magic
link** — the code step is dead/misaligned UI that never matches what the user
receives.

We want a single, centered, dark-themed card — Greybeam logo + wordmark, an
email field, and an "Email link" button — that also hosts every loading and
error state, so nothing is left light-themed. Reference look: the Omni login
screen (subtle radial glow behind a centered dark card).

## Goals

- One reusable dark card shell shared by login + all pre-dashboard states.
- Centered in the viewport (not pinned to the top).
- Subtle radial glow using the two brand colors: purple `#9F57E7` + lime
  `#C9E930`.
- Magic-link login: email → "Email link" → "Check your email". Remove the
  in-app 6-digit code step.
- Work-email-only validation on the client.
- Terms-of-service link on the login step.

## Non-Goals

- Removing `verifyOtp` from the `BrowserAuthClient` interface (stays, unused).
- Server-side work-email enforcement (follow-up; this change is client-only).
- Restyling the post-login onboarding (signed-in header + ConnectWizard) — those
  are already dark and are not login/loading screens.
- Google / SSO buttons (Omni has one; out of scope, YAGNI).

## Design Tokens (existing)

From `tailwind.config.ts` / `chart-colors.ts`:

- `canvas` `#161616` — page background
- `surface` `#1C1C1C` — card background
- `hairline` `#2A2A2A` — borders
- `chart-purple` `#9F57E7` — brand purple (accent, spinner, glow)
- `chart-lime` `#C9E930` — brand lime (glow)

Dark mode is class-based (`darkMode: "class"`), so the auth shell sets the `dark`
class and `[color-scheme:dark]` like the existing ConnectWizard wrapper does.

## Components

### `apps/web/src/components/auth/auth-card.tsx` (new)

The centered dark shell wrapping every pre-dashboard state.

- Full-screen container: `dark [color-scheme:dark] min-h-screen bg-canvas flex
  items-center justify-center p-6`, `relative` with `overflow-hidden`.
- **Radial glow layer**: an absolutely-positioned, `aria-hidden`, pointer-events-
  none div behind the card with two low-opacity radial gradients — purple toward
  the top, lime toward the bottom — blurred and subtle (think ~10–15% alpha).
  Implemented via inline `style` `background` with two `radial-gradient(...)`
  layers, or a blurred element; kept faint so the card stays the focus.
- **Card**: `relative w-full max-w-sm rounded-xl border border-hairline
  bg-surface p-6 shadow-xl`.
- **Brand header** (always shown): logo `img` (`/greybeam_assets/greybeam_logo.svg`)
  + `Greybeam` wordmark, reusing the dashboard-header pattern
  (`font-display ... text-slate-50`), centered.
- Slots: `children` (body) and optional `footer` (terms).

Props:

```ts
interface AuthCardProps {
  children: React.ReactNode;
  footer?: React.ReactNode;
}
```

### `apps/web/src/components/auth/auth-status.tsx` (new)

Small status block used as the card body for loading states — "extend the card
to the bottom with the status".

- A small `animate-spin` ring in brand purple (a bordered circle, purple top
  border) + a status label (`text-sm text-slate-300`).
- Centered, stacked.

Props:

```ts
interface AuthStatusProps {
  label: string; // "Authenticating", "Loading workspace", "Check your email"
}
```

### `apps/web/src/lib/work-email.ts` (new)

Client-side work-email validation.

- `FREE_EMAIL_DOMAINS`: a `Set` of common consumer providers — gmail.com,
  googlemail.com, yahoo.com, yahoo.co.uk, outlook.com, hotmail.com, live.com,
  msn.com, icloud.com, me.com, mac.com, aol.com, proton.me, protonmail.com,
  gmx.com, mail.com, zoho.com, yandex.com, qq.com, 163.com (extendable).
- `isWorkEmail(email: string): boolean` — lowercases, extracts the domain after
  `@`, returns `false` for empty/malformed input or a blocklisted domain.
- Blocklist (not allowlist): block known free providers rather than trying to
  verify a domain is a real company. Good enough for a lead magnet.

### `apps/web/src/components/auth/login-form.tsx` (rewrite)

Single-step magic-link flow.

- State: `email`, `error`, `pending`, and a `sent` boolean (replaces the
  `step: "request" | "verify"` machine).
- **Request view**: email `input` (dark styling) + **"Email link"** submit
  button. On submit:
  1. If not `isWorkEmail(email)` → set error "Please use your work email."
     and do **not** call the client.
  2. Else call `authClient.signInWithOtp({ email })`; on success set `sent = true`.
  - Error + generic-catch paths render in the existing `role="alert"` region.
  - Button label: "Email link" idle, "Sending link" pending.
- **Sent view** (`sent === true`): renders `AuthStatus`-style confirmation
  "Check your email" + the address + a "Send to a different email" button that
  resets to the request view.
- Footer (request view only): muted text "By continuing you agree to our Terms
  of Service" linking to `https://www.greybeam.ai/terms` in a new tab. Add a
  `// TODO` that the terms page needs updating soon.
- The 6-digit code input, `verifyOtp` call, and "Verify code" button are removed.

Dark input styling: `bg-canvas border-hairline text-slate-100
placeholder:text-slate-500 focus:border-[#9F57E7] focus:ring-1
focus:ring-[#9F57E7]` (or equivalent token classes).

### `apps/web/src/components/org/org-shell.tsx` (edit)

Wrap every pre-dashboard branch in `AuthCard`, replacing the light
`<main class="min-h-screen bg-slate-50 p-6">` wrappers.

| Branch | Card body |
|---|---|
| `!authClientResolved` | `AuthStatus label="Authenticating"` |
| `!authClient` | "Authentication is not configured" message (dark, in-card) |
| `loadingSession` | `AuthStatus label="Authenticating"` |
| `!session` | `LoginForm` |
| membership `idle`/`loading` | `AuthStatus label="Loading workspace"` |
| membership `error` | error text + Retry button (dark, in-card) |

The membership-error branch keeps the signed-in identity + Retry, restyled for
the dark card. The `membership.organizations.length === 0` (ConnectWizard) and
the final dashboard branches are unchanged.

## Data Flow / Behavior

1. User lands unauthenticated → `AuthCard` + `LoginForm` (request view).
2. Submits a work email → `signInWithOtp` sends a magic link → card shows
   "Check your email".
3. User clicks the link in their email → returns to the app → `getSession`
   resolves → brief "Authenticating" → membership loads → "Loading workspace" →
   dashboard.
4. Non-work email → inline "Please use your work email." error, no email sent.

## Error Handling

- Work-email rejection: inline alert, no network call.
- `signInWithOtp` error: message surfaced in `role="alert"`.
- Thrown/network error: generic "Something went wrong. Please try again." +
  re-enabled submit.
- Membership fetch error: in-card message + Retry (existing behavior, restyled).
- Missing Supabase env: in-card "Authentication is not configured" message.

## Testing

- **`work-email.test.ts`** (new): accepts company domains; rejects each
  blocklisted provider; rejects empty/malformed input; case-insensitive.
- **`login-form.test.tsx`** (rewrite): submit work email → `signInWithOtp`
  called → "Check your email" shown; non-work email → error, client NOT called;
  send-error path; generic-error path + re-enabled submit; "send to a different
  email" resets to the form. Drop the code/verify/`verifyOtp` tests.
- **`org-shell.test.tsx`** + **`dashboard-runtime-shell.integration.test.tsx`**:
  update status-string assertions ("Loading authentication" → "Authenticating";
  add "Loading workspace"); keep the membership-error and ConnectWizard
  assertions.

## Follow-ups (out of scope)

- Server-side work-email enforcement (Supabase auth hook / API guard).
- Update the published Terms of Service at greybeam.ai/terms.
- Optionally drop the now-unused `verifyOtp` from `BrowserAuthClient`.
