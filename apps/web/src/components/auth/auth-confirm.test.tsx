import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AuthConfirm from "./auth-confirm";
import type { BrowserAuthClient } from "../../lib/supabase-client";

// Mock next/navigation before importing the component
const mockReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useSearchParams: vi.fn(() => new URLSearchParams()),
  useRouter: vi.fn(() => ({ replace: mockReplace })),
}));

import { useSearchParams } from "next/navigation";

function authClient(overrides: Partial<BrowserAuthClient> = {}): BrowserAuthClient {
  return {
    getSession: vi.fn(),
    onAuthStateChange: vi.fn(),
    signInWithOtp: vi.fn(),
    verifyOtp: vi.fn(),
    verifyEmailOtp: vi.fn().mockResolvedValue({ error: null }),
    signOut: vi.fn(),
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AuthConfirm", () => {
  it("calls verifyEmailOtp with the token_hash from the URL and redirects to /dashboard on success", async () => {
    const mockVerify = vi.fn().mockResolvedValue({ error: null });
    const client = authClient({ verifyEmailOtp: mockVerify });

    vi.mocked(useSearchParams).mockReturnValue(
      new URLSearchParams("token_hash=abc123&type=email") as unknown as ReturnType<typeof useSearchParams>,
    );

    render(<AuthConfirm authClient={client} />);

    await waitFor(() => {
      expect(mockVerify).toHaveBeenCalledWith({ tokenHash: "abc123", type: "email" });
    });
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("shows the error state when verifyEmailOtp returns an error", async () => {
    const mockVerify = vi.fn().mockResolvedValue({ error: { message: "otp_expired" } });
    const client = authClient({ verifyEmailOtp: mockVerify });

    vi.mocked(useSearchParams).mockReturnValue(
      new URLSearchParams("token_hash=expiredtoken&type=email") as unknown as ReturnType<typeof useSearchParams>,
    );

    render(<AuthConfirm authClient={client} />);

    expect(await screen.findByText(/sign-in link has expired/i)).toBeInTheDocument();
    expect(screen.getByText(/return to sign in/i)).toBeInTheDocument();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("shows the error state and does NOT call verifyEmailOtp when token_hash is missing", () => {
    const mockVerify = vi.fn();
    const client = authClient({ verifyEmailOtp: mockVerify });

    vi.mocked(useSearchParams).mockReturnValue(
      new URLSearchParams("") as unknown as ReturnType<typeof useSearchParams>,
    );

    render(<AuthConfirm authClient={client} />);

    expect(screen.getByText(/sign-in link has expired/i)).toBeInTheDocument();
    expect(mockVerify).not.toHaveBeenCalled();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("shows the error state immediately and does NOT call verifyEmailOtp when authClient is null", () => {
    const mockVerify = vi.fn();

    vi.mocked(useSearchParams).mockReturnValue(
      new URLSearchParams("token_hash=abc123&type=email") as unknown as ReturnType<typeof useSearchParams>,
    );

    render(<AuthConfirm authClient={null} />);

    expect(screen.getByText(/sign-in link has expired/i)).toBeInTheDocument();
    expect(screen.getByText(/return to sign in/i)).toBeInTheDocument();
    expect(mockVerify).not.toHaveBeenCalled();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("shows the error state when verifyEmailOtp throws (network/runtime failure)", async () => {
    const mockVerify = vi.fn().mockRejectedValue(new Error("Network failure"));
    const client = authClient({ verifyEmailOtp: mockVerify });

    vi.mocked(useSearchParams).mockReturnValue(
      new URLSearchParams("token_hash=abc123&type=email") as unknown as ReturnType<typeof useSearchParams>,
    );

    render(<AuthConfirm authClient={client} />);

    expect(await screen.findByText(/sign-in link has expired/i)).toBeInTheDocument();
    expect(screen.getByText(/return to sign in/i)).toBeInTheDocument();
    expect(mockReplace).not.toHaveBeenCalled();
  });
});
