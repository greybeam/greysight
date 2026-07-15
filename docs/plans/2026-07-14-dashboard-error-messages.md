# Dashboard Error Messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show actionable, safely classified Snowflake errors in the core dashboard and give unknown failures a GitHub reporting path.

**Architecture:** Classify raw Snowflake failures once in `shared/connect`, preserve the fixed error code through source outcomes and dashboard-run finalization, and expose structured detail for failed dashboard requests. The web client validates only allowlisted codes, maps them to curated copy, and renders a GitHub report link only for unknown failures.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, Snowflake Python Connector, TypeScript, React 19, Next.js, Vitest, Testing Library, pytest.

---

## File Map

- `shared/connect/src/greysight_connect/snowflake_client.py`: own Snowflake failure codes, safe classification, and safe query-error propagation.
- `shared/connect/src/greysight_connect/__init__.py`: export the failure-code type.
- `shared/connect/tests/test_snowflake_client.py`: prove classification and raw-message redaction.
- `apps/api/app/services/snowflake_client.py`: re-export the shared failure-code type.
- `apps/api/app/services/parallel_source_runner.py`: preserve safe error metadata when a source becomes unavailable.
- `apps/api/app/services/dashboard_datasets.py`: choose a representative safe failure when every dashboard source group is unavailable.
- `apps/api/app/routes/dashboard_runs.py`: persist run error metadata and return structured deferred-source failures.
- `apps/api/tests/test_dashboard_runs_async.py`: prove failed runs expose only the safe code/message.
- `apps/api/tests/test_dashboard_run_sources.py`: prove deferred failures use structured safe detail.
- `apps/web/src/lib/dashboard-errors.ts`: validate fixed codes, provide curated messages, and define the GitHub issue URL.
- `apps/web/src/lib/dashboard-api.ts`: parse structured failed responses into a typed dashboard error.
- `apps/web/src/lib/dashboard-contracts.ts`: parse the existing backend `error_code` run field.
- `apps/web/src/components/dashboard/section-empty-state.tsx`: accept React content so the existing empty state can include an accessible link.
- `apps/web/src/components/dashboard/cost-dashboard.tsx`: retain error codes in load state and render actionable/catch-all failures.
- `apps/web/src/components/dashboard/spend-sections.tsx`: replace the AI detail's permanent error skeleton with the same safe error surface.
- `apps/web/src/lib/dashboard-api.test.ts`: prove unknown/malformed failures cannot inject response text.
- `apps/web/src/components/dashboard/cost-dashboard.test.tsx`: prove unknown dashboard failures show the GitHub report link.

### Task 1: Classify and preserve safe Snowflake failures

**Files:**
- Modify: `shared/connect/src/greysight_connect/snowflake_client.py`
- Modify: `shared/connect/src/greysight_connect/__init__.py`
- Test: `shared/connect/tests/test_snowflake_client.py`

- [ ] **Step 1: Extend the existing network-policy test first**

Add an assertion that the raised safe exception carries `error_code == "network_policy"`, and add a query-path regression that calls `execute_source_query` with a connector failure containing a secret marker and asserts the resulting `SnowflakeQueryError` has the same code, contains only curated text, and omits the marker.

```python
assert exc_info.value.error_code == "network_policy"
assert "PEMSECRETMARKER" not in str(exc_info.value)
```

- [ ] **Step 2: Run the shared test and verify RED**

Run: `rtk test uv run --directory shared/connect pytest tests/test_snowflake_client.py -q`

Expected: FAIL because the exception has no `error_code` and query execution collapses the failure to generic text.

- [ ] **Step 3: Add the fixed classifier and safe exception metadata**

Define an allowlisted literal and one classifier:

```python
SnowflakeFailureCode = Literal[
    "network_policy", "authentication", "timeout", "role", "warehouse", "unknown"
]

class SnowflakeQueryError(RuntimeError):
    def __init__(self, message: str, *, error_code: SnowflakeFailureCode = "unknown"):
        super().__init__(message)
        self.error_code = error_code
```

Give `SnowflakeValidationError` the same field. Implement `_failure_code(exc)` using the existing message predicates, make `_base_user_safe_message` map the returned code to curated copy, and make both connection and query catches raise safe exceptions with that code. Do not attach the raw exception as a cause.

- [ ] **Step 4: Run the shared test and verify GREEN**

