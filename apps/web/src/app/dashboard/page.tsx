import CostDashboard from "../../components/dashboard/cost-dashboard";

export default function DashboardPage() {
  const dataSource = process.env.DATA_SOURCE === "snowflake" ? "snowflake" : "demo";

  return <CostDashboard dataSource={dataSource} />;
}
