# Direct Warehouse Suspend — Sanitized Spike Report

**Date:** 2026-07-14  
**Run:** `7e672dbf-0c3c-4b06-bbda-c7f8d8581dc2` (`04:59:17Z`–`05:01:54Z`)  
**Environment:** authorized development Snowflake account; identifiers and credentials omitted  
**Warehouse:** `DEV_WH` (`STANDARD`, `X-Small`, `AUTO_SUSPEND=60`, `AUTO_RESUME=true`)  
**Client:** `snowflake-connector-python 3.12.4`; three independent sessions

## Decision

**Decision: GO.**

The observed idle `quiescing=''` encoding is normalized to zero, its dedicated
regression test passes, and full verification passes.

The spike found no query abort, cancellation, or query error attributable to
`ALTER WAREHOUSE "DEV_WH" SUSPEND`. Every measured compute workload completed.
Suspension also preserved the compared warehouse configuration. This decision
does not classify Snowflake error `90064` as success or as proof that no state
change occurred; the worker contract remains `UNKNOWN_IDEMPOTENT`.

## Method

The harness used three independent sessions and deterministic barriers around a
real analytical pivot workload. The workload returned 2,000 rows, took about 24
seconds without races, and ran with `USE_CACHED_RESULT=FALSE`. This was important:
`SYSTEM$WAIT` did not expose the real compute transition, and `SELECT 1` did not
force a suspended warehouse to resume.

The supplied sanitized notes did not retain Snowflake query IDs. Timings, row
counts, state observations, and connector errno/message evidence are preserved
below. No account identifier, user, host, role, key path, or secret is recorded.

## Availability and race evidence

| Case | Scenario | Query result | Suspend result | Observed state evidence |
| --- | --- | --- | --- | --- |
| A | Query executing before suspend | Success; 2,000 rows; 24.0 s | Accepted in 191 ms | Observer confirmed the measured query with `running=1` before suspend; workload drained successfully. |
| B | Query submitted between deciding `SHOW` and suspend | Success; 2,000 rows; 28.9 s | Accepted in 170 ms | Monotonic order was `SHOW` return → workload submit → suspend submit, less than 1 ms apart. |
| C | Query submitted during transition | Success; 2,000 rows; 43.6 s | Accepted | Captured `state='SUSPENDING'`, `quiescing='100'`, `running=1`; the new workload completed. |
| D | Query submitted after full suspension | Success; 2,000 rows; 25.0 s | Not applicable | A real compute query produced the observed `SUSPENDED → STARTED` auto-resume transition. |
| E | Reissued suspend during transition and after suspension | Linked workload succeeded; 2,000 rows; 22.4 s | Both requests raised `errno=90064` | One request occurred during the transition and one after the warehouse was observed suspended; neither interrupted the linked workload. |

Suspend submission in the measured accepted cases returned in roughly 170–191
ms while compute continued to drain. With real compute, the transition exposed
`SUSPENDING` and `quiescing='100'`. A synthetic `SYSTEM$WAIT` run instead moved
directly to `SUSPENDED` while `running=1`, so it is not a valid substitute for
testing this transition. Similarly, `SELECT 1` can be served without warehouse
compute and is not a valid auto-resume smoke query.

## `90064` evidence and contract

The two Case E observations retained this sanitized connector evidence:

- `errno`: `90064`
- Snowflake message: `Invalid state … cannot be suspended` (sanitized fragment)
- contexts: a reissued request during transition and a request after the
  warehouse was observed `SUSPENDED`
- workload effect: the linked compute workload completed successfully

The supplied notes did not retain the connector exception class or SQLSTATE, so
this report does not invent them. The observations establish only the returned
errno/message and surrounding observed states. They do not establish that every
`90064` means “already suspended,” that the command succeeded, or that it had
any particular state effect.

The approved worker behavior is therefore:

- classify `90064` as `UNKNOWN_IDEMPOTENT`;
- preserve available sanitized connector metadata in correlated logs;
- write no suspend audit event;
- keep the otherwise healthy Snowflake session open;
- return retry backoff and let the next snapshot decide whether another request
  is eligible.

## `SHOW WAREHOUSES` encoding regression

The connector returned `quiescing=''` for an idle warehouse and
`quiescing='100'` while real compute drained. `running` and `queued` were integer
statement counts, and `auto_resume` was the string `'true'`.

The direct-suspend parser normalizes the specifically observed empty string for
`quiescing` to numeric zero. Missing (`None`/absent), malformed, negative,
fractional, and non-finite values continue to fail closed. The dedicated
regression using an otherwise eligible row with `quiescing=''` passes. The full
verification matrix also passes: 228 worker, 516 API, 65 shared, and 380 web
tests, plus typecheck, full lint, Ruff, and diff checks.

## Configuration invariance

The following fields matched byte-for-byte between baseline and final snapshots:
`actives`, `auto_resume`, `auto_suspend`, `available`, `comment`, `created_on`,
`disabled_reasons`, `failed`, `generation`, `is_current`, `is_default`,
`max_query_performance_level`, `name`, `other`, `owner`, `owner_role_type`,
`pendings`, `provisioning`, `query_throughput_multiplier`, `resource_constraint`,
`resource_monitor`, `size`, `suspended`, `tables`, `type`, and `uuid`.

Volatile runtime or metadata fields were excluded: `state`, `started_clusters`,
`running`, `queued`, `quiescing`, `resumed_on`, and `updated_on`. In particular,
`updated_on` changed with state transitions and is not evidence of a configuration
mutation.

## Privilege observation

The first suspend attempt failed with Snowflake access-control error
`003001 / 42501` (`errno=3001`) and a message indicating required warehouse
usage context. The smoke role was then granted `MODIFY, OPERATE` on `DEV_WH`,
after which the test proceeded. That combined grant proves what was used for
this run; it does not prove that `MODIFY` is universally required.

Provisioning may instead use individual warehouse `OPERATE` with the required
`USAGE` context, or the documented account-level `MANAGE WAREHOUSES` grant.

## Final state and cleanup

The warehouse was left in its observed final `SUSPENDED` state as authorized;
its properties were not changed or restored. Harness-owned outstanding queries
at exit were `0`, and all three sessions closed cleanly. No object was created,
dropped, or altered apart from the authorized `RESUME`/`SUSPEND` commands on
`DEV_WH`.
