import DashboardRuntimeShell from "../../components/dashboard/dashboard-runtime-shell";
import { getAuthMode } from "../../lib/auth-mode";

export default function DashboardPage() {
  const { authRequired } = getAuthMode({
    AUTH_REQUIRED: process.env.AUTH_REQUIRED,
  });

  return <DashboardRuntimeShell authRequired={authRequired} />;
}
