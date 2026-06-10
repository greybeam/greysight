import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import LoginForm from "./login-form";
import type { BrowserAuthClient } from "../../lib/supabase-client";

function authClientWithOtp(
  signInWithOtp: BrowserAuthClient["signInWithOtp"],
): BrowserAuthClient {
  return {
    getSession: vi.fn(),
    onAuthStateChange: vi.fn(),
    signInWithOtp,
    signOut: vi.fn(),
  };
}

describe("LoginForm", () => {
  afterEach(() => cleanup());

  it("sends a passwordless email OTP and renders success state", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
    const authClient = authClientWithOtp(signInWithOtp);

    render(<LoginForm authClient={authClient} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email magic link" }));

    await waitFor(() => {
      expect(signInWithOtp).toHaveBeenCalledWith({
        email: "owner@example.com",
        options: {
          emailRedirectTo: window.location.href,
        },
      });
    });
    expect(screen.getByText("Check your email for the sign-in link.")).toBeInTheDocument();
  });

  it("renders an error when OTP sending fails", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({
      error: { message: "Email login is unavailable" },
    });
    const authClient = authClientWithOtp(signInWithOtp);

    render(<LoginForm authClient={authClient} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email magic link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Email login is unavailable",
    );
  });
});
