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

function authClient(overrides: Partial<BrowserAuthClient> = {}): BrowserAuthClient {
  return {
    getSession: vi.fn(),
    onAuthStateChange: vi.fn(),
    signInWithOtp: vi.fn().mockResolvedValue({ error: null }),
    verifyOtp: vi.fn().mockResolvedValue({ error: null }),
    signOut: vi.fn(),
    ...overrides,
  };
}

describe("LoginForm", () => {
  afterEach(() => cleanup());

  it("requests a passcode then advances to the code step", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));

    await waitFor(() => {
      expect(signInWithOtp).toHaveBeenCalledWith({ email: "owner@example.com" });
    });
    expect(screen.getByLabelText("6-digit code")).toBeInTheDocument();
  });

  it("verifies the entered code", async () => {
    const verifyOtp = vi.fn().mockResolvedValue({ error: null });
    render(<LoginForm authClient={authClient({ verifyOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    await screen.findByLabelText("6-digit code");

    fireEvent.change(screen.getByLabelText("6-digit code"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));

    await waitFor(() => {
      expect(verifyOtp).toHaveBeenCalledWith({
        email: "owner@example.com",
        token: "123456",
      });
    });
  });

  it("shows the send error in the alert region", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({
      error: { message: "Email login is unavailable" },
    });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Email login is unavailable",
    );
  });

  it("shows the verify error in the alert region", async () => {
    const verifyOtp = vi.fn().mockResolvedValue({
      error: { message: "Invalid or expired code" },
    });
    render(<LoginForm authClient={authClient({ verifyOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    await screen.findByLabelText("6-digit code");

    fireEvent.change(screen.getByLabelText("6-digit code"), {
      target: { value: "000000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid or expired code",
    );
  });

  it("shows a generic error and re-enables submit when sending rejects", async () => {
    const signInWithOtp = vi.fn().mockRejectedValue(new Error("network down"));
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    const submit = screen.getByRole("button", { name: "Email me a code" });
    fireEvent.click(submit);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Email me a code" }),
      ).not.toBeDisabled(),
    );
  });

  it("shows a generic error and re-enables submit when verifying rejects", async () => {
    const verifyOtp = vi.fn().mockRejectedValue(new Error("network down"));
    render(<LoginForm authClient={authClient({ verifyOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    await screen.findByLabelText("6-digit code");

    fireEvent.change(screen.getByLabelText("6-digit code"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Verify code" }),
      ).not.toBeDisabled(),
    );
  });

  it("can reset to a different email", async () => {
    render(<LoginForm authClient={authClient()} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    await screen.findByLabelText("6-digit code");

    fireEvent.click(screen.getByRole("button", { name: "Use a different email" }));

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });
});
