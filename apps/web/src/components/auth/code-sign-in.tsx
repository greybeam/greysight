"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { BrowserAuthClient } from "../../lib/supabase-client";

type CodeSignInProps = {
  authClient: BrowserAuthClient;
  email: string;
  // Injectable for tests; defaults to the env-configured length.
  expectedCodeLength?: number;
};

// Supabase's Email OTP Length is configurable from 6 to 10 digits. Manual
// submit accepts the whole range so a misconfigured expected length never
// locks the user out; auto-submit fires only at the expected length.
const CODE_MIN_LENGTH = 6;
const CODE_MAX_LENGTH = 10;
const DEFAULT_CODE_LENGTH = 8;
const CODE_PATTERN = /^\d{6,10}$/;
const INVALID_CODE_ERROR = "Enter the code from the email we sent you.";
const VERIFY_ERROR =
  "That code is incorrect or has expired. Request a new code and try again.";

// The expected code length must match Supabase's Email OTP Length for
// auto-submit to fire at the right moment. Invalid/out-of-range values fall
// back to the default rather than breaking sign-in.
export function resolveExpectedCodeLength(
  // Literal `process.env.NEXT_PUBLIC_*` token so Turbopack inlines it into the
  // client bundle (see the note in supabase-client.ts).
  raw: string | undefined = process.env.NEXT_PUBLIC_AUTH_CODE_LENGTH,
): number {
  const parsed = Number.parseInt(raw ?? "", 10);
  if (
    !Number.isInteger(parsed) ||
    parsed < CODE_MIN_LENGTH ||
    parsed > CODE_MAX_LENGTH
  ) {
    return DEFAULT_CODE_LENGTH;
  }
  return parsed;
}

// The primary sign-in flow: the emailed code is entered here and verified
// directly. Chosen over magic links because email security scanners (even
// JS-executing ones) can consume a single-use link; typing a code requires a
// human. The code auto-submits once the expected number of digits is entered.
export default function CodeSignIn({
  authClient,
  email,
  expectedCodeLength = resolveExpectedCodeLength(),
}: CodeSignInProps) {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const submittingRef = useRef(false);
  // A failed auto-submitted value must not loop: remember it and only
  // auto-submit again after the user edits the input.
  const lastAutoSubmittedRef = useRef<string | null>(null);

  async function verify(token: string) {
    if (submittingRef.current) {
      return;
    }
    setError(null);
    submittingRef.current = true;
    setPending(true);
    try {
      const result = await authClient.verifyEmailCode({ email, token });
      if (result.error) {
        setError(VERIFY_ERROR);
        submittingRef.current = false;
        setPending(false);
        return;
      }
      // Success: leave pending set (button stays disabled) through the redirect.
      router.replace("/dashboard");
    } catch {
      setError(VERIFY_ERROR);
      submittingRef.current = false;
      setPending(false);
    }
  }

  function handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    // Strip whitespace: codes copied from the email carry letter-spacing that
    // some clients paste as literal spaces.
    const cleaned = event.target.value.replace(/\s+/g, "");
    setCode(cleaned);

    // Auto-submit once the full expected code is present (typing or paste).
    if (
      cleaned.length === expectedCodeLength &&
      /^\d+$/.test(cleaned) &&
      !submittingRef.current &&
      cleaned !== lastAutoSubmittedRef.current
    ) {
      lastAutoSubmittedRef.current = cleaned;
      void verify(cleaned);
    }
  }

  async function submitCode(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) {
      return;
    }

    const trimmed = code.trim();
    if (!CODE_PATTERN.test(trimmed)) {
      setError(INVALID_CODE_ERROR);
      return;
    }
    await verify(trimmed);
  }

  return (
    <form className="space-y-3 text-left" onSubmit={submitCode}>
      <label className="sr-only" htmlFor="code">
        Sign-in code
      </label>
      <div className="flex gap-2">
        <input
          aria-describedby={error ? "code-error" : undefined}
          aria-invalid={error ? true : undefined}
          autoComplete="one-time-code"
          className="flex-1 rounded-md border border-slate-600 bg-canvas px-3 py-2 text-center text-sm tracking-[0.4em] text-slate-100 placeholder:tracking-normal placeholder:text-slate-500 focus:border-chart-purple focus:outline-none focus:ring-1 focus:ring-chart-purple"
          disabled={pending}
          id="code"
          inputMode="numeric"
          maxLength={CODE_MAX_LENGTH}
          name="code"
          onChange={handleChange}
          placeholder="Enter code"
          value={code}
        />
        <button
          className="shrink-0 whitespace-nowrap rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chart-purple focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-60"
          disabled={pending}
          type="submit"
        >
          {pending ? "Signing in" : "Sign in with code"}
        </button>
      </div>
      {error ? (
        <p className="text-sm font-medium text-red-400" id="code-error" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}
