import { describe, expect, it } from "vitest";

import { SNOWFLAKE_SETUP_SQL } from "./snowflake-setup-sql";

describe("SNOWFLAKE_SETUP_SQL", () => {
  it("grants IMPORTED PRIVILEGES for billed-dollar access", () => {
    // The grant is the access-contract boundary: a regression that drops it or
    // reverts to the narrower USAGE_VIEWER role would silently break billed
    // dollars. Pin the intended grant; reject the old one.
    expect(SNOWFLAKE_SETUP_SQL).toContain(
      "GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE IDENTIFIER($role_name)",
    );
    expect(SNOWFLAKE_SETUP_SQL).not.toContain("USAGE_VIEWER");
  });

  it("uses the Codex-reviewed least-privilege setup", () => {
    expect(SNOWFLAKE_SETUP_SQL).not.toContain("MUST_CHANGE_PASSWORD");
    expect(SNOWFLAKE_SETUP_SQL).not.toContain("BEGIN;");
  });
});
