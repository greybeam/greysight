import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { FREE_EMAIL_DOMAINS, isWorkEmail } from "./work-email";

describe("isWorkEmail", () => {
  it("accepts a company domain", () => {
    expect(isWorkEmail("kyle@greybeam.ai")).toBe(true);
  });

  it("rejects common free providers", () => {
    for (const email of [
      "a@gmail.com",
      "a@googlemail.com",
      "a@yahoo.com",
      "a@outlook.com",
      "a@hotmail.com",
      "a@live.com",
      "a@icloud.com",
      "a@aol.com",
      "a@proton.me",
      "a@protonmail.com",
      "a@gmx.com",
      "a@mail.com",
    ]) {
      expect(isWorkEmail(email)).toBe(false);
    }
  });

  it("is case-insensitive and trims surrounding whitespace", () => {
    expect(isWorkEmail("  Person@GMAIL.com ")).toBe(false);
    expect(isWorkEmail("Person@Greybeam.AI")).toBe(true);
  });

  it("rejects all blocklisted domains in FREE_EMAIL_DOMAINS", () => {
    for (const domain of [
      "yahoo.co.uk",
      "msn.com",
      "me.com",
      "mac.com",
      "zoho.com",
      "yandex.com",
      "qq.com",
      "163.com",
    ]) {
      expect(isWorkEmail(`a@${domain}`)).toBe(false);
    }
  });

  it("rejects empty or malformed input", () => {
    for (const value of [
      "",
      "  ",
      "no-at-sign",
      "a@",
      "@b.com",
      "a@@b.com",
      "a@.com",
      "a@b.",
      "a@b..com",
      "a@b",
    ]) {
      expect(isWorkEmail(value)).toBe(false);
    }
  });

  it("stays in lockstep with the shared fixture", () => {
    const thisFile = import.meta.url.startsWith("file:")
      ? fileURLToPath(import.meta.url)
      : import.meta.url;
    const fixture = JSON.parse(
      readFileSync(
        new URL("../../../../shared/free-email-domains.json", `file://${thisFile}`).pathname,
        "utf8",
      ),
    ) as string[];
    expect(new Set(FREE_EMAIL_DOMAINS)).toEqual(new Set(fixture));
  });
});
