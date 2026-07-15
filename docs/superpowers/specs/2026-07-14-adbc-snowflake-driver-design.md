# ADBC Snowflake Driver Migration Design

## Goal

Replace every use of `snowflake-connector-python` with the Apache Arrow ADBC
Snowflake DB-API driver while preserving Greysight's current onboarding,
query, validation, and Automated Savings behavior.

## Scope

The migration covers the shared `greysight-connect` package, the API, and the
Automated Savings worker. It removes all runtime, test, and dependency
references to `snowflake.connector` and replaces them with
`adbc_driver_snowflake.dbapi`.

The change does not add authentication methods, alter onboarding fields,
change approved Snowflake SQL semantics, or redesign dashboard metadata.

## Authentication and Connection Configuration

Greysight remains key-pair-only. Users continue to provide:

- the required Snowflake account identifier, such as `ORG-ACCOUNT`, obtained
  from `CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME()`;
- user, role, and warehouse;
- optional database and schema;
- a PKCS#8 PEM private key and an optional passphrase.

The ADBC database options use the existing values and always select JWT
authentication. The original PKCS#8 PEM content is passed through
`adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_value`, and the
passphrase is supplied separately through the corresponding password option
when present. The implementation must not normalize the key into a second
unencrypted PEM string, persist it, or log either secret.

The existing `account_locator` remains separate from the connection account
identifier. It is the value returned by `CURRENT_ACCOUNT()` and continues to
support Organization Usage filters, cache isolation, and dashboard metadata.
It is not passed to ADBC as a connection option.

`SnowflakeConnectionConfig` remains the source of connection configuration.
It will expose ADBC database options instead of Python connector keyword
arguments. A shared connection helper will invoke
`adbc_driver_snowflake.dbapi.connect` with autocommit enabled to preserve the
current command semantics. The API uses short-lived connections as it does
today. The worker continues to maintain one warm connection per tenant and
supplies its stricter login, request, and network timeouts plus session
keep-alive through the shared configuration path.

## Query Execution

Public Greysight query functions retain their current signatures and return
`list[dict[str, Any]]` with lowercase column names.

Approved SQL assets retain their named `%(name)s` placeholders. Immediately
before execution, the shared connector adapter replaces those placeholders
with ADBC/Snowflake `?` markers and creates a positional parameter sequence in
placeholder occurrence order. Values are always bound; they are never
interpolated into SQL. Repeated placeholders produce repeated positional
values. Existing boundary validation for `window_days`, `account_locator`, and
unknown bind keys remains in force.

The API's source queries, metadata `SHOW` statements, and connection validation
probes continue through the current public functions. The worker continues to
execute only `SHOW WAREHOUSES` and quoted `ALTER WAREHOUSE ... SUSPEND`
statements.

## Errors and Resource Lifecycle

Existing user-safe validation and query messages remain unchanged. ADBC error
metadata is adapted as follows:

- `vendor_code` supplies the existing Snowflake numeric error field;
- `sqlstate` supplies SQL state diagnostics;
- ADBC timeout status and timeout failures retain the current timeout message;
- Snowflake vendor code `90064` remains the worker's
  `UNKNOWN_IDEMPOTENT` suspend outcome.

Raw exceptions, PEM content, passphrases, and connection secrets must not reach
user responses or logs.

Every cursor and connection is closed on the same success and failure paths as
today. Cursor cleanup failure after a successful suspend must not convert the
accepted command into a retryable failure. Closing a worker session clears its
reference so the next operation establishes a new connection.

## Dependencies

Replace the pinned `snowflake-connector-python` dependency with a pinned
`adbc-driver-snowflake` DB-API installation in the shared package and its API
and worker consumers. Regenerate all three uv lockfiles. The final tree must
contain no imports or dependency entries for `snowflake.connector`.

## Test Strategy

Tests will be replaced in place rather than layered on top of connector-specific
coverage. Assertions that only restate Python connector implementation details
will be removed. Existing behavioral regression tests remain, adapted to ADBC
test doubles and error types.

The focused replacement coverage will verify:

- unchanged onboarding values map to JWT-only ADBC options, with original PEM
  and optional passphrase supplied separately and no password-auth branch;
- named approved-SQL parameters become ordered `?` bindings without value
  interpolation;
- query rows retain lowercase dictionary keys and resources close on all paths;
- validation errors remain user-safe and ADBC diagnostic fields are adapted;
- worker connections are reused, bounded by configured timeouts, recycled after
  failure, and preserve `SHOW WAREHOUSES`, quoted suspend, and vendor code
  `90064` behavior.

Verification runs the shared package, API, and Automated Savings test suites
and Ruff checks. Because hermetic tests cannot prove real Snowflake driver
compatibility, final acceptance also includes a live smoke test of onboarding,
a dashboard run, and an Automated Savings observation/suspend flow in an
authorized test account.

## Success Criteria

- Users onboard with exactly the same fields and key-pair flow.
- API dashboard and metadata queries use ADBC and preserve their contracts.
- Automated Savings uses ADBC while preserving its safety invariants.
- No Python Snowflake connector imports or dependencies remain.
- Replacement tests and repository checks pass without redundant test growth.
- A live Snowflake smoke test confirms the ADBC options and authentication flow.
