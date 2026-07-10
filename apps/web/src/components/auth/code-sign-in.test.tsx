import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CodeSignIn from "./code-sign-in";
import type { BrowserAuthClient } from "../../lib/supabase-client";

const mockReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: vi.fn(() => ({ replace: mockReplace })),
}));

function authClient(overrides: Partial<BrowserAuthClient> = {}): BrowserAuthClient {
  return {
    getSession: vi.fn(),
    onAuthStateChange: vi.fn(),
    signInWithOtp: vi.fn().mockResolvedValue({ error: null }),
    verifyOtp: vi.fn().mockResolvedValue({ error: null }),
    verifyEmailCode: vi.fn().mockResolvedValue({ error: null }),
    verifyEmailOtp: vi.fn().mockResolvedValue({ error: null }),
    signOut: vi.fn(),
    ...overrides,
  };
}

function resendButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /resend code/i }) as HTMLButtonElement;
}

// Advance fake timers inside act so React flushes the countdown state updates.
function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

describe("CodeSignIn resend code", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    cleanup();
    vi.clearAllMocks();
  });

  it("counts down from 60s and enables the button at zero", () => {
    render(<CodeSignIn authClient={authClient()} email="alice@acme.com" />);
    // Starts disabled on mount because a code was just sent.
    expect(resendButton()).toBeDisabled();
    expect(resendButton()).toHaveTextContent("Resend code in 60s");

    advance(1000);
    expect(resendButton()).toHaveTextContent("Resend code in 59s");

    advance(59000);
    const button = resendButton();
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent("Resend code");
  });

  it("resends via signInWithOtp, confirms, and restarts the cooldown", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
    render(
      <CodeSignIn authClient={authClient({ signInWithOtp })} email="alice@acme.com" />,
    );
    advance(60000);

    await act(async () => {
      fireEvent.click(resendButton());
    });

    expect(signInWithOtp).toHaveBeenCalledWith({ email: "alice@acme.com" });
    expect(
      screen.getByText("New code sent to alice@acme.com"),
    ).toBeInTheDocument();
    const button = resendButton();
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Resend code in 60s");
  });

  it("does not restart the cooldown when the resend fails", async () => {
    const signInWithOtp = vi
      .fn()
      .mockResolvedValue({ error: { message: "rate limit exceeded" } });
    render(
      <CodeSignIn authClient={authClient({ signInWithOtp })} email="alice@acme.com" />,
    );
    advance(60000);

    await act(async () => {
      fireEvent.click(resendButton());
    });

    // An error is surfaced (exact copy is covered by the shared-helper tests).
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    // The button stays enabled so the user can retry immediately.
    expect(resendButton()).toBeEnabled();
  });

  it("blocks a second resend and a code verify while a resend is in flight", async () => {
    let resolveResend!: (value: { error: null }) => void;
    const deferred = new Promise<{ error: null }>((resolve) => {
      resolveResend = resolve;
    });
    const signInWithOtp = vi.fn().mockReturnValue(deferred);
    const verifyEmailCode = vi.fn().mockResolvedValue({ error: null });
    render(
      <CodeSignIn
        authClient={authClient({ signInWithOtp, verifyEmailCode })}
        email="alice@acme.com"
      />,
    );
    advance(60000);

    // Enter a valid-but-shorter-than-expected code so no auto-submit fires yet.
    fireEvent.change(screen.getByLabelText("Sign-in code"), {
      target: { value: "123456" },
    });

    const form = screen.getByLabelText("Sign-in code").closest("form")!;
    act(() => {
      fireEvent.click(resendButton());
      fireEvent.click(resendButton());
      // Submitting a code mid-resend must not race verifyEmailCode.
      fireEvent.submit(form);
    });

    expect(signInWithOtp).toHaveBeenCalledTimes(1);
    expect(verifyEmailCode).not.toHaveBeenCalled();

    await act(async () => {
      resolveResend({ error: null });
      await deferred;
    });
  });
});
