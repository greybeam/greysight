# Auth Card — Dark Theme Login & Loading Screens — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the light-themed login + loading/error screens with one centered, dark-themed Greybeam card that hosts every pre-dashboard state, and convert the login UI to a single-step magic-link flow with work-email-only validation.

**Architecture:** A presentational `AuthCard` shell (centered dark card, brand header, radial brand glow) wraps every pre-dashboard branch in `org-shell.tsx`. A small `AuthStatus` block renders the loading states inside that card. `LoginForm` is rewritten to a single-step magic-link flow (email → "Email link" → "Check your email"), gated by a pure `isWorkEmail` helper. No backend/auth-client behavior changes — `signInWithOtp` already sends a magic link; the dead 6-digit code step is removed from the UI only.

**Tech Stack:** Next.js (App Router, client components), React, TypeScript, Tailwind (class-based dark mode, tokens in `tailwind.config.ts`), Tremor (dashboard only), Vitest + Testing Library (jsdom).

## Commit Workflow

Each task ends with a "Commit" step, but per this repo's workflow commits are **not** made autonomously by an executing subagent. The orchestrator (or Kyle) reviews the task's diff and commits after approval. Treat each task's "Commit" step as the checkpoint where, once the task is approved, the listed `git add`/`git commit` is run. A subagent implementing a task should stop after its tests pass and hand back for review rather than committing itself.

## Global Constraints

- Dark theme tokens (from `apps/web/tailwind.config.ts` / `src/lib/chart-colors.ts`), exact values: `canvas` `#161616`, `surface` `#1C1C1C`, `hairline` `#2A2A2A`, `chart-purple` `#9F57E7`, `chart-lime` `#C9E930`. Use the Tailwind token classes (`bg-canvas`, `bg-surface`, `border-hairline`, `bg-chart-purple`, `text-chart-purple`) where they exist; use the literal hex only in the glow `style`.
- Dark mode is class-based: the auth shell root sets `dark [color-scheme:dark]` (matches the existing ConnectWizard wrapper).
- Brand copy is exactly `Greybeam`. Button label is exactly `Email link` (idle) / `Sending link` (pending). Loading labels are exactly `Authenticating`, `Loading workspace`, `Check your email`. Work-email error is exactly `Please use your work email.` Generic error is exactly `Something went wrong. Please try again.`
- Terms URL is exactly `https://www.greybeam.ai/terms`, opened with `target="_blank" rel="noopener noreferrer"`.
- The logo is decorative: `<img alt="" ... src="/greybeam_assets/greybeam_logo.svg">` (the visible `Greybeam` wordmark names the brand to assistive tech).
- Inputs and interactive controls must keep a visible focus ring (never bare `outline-none`). Inputs use a resting border lighter than `hairline` (use `border-slate-600`) to clear contrast, plus `focus:ring-chart-purple`.
- `verifyOtp` stays in the `BrowserAuthClient` interface (unused by UI). Do not remove it.
- Test runner (from repo root): `npm --workspace apps/web run test -- <path>` (vitest is configured with `vitest run`, jsdom, setup `./src/test/setup.ts`). Lint: `npm --workspace apps/web run lint`. Typecheck: `npm --workspace apps/web run typecheck`.
- This change does NOT touch the magic-link redirect: the browser client uses Supabase defaults (`detectSessionInUrl: true`), no `emailRedirectTo`, no `/auth/callback` route. Don't add any.

---

## File Structure

