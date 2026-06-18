import resolveApiUrl from "./api-client";

export type MembershipOrganization = {
  id: string;
  name: string;
  // Snowflake account locator from the org's persisted connection, when one
  // exists. Lets the dashboard show the account before any analysis run.
  accountLocator: string | null;
};

function parseOrganizations(payload: unknown): MembershipOrganization[] {
  if (typeof payload !== "object" || payload === null) {
    throw new Error("Malformed memberships response");
  }
  const organizations = (payload as { organizations?: unknown }).organizations;
  if (!Array.isArray(organizations)) {
    throw new Error("Malformed memberships response");
  }
  return organizations.map((item) => {
    if (
      typeof item !== "object" ||
      item === null ||
      typeof (item as { id?: unknown }).id !== "string" ||
      typeof (item as { name?: unknown }).name !== "string"
    ) {
      throw new Error("Malformed membership entry");
    }
    const entry = item as {
      id: string;
      name: string;
      account_locator?: unknown;
      accountLocator?: unknown;
    };
    const id = entry.id.trim();
    if (id.length === 0) {
      throw new Error("Malformed membership entry");
    }
    const rawLocator = entry.account_locator ?? entry.accountLocator;
    const accountLocator =
      typeof rawLocator === "string" && rawLocator.trim().length > 0
        ? rawLocator
        : null;
    return { id, name: entry.name, accountLocator };
  });
}

export async function fetchSessionMemberships(
  accessToken: string,
): Promise<MembershipOrganization[]> {
  const response = await fetch(resolveApiUrl("/api/session/memberships"), {
    headers: { authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Membership lookup failed with ${response.status}`);
  }
  return parseOrganizations(await response.json());
}

export default fetchSessionMemberships;
