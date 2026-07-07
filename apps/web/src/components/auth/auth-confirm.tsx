"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import createBrowserAuthClient, {
  type BrowserAuthClient,
} from "../../lib/supabase-client";
import AuthCard from "./auth-card";
import AuthStatus from "./auth-status";

type AuthConfirmProps = {
  authClient?: BrowserAuthClient | null;
};

export default function AuthConfirm({
  authClient = createBrowserAuthClient(),
}: AuthConfirmProps) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const tokenHash = searchParams.get("token_hash") ?? "";
  const type = searchParams.get("type") ?? "email";

  const [state, setState] = useState<"pending" | "error">(
    tokenHash && authClient ? "pending" : "error",
  );
  const verifiedRef = useRef(false);

  useEffect(() => {
    if (!tokenHash || verifiedRef.current || !authClient) {
      return;
    }
    verifiedRef.current = true;

    void (async () => {
      try {
        const result = await authClient.verifyEmailOtp({ tokenHash, type });
        if (result.error) {
          setState("error");
        } else {
          router.replace("/dashboard");
        }
      } catch {
        setState("error");
      }
    })();
  }, [authClient, router, tokenHash, type]);

  if (state === "error") {
    return (
      <AuthCard>
        <div className="space-y-3 text-center">
          <h2 className="text-base font-semibold text-slate-50">
            This sign-in link has expired
          </h2>
          <p className="text-sm text-slate-400">
            It may have already been used, or opened by your email security
            software. Request a new link to sign in.
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

  return (
    <AuthCard>
      <AuthStatus label="Confirming sign-in…" />
    </AuthCard>
  );
}
