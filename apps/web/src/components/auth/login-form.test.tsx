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
    verifyEmailOtp: vi.fn().mockResolvedValue({ error: null }),
    signOut: vi.fn(),
    ...overrides,
  };
}

describe("LoginForm", () => {
  afterEach(() => cleanup());

  it("sends a magic link for a work email and shows the check-email confirmation", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    await waitFor(() => {
      expect(signInWithOtp).toHaveBeenCalledWith({ email: "owner@greybeam.ai" });
    });
    expect(await screen.findByText("Check your email")).toBeInTheDocument();
    expect(screen.getByText(/owner@greybeam\.ai/)).toBeInTheDocument();
  });

  it("rejects a free-provider email without calling the client", async () => {
    const signInWithOtp = vi.fn();
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "person@gmail.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Please use your work email.",
    );
    expect(signInWithOtp).not.toHaveBeenCalled();
  });

  it("does not surface provider wording verbatim on a send error", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({
      error: { message: "Email login is unavailable (internal code 500)" },
    });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).not.toHaveTextContent("internal code 500");
  });

  it("shows a friendly rate-limit message", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({
      error: { message: "Email rate limit exceeded" },
    });
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many requests. Please wait a moment and try again.",
    );
  });

  it("shows a generic error and re-enables submit when sending rejects", async () => {
    const signInWithOtp = vi.fn().mockRejectedValue(new Error("network down"));
    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Email link" }),
      ).not.toBeDisabled(),
    );
  });

  it("can reset to send to a different email", async () => {
    render(<LoginForm authClient={authClient()} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));
    await screen.findByText("Check your email");

    fireEvent.click(
      screen.getByRole("button", { name: "Send to a different email" }),
    );

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("links to the terms of service on the request step", () => {
    render(<LoginForm authClient={authClient()} />);
    const link = screen.getByRole("link", { name: /terms of service/i });
    expect(link).toHaveAttribute("href", "https://www.greybeam.ai/terms");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("disables the email input while pending and shows the originally-submitted address in the sent view", async () => {
    let resolveOtp!: (value: { error: null }) => void;
    const deferredOtp = new Promise<{ error: null }>((resolve) => {
      resolveOtp = resolve;
    });
    const signInWithOtp = vi.fn().mockReturnValue(deferredOtp);

    render(<LoginForm authClient={authClient({ signInWithOtp })} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@greybeam.ai" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Email link" }));

    // While the request is in flight the input must be disabled
    await waitFor(() =>
      expect(screen.getByLabelText("Email")).toBeDisabled(),
    );

    // Resolve the deferred promise (simulating the server response)
    resolveOtp({ error: null });

    // The sent view must show the address that was actually submitted
    expect(await screen.findByText("Check your email")).toBeInTheDocument();
    expect(screen.getByText(/owner@greybeam\.ai/)).toBeInTheDocument();
  });
});
