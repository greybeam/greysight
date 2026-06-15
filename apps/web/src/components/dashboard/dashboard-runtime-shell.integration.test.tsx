import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchDashboardView,
  pollDashboardRun,
  startDashboardRun,
} from "../../lib/dashboard-api";
import demoDashboardView from "../../lib/demo-dashboard-view";
import { FETCH_WINDOW_DAYS } from "../../lib/dashboard-contracts";
import type { AuthSession, SessionChangeCallback } from "../../lib/supabase-client";
import DashboardRuntimeShell from "./dashboard-runtime-shell";

vi.mock("../../lib/dashboard-api", () => ({
  fetchDashboardView: vi.fn(),
  fetchDemoDashboardView: vi.fn(),
  pollDashboardRun: vi.fn(),
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
    vi.mocked(pollDashboardRun).mockResolvedValue({
      id: "run-1",
      source: "snowflake",
      status: "completed",
      window_days: FETCH_WINDOW_DAYS,
    });
    vi.mocked(fetchDashboardView).mockResolvedValue(demoDashboardView);

    render(<DashboardRuntimeShell authRequired dataSource="snowflake" />);

    // Wait for the live membership lookup to resolve and select the org so the
    // authenticated Snowflake runtime is in place (CostDashboard remounts on the
    // demo -> snowflake key transition).
    await screen.findByText("Sign out");
    const runButton = await screen.findByRole("button", { name: "Run analysis" });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    await waitFor(() => expect(startDashboardRun).toHaveBeenCalled());

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
