import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSessionMemberships } from "./session-memberships";

describe("fetchSessionMemberships", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns organizations and sends the bearer token", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ organizations: [{ id: "org-1", name: "Acme" }] }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const organizations = await fetchSessionMemberships("access-token");

    expect(organizations).toEqual([
      { id: "org-1", name: "Acme", accountLocator: null },
    ]);
    const [, init] = fetchSpy.mock.calls[0];
    expect((init?.headers as Record<string, string>).authorization).toBe(
      "Bearer access-token",
    );
  });

  it("throws when the request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("nope", { status: 500 }),
    );

    await expect(fetchSessionMemberships("access-token")).rejects.toThrow();
  });

  it("throws on a malformed payload", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ organizations: [{ id: 1 }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(fetchSessionMemberships("access-token")).rejects.toThrow();
  });

  it("rejects an entry with an empty id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ organizations: [{ id: "", name: "Acme" }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(fetchSessionMemberships("access-token")).rejects.toThrow();
  });

  it("rejects an entry with a whitespace-only id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ organizations: [{ id: "  ", name: "Acme" }] }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );

    await expect(fetchSessionMemberships("access-token")).rejects.toThrow();
  });

  it("trims the id and keeps the name as-is", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ organizations: [{ id: " org-1 ", name: " Acme " }] }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );

    const organizations = await fetchSessionMemberships("access-token");

    expect(organizations).toEqual([
      { id: "org-1", name: " Acme ", accountLocator: null },
    ]);
  });

  it("parses the account locator when present", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          organizations: [
            { id: "org-1", name: "Acme", account_locator: "IJ42635" },
          ],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const organizations = await fetchSessionMemberships("access-token");

    expect(organizations).toEqual([
      { id: "org-1", name: "Acme", accountLocator: "IJ42635" },
    ]);
  });
});
