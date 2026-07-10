"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { BrowserAuthClient } from "../../lib/supabase-client";
import { friendlyAuthError } from "./auth-errors";

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
// A code was just sent when this view mounts, so start the resend cooldown
// immediately to discourage a burst of duplicate sends.
const RESEND_COOLDOWN_SECONDS = 60;
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

  // Resend flow: countdown gates the button, and a separate in-flight guard
  // prevents double submits. `resendError`/`resendNotice` are mutually
  // exclusive user feedback near the input.
  // A code was just sent, so the cooldown deadline is one full window from now.
  // Deriving remaining time from a wall-clock deadline (not accumulated ticks)
  // keeps the countdown honest when the tab is backgrounded or the device
  // sleeps — the interval below only forces re-renders and self-corrects.
  const [cooldownUntil, setCooldownUntil] = useState(
    () => Date.now() + RESEND_COOLDOWN_SECONDS * 1000,
  );
  const [remaining, setRemaining] = useState(RESEND_COOLDOWN_SECONDS);
  const [resending, setResending] = useState(false);
  const [resendError, setResendError] = useState<string | null>(null);
  const [resendNotice, setResendNotice] = useState<string | null>(null);
  // Ref mirror of `resending` so the verify/auto-submit guards read the live
  // value rather than a stale closure. The state drives disabled props.
  const resendingRef = useRef(false);

  // Recompute `remaining` from the wall-clock deadline (never accumulated
  // ticks), so a backgrounded/throttled tab self-corrects: the interval clears
  // at zero and on unmount, and a "visibilitychange" recompute snaps the count
  // to the truth the instant the tab is refocused. `Date.now()` stays inside
  // these callbacks — never in render — to keep render pure.
  useEffect(() => {
    function recompute(): number {
      const next = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
      setRemaining(next);
      return next;
    }
    if (recompute() <= 0) {
      return;
    }
    const timer = setInterval(() => {
      if (recompute() <= 0) {
        clearInterval(timer);
      }
    }, 1000);
    const onVisible = () => recompute();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [cooldownUntil]);

  async function resend() {
    // No resend while a resend or a verify is already in flight, or while the
    // cooldown is still running.
    if (resendingRef.current || submittingRef.current || remaining > 0) {
      return;
    }
    resendingRef.current = true;
    setResending(true);
    setResendError(null);
    setResendNotice(null);
    try {
      const result = await authClient.signInWithOtp({ email });
      if (result.error) {
        // Do not restart the cooldown on failure so the user can retry at once.
        setResendError(friendlyAuthError(result.error.message));
        return;
      }
      setResendNotice(`New code sent to ${email}`);
      // A fresh code invalidates whatever was typed: clear the box, drop any
      // stale verify error, and reset the auto-submit dedupe so the same digits
      // aren't left ready to resubmit.
      setError(null);
      setCode("");
      lastAutoSubmittedRef.current = null;
      setCooldownUntil(Date.now() + RESEND_COOLDOWN_SECONDS * 1000);
    } catch {
      setResendError(friendlyAuthError());
    } finally {
      resendingRef.current = false;
      setResending(false);
    }
  }

  async function verify(token: string) {
    // Symmetric with `resend()`: never race a verify against an in-flight
    // resend (or another verify).
    if (submittingRef.current || resendingRef.current) {
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
    // some clients paste as literal spaces. Truncate to the max code length
    // only after stripping, so a spaced paste (e.g. "12345 67890") isn't cut
    // short before normalization.
    const cleaned = event.target.value
      .replace(/\s+/g, "")
      .slice(0, CODE_MAX_LENGTH);
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
    // Don't race the primary submit against an in-flight resend (or verify).
    if (submittingRef.current || resendingRef.current) {
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
          disabled={pending || resending}
          id="code"
          inputMode="numeric"
          name="code"
          onChange={handleChange}
          placeholder="Enter code"
          value={code}
        />
        <button
          className="shrink-0 whitespace-nowrap rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chart-purple focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-60"
          disabled={pending || resending}
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
      <div className="flex flex-col gap-2">
        <button
          className="self-start text-sm font-medium text-slate-300 underline hover:text-slate-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-chart-purple disabled:cursor-default disabled:text-slate-500 disabled:no-underline disabled:opacity-60"
          disabled={remaining > 0 || resending || pending}
          onClick={() => void resend()}
          type="button"
        >
          {remaining > 0 ? `Resend code in ${remaining}s` : "Resend code"}
        </button>
        {resendNotice ? (
          <p
            aria-live="polite"
            className="text-sm font-medium text-slate-300"
            role="status"
          >
            {resendNotice}
          </p>
        ) : null}
        {resendError ? (
          <p className="text-sm font-medium text-red-400" role="alert">
            {resendError}
          </p>
        ) : null}
      </div>
    </form>
  );
}
