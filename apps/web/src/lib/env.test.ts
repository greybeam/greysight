import { afterEach, describe, expect, it, vi } from "vitest";

import getPublicApiBaseUrl from "./env";

describe("getPublicApiBaseUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("uses the configured API base URL when present", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.local ");

    expect(getPublicApiBaseUrl()).toBe("http://api.local");
  });

  it("defaults local development to the FastAPI dev server", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
    vi.stubEnv("NODE_ENV", "development");

    expect(getPublicApiBaseUrl()).toBe("http://localhost:8000");
  });
});