- **Create** `apps/web/src/lib/work-email.ts` — `FREE_EMAIL_DOMAINS` + `isWorkEmail`. Pure, no React.
- **Create** `apps/web/src/lib/work-email.test.ts` — unit tests for the helper.
- **Create** `apps/web/src/components/auth/auth-card.tsx` — presentational dark card shell (`<main>` landmark, glow, brand header, `children`).
- **Create** `apps/web/src/components/auth/auth-card.test.tsx` — renders brand + children, decorative logo.
- **Create** `apps/web/src/components/auth/auth-status.tsx` — spinner + label, `role="status"`.
- **Create** `apps/web/src/components/auth/auth-status.test.tsx` — renders label with status role.
- **Rewrite** `apps/web/src/components/auth/login-form.tsx` — single-step magic-link + work-email gate + terms + sent view.
- **Rewrite** `apps/web/src/components/auth/login-form.test.tsx` — magic-link tests.
- **Modify** `apps/web/src/components/org/org-shell.tsx` — wrap every pre-dashboard branch in `AuthCard`/`AuthStatus`, dark styling, new labels, preserve sign-out in membership-error.
- **Modify** `apps/web/src/components/org/org-shell.test.tsx` — update status strings; keep invariants.
- **Modify** `apps/web/src/components/dashboard/dashboard-runtime-shell.integration.test.tsx` — update status strings if asserted.

---

## Task 1: Work-email validation helper

**Files:**
- Create: `apps/web/src/lib/work-email.ts`
- Test: `apps/web/src/lib/work-email.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `export function isWorkEmail(email: string): boolean` and `export const FREE_EMAIL_DOMAINS: ReadonlySet<string>`.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/lib/work-email.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { isWorkEmail } from "./work-email";

describe("isWorkEmail", () => {
  it("accepts a company domain", () => {
    expect(isWorkEmail("kyle@greybeam.ai")).toBe(true);
  });

  it("rejects common free providers", () => {
    for (const email of [
      "a@gmail.com",
      "a@googlemail.com",
      "a@yahoo.com",
      "a@outlook.com",
      "a@hotmail.com",
      "a@live.com",
      "a@icloud.com",
      "a@aol.com",
      "a@proton.me",
      "a@protonmail.com",
      "a@gmx.com",
      "a@mail.com",
    ]) {
      expect(isWorkEmail(email)).toBe(false);
    }
  });

  it("is case-insensitive and trims surrounding whitespace", () => {
    expect(isWorkEmail("  Person@GMAIL.com ")).toBe(false);
    expect(isWorkEmail("Person@Greybeam.AI")).toBe(true);
  });

  it("rejects all blocklisted domains in FREE_EMAIL_DOMAINS", () => {
    for (const domain of [
      "yahoo.co.uk",
      "msn.com",
      "me.com",
      "mac.com",
      "zoho.com",
      "yandex.com",
      "qq.com",
      "163.com",
    ]) {
      expect(isWorkEmail(`a@${domain}`)).toBe(false);
    }
  });

  it("rejects empty or malformed input", () => {
    for (const value of [
      "",
      "  ",
      "no-at-sign",
      "a@",
      "@b.com",
      "a@@b.com",
      "a@.com",
      "a@b.",
      "a@b..com",
      "a@b",
    ]) {
      expect(isWorkEmail(value)).toBe(false);
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --workspace apps/web run test -- src/lib/work-email.test.ts`
Expected: FAIL — cannot resolve `./work-email`.

- [ ] **Step 3: Write minimal implementation**

Create `apps/web/src/lib/work-email.ts`:

```ts
// Consumer email providers we reject so sign-ups use a work address. Blocklist
// (not allowlist): block known free providers rather than trying to prove a
// domain belongs to a real company — good enough for a lead-magnet gate. This is
// a client-side check only; server-side enforcement is a tracked follow-up.
export const FREE_EMAIL_DOMAINS: ReadonlySet<string> = new Set([
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
  "163.com",
]);

// True only for a syntactically plausible email whose domain is not a known free
// provider. The pattern requires a non-empty local part, exactly one "@", and a
// dotted domain whose labels are each non-empty — so malformed forms like
// "a@.com", "a@b.", and "a@b..com" are rejected, not just the no-"@" cases.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/;

export function isWorkEmail(email: string): boolean {
  const normalized = email.trim().toLowerCase();
  if (!EMAIL_PATTERN.test(normalized)) {
    return false;
  }
  const domain = normalized.slice(normalized.indexOf("@") + 1);
  return !FREE_EMAIL_DOMAINS.has(domain);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --workspace apps/web run test -- src/lib/work-email.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/work-email.ts apps/web/src/lib/work-email.test.ts
git commit -m "feat(auth): add work-email validation helper"
```

