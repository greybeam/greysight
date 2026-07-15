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
- a PKCS#8 PEM private key supplied directly or through the existing
  `SNOWFLAKE_PRIVATE_KEY_PATH`, plus an optional passphrase.

The ADBC database options use the existing values and always select JWT
authentication. The original PKCS#8 PEM content is passed through
`adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_value`, and the
passphrase is supplied separately through the corresponding password option
when present. The implementation must not normalize the key into a second
unencrypted PEM string, persist it, or log either secret.

For `private_key_path`, Greysight reads the existing file content and passes it
through the same PKCS#8 value option. This preserves encrypted path-based keys;
ADBC's separate path option does not accept encrypted PKCS#8 keys. Missing,
unreadable, malformed, or incorrectly encrypted files retain the current
user-safe configuration failure.

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
supplies its stricter login, request, and client timeouts plus session
keep-alive through the shared configuration path. For the worker, all three
ADBC timeout values use `AUTO_SAVINGS_SOCKET_TIMEOUT_SECONDS`; in particular,
`adbc.snowflake.sql.client_option.client_timeout` bounds network round trips
and response reads below the poll watchdog. The existing
`socket_timeout_seconds < poll_timeout_seconds` validation remains load
bearing. Short-lived API connections map `GREYSIGHT_QUERY_TIMEOUT_SECONDS` to
the same three timeout options.

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

The rewrite recognizes only the exact `%(name)s` placeholder form. Other
percent characters remain literal, and rewriting applies only to approved SQL
text, never to bound values.

The API's source queries, metadata `SHOW` statements, and connection validation
probes continue through the current public functions. The worker continues to
execute only `SHOW WAREHOUSES` and quoted `ALTER WAREHOUSE ... SUSPEND`
statements. Each ADBC cursor receives the `greysight` query tag through the
Snowflake statement option, preserving current query-history attribution.

ADBC row values may differ from the Python connector because its DB-API facade
converts Arrow values. Existing downstream boundaries must continue to accept
timezone-aware `datetime` values and timestamp strings for warehouse identity,
and integer-compatible `int`, `Decimal`, and string values for warehouse
counts. Dashboard serialization continues to normalize finite `Decimal`
values. Naive timestamps and non-integral or non-finite warehouse counts remain
fail closed. No driver numeric-format option is added solely to imitate the old
connector.

## Errors and Resource Lifecycle

Existing user-safe validation and query messages remain unchanged. ADBC error
metadata is adapted as follows:

- `vendor_code` supplies the existing Snowflake numeric error field;
- `sqlstate` supplies SQL state diagnostics;
- ADBC timeout status and timeout failures retain the current timeout message;
- Snowflake vendor code `90064` remains the worker's
  `UNKNOWN_IDEMPOTENT` suspend outcome.

Only ADBC DB-API errors are eligible for Snowflake vendor-code classification.
Vendor codes `2003` and `3001` continue to identify unavailable objects. The
worker classifies `90064` as unknown-but-idempotent only when it is present on
an ADBC error. Missing or zero vendor codes fail closed as ordinary retryable
errors. Worker telemetry may include a bounded, whitespace-normalized ADBC
message for the `90064` path, but never connection options or secrets.

Raw exceptions, PEM content, passphrases, and connection secrets must not reach
user responses or logs.

Every cursor and connection is closed on the same success and failure paths as
today. Cursor cleanup failure after a successful suspend must not convert the
accepted command into a retryable failure. Closing a worker session clears its
reference so the next operation establishes a new connection.

## Dependencies

Replace the pinned `snowflake-connector-python` dependency with a pinned
`adbc-driver-snowflake[dbapi]` installation in the shared package and its API
and worker consumers. The DB-API extra deliberately adds matching pinned
`adbc-driver-manager` and PyArrow transitive dependencies and their native
wheels. Record this Python dependency boundary in
`docs/dependency-compatibility.md`, verify supported API/worker deployment
platforms resolve wheels, and regenerate all three uv lockfiles. The final tree
must contain no imports or dependency entries for `snowflake.connector`.

## Test Strategy

Tests will be replaced in place rather than layered on top of connector-specific
coverage. Assertions that only restate Python connector implementation details
will be removed. Existing behavioral regression tests remain, adapted to ADBC
test doubles and error types.

The focused replacement coverage will verify:

- unchanged onboarding values map to JWT-only ADBC options, with original PEM
  or path-loaded PEM and optional passphrase supplied separately and no
  password-auth branch;
- named approved-SQL parameters become ordered `?` bindings without value
  interpolation;
- query rows retain lowercase dictionary keys and resources close on all paths;
- ADBC-like timestamp and numeric values preserve the worker's timezone-aware
  identity and fail-closed activity parsing contracts;
- validation errors remain user-safe, ADBC diagnostic fields are adapted, and
  absent vendor codes never receive Snowflake-specific classifications;
- worker connections are reused, bounded by configured timeouts, recycled after
  failure, and preserve `SHOW WAREHOUSES`, quoted suspend, and vendor code
  `90064` behavior.

Verification runs the shared package, API, and Automated Savings test suites
and Ruff checks. Because hermetic tests cannot prove real Snowflake driver
compatibility, final acceptance also includes a live smoke test of onboarding,
a dashboard run, and an Automated Savings observation/suspend flow in an
authorized test account.

The live authentication smoke test must cover an encrypted PKCS#8 key and
passphrase. It should exercise both stored PEM onboarding and, for a self-hosted
configuration, `SNOWFLAKE_PRIVATE_KEY_PATH`. The `90064` path remains a
hermetic error-contract test because deliberately inducing that ambiguous
network result is unsafe and unreliable.

## Success Criteria

- Users onboard with exactly the same fields and key-pair flow.
- API dashboard and metadata queries use ADBC and preserve their contracts.
- Automated Savings uses ADBC while preserving its safety invariants.
- No Python Snowflake connector imports or dependencies remain.
- Replacement tests and repository checks pass without redundant test growth.
- A live Snowflake smoke test confirms the ADBC options and authentication flow.
