# Dashboard Error Messages

## Goal

Make core dashboard failures understandable without exposing Snowflake exception
details. When Greysight recognizes the failure, the dashboard gives the user an
actionable next step. When it does not, the dashboard offers a direct path to
report the issue to Greybeam through GitHub.

## Scope

This change applies only to the core dashboard, including starting a Snowflake
run, loading its prepared view, and fetching the deferred AI source. Automated
Savings and its background worker are explicitly out of scope.

## Error Contract

The backend classifies Snowflake failures into a small fixed set of safe codes:

- `network_policy`
- `authentication`
- `timeout`
- `role`
- `warehouse`
- `unknown`

The API pairs the code with curated user-safe copy. Raw connector messages,
stack traces, account identifiers, credential material, and other Snowflake
exception details never cross the API boundary.

The classification is preserved through dashboard source execution so that a
failed run and a failed deferred source do not collapse into an unhelpful
generic `502`. Existing authentication, organization membership checks, and
HTTP status behavior remain unchanged. Successful response shapes remain
unchanged; failed dashboard requests gain the structured safe error detail.

## Frontend Behavior

The dashboard API client reads the structured safe error response for failed
requests. The dashboard presents the corresponding actionable message for a
known code.

Unknown failures, malformed error responses, browser network failures, and
other unclassified errors use a catch-all message and include a **Report this
issue** link to:

`https://github.com/greybeam/greysight/issues/new`

The link opens in a new tab with standard external-link protections. Existing
dashboard data remains visible when a refresh fails; the improved error appears
in the existing inline failure surface. A failure with no usable dashboard view
uses the existing empty-state surface.

## Security

Error codes are an allowlisted enum, not connector text interpreted by the
browser. Server-side logging retains only the existing sanitized diagnostic
metadata. The frontend never renders raw response bodies.

## Verification

Testing stays focused on regressions that typechecking cannot catch:

1. An API test proves a representative Snowflake connection failure is returned
   as the correct safe category without leaking its raw message.
2. A dashboard test proves an unclassified failure shows the catch-all state and
   GitHub reporting path.

Run the targeted API and web tests first, followed by the repository's API test,
web test, lint, and typecheck commands.