---

## Task 2: AuthCard shell

**Files:**
- Create: `apps/web/src/components/auth/auth-card.tsx`
- Test: `apps/web/src/components/auth/auth-card.test.tsx`

**Interfaces:**
- Consumes: nothing (presentational).
- Produces: `export default function AuthCard({ children }: { children: React.ReactNode }): JSX.Element`. Renders a `<main>` landmark, a decorative logo (`alt=""`), the `Greybeam` wordmark, and `children` inside the card.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/components/auth/auth-card.test.tsx`:

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import AuthCard from "./auth-card";

afterEach(() => cleanup());

describe("AuthCard", () => {
  it("renders the brand wordmark and its children", () => {
    render(
      <AuthCard>
        <p>card body</p>
      </AuthCard>,
    );
    expect(screen.getByText("Greybeam")).toBeInTheDocument();
    expect(screen.getByText("card body")).toBeInTheDocument();
  });

  it("exposes a main landmark", () => {
    render(
      <AuthCard>
        <p>card body</p>
      </AuthCard>,
    );
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("renders the logo as decorative (empty alt)", () => {
    const { container } = render(
      <AuthCard>
        <p>card body</p>
      </AuthCard>,
    );
    const logo = container.querySelector('img[src="/greybeam_assets/greybeam_logo.svg"]');
    expect(logo).not.toBeNull();
    expect(logo?.getAttribute("alt")).toBe("");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --workspace apps/web run test -- src/components/auth/auth-card.test.tsx`
Expected: FAIL — cannot resolve `./auth-card`.

- [ ] **Step 3: Write minimal implementation**

Create `apps/web/src/components/auth/auth-card.tsx`:

```tsx
"use client";

import type { ReactNode } from "react";

// Subtle brand glow behind the card: a purple wash toward the top and a lime
// wash toward the bottom, both low-opacity. Literal hex (rgba) is required here
// because Tailwind has no alpha token for these brand colors. Kept faint so the
// card stays the focus.
const GLOW_BACKGROUND =
  "radial-gradient(60% 50% at 30% 15%, rgba(159, 87, 231, 0.14), transparent 70%)," +
  "radial-gradient(55% 45% at 75% 90%, rgba(201, 233, 48, 0.10), transparent 70%)";

export default function AuthCard({ children }: { children: ReactNode }) {
  return (
    <main className="dark relative flex min-h-screen items-center justify-center overflow-hidden bg-canvas p-6 [color-scheme:dark]">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{ background: GLOW_BACKGROUND }}
      />
      <section className="relative w-full max-w-sm rounded-xl border border-hairline bg-surface p-6 shadow-xl">
        <div className="flex flex-col items-center gap-2">
          {/* Static brand mark from /public; next/image would force
              dangerouslyAllowSVG for no benefit. Decorative — the wordmark below
              already names the brand to assistive tech. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            alt=""
            className="h-10 w-10 rounded-md"
            height={40}
            src="/greybeam_assets/greybeam_logo.svg"
            width={40}
          />
          <h1 className="font-display text-xl font-semibold text-slate-50">
            Greybeam
          </h1>
        </div>
        <div className="mt-6">{children}</div>
      </section>
    </main>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --workspace apps/web run test -- src/components/auth/auth-card.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/auth/auth-card.tsx apps/web/src/components/auth/auth-card.test.tsx
git commit -m "feat(auth): add dark AuthCard shell with brand glow"
```

---

## Task 3: AuthStatus block

**Files:**
- Create: `apps/web/src/components/auth/auth-status.tsx`
- Test: `apps/web/src/components/auth/auth-status.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces: `export default function AuthStatus({ label }: { label: string }): JSX.Element`. Renders a `role="status"` `aria-live="polite"` container with an `aria-hidden` spinner and the `label` text.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/components/auth/auth-status.test.tsx`:

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import AuthStatus from "./auth-status";

