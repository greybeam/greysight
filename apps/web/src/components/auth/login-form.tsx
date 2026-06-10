"use client";

import { useState } from "react";
import type { BrowserAuthClient } from "../../lib/supabase-client";

type LoginFormProps = {
  authClient: BrowserAuthClient | null;
};

export default function LoginForm({ authClient }: LoginFormProps) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);

  async function submitLogin(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSent(false);

    if (!authClient) {
      setError("Authentication is not configured.");
      return;
    }

    setPending(true);
    const result = await authClient.signInWithOtp({
      email: email.trim(),
      options: {
        emailRedirectTo: window.location.href,
      },
    });
    setPending(false);

    if (result.error) {
      setError(result.error.message);
      return;
    }

    setSent(true);
  }

  return (
    <form
      className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
      onSubmit={submitLogin}
    >
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
        className="mt-4 rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800 disabled:bg-slate-400"
        disabled={pending}
        type="submit"
      >
        {pending ? "Sending link" : "Email magic link"}
      </button>
      {sent ? (
        <p className="mt-3 text-sm font-medium text-emerald-700">
          Check your email for the sign-in link.
        </p>
      ) : null}
      {error ? (
        <p className="mt-3 text-sm font-medium text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}
