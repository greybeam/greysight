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

The backend reuses the Snowflake client's existing safe-message classifier.
Known failures such as network-policy, authentication, timeout, role, and
warehouse problems carry curated `user_safe_message` copy through dashboard
source execution. Unknown failures carry no user-safe message and fall back to
neutral dashboard copy.

Raw connector messages, stack traces, account identifiers, credential material,
and other Snowflake exception details never cross the API boundary. Existing
authentication, organization membership checks, HTTP statuses, and successful
response shapes remain unchanged. Known deferred-source failures include a
curated `detail.user_safe_message`; generic error bodies remain unchanged.

## Frontend Behavior

The dashboard prefers a run's existing `user_safe_message` over its generic
`error` field. The API client reads only `detail.user_safe_message` from a
failed deferred-source response; plain strings, malformed or missing detail,
and browser-level failures remain unknown.

Unknown failures, malformed error responses, browser network failures, and
other unclassified errors use a catch-all message and include a **Report this
issue** link to:

`https://github.com/greybeam/greysight/issues/new`

The link opens in a new tab with standard external-link protections. Existing
dashboard data remains visible when a refresh fails; the improved error appears
in the existing inline failure surface. A failure with no usable dashboard view
uses the existing empty-state surface.

## Security

The backend is the sole owner of Snowflake exception classification and curated
copy. Server-side logging retains only the existing sanitized diagnostic
metadata. The frontend never renders an unselected raw response body.

## Verification

Testing stays focused on regressions that typechecking cannot catch:

1. A shared/API test proves a representative Snowflake connection failure is
   returned as curated safe copy without leaking its raw message.
2. A dashboard test proves an unclassified failure shows the catch-all state and
   GitHub reporting path.

Run the targeted API and web tests first, followed by the repository's API test,
web test, lint, and typecheck commands.