afterEach(() => cleanup());

describe("AuthStatus", () => {
  it("announces the label via a status region", () => {
    render(<AuthStatus label="Authenticating" />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Authenticating");
    expect(status).toHaveAttribute("aria-live", "polite");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --workspace apps/web run test -- src/components/auth/auth-status.test.tsx`
Expected: FAIL — cannot resolve `./auth-status`.

- [ ] **Step 3: Write minimal implementation**

Create `apps/web/src/components/auth/auth-status.tsx`:

```tsx
"use client";

export default function AuthStatus({ label }: { label: string }) {
  return (
    <div
      aria-live="polite"
      className="flex flex-col items-center gap-3 py-2"
      role="status"
    >
      <span
        aria-hidden
        className="h-6 w-6 animate-spin rounded-full border-2 border-hairline border-t-chart-purple"
      />
      <p className="text-sm text-slate-300">{label}</p>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --workspace apps/web run test -- src/components/auth/auth-status.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/auth/auth-status.tsx apps/web/src/components/auth/auth-status.test.tsx
git commit -m "feat(auth): add AuthStatus spinner/label block"
```

---

## Task 4: LoginForm — single-step magic-link rewrite

**Files:**
- Rewrite: `apps/web/src/components/auth/login-form.tsx`
- Rewrite: `apps/web/src/components/auth/login-form.test.tsx`

**Interfaces:**
- Consumes: `isWorkEmail` from `../../lib/work-email` (Task 1); `BrowserAuthClient` type from `../../lib/supabase-client`.
- Produces: `export default function LoginForm({ authClient }: { authClient: BrowserAuthClient | null })`. Unchanged prop shape — `org-shell` (Task 5) renders `<LoginForm authClient={authClient} />` inside `AuthCard`. LoginForm renders ONLY the card body (no outer card/border of its own — `AuthCard` provides it).

- [ ] **Step 1: Write the failing test**

Replace the entire contents of `apps/web/src/components/auth/login-form.test.tsx`:

```tsx
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

  it("sends a magic link for a work email and shows the check-email confirmation", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    await waitFor(() => {
      expect(signInWithOtp).toHaveBeenCalledWith({ email: "owner@greybeam.ai" });
    });
    expect(await screen.findByText("Check your email")).toBeInTheDocument();
    expect(screen.getByText(/owner@greybeam\.ai/)).toBeInTheDocument();
  });

  it("rejects a free-provider email without calling the client", async () => {
    const signInWithOtp = vi.fn();
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "person@gmail.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Please use your work email.",
    );
    expect(signInWithOtp).not.toHaveBeenCalled();
  });

  it("does not surface provider wording verbatim on a send error", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({
      error: { message: "Email login is unavailable (internal code 500)" },
    });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).not.toHaveTextContent("internal code 500");
  });

  it("shows a friendly rate-limit message", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({
      error: { message: "Email rate limit exceeded" },
    });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many requests. Please wait a moment and try again.",
    );
  });

  it("shows a generic error and re-enables submit when sending rejects", async () => {
    const signInWithOtp = vi.fn().mockRejectedValue(new Error("network down"));
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Email link" }),
      ).not.toBeDisabled(),
    );
  });

  it("can reset to send to a different email", async () => {
    render(<LoginForm authClient={authClient()} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));
    await screen.findByText("Check your email");

    fireEvent.click(
      screen.getByRole("button", { name: "Send to a different email" }),
    );

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("links to the terms of service on the request step", () => {
    render(<LoginForm authClient={authClient()} />);
    const link = screen.getByRole("link", { name: /terms of service/i });
    expect(link).toHaveAttribute("href", "https://www.greybeam.ai/terms");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --workspace apps/web run test -- src/components/auth/login-form.test.tsx`
Expected: FAIL — old `LoginForm` has no "Email link" button / "Check your email" text.

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `apps/web/src/components/auth/login-form.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { isWorkEmail } from "../../lib/work-email";
import type { BrowserAuthClient } from "../../lib/supabase-client";

type LoginFormProps = {
  authClient: BrowserAuthClient | null;
};

const TERMS_URL = "https://www.greybeam.ai/terms";
const GENERIC_ERROR = "Something went wrong. Please try again.";
const RATE_LIMIT_ERROR = "Too many requests. Please wait a moment and try again.";
const WORK_EMAIL_ERROR = "Please use your work email.";

// Never surface provider/internal wording verbatim. Recognize the one
// user-actionable case (rate limiting) and fall back to the generic message for
// everything else.
function friendlyAuthError(message?: string | null): string {
  if (message && /rate limit|too many/i.test(message)) {
    return RATE_LIMIT_ERROR;
  }
  return GENERIC_ERROR;
}

export default function LoginForm({ authClient }: LoginFormProps) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const sentHeadingRef = useRef<HTMLHeadingElement>(null);

  // Move focus to the confirmation heading when the sent view appears so
  // keyboard / screen-reader users land on the new content.
  useEffect(() => {
    if (sent) {
      sentHeadingRef.current?.focus();
    }
  }, [sent]);

  async function requestLink(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!authClient) {
      setError("Authentication is not configured.");
      return;
    }
    const trimmed = email.trim();
    if (!isWorkEmail(trimmed)) {
      setError(WORK_EMAIL_ERROR);
      return;
    }
    setPending(true);
    try {
      const result = await authClient.signInWithOtp({ email: trimmed });
      if (result.error) {
        setError(friendlyAuthError(result.error.message));
        return;
      }
      setSent(true);
    } catch {
      setError(GENERIC_ERROR);
    } finally {
      setPending(false);
    }
  }

  function resetEmail() {
    setSent(false);
    setError(null);
  }

  if (sent) {
    return (
      <div className="space-y-4 text-center">
        <h2
          className="text-base font-semibold text-slate-50 focus:outline-none"
          ref={sentHeadingRef}
          tabIndex={-1}
        >
          Check your email
        </h2>
        <p className="text-sm text-slate-400">
          We sent a sign-in link to{" "}
          <span className="font-medium text-slate-200">{email.trim()}</span>.
          Click it to finish signing in.
        </p>
        <button
          className="text-sm font-medium text-slate-300 underline hover:text-slate-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-chart-purple"
          onClick={resetEmail}
          type="button"
        >
          Send to a different email
        </button>
      </div>
    );
  }

  return (
    <form className="space-y-4" onSubmit={requestLink}>
      <div className="space-y-2">
        <label className="text-sm font-medium text-slate-300" htmlFor="email">
          Email
        </label>
        <input
          autoComplete="email"
          className="w-full rounded-md border border-slate-600 bg-canvas px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-chart-purple focus:outline-none focus:ring-1 focus:ring-chart-purple"
          id="email"
          name="email"
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@company.com"
          required
          type="email"
          value={email}
        />
      </div>
      <button
        className="w-full rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chart-purple focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-60"
        disabled={pending}
        type="submit"
      >
        {pending ? "Sending link" : "Email link"}
      </button>
      {error ? (
        <p className="text-sm font-medium text-red-400" role="alert">
          {error}
        </p>
      ) : null}
      {/* TODO: the published Terms of Service at greybeam.ai/terms needs an
          update; this links to the current page for now. */}
      <p className="text-center text-xs text-slate-500">
        By continuing you agree to our{" "}
        <a
          className="text-slate-400 underline hover:text-slate-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-chart-purple"
          href={TERMS_URL}
          rel="noopener noreferrer"
          target="_blank"
        >
          Terms of Service
        </a>
        .
      </p>
    </form>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --workspace apps/web run test -- src/components/auth/login-form.test.tsx`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/auth/login-form.tsx apps/web/src/components/auth/login-form.test.tsx
git commit -m "feat(auth): single-step magic-link login with work-email gate and terms"
```

---

## Task 5: OrgShell — wire every pre-dashboard state into the dark card

**Files:**
- Modify: `apps/web/src/components/org/org-shell.tsx`
- Modify: `apps/web/src/components/org/org-shell.test.tsx`
- Modify: `apps/web/src/components/dashboard/dashboard-runtime-shell.integration.test.tsx`

**Interfaces:**
- Consumes: `AuthCard` (Task 2, default export), `AuthStatus` (Task 3, default export), rewritten `LoginForm` (Task 4). All existing `OrgShell` props/behavior unchanged.
- Produces: no new exports. Pre-dashboard branches now render through `AuthCard`; status strings are `Authenticating` and `Loading workspace`.

- [ ] **Step 1: Update the tests first (failing)**

In `apps/web/src/components/org/org-shell.test.tsx`, update the two SSR-determinism assertions from `"Loading authentication"` to `"Authenticating"`:

Find both occurrences:
```ts
      expect(markup).toContain("Loading authentication");
```
and replace each with:
```ts
      expect(markup).toContain("Authenticating");
```

Then add two new assertions near the existing membership/loading tests (place inside the top-level `describe("OrgShell", ...)` block, e.g. after the SSR tests):

```ts
  it("shows the login form inside the dark brand card when there is no session", async () => {
    render(
      <OrgShell authClient={authClient(null)} authRequired>
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(await screen.findByRole("button", { name: "Email link" })).toBeInTheDocument();
    // The "Greybeam" wordmark is rendered only by AuthCard, so asserting it
    // proves the login form is wrapped in the new card (the old shell rendered
    // no wordmark). This fails before Task 5's wiring and passes after.
    expect(screen.getByText("Greybeam")).toBeInTheDocument();
  });

  it("shows the workspace-loading status while memberships resolve", async () => {
    let resolveMemberships: (orgs: MembershipOrganization[]) => void = () => {};
    const fetchMemberships = vi.fn(
      () =>
        new Promise<MembershipOrganization[]>((resolve) => {
          resolveMemberships = resolve;
        }),
    );
    render(
      <OrgShell
        authClient={authClient(session)}
        authRequired
        fetchMemberships={fetchMemberships}
      >
        <p>dashboard</p>
      </OrgShell>,
    );
    expect(await screen.findByText("Loading workspace")).toBeInTheDocument();
    resolveMemberships([]);
  });
```

In the membership-error test, after `await screen.findByText(/couldn’t load your organizations/i)`, add an assertion that sign-out is still present:
```ts
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
```

In `apps/web/src/components/dashboard/dashboard-runtime-shell.integration.test.tsx`, if it asserts the string `"Loading authentication"`, replace those occurrences with `"Authenticating"`. (Grep first: `grep -n "Loading authentication" apps/web/src/components/dashboard/dashboard-runtime-shell.integration.test.tsx` — if no matches, no change needed here.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --workspace apps/web run test -- src/components/org/org-shell.test.tsx`
Expected: FAIL — markup contains "Loading authentication" not "Authenticating"; no "Email link" / "Loading workspace" / role main yet.

- [ ] **Step 3: Implement the OrgShell changes**

In `apps/web/src/components/org/org-shell.tsx`:

3a. Add imports near the existing component imports (after the `LoginForm` import):
```ts
import AuthCard from "../auth/auth-card";
import AuthStatus from "../auth/auth-status";
```

3b. Replace the `!authClientResolved` branch:
```tsx
  if (!authClientResolved) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <p className="text-sm text-slate-600">Loading authentication</p>
      </main>
    );
  }
```
with:
```tsx
  if (!authClientResolved) {
    return (
      <AuthCard>
        <AuthStatus label="Authenticating" />
      </AuthCard>
    );
  }
```

3c. Replace the `!authClient` (not configured) branch:
```tsx
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
```
with:
```tsx
  if (!authClient) {
    return (
      <AuthCard>
        <div className="space-y-2 text-center">
          <h2 className="text-base font-semibold text-slate-50">
            Authentication is not configured
          </h2>
          <p className="text-sm text-slate-400">
            Set public Supabase URL and anon key to enable login.
          </p>
        </div>
      </AuthCard>
    );
  }
```

3d. Replace the `loadingSession` branch:
```tsx
  if (loadingSession) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <p className="text-sm text-slate-600">Loading authentication</p>
      </main>
    );
  }
