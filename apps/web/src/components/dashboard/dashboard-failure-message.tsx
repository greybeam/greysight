import { DASHBOARD_ISSUE_URL } from "../../lib/dashboard-errors";

export default function DashboardFailureMessage({
  message,
  reportable,
}: {
  message: string;
  reportable: boolean;
}) {
  return (
    <>
      {message}
      {reportable ? (
        <>
          {" "}
          <a
            className="underline underline-offset-2 hover:text-slate-100"
            href={DASHBOARD_ISSUE_URL}
            rel="noreferrer"
            target="_blank"
          >
            Report this issue
          </a>
        </>
      ) : null}
    </>
  );
}
