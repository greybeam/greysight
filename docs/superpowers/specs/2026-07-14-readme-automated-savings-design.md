# README Automated Savings update

## Goal

Update the root README so a technical reader can understand what Automated
Savings does, how its components interact, and how to exercise the feature
locally against a real Supabase project and Snowflake warehouse.

## Scope

- Mention Automated Savings in the README's opening description.
- Add a dedicated Automated Savings section that explains its opt-in behavior,
  polling model, eligibility checks, and narrow Snowflake write operation.
- Qualify the existing statement that Greysight only executes read-only SQL so
  it remains accurate for the dashboard while identifying the worker as the
  explicit exception.
- Add local trial steps covering authenticated Supabase setup, migrations, an
  organization Snowflake connection, required suspend access, UI enrollment,
  and `uv run dev.py` from `apps/auto-savings`.
- Warn readers to use a disposable test warehouse because the worker can issue
  a real suspend command.
- State that a fully local SQLite or DuckDB backend is planned but is not yet
  available.
- Add the worker to the project layout and link the existing Automated Savings
  documentation.

## Writing style

Use plain, declarative technical prose. Avoid promotional language, slogans,
and attempts to make the feature description punchy. Keep operational detail
in the existing dedicated guides and link to them from the README.

## Verification

Review the rendered Markdown structure, verify every referenced path and
command exists, and inspect the final diff for statements that conflict with
the worker's documented safety invariants.
