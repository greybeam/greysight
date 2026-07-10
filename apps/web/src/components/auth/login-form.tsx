"use client";

import { useEffect, useRef, useState } from "react";
import { isWorkEmail } from "../../lib/work-email";
import type { BrowserAuthClient } from "../../lib/supabase-client";
import { GENERIC_ERROR, friendlyAuthError } from "./auth-errors";
import CodeSignIn from "./code-sign-in";

type LoginFormProps = {
  authClient: BrowserAuthClient | null;
};

const TERMS_URL = "https://www.greybeam.ai/terms";
const WORK_EMAIL_ERROR = "Please use your work email.";

export default function LoginForm({ authClient }: LoginFormProps) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [sentEmail, setSentEmail] = useState("");
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
      setSentEmail(trimmed);
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
          Enter the code we sent to{" "}
          <span className="font-medium text-slate-200">{sentEmail}</span>.
        </p>
        {authClient ? (
          <CodeSignIn authClient={authClient} email={sentEmail} />
        ) : null}
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
      <label className="sr-only" htmlFor="email">
        Email
      </label>
      <div className="flex gap-2">
        <input
          autoComplete="email"
          className="flex-1 rounded-md border border-slate-600 bg-canvas px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-chart-purple focus:outline-none focus:ring-1 focus:ring-chart-purple"
          disabled={pending}
          id="email"
          name="email"
          onChange={(event) => setEmail(event.target.value)}
          placeholder="your@work-email.com"
          required
          type="email"
          value={email}
        />
        <button
          className="shrink-0 whitespace-nowrap rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chart-purple focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-60"
          disabled={pending}
          type="submit"
        >
          {pending ? "Sending code" : "Email me a code"}
        </button>
      </div>
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
