import { describe, expect, it } from "vitest";
import resolveApiUrl from "./api-client";

describe("resolveApiUrl", () => {
  it("uses configured local API base URL", () => {
    expect(resolveApiUrl("/health", "http://localhost:8000")).toBe(
      "http://localhost:8000/health",
    );
  });

  it("uses relative same-origin paths when no external base URL is configured", () => {
    expect(resolveApiUrl("/health", "")).toBe("/health");
  });
});
