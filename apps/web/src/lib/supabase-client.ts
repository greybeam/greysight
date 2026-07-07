import { createClient, type Session } from "@supabase/supabase-js";

type PublicSupabaseEnv = Record<string, string | undefined> & {
  NEXT_PUBLIC_SUPABASE_ANON_KEY?: string;
  NEXT_PUBLIC_SUPABASE_URL?: string;
};

export type AuthSession = {
  accessToken: string;
  user?: {
    email?: string | null;
    appMetadata?: Record<string, unknown> | null;
  } | null;
};

export type SessionChangeCallback = (session: AuthSession | null) => void;

export type BrowserAuthClient = {
  getSession(): Promise<{
    error?: { message: string } | null;
    session: AuthSession | null;
  }>;
  onAuthStateChange(callback: SessionChangeCallback): {
    unsubscribe(): void;
  };
  signInWithOtp(input: { email: string }): Promise<{
    error?: { message: string } | null;
  }>;
  verifyOtp(input: { email: string; token: string }): Promise<{
    error?: { message: string } | null;
  }>;
  verifyEmailOtp(input: { tokenHash: string; type?: string }): Promise<{
    error?: { message: string } | null;
  }>;
  signOut(): Promise<{
    error?: { message: string } | null;
  }>;
};

export type AuthClientFactory = (settings: {
  supabaseAnonKey: string;
  supabaseUrl: string;
}) => BrowserAuthClient;

function toAuthSession(session: Session | null): AuthSession | null {
  if (!session) return null;

  return {
    accessToken: session.access_token,
    user: {
      email: session.user.email ?? null,
      appMetadata: session.user.app_metadata ?? null,
    },
  };
}

export const createSupabaseBrowserAuthClient: AuthClientFactory = ({
  supabaseAnonKey,
  supabaseUrl,
}) => {
  const supabase = createClient(supabaseUrl, supabaseAnonKey);

  return {
    async getSession() {
      const { data, error } = await supabase.auth.getSession();
      return {
        error: error ? { message: error.message } : null,
        session: toAuthSession(data.session),
      };
    },
    onAuthStateChange(callback) {
      const { data } = supabase.auth.onAuthStateChange((_event, session) => {
        callback(toAuthSession(session));
      });

      return {
        unsubscribe() {
          data.subscription.unsubscribe();
        },
      };
    },
    async signInWithOtp(input) {
      const { error } = await supabase.auth.signInWithOtp({ email: input.email });
      return { error: error ? { message: error.message } : null };
    },
    async verifyOtp(input) {
      const { error } = await supabase.auth.verifyOtp({
        email: input.email,
        token: input.token,
        type: "email",
      });
      return { error: error ? { message: error.message } : null };
    },
    async verifyEmailOtp(input) {
      const { error } = await supabase.auth.verifyOtp({
        token_hash: input.tokenHash,
        type: (input.type ?? "email") as "email",
      });
      return { error: error ? { message: error.message } : null };
    },
    async signOut() {
      const { error } = await supabase.auth.signOut();
      return { error: error ? { message: error.message } : null };
    },
  };
};

let authClientFactory: AuthClientFactory = createSupabaseBrowserAuthClient;

export function configureBrowserAuthClient(factory: AuthClientFactory): void {
  authClientFactory = factory;
}

export function resetBrowserAuthClientFactory(): void {
  authClientFactory = createSupabaseBrowserAuthClient;
}

export function createBrowserAuthClient(
  // The default MUST reference the exact static `process.env.NEXT_PUBLIC_*`
  // tokens. Turbopack only inlines those literal tokens into the client bundle;
  // an aliased `= process.env` default is not inlined and reads as `undefined`
  // in the browser, which surfaced as "Authentication is not configured".
  env: PublicSupabaseEnv = {
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  },
): BrowserAuthClient | null {
  const supabaseUrl = env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const supabaseAnonKey = env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();

  if (!supabaseUrl || !supabaseAnonKey) return null;

  return authClientFactory({ supabaseAnonKey, supabaseUrl });
}

export default createBrowserAuthClient;
