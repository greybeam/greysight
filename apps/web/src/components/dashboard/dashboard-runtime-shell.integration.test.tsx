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

const session: AuthSession = {
  accessToken: "test-access-token",
  user: {
    email: "owner@example.com",
    appMetadata: {
      organization_ids: ["22222222-2222-4222-8222-222222222222"],
    },
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
    signOut: vi.fn(),
  })),
}));

describe("DashboardRuntimeShell integration", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("starts runs with a UUID organization id for a typed organization name", async () => {
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(
      "11111111-1111-4111-8111-111111111111",
    );
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

    fireEvent.change(await screen.findByLabelText("Organization name"), {
      target: { value: "Acme Analytics" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create organization" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run analysis" }));

    await waitFor(() => expect(startDashboardRun).toHaveBeenCalled());

    const [{ organizationId, windowDays }] = vi.mocked(
      startDashboardRun,
    ).mock.calls[0];
    expect(organizationId).toBe("22222222-2222-4222-8222-222222222222");
    expect(organizationId).not.toBe("Acme Analytics");
    expect(windowDays).toBe(FETCH_WINDOW_DAYS);
    expect(fetchDashboardView).toHaveBeenCalledWith(
      "run-1",
      { windowDays: 30 },
      { accessToken: "test-access-token" },
    );
    expect(screen.getByText("Acme Analytics")).toBeInTheDocument();
  });
});