Run: `rtk test uv run --directory shared/connect pytest tests/test_snowflake_client.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the shared classifier**

```bash
rtk git add shared/connect/src/greysight_connect/snowflake_client.py shared/connect/src/greysight_connect/__init__.py shared/connect/tests/test_snowflake_client.py
rtk git commit -m "feat: classify safe Snowflake failures"
```

### Task 2: Carry safe failures through dashboard API responses

**Files:**
- Modify: `apps/api/app/services/snowflake_client.py`
- Modify: `apps/api/app/services/parallel_source_runner.py`
- Modify: `apps/api/app/services/dashboard_datasets.py`
- Modify: `apps/api/app/routes/dashboard_runs.py`
- Test: `apps/api/tests/test_dashboard_runs_async.py`
- Test: `apps/api/tests/test_dashboard_run_sources.py`

- [ ] **Step 1: Write the failed-run and deferred-source tests first**

For the worker, raise a `DashboardSourcesUnavailableError` carrying `network_policy` and assert the finalized run has:

```python
assert final.error_code == "network_policy"
assert final.user_safe_message == NETWORK_POLICY_MESSAGE
assert "SECRET" not in final.model_dump_json()
```

For the deferred route, make `execute_source_query` raise `SnowflakeQueryError(NETWORK_POLICY_MESSAGE, error_code="network_policy")` and assert the `502` body is:

```python
assert response.json() == {
    "detail": {"code": "network_policy", "message": NETWORK_POLICY_MESSAGE}
}
```

- [ ] **Step 2: Run the API tests and verify RED**

Run: `rtk test uv run --directory apps/api pytest tests/test_dashboard_runs_async.py tests/test_dashboard_run_sources.py -q`

Expected: FAIL because source outcomes and finalized runs discard the safe code and the deferred route returns a generic string.

- [ ] **Step 3: Preserve safe metadata in source outcomes**

Extend `SourceOutcome` with nullable `error_code` and `user_safe_message`. In `run_sources_parallel`, catch the configured unavailable exception as `exc` and copy only its safe attributes:

```python
outcome = SourceOutcome(
    key=job.key,
    rows=None,
    available=False,
    error_code=getattr(exc, "error_code", "unknown"),
    user_safe_message=str(exc),
)
```

Keep successful outcomes unchanged and never retain the exception object.

- [ ] **Step 4: Preserve the representative all-sources failure**

Give `DashboardSourcesUnavailableError` safe `error_code` and `user_safe_message` fields. When both required groups are unavailable, choose the first unavailable outcome with safe metadata; fall back to `unknown` plus the existing generic billing/Account Usage message.

- [ ] **Step 5: Persist and return structured failures**

Extend `InMemoryDashboardRunRepository.finalize_run` to accept `error_code` and `user_safe_message` and update the already-existing `DashboardRun` fields. In `_run_dashboard_worker`, finalize a known source failure with those fields while preserving the neutral fallback for unexpected exceptions.

Catch `SnowflakeQueryError` separately in `trigger_dashboard_source`, mark the source failed, and return the existing `502` with a safe object detail:

```python
raise HTTPException(
    status_code=status.HTTP_502_BAD_GATEWAY,
    detail={"code": exc.error_code, "message": str(exc)},
) from None
```

Leave membership checks and all success responses unchanged.

- [ ] **Step 6: Run the API tests and verify GREEN**

Run: `rtk test uv run --directory apps/api pytest tests/test_dashboard_runs_async.py tests/test_dashboard_run_sources.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the dashboard API contract**

```bash
rtk git add apps/api/app/services/snowflake_client.py apps/api/app/services/parallel_source_runner.py apps/api/app/services/dashboard_datasets.py apps/api/app/routes/dashboard_runs.py apps/api/tests/test_dashboard_runs_async.py apps/api/tests/test_dashboard_run_sources.py
rtk git commit -m "feat: expose safe dashboard failure details"
```

### Task 3: Parse only allowlisted dashboard errors in the web client

**Files:**
- Create: `apps/web/src/lib/dashboard-errors.ts`
- Modify: `apps/web/src/lib/dashboard-api.ts`
- Modify: `apps/web/src/lib/dashboard-contracts.ts`
- Test: `apps/web/src/lib/dashboard-api.test.ts`

- [ ] **Step 1: Write the client boundary test first**

Add one test for a structured `network_policy` response and one malformed/unknown response containing `SECRET_RESPONSE_MARKER`. Assert the former throws a typed error with the known code and the latter throws `unknown` without including the marker in its message.

- [ ] **Step 2: Run the web API test and verify RED**

Run from `apps/web`: `rtk test npx vitest run src/lib/dashboard-api.test.ts`

Expected: FAIL because `fetchJson` currently throws only an HTTP-status string.

