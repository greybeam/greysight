import resolveApiUrl from "./api-client";

export interface ConnectSnowflakeInput {
  orgName: string;
  account: string;
  user: string;
  role: string;
  warehouse: string;
  database?: string;
  schema?: string;
  privateKeyPem: string;
  passphrase?: string;
}

interface ConnectOptions {
  accessToken?: string | null;
}

export class ConnectValidationError extends Error {}
export class ConnectConflictError extends Error {}

export async function connectSnowflake(
  input: ConnectSnowflakeInput,
  options: ConnectOptions = {},
): Promise<string> {
  const headers = new Headers({ "content-type": "application/json" });
  const accessToken = options.accessToken?.trim();
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  const response = await fetch(resolveApiUrl("/api/onboarding/connect"), {
    method: "POST",
    headers,
    cache: "no-store",
    body: JSON.stringify({
      org_name: input.orgName,
      account: input.account,
      user: input.user,
      role: input.role,
      warehouse: input.warehouse,
      database: input.database || null,
      schema: input.schema || null,
      private_key_pem: input.privateKeyPem,
      passphrase: input.passphrase || null,
    }),
  });

  if (response.status === 201) {
    const payload = (await response.json()) as { id: string };
    return payload.id;
  }

  const detail = await safeDetail(response);
  if (response.status === 422) throw new ConnectValidationError(detail);
  if (response.status === 409) throw new ConnectConflictError(detail);
  throw new Error(detail || `Connect failed with ${response.status}`);
}

async function safeDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : "";
  } catch {
    return "";
  }
}
