# Dashboard Error Messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface existing curated Snowflake failure messages in the core dashboard and give unknown failures a GitHub reporting path.

**Architecture:** Reuse the safe-message classifier already owned by `shared/connect`; do not add a second error-code system. Carry only optional `user_safe_message` text through failed dashboard sources, prefer it in the existing frontend run contract, and treat missing or malformed detail as an unknown reportable failure.

**Tech Stack:** Python 3.12, FastAPI, TypeScript, React 19, Vitest, Testing Library, pytest.

---

### Task 1: Preserve existing safe Snowflake messages

**Files:**
- Modify: `shared/connect/src/greysight_connect/snowflake_client.py`
- Modify: `apps/api/app/services/parallel_source_runner.py`
- Modify: `apps/api/app/services/dashboard_datasets.py`
- Modify: `apps/api/app/routes/dashboard_runs.py`
- Test: `shared/connect/tests/test_snowflake_client.py`

- [ ] **Step 1: Write the failing security regression**

Extend the existing network-policy test through `execute_source_query`. Assert
that the resulting `SnowflakeQueryError` contains the existing curated network
policy copy and does not contain the raw `PEMSECRETMARKER`.

- [ ] **Step 2: Verify RED**

Run: `rtk test uv run --directory shared/connect pytest tests/test_snowflake_client.py -q`

Expected: FAIL because source queries currently replace every connection failure
with `Could not query Snowflake.`

- [ ] **Step 3: Implement the narrow propagation path**

In `execute_source_query`, preserve `str(exc)` when `_connect` raises the already
sanitized `SnowflakeValidationError`; use the existing `_user_safe_message` for
query-execution failures. Never retain the raw exception as a cause.

Add optional `user_safe_message` to unavailable `SourceOutcome` values and copy
only sanitized `SnowflakeQueryError` text into it. When every required dashboard
source group is unavailable, initialize `DashboardSourcesUnavailableError` with
the first safe outcome message, falling back to the existing generic message.

Extend `finalize_run` to populate the already-existing `DashboardRun.user_safe_message`
field. The worker passes the known source failure into that field and leaves raw
exception detail out of `error`.

Catch `SnowflakeQueryError` separately for the deferred AI route and return its
sanitized string as `detail.user_safe_message` with the existing `502` status.
Keep the existing plain-string generic catch for all other exceptions; this
shape difference is the known/unknown signal and needs no error enum.

- [ ] **Step 4: Verify GREEN**

Run: `rtk test uv run --directory shared/connect pytest tests/test_snowflake_client.py -q`

Expected: PASS.

### Task 2: Display safe messages and make unknown failures reportable

**Files:**
- Modify: `apps/web/src/lib/dashboard-api.ts`
- Modify: `apps/web/src/components/dashboard/cost-dashboard.tsx`
- Modify: `apps/web/src/components/dashboard/section-empty-state.tsx`
- Modify: `apps/web/src/components/dashboard/spend-sections.tsx`
- Test: `apps/web/src/components/dashboard/cost-dashboard.test.tsx`

- [ ] **Step 1: Write the failing user-visible regression**

Reject an initial dashboard request with an unknown error. Assert the dashboard
leaves its skeleton state, shows neutral catch-all copy, and renders an external
`Report this issue` link to:

`https://github.com/greybeam/greysight/issues/new`

- [ ] **Step 2: Verify RED**

Run from `apps/web`: `rtk test npx vitest run src/components/dashboard/cost-dashboard.test.tsx`

Expected: FAIL because the current catch block has no report action.

- [ ] **Step 3: Implement the small frontend surface**

Add a small `DashboardApiError` carrying optional `userSafeMessage`. Update
dashboard `fetchJson` to read only a string at `detail.user_safe_message` from
failed JSON responses; ignore plain strings, malformed JSON, and every other
body field. Do not render a raw response body.

In `cost-dashboard.tsx`:

- prefer `run.user_safe_message ?? run.error`;
- preserve only `DashboardApiError.userSafeMessage` when available;
- use neutral catch-all copy for browser/malformed failures; and
- show `Report this issue` only for the catch-all state.

Allow `SectionEmptyState.message` to accept `ReactNode` so the existing empty
state can contain the accessible external link.

Carry a message in `AiSpendDetailState` when the deferred request fails. Render
that error inside the AI section instead of continuing to show loading skeletons;
unknown AI failures use the same report link.

- [ ] **Step 4: Verify GREEN**

Run from `apps/web`: `rtk test npx vitest run src/components/dashboard/cost-dashboard.test.tsx`

Expected: PASS.

### Task 3: Repository verification

- [ ] Run: `rtk test uv run --directory shared/connect pytest`
- [ ] Run: `rtk npm run test:api`
- [ ] Run: `rtk npm run test:web`
- [ ] Run: `rtk npm run typecheck`
- [ ] Run: `rtk npm run lint`
- [ ] Inspect the final diff for raw Snowflake messages, secrets, stack traces,
      or unrelated changes.
