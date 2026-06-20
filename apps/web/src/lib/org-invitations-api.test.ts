import { afterEach, describe, expect, it, vi } from "vitest";

import {
  inviteUser,
  InviteConflictError,
  InviteValidationError,
} from "./org-invitations-api";

afterEach(() => vi.restoreAllMocks());

describe("inviteUser", () => {
  it("posts the email with the bearer token and returns it", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ email: "new@acme.com" }), { status: 200 }),
      );

    const email = await inviteUser(
      { organizationId: "org-1", email: "new@acme.com" },
      { accessToken: "tok" },
    );

    expect(email).toBe("new@acme.com");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/organizations/org-1/invitations");
    expect(JSON.parse(String(init?.body))).toEqual({ email: "new@acme.com" });
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer tok");
  });

  it("throws InviteValidationError on 422", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Please use your work email." }), {
        status: 422,
      }),
    );
    await expect(
      inviteUser({ organizationId: "org-1", email: "x@gmail.com" }, {}),
    ).rejects.toBeInstanceOf(InviteValidationError);
  });

  it("throws InviteConflictError on 409", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "new@acme.com is already a member." }),
        { status: 409 },
      ),
    );
    await expect(
      inviteUser({ organizationId: "org-1", email: "new@acme.com" }, {}),
    ).rejects.toBeInstanceOf(InviteConflictError);
  });
});