```
with:
```tsx
  if (loadingSession) {
    return (
      <AuthCard>
        <AuthStatus label="Authenticating" />
      </AuthCard>
    );
  }
```

3e. Replace the `!session` (login) branch:
```tsx
  if (!session) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <LoginForm authClient={authClient} />
      </main>
    );
  }
```
with:
```tsx
  if (!session) {
    return (
      <AuthCard>
        <LoginForm authClient={authClient} />
      </AuthCard>
    );
  }
```

3f. Replace the membership idle/loading branch:
```tsx
  if (membership.status === "idle" || membership.status === "loading") {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <p className="text-sm text-slate-600">Loading your workspace</p>
      </main>
    );
  }
```
with:
```tsx
  if (membership.status === "idle" || membership.status === "loading") {
    return (
      <AuthCard>
        <AuthStatus label="Loading workspace" />
      </AuthCard>
    );
  }
```

3g. Replace the membership error branch (preserving the signed-in header + Sign out + Retry, restyled dark):
```tsx
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
```
with:
```tsx
  if (membership.status === "error") {
    return (
      <AuthCard>
        <div className="space-y-4">
          {signedInHeader}
          <p className="text-sm text-red-400" role="alert">
            We couldn’t load your organizations. Please try again.
          </p>
          <button
            className="w-full rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chart-purple focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
            onClick={() => accessToken && void loadMemberships(accessToken)}
            type="button"
          >
            Retry
          </button>
        </div>
      </AuthCard>
    );
  }
