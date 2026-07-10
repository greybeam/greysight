import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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
    verifyEmailCode: vi.fn(),
    verifyEmailOtp: vi.fn().mockResolvedValue({ error: null }),
    signOut: vi.fn(),
    ...overrides,
  };
}

function setParams(query: string) {
  vi.mocked(useSearchParams).mockReturnValue(
    new URLSearchParams(query) as unknown as ReturnType<typeof useSearchParams>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AuthConfirm", () => {
  it("does NOT call verifyEmailOtp on mount — it waits for a click", () => {
    const mockVerify = vi.fn().mockResolvedValue({ error: null });
    setParams("token_hash=abc123&type=email");

    render(<AuthConfirm authClient={authClient({ verifyEmailOtp: mockVerify })} />);

    expect(
      screen.getByRole("button", { name: /confirm email address/i }),
    ).toBeInTheDocument();
    expect(mockVerify).not.toHaveBeenCalled();
  });

  it("verifies and redirects to /dashboard when the confirm button is clicked", async () => {
    const mockVerify = vi.fn().mockResolvedValue({ error: null });
    setParams("token_hash=abc123&type=email");

    render(<AuthConfirm authClient={authClient({ verifyEmailOtp: mockVerify })} />);

    fireEvent.click(
      screen.getByRole("button", { name: /confirm email address/i }),
    );

    await waitFor(() => {
      expect(mockVerify).toHaveBeenCalledWith({
        tokenHash: "abc123",
        type: "email",
      });
    });
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("shows the expired copy when verification fails with an otp_expired code", async () => {
    const mockVerify = vi.fn().mockResolvedValue({
      error: { message: "Token has expired", code: "otp_expired" },
    });
    setParams("token_hash=expiredtoken&type=email");

    render(<AuthConfirm authClient={authClient({ verifyEmailOtp: mockVerify })} />);

    fireEvent.click(
      screen.getByRole("button", { name: /confirm email address/i }),
    );

    expect(await screen.findByText(/sign-in link has expired/i)).toBeInTheDocument();
    expect(screen.getByText(/return to sign in/i)).toBeInTheDocument();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("shows generic copy for a non-otp failure (network/config)", async () => {
    const mockVerify = vi.fn().mockRejectedValue(new Error("Network failure"));
    setParams("token_hash=abc123&type=email");

    render(<AuthConfirm authClient={authClient({ verifyEmailOtp: mockVerify })} />);

    fireEvent.click(
      screen.getByRole("button", { name: /confirm email address/i }),
    );

    expect(
      await screen.findByText(/something went wrong signing you in/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/sign-in link has expired/i)).not.toBeInTheDocument();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("does not double-fire verification when the button is clicked twice", async () => {
    let resolveVerify!: (value: { error: null }) => void;
    const deferred = new Promise<{ error: null }>((resolve) => {
      resolveVerify = resolve;
    });
    const mockVerify = vi.fn().mockReturnValue(deferred);
    setParams("token_hash=abc123&type=email");

    render(<AuthConfirm authClient={authClient({ verifyEmailOtp: mockVerify })} />);

    const button = screen.getByRole("button", { name: /confirm email address/i });
    fireEvent.click(button);
    fireEvent.click(button);

    resolveVerify({ error: null });

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/dashboard"));
    expect(mockVerify).toHaveBeenCalledTimes(1);
  });
});
