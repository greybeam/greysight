import { describe, expect, it } from "vitest";

import { showBrandLogo } from "./brand";

describe("showBrandLogo", () => {
  it("shows the brand logo for the greybeam SaaS build", () => {
    expect(showBrandLogo("greybeam")).toBe(true);
  });

  it("hides the brand logo when unset (OSS self-host default)", () => {
    expect(showBrandLogo(undefined)).toBe(false);
  });

  it("hides the brand logo for any other brand value", () => {
    expect(showBrandLogo("acme")).toBe(false);
    expect(showBrandLogo("")).toBe(false);
  });
});
