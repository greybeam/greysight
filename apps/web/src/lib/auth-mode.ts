type PublicAuthEnv = Record<string, string | undefined> & {
  AUTH_REQUIRED?: string;
  NEXT_PUBLIC_AUTH_REQUIRED?: string;
};

export type AuthMode = {
  authRequired: boolean;
};

export function getAuthMode(env: PublicAuthEnv = process.env): AuthMode {
  const configuredValue = env.AUTH_REQUIRED ?? env.NEXT_PUBLIC_AUTH_REQUIRED;
  return {
    authRequired: configuredValue?.trim().toLowerCase() === "true",
  };
}
