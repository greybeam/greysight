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
    try {
      const result = await authClient.signInWithOtp({ email: email.trim() });
      if (result.error) {
        setError(result.error.message);
        return;
      }
      setCode("");
      setStep("verify");
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
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
    try {
      const result = await authClient.verifyOtp({
        email: email.trim(),
        token: code.trim(),
      });
      if (result.error) {
        setError(result.error.message);
      }
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
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
