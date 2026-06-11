import { describe, expect, it } from "vitest";

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

describe("dashboard component imports", () => {
  it("does not import dashboard analytics transforms from render components", () => {
    const directory = join(process.cwd(), "src/components/dashboard");
    const bannedImport = ["dashboard", "transforms"].join("-");
    const offenders = readdirSync(directory)
      .filter((file) => file.endsWith(".tsx"))
      .filter((file) =>
        readFileSync(join(directory, file), "utf8").includes(bannedImport),
      );

    expect(offenders).toEqual([]);
  });
});
