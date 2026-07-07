import { afterEach, describe, expect, it, vi } from "vitest";
import {
  configureBrowserAuthClient,
  createBrowserAuthClient,
  createSupabaseBrowserAuthClient,
  resetBrowserAuthClientFactory,
} from "./supabase-client";

const createClient = vi.hoisted(() => vi.fn());

vi.mock("@supabase/supabase-js", () => ({
  createClient,
}));

describe("supabase-client", () => {
  afterEach(() => {
    resetBrowserAuthClientFactory();
    createClient.mockReset();
  });

  it("does not create a browser auth client without public Supabase env vars", () => {
    expect(createBrowserAuthClient({})).toBeNull();
    expect(createClient).not.toHaveBeenCalled();
  });

  it("preserves injectable auth clients for tests", () => {
    const authClient = {
      getSession: vi.fn(),
      onAuthStateChange: vi.fn(),
      signInWithOtp: vi.fn(),
      verifyOtp: vi.fn(),
      verifyEmailOtp: vi.fn(),
      signOut: vi.fn(),
    };

    configureBrowserAuthClient(() => authClient);

    expect(
      createBrowserAuthClient({
        NEXT_PUBLIC_SUPABASE_URL: "https://project.supabase.co",
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon-key",
      }),
    ).toBe(authClient);
  });

  it("creates a production Supabase browser auth client from public env vars", async () => {
    const unsubscribe = vi.fn();
    const supabaseClient = {
      auth: {
        getSession: vi.fn().mockResolvedValue({
          data: {
            session: {
              access_token: "access-token",
              user: { email: "owner@example.com" },
            },
          },
          error: null,
        }),
        onAuthStateChange: vi.fn(() => ({
          data: { subscription: { unsubscribe } },
        })),
        signInWithOtp: vi.fn().mockResolvedValue({ error: null }),
        signOut: vi.fn().mockResolvedValue({ error: null }),
      },
    };
    createClient.mockReturnValue(supabaseClient);

    const authClient = createBrowserAuthClient({
      NEXT_PUBLIC_SUPABASE_URL: "https://project.supabase.co",
      NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon-key",
    });

    expect(createClient).toHaveBeenCalledWith(
      "https://project.supabase.co",
      "anon-key",
    );
    expect(await authClient?.getSession()).toEqual({
      session: {
        accessToken: "access-token",
        user: { email: "owner@example.com", appMetadata: null },
      },
      error: null,
    });

    authClient?.onAuthStateChange(vi.fn()).unsubscribe();

    expect(unsubscribe).toHaveBeenCalled();
  });

  it("maps missing Supabase sessions and auth errors", async () => {
    const supabaseClient = {
      auth: {
        getSession: vi.fn().mockResolvedValue({
          data: { session: null },
          error: { message: "Session expired" },
        }),
        onAuthStateChange: vi.fn(),
        signInWithOtp: vi
          .fn()
          .mockResolvedValue({ error: { message: "Email rejected" } }),
        verifyOtp: vi
          .fn()
          .mockResolvedValue({ error: { message: "Invalid code" } }),
        signOut: vi
          .fn()
          .mockResolvedValue({ error: { message: "Already signed out" } }),
      },
    };
    createClient.mockReturnValue(supabaseClient);

    const authClient = createSupabaseBrowserAuthClient({
      supabaseUrl: "https://project.supabase.co",
      supabaseAnonKey: "anon-key",
    });

    await expect(authClient.getSession()).resolves.toEqual({
      error: { message: "Session expired" },
      session: null,
    });
    await expect(
      authClient.signInWithOtp({ email: "owner@example.com" }),
    ).resolves.toEqual({ error: { message: "Email rejected" } });
    await expect(
      authClient.verifyOtp({ email: "owner@example.com", token: "123456" }),
    ).resolves.toEqual({ error: { message: "Invalid code" } });
    await expect(authClient.signOut()).resolves.toEqual({
      error: { message: "Already signed out" },
    });
  });

  it("verifies an email OTP code", async () => {
    const verifyOtp = vi.fn().mockResolvedValue({ data: {}, error: null });
    const supabaseClient = {
      auth: {
        getSession: vi.fn(),
        onAuthStateChange: vi.fn(),
        signInWithOtp: vi.fn().mockResolvedValue({ error: null }),
        signOut: vi.fn(),
        verifyOtp,
      },
    };
    createClient.mockReturnValue(supabaseClient);

    const authClient = createSupabaseBrowserAuthClient({
      supabaseUrl: "https://project.supabase.co",
      supabaseAnonKey: "anon-key",
    });

    await authClient.verifyOtp({ email: "owner@example.com", token: "123456" });

    expect(verifyOtp).toHaveBeenCalledWith({
      email: "owner@example.com",
      token: "123456",
      type: "email",
    });
  });

  it("verifies a token-hash email OTP (scanner-safe link flow)", async () => {
    const verifyOtp = vi.fn().mockResolvedValue({ data: {}, error: null });
    const supabaseClient = {
      auth: {
        getSession: vi.fn(),
        onAuthStateChange: vi.fn(),
        signInWithOtp: vi.fn().mockResolvedValue({ error: null }),
        signOut: vi.fn(),
        verifyOtp,
      },
    };
    createClient.mockReturnValue(supabaseClient);

    const authClient = createSupabaseBrowserAuthClient({
      supabaseUrl: "https://project.supabase.co",
      supabaseAnonKey: "anon-key",
    });

    await authClient.verifyEmailOtp({ tokenHash: "abc123", type: "email" });

    expect(verifyOtp).toHaveBeenCalledWith({
      token_hash: "abc123",
      type: "email",
    });
  });
});
