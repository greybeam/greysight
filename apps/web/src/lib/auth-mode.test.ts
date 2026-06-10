import { describe, expect, it } from "vitest";

import { getAuthMode } from "./auth-mode";

describe("getAuthMode", () => {
  it("allows the local demo bypass when server auth is disabled", () => {
    expect(getAuthMode({ AUTH_REQUIRED: "false" })).toEqual({
      authRequired: false,
    });
  });

  it("requires auth when server auth is explicitly enabled", () => {
    expect(getAuthMode({ AUTH_REQUIRED: "true" })).toEqual({
      authRequired: true,
    });
  });

  it("uses public auth mode when server auth mode is unavailable", () => {
    expect(getAuthMode({ NEXT_PUBLIC_AUTH_REQUIRED: "true" })).toEqual({
      authRequired: true,
    });
  });
});
