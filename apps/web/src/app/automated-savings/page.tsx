import { AutomatedSavingsShell } from "../../components/automated-savings/automated-savings-shell";
import { getAuthMode } from "../../lib/auth-mode";

export default function AutomatedSavingsPage() {
  const { authRequired } = getAuthMode({
    AUTH_REQUIRED: process.env.AUTH_REQUIRED,
    NEXT_PUBLIC_AUTH_REQUIRED: process.env.NEXT_PUBLIC_AUTH_REQUIRED,
  });

  return <AutomatedSavingsShell authRequired={authRequired} />;
}
