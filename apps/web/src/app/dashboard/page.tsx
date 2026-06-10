import DashboardRuntimeShell from "../../components/dashboard/dashboard-runtime-shell";
import { getAuthMode } from "../../lib/auth-mode";

function getDashboardDataSource(): "demo" | "snowflake" {
  return process.env.DATA_SOURCE === "snowflake" ? "snowflake" : "demo";
}

export default function DashboardPage() {
  const { authRequired } = getAuthMode({
    AUTH_REQUIRED: process.env.AUTH_REQUIRED,
    NEXT_PUBLIC_AUTH_REQUIRED: process.env.NEXT_PUBLIC_AUTH_REQUIRED,
  });

  return (
    <DashboardRuntimeShell
      authRequired={authRequired}
      dataSource={getDashboardDataSource()}
    />
  );
}
