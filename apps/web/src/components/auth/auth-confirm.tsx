"use client";

import { useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import createBrowserAuthClient, {
  type AuthError,
  type BrowserAuthClient,
} from "../../lib/supabase-client";
import AuthCard from "./auth-card";
import AuthStatus from "./auth-status";

type AuthConfirmProps = {
  authClient?: BrowserAuthClient | null;
};

type ConfirmState = "idle" | "pending" | "expired" | "generic-error";

const EXPIRED_COPY =
  "It may have already been used, or opened by your email security software. Request a new link to sign in.";
const GENERIC_COPY =
  "Something went wrong signing you in. Please try again.";

// Distinguish a consumed/expired single-use token (the user should request a
// fresh link) from a transient network/config failure (the user can retry).
// Prefer the structured code/status the provider returns; fall back to matching
// the message only when no code is present.
function isExpiredOtpError(error: AuthError): boolean {
  const code = error.code?.toLowerCase() ?? "";
  if (code) {
    return /otp_expired|otp_disabled|invalid|expired|token/.test(code);
  }
  if (error.status === 401 || error.status === 403) {
    return true;
  }
  return /expired|invalid|token/i.test(error.message ?? "");
}

export default function AuthConfirm({
  authClient = createBrowserAuthClient(),
}: AuthConfirmProps) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const tokenHash = searchParams.get("token_hash") ?? "";
  const type = searchParams.get("type") ?? "email";

  const [state, setState] = useState<ConfirmState>(
    tokenHash && authClient ? "idle" : "expired",
  );
  const verifyingRef = useRef(false);

  async function confirm() {
    if (!tokenHash || !authClient || verifyingRef.current) {
      return;
    }
    verifyingRef.current = true;
    setState("pending");

    try {
      const result = await authClient.verifyEmailOtp({ tokenHash, type });
      if (result.error) {
        console.error(
          `auth/confirm verifyEmailOtp failed (code=${result.error.code ?? "unknown"})`,
          result.error,
        );
        setState(isExpiredOtpError(result.error) ? "expired" : "generic-error");
        verifyingRef.current = false;
        return;
      }
      router.replace("/dashboard");
    } catch (error) {
      console.error("auth/confirm verifyEmailOtp threw", error);
      setState("generic-error");
      verifyingRef.current = false;
    }
  }

  if (state === "expired" || state === "generic-error") {
    return (
      <AuthCard>
        <div className="space-y-3 text-center">
          <h2 className="text-base font-semibold text-slate-50">
            {state === "expired"
              ? "This sign-in link has expired"
              : "We couldn't sign you in"}
          </h2>
          <p className="text-sm text-slate-400">
            {state === "expired" ? EXPIRED_COPY : GENERIC_COPY}
          </p>
          <a
            className="inline-block text-sm font-medium text-slate-300 underline hover:text-slate-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-chart-purple"
            href="/dashboard"
          >
            Return to sign in
          </a>
        </div>
      </AuthCard>
    );
  }

  if (state === "pending") {
    return (
      <AuthCard>
        <AuthStatus label="Signing you in…" />
      </AuthCard>
    );
  }

  return (
    <AuthCard>
      <div className="space-y-4 text-center">
        <h2 className="text-base font-semibold text-slate-50">
          Confirm your sign-in
        </h2>
        <p className="text-sm text-slate-400">
          Click below to finish signing in to Greybeam.
        </p>
        <button
          className="w-full rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chart-purple focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-60"
          onClick={() => void confirm()}
          type="button"
        >
          Confirm email address
        </button>
      </div>
    </AuthCard>
  );
}