```

Leave the `signedInHeader` definition, the `membership.organizations.length === 0` (ConnectWizard) branch, the final `AccountChromeProvider` branch, and the `!authRequired` demo-banner branch unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm --workspace apps/web run test -- src/components/org/org-shell.test.tsx`
Expected: PASS (existing tests + the new login-card, workspace-loading, and sign-out-present assertions).

Then run the integration test:
Run: `npm --workspace apps/web run test -- src/components/dashboard/dashboard-runtime-shell.integration.test.tsx`
Expected: PASS.

- [ ] **Step 5: Full check — suite, lint, types**

Run: `npm --workspace apps/web run test`
Expected: PASS (whole web suite green).

Run: `npm --workspace apps/web run lint && npm --workspace apps/web run typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/org/org-shell.tsx apps/web/src/components/org/org-shell.test.tsx apps/web/src/components/dashboard/dashboard-runtime-shell.integration.test.tsx
git commit -m "feat(auth): render login and loading/error states in the dark AuthCard"
```

---

## Final verification (after all tasks)

- [ ] Run the whole web suite once more: `npm --workspace apps/web run test`
- [ ] Lint + typecheck clean: `npm --workspace apps/web run lint && npm --workspace apps/web run typecheck`
- [ ] Manual visual check (Kyle verifies in browser): with `AUTH_REQUIRED=true` + Supabase env set, load the app → centered dark card, logo + "Greybeam", email + "Email link", terms link, subtle purple/lime glow. Submit a work email → "Check your email". Submit a gmail address → "Please use your work email." Confirm the loading transitions read "Authenticating" then "Loading workspace".

## Notes carried from the spec (do not implement here)

- Server-side work-email enforcement, `emailRedirectTo`/callback routing, expired/reused-link UI, resend-with-cooldown + rate-limit flow, and dropping the unused `verifyOtp` are tracked follow-ups — out of scope for this plan.