- [ ] **Step 3: Add the fixed frontend contract**

In `dashboard-errors.ts`, define the same fixed code union, curated message map, `GITHUB_ISSUE_URL`, a code guard, and `DashboardApiError`. The class message must always come from the local curated map.

Update `fetchJson` to parse only `detail.code`; ignore arbitrary `detail.message` and raw response text. Unknown/malformed/non-JSON bodies become `new DashboardApiError("unknown")`.

Add `error_code` to `DashboardRun`, its optional parser keys, and validate it against the fixed code guard.

- [ ] **Step 4: Run the web API test and verify GREEN**

Run from `apps/web`: `rtk test npx vitest run src/lib/dashboard-api.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit the browser error boundary**

```bash
rtk git add apps/web/src/lib/dashboard-errors.ts apps/web/src/lib/dashboard-api.ts apps/web/src/lib/dashboard-contracts.ts apps/web/src/lib/dashboard-api.test.ts
rtk git commit -m "feat: parse safe dashboard errors"
```

### Task 4: Render actionable dashboard failures and the report link

**Files:**
- Modify: `apps/web/src/components/dashboard/section-empty-state.tsx`
- Modify: `apps/web/src/components/dashboard/cost-dashboard.tsx`
- Modify: `apps/web/src/components/dashboard/spend-sections.tsx`
- Test: `apps/web/src/components/dashboard/cost-dashboard.test.tsx`

- [ ] **Step 1: Write the unknown-failure UI test first**

Reject the initial dashboard request with `new DashboardApiError("unknown")`. Assert the dashboard leaves the skeleton state, displays the curated catch-all, and renders an external link named `Report this issue` whose `href` is `GITHUB_ISSUE_URL`.

- [ ] **Step 2: Run the component test and verify RED**

Run from `apps/web`: `rtk test npx vitest run src/components/dashboard/cost-dashboard.test.tsx`

Expected: FAIL because the current catch block discards the error and renders no report link.

- [ ] **Step 3: Retain the failure code in dashboard state**

Add `errorCode?: DashboardFailureCode` to `LoadState`. For failed run responses, prefer the parsed `run.error_code`; for thrown request errors, normalize with the helper from `dashboard-errors.ts`. Use the curated message map instead of hard-coded generic copy.

- [ ] **Step 4: Add the catch-all report action**

Allow `SectionEmptyState.message` to accept `ReactNode`. For `unknown` failures, append:

```tsx
<a href={GITHUB_ISSUE_URL} target="_blank" rel="noreferrer">
  Report this issue
</a>
```

Use the same action in the existing inline fallback when a stale view remains visible.

- [ ] **Step 5: Stop the deferred AI section from hanging on errors**

Change the AI error state to retain the normalized code/message. When the deferred trigger or poll fails, render the safe empty/error state inside the AI section instead of leaving chart skeletons indefinitely. Unknown AI failures use the same GitHub report link.

- [ ] **Step 6: Run the component test and verify GREEN**

Run from `apps/web`: `rtk test npx vitest run src/components/dashboard/cost-dashboard.test.tsx`

Expected: PASS.

- [ ] **Step 7: Commit the dashboard UI**

```bash
rtk git add apps/web/src/components/dashboard/section-empty-state.tsx apps/web/src/components/dashboard/cost-dashboard.tsx apps/web/src/components/dashboard/spend-sections.tsx apps/web/src/components/dashboard/cost-dashboard.test.tsx
rtk git commit -m "feat: show actionable dashboard failures"
```

### Task 5: Full verification and security review

**Files:**
- Verify only; no planned production edits.

- [ ] **Step 1: Run shared package tests**

Run: `rtk test uv run --directory shared/connect pytest`

Expected: PASS.

- [ ] **Step 2: Run API tests**

Run: `rtk npm run test:api`

Expected: PASS.

- [ ] **Step 3: Run web tests**

Run: `rtk npm run test:web`

Expected: PASS.

- [ ] **Step 4: Run typecheck and lint**

Run: `rtk npm run typecheck`

Expected: PASS.

Run: `rtk npm run lint`

Expected: PASS.

- [ ] **Step 5: Review the security boundary**

Inspect the final diff and confirm raw Snowflake exception text, response bodies, credentials, account identifiers, and stack traces cannot reach React rendering. Confirm external links use `target="_blank"` with `rel="noreferrer"`.

- [ ] **Step 6: Record final status**

Run: `rtk git status --short`

Expected: only the implementation-plan checklist may remain modified if it was checked during execution; no unrelated files.
