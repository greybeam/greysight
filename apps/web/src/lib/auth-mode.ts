type PublicAuthEnv = Record<string, string | undefined> & {
  AUTH_REQUIRED?: string;
  NEXT_PUBLIC_AUTH_REQUIRED?: string;
};

export type AuthMode = {
  authRequired: boolean;
};

export function getAuthMode(
  // The default MUST reference the exact static `process.env.NEXT_PUBLIC_*`
  // token. Turbopack only inlines that literal token into the client bundle; an
  // aliased `= process.env` default is not inlined and reads as `undefined` in
  // the browser, which would break client-side auth-mode detection.
  env: PublicAuthEnv = {
    AUTH_REQUIRED: process.env.AUTH_REQUIRED,
    NEXT_PUBLIC_AUTH_REQUIRED: process.env.NEXT_PUBLIC_AUTH_REQUIRED,
  },
): AuthMode {
  const configuredValue = env.AUTH_REQUIRED ?? env.NEXT_PUBLIC_AUTH_REQUIRED;
  return {
    authRequired: configuredValue?.trim().toLowerCase() === "true",
  };
}
