import { afterEach, describe, expect, it, vi } from "vitest";

import { connectSnowflake, ConnectValidationError } from "./onboarding-api";

afterEach(() => vi.restoreAllMocks());

describe("connectSnowflake", () => {
  it("posts the payload and returns the new org id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ id: "org-123" }), { status: 201 }));

    const id = await connectSnowflake(
      {
        orgName: "Acme",
        account: "GOPGUKF-JO19546",
        user: "GREYBEAM_USER",
        role: "GREYBEAM_ROLE",
        warehouse: "GREYBEAM_WH",
        privateKeyPem: "PEM",
      },
      { accessToken: "tok" },
    );

    expect(id).toBe("org-123");
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toMatchObject({ org_name: "Acme", account: "GOPGUKF-JO19546" });
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer tok");
  });

  it("throws ConnectValidationError with the server message on 422", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Could not access required Snowflake Account Usage views." }), { status: 422 }),
    );
    await expect(
      connectSnowflake(
        { orgName: "A", account: "x", user: "u", role: "r", warehouse: "w", privateKeyPem: "P" },
        {},
      ),
    ).rejects.toBeInstanceOf(ConnectValidationError);
  });
});
