import { describe, expect, it } from "vitest";

import { SNOWFLAKE_SETUP_SQL } from "./snowflake-setup-sql";

describe("SNOWFLAKE_SETUP_SQL", () => {
  it("uses the Codex-reviewed least-privilege setup", () => {
    expect(SNOWFLAKE_SETUP_SQL).not.toContain("MUST_CHANGE_PASSWORD");
    expect(SNOWFLAKE_SETUP_SQL).not.toContain("BEGIN;");
  });
});
