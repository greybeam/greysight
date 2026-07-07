import { Suspense } from "react";
import AuthConfirm from "../../../components/auth/auth-confirm";
import AuthCard from "../../../components/auth/auth-card";
import AuthStatus from "../../../components/auth/auth-status";

export default function ConfirmPage() {
  return (
    <Suspense
      fallback={
        <AuthCard>
          <AuthStatus label="Confirming sign-in…" />
        </AuthCard>
      }
    >
      <AuthConfirm />
    </Suspense>
  );
}
