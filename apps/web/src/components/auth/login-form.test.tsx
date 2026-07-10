import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import LoginForm from "./login-form";
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

async function sendCodeTo(email: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
  await screen.findByText("Check your email");
}

describe("LoginForm code sign-in", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("auto-submits a full-length code and redirects to /dashboard", async () => {
    const verifyEmailCode = vi.fn().mockResolvedValue({ error: null });
    render(<LoginForm authClient={authClient({ verifyEmailCode })} />);
    await sendCodeTo("owner@greybeam.ai");

    // Default expected length is 8 — entering the full code needs no click.
    fireEvent.change(screen.getByLabelText("Sign-in code"), {
      target: { value: "12345678" },
    });

    await waitFor(() => {
      expect(verifyEmailCode).toHaveBeenCalledWith({
        email: "owner@greybeam.ai",
        token: "12345678",
      });
    });
    await waitFor(() =>
      expect(mockReplace).toHaveBeenCalledWith("/dashboard"),
    );
  });

  it("strips whitespace from a pasted code and auto-submits", async () => {
    const verifyEmailCode = vi.fn().mockResolvedValue({ error: null });
    render(<LoginForm authClient={authClient({ verifyEmailCode })} />);
    await sendCodeTo("owner@greybeam.ai");

    // Codes copied from the email often paste with spaces (letter-spacing).
    fireEvent.change(screen.getByLabelText("Sign-in code"), {
      target: { value: " 1234 5678 " },
    });

    await waitFor(() => {
      expect(verifyEmailCode).toHaveBeenCalledWith({
        email: "owner@greybeam.ai",
        token: "12345678",
      });
    });
  });

  it("keeps all 10 digits of a spaced paste (whitespace stripped before the length cap)", async () => {
    const verifyEmailCode = vi.fn().mockResolvedValue({ error: null });
    render(
      <CodeSignIn
        authClient={authClient({ verifyEmailCode })}
        email="owner@greybeam.ai"
        expectedCodeLength={10}
      />,
    );

    // 11 raw chars (10 digits + a space). A DOM maxLength would truncate to
    // "12345 6789" first, dropping the final digit after normalization.
    fireEvent.change(screen.getByLabelText("Sign-in code"), {
      target: { value: "12345 67890" },
    });

    await waitFor(() => {
      expect(verifyEmailCode).toHaveBeenCalledWith({
        email: "owner@greybeam.ai",
        token: "1234567890",
      });
    });
  });

  it("rejects a too-short code without calling the client", async () => {
    const verifyEmailCode = vi.fn();
    render(<LoginForm authClient={authClient({ verifyEmailCode })} />);
    await sendCodeTo("owner@greybeam.ai");

    fireEvent.change(screen.getByLabelText("Sign-in code"), {
      target: { value: "12345" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in with code" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(verifyEmailCode).not.toHaveBeenCalled();
  });

  it("does not re-auto-submit a failed code until the user edits it", async () => {
    const verifyEmailCode = vi
      .fn()
      .mockResolvedValue({ error: { message: "Token has expired" } });
    render(<LoginForm authClient={authClient({ verifyEmailCode })} />);
    await sendCodeTo("owner@greybeam.ai");

    const input = screen.getByLabelText("Sign-in code");
    fireEvent.change(input, { target: { value: "12345678" } });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(verifyEmailCode).toHaveBeenCalledTimes(1);

    // The same value must not loop the failed auto-submit.
    fireEvent.change(input, { target: { value: "12345678" } });
    expect(verifyEmailCode).toHaveBeenCalledTimes(1);

    // Editing to a different full-length code auto-submits again.
    fireEvent.change(input, { target: { value: "87654321" } });
    await waitFor(() => expect(verifyEmailCode).toHaveBeenCalledTimes(2));
  });

  it("still accepts a manually submitted 8-digit code when the expected length is misconfigured to 6", async () => {
    const verifyEmailCode = vi.fn().mockResolvedValue({ error: null });
    render(
      <CodeSignIn
        authClient={authClient({ verifyEmailCode })}
        email="owner@greybeam.ai"
        expectedCodeLength={6}
      />,
    );

    // Longer than the (misconfigured) expected length: no auto-submit fires,
    // but the input still holds it and the manual button verifies it.
    fireEvent.change(screen.getByLabelText("Sign-in code"), {
      target: { value: "12345678" },
    });
    expect(verifyEmailCode).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Sign in with code" }));

    await waitFor(() => {
      expect(verifyEmailCode).toHaveBeenCalledWith({
        email: "owner@greybeam.ai",
        token: "12345678",
      });
    });
  });

  it("does not call verifyEmailCode again while a verify is in flight", async () => {
    let resolveVerify!: (value: { error: null }) => void;
    const deferred = new Promise<{ error: null }>((resolve) => {
      resolveVerify = resolve;
    });
    const verifyEmailCode = vi.fn().mockReturnValue(deferred);
    render(<LoginForm authClient={authClient({ verifyEmailCode })} />);
    await sendCodeTo("owner@greybeam.ai");

    fireEvent.change(screen.getByLabelText("Sign-in code"), {
      target: { value: "123456" },
    });
    const form = screen.getByLabelText("Sign-in code").closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);

    resolveVerify({ error: null });

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/dashboard"));
    expect(verifyEmailCode).toHaveBeenCalledTimes(1);
    // Success keeps the button disabled through the redirect.
    expect(screen.getByRole("button", { name: "Signing in" })).toBeDisabled();
  });
});
