import { describe, expect, it } from "vitest";

import { SNOWFLAKE_SETUP_SQL } from "./snowflake-setup-sql";

describe("SNOWFLAKE_SETUP_SQL", () => {
  it("uses the Codex-reviewed least-privilege setup", () => {
    expect(SNOWFLAKE_SETUP_SQL).toContain("CREATE USER IF NOT EXISTS");
    expect(SNOWFLAKE_SETUP_SQL).toContain("TYPE = SERVICE");
    expect(SNOWFLAKE_SETUP_SQL).toContain("AUTO_RESUME = TRUE");
    expect(SNOWFLAKE_SETUP_SQL).toContain("GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER");
    expect(SNOWFLAKE_SETUP_SQL).not.toContain("MUST_CHANGE_PASSWORD");
    expect(SNOWFLAKE_SETUP_SQL).not.toContain("BEGIN;");
  });
});
