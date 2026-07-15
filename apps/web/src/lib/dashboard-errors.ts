export const DASHBOARD_ISSUE_URL =
  "https://github.com/greybeam/greysight/issues/new";

export class DashboardApiError extends Error {
  constructor(
    message: string,
    readonly userSafeMessage: string | null = null,
  ) {
    super(message);
    this.name = "DashboardApiError";
  }
}

export function dashboardFailure(error: unknown): {
  message: string;
  reportable: boolean;
} {
  if (error instanceof DashboardApiError && error.userSafeMessage) {
    return { message: error.userSafeMessage, reportable: false };
  }
  return { message: "Could not load dashboard data.", reportable: true };
}
