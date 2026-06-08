# Greysight Agent Guidelines

Greysight is a free Snowflake cost observability tool.

## Development Guidelines
Always use subagent driven development if possible. You are the manager delegating work to other workers. This allows you to retain full context as long as reasonable possible.

## Structure
<!-- placeholder, put high level project structure here -->

## Where to look
<!-- placeholder, create simple chart showing task, location, notes. Task being a specific function of this project, where it's located, and any additional notes about that directory or file -->

## Guides
<!-- placeholder, any agent guidelines are hyperlinked here and live in the /docs directory -->

## Core Principles
1. **Every change needs a test.** Must fail without change, pass with it
2. **Assert invariants.** Don't silently fail. Don't hedge with if-statements
3. **Own your regressions.** If tests fail after your change, they are your regressions. Debug them directly. Never stash/revert to "check if they fail on main" — that wastes time and is categorically banned.
4. **Validate your hypotheses.**: If you suspect a given cause for a bug, validate it and provide incontrovertible evidence. NEVER make unearned assumptions.