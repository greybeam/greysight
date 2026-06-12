import { describe, expect, it } from "vitest";

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

describe("dashboard component imports", () => {
  it("does not import dashboard analytics transforms from render components", () => {
    const testFilePath = import.meta.url.startsWith("file:")
      ? fileURLToPath(import.meta.url)
      : import.meta.url;
    const directory = dirname(testFilePath);
    const bannedImport = ["dashboard", "transforms"].join("-");
    const offenders = readdirSync(directory)
      .filter((file) => file.endsWith(".ts") || file.endsWith(".tsx"))
      .filter((file) => !file.includes(".test."))
      .filter((file) =>
        readFileSync(join(directory, file), "utf8").includes(bannedImport),
      );

    expect(offenders).toEqual([]);
  });
});
