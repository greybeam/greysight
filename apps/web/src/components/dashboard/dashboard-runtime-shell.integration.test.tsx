import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchDashboardView,
  pollUntilTerminal,
  startDashboardRun,
} from "../../lib/dashboard-api";
import demoDashboardView from "../../lib/demo-dashboard-view";
import {
  FETCH_WINDOW_DAYS,
  type DashboardView,
} from "../../lib/dashboard-contracts";
import type { AuthSession, SessionChangeCallback } from "../../lib/supabase-client";
import DashboardRuntimeShell from "./dashboard-runtime-shell";

vi.mock("../../lib/dashboard-api", () => ({
  fetchDashboardView: vi.fn(),
  fetchDemoDashboardView: vi.fn(),
  pollUntilTerminal: vi.fn(),
  startDashboardRun: vi.fn(),
}));

vi.mock("../../lib/session-memberships", () => ({
  fetchSessionMemberships: vi
    .fn()
    .mockResolvedValue([
      { id: "22222222-2222-4222-8222-222222222222", name: "Acme Analytics" },
    ]),
}));

const session: AuthSession = {
  accessToken: "test-access-token",
  user: {
    email: "owner@example.com",
    appMetadata: null,
  },
};

vi.mock("../../lib/supabase-client", () => ({
  default: vi.fn(() => ({
    getSession: vi.fn().mockResolvedValue({ session, error: null }),
    onAuthStateChange: vi.fn((callback: SessionChangeCallback) => {
      callback(session);
      return { unsubscribe: vi.fn() };
    }),
    signInWithOtp: vi.fn(),
    verifyOtp: vi.fn(),
    signOut: vi.fn(),
  })),
}));

describe("DashboardRuntimeShell integration", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("starts runs with the resolved membership organization id", async () => {
    vi.mocked(startDashboardRun).mockResolvedValue({
      id: "run-1",
      source: "snowflake",
      status: "queued",
      window_days: FETCH_WINDOW_DAYS,
    });
    const completedView: DashboardView = {
      ...demoDashboardView,
      run: {
        ...demoDashboardView.run,
        id: "run-1",
        source: "snowflake",
        status: "completed",
      },
    };
    vi.mocked(fetchDashboardView).mockResolvedValue(completedView);
    // Drive the progressive `/view` poll: fetch once (so fetchDashboardView is
    // invoked with run-1), surface it, then resolve as the terminal view.
    vi.mocked(pollUntilTerminal).mockImplementation(
      async (fetcher, _isTerminal, options) => {
        const result = (await fetcher()) as DashboardView;
        options?.onResult?.(result);
        return completedView as never;
      },
    );

    render(<DashboardRuntimeShell authRequired dataSource="snowflake" />);

    // Wait for the live membership lookup to resolve and select the org so the
    // authenticated Snowflake runtime is in place (CostDashboard remounts on the
    // demo -> snowflake key transition). Never hold a button reference across the
    // remount: query it fresh inside each waitFor / at click time so a stale,
    // detached pre-remount node can't deadlock the wait.
    await screen.findByText("Sign out", undefined, { timeout: 3000 });
    await waitFor(
      () => expect(screen.getByRole("button", { name: "Run analysis" })).toBeEnabled(),
      { timeout: 3000 },
    );
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    await waitFor(() => expect(startDashboardRun).toHaveBeenCalled(), {
      timeout: 3000,
    });

    const [{ organizationId, windowDays }] = vi.mocked(
      startDashboardRun,
    ).mock.calls[0];
    expect(organizationId).toBe("22222222-2222-4222-8222-222222222222");
    expect(windowDays).toBe(FETCH_WINDOW_DAYS);
    expect(fetchDashboardView).toHaveBeenCalledWith(
      "run-1",
      { windowDays: 30 },
      { accessToken: "test-access-token" },
    );
  });
});
