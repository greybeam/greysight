import resolveApiUrl from "./api-client";

export interface InviteUserInput {
  organizationId: string;
  email: string;
}

interface InviteOptions {
  accessToken?: string | null;
}

export class InviteValidationError extends Error {}
export class InviteConflictError extends Error {}

export async function inviteUser(
  input: InviteUserInput,
  options: InviteOptions = {},
): Promise<string> {
  const headers = new Headers({ "content-type": "application/json" });
  const accessToken = options.accessToken?.trim();
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  const response = await fetch(
    resolveApiUrl(
      `/api/organizations/${encodeURIComponent(input.organizationId)}/invitations`,
    ),
    {
      method: "POST",
      headers,
      cache: "no-store",
      body: JSON.stringify({ email: input.email }),
    },
  );

  if (response.status === 200) {
    const payload = (await response.json()) as { email: string };
    return payload.email;
  }

  const detail = await safeDetail(response);
  if (response.status === 422) throw new InviteValidationError(detail);
  if (response.status === 409) throw new InviteConflictError(detail);
  throw new Error(detail || `Invite failed with ${response.status}`);
}

async function safeDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : "";
  } catch {
    return "";
  }
}
