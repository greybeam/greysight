# ADBC Snowflake Driver Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every `snowflake-connector-python` use with the ADBC Snowflake DB-API driver without changing onboarding, approved SQL, dashboard contracts, or Automated Savings safety behavior.

**Architecture:** Keep `SnowflakeConnectionConfig` and the three existing shared query entry points as the compatibility boundary. Build JWT-only ADBC database options from the existing account and PKCS#8 inputs, translate approved named binds to ADBC positional binds immediately before execution, and adapt ADBC errors into the existing user-safe and worker contracts. The API continues using short-lived connections; the worker continues reusing one bounded connection per tenant.

**Tech Stack:** Python 3.12, `adbc-driver-snowflake[dbapi]`, `adbc-driver-manager`, PyArrow, cryptography, pytest, Ruff, uv

---

## File map

- `shared/connect/src/greysight_connect/snowflake_client.py`: ADBC options, connection/cursor creation, bind translation, row conversion, validation, and error adaptation.
- `shared/connect/src/greysight_connect/__init__.py`: export the shared ADBC cursor helper used by the worker.
- `shared/connect/tests/test_snowflake_client.py`: replace Python-connector configuration, binding, validation, and error tests in place.
- `shared/connect/tests/test_metadata_query.py`: replace the direct connector patch with an ADBC DB-API test double.
- `apps/auto-savings/src/auto_savings/snowflake_session.py`: warm ADBC session, worker timeout overrides, and ADBC error classification.
- `apps/auto-savings/tests/test_snowflake_session.py`: replace connector-specific session/error tests in place.
- `apps/auto-savings/tests/test_engine.py`: replace the imported Python-connector error used by engine retry tests.
- `shared/connect/pyproject.toml`, `apps/api/pyproject.toml`, `apps/auto-savings/pyproject.toml`: pin ADBC DB-API and remove the old connector/unused PyOpenSSL pins.
- `shared/connect/uv.lock`, `apps/api/uv.lock`, `apps/auto-savings/uv.lock`: regenerated dependency graphs.
- `docs/dependency-compatibility.md`: document the native ADBC/PyArrow deployment boundary.
- `docs/security-model.md`: rename the bind-parameter implementation reference from the old connector to ADBC.

### Task 1: Replace the dependency graph

**Files:**
- Modify: `shared/connect/pyproject.toml`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/auto-savings/pyproject.toml`
- Modify: `shared/connect/uv.lock`
- Modify: `apps/api/uv.lock`
- Modify: `apps/auto-savings/uv.lock`
- Modify: `docs/dependency-compatibility.md`

- [ ] **Step 1: Replace direct dependency pins**

In all three `pyproject.toml` files, replace:

```toml
"snowflake-connector-python==3.12.4",
```

with:

```toml
"adbc-driver-snowflake[dbapi]==1.11.0",
```

Remove `pyopenssl` from `shared/connect/pyproject.toml` and
`pyopenssl==24.3.0` from `apps/api/pyproject.toml`; repository search confirms
there are no direct imports. Keep `cryptography` in `shared/connect` because it
validates PEM/passphrase pairs without serializing a second unencrypted key.

- [ ] **Step 2: Regenerate each lockfile independently**

Run:

```bash
rtk uv lock --directory shared/connect
rtk uv lock --directory apps/api
rtk uv lock --directory apps/auto-savings
```

Expected: all commands succeed; each lock contains `adbc-driver-snowflake`,
`adbc-driver-manager`, and `pyarrow`, and contains no
`snowflake-connector-python` package.

- [ ] **Step 3: Verify the pinned API names before production edits**

Run:

```bash
rtk uv run --directory shared/connect python -c "import adbc_driver_manager, adbc_driver_snowflake, adbc_driver_snowflake.dbapi; print(adbc_driver_snowflake.DatabaseOptions.CLIENT_TIMEOUT.value); print(adbc_driver_snowflake.StatementOptions.QUERY_TAG.value)"
```

Expected output includes:

```text
adbc.snowflake.sql.client_option.client_timeout
adbc.snowflake.statement.query_tag
```

- [ ] **Step 4: Document the compatibility boundary**

Append a Python section to `docs/dependency-compatibility.md` stating that
`adbc-driver-snowflake[dbapi]==1.11.0` is pinned across the shared package, API,
and worker; its matching driver-manager and PyArrow native wheels are required;
and the supported Python 3.12 Debian deployment images must continue resolving
binary wheels.

- [ ] **Step 5: Commit the dependency-only change**

```bash
rtk git add shared/connect/pyproject.toml shared/connect/uv.lock apps/api/pyproject.toml apps/api/uv.lock apps/auto-savings/pyproject.toml apps/auto-savings/uv.lock docs/dependency-compatibility.md
rtk git commit -m "build: replace Snowflake Python connector with ADBC"
```

### Task 2: Build JWT-only ADBC connection options

**Files:**
- Modify: `shared/connect/tests/test_snowflake_client.py`
- Modify: `shared/connect/src/greysight_connect/snowflake_client.py`

- [ ] **Step 1: Replace configuration tests with ADBC contract tests**

Replace tests that patch `snowflake.connector.connect`, assert
`connector_kwargs()`, or assert DER output with focused tests for
`adbc_db_kwargs()`:

```python
def _generate_pem(*, passphrase: str | None = None) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode("utf-8"))
        if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    ).decode("utf-8")


def test_adbc_db_kwargs_preserves_existing_jwt_onboarding_contract() -> None:
    pem = _generate_pem(passphrase="hunter2")
    config = SnowflakeConnectionConfig(
        account="ORG-ACCOUNT",
        user="svc",
        role="GREYSIGHT_RL",
        warehouse="GREYSIGHT_WH",
        database=None,
        schema=None,
        private_key_pem=pem,
        private_key_passphrase="hunter2",
        query_timeout_seconds=120,
    )

    options = config.adbc_db_kwargs()

    assert options["adbc.snowflake.sql.account"] == "ORG-ACCOUNT"
    assert options["username"] == "svc"
    assert options["adbc.snowflake.sql.role"] == "GREYSIGHT_RL"
    assert options["adbc.snowflake.sql.warehouse"] == "GREYSIGHT_WH"
    assert options["adbc.snowflake.sql.db"] == "SNOWFLAKE"
    assert options["adbc.snowflake.sql.schema"] == "ACCOUNT_USAGE"
    assert options["adbc.snowflake.sql.auth_type"] == "auth_jwt"
    assert options["adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_value"] == pem
    assert options["adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_password"] == "hunter2"
    assert "password" not in options
```

Add one path test that writes the same encrypted PEM to `tmp_path`, supplies
`private_key_path`, and asserts the value/password options are identical. Keep
the existing missing-field, malformed-account, malformed-PEM, wrong-passphrase,
environment-loading, and secret-free `repr` behaviors, renaming only calls from
`connector_kwargs()` to `adbc_db_kwargs()`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
rtk uv run --directory shared/connect pytest tests/test_snowflake_client.py -k 'adbc_db_kwargs or malformed or repr or environment' -q
```

Expected: FAIL because `adbc_db_kwargs` does not exist and the old connector
method still returns Python-connector keys.

- [ ] **Step 3: Implement PEM validation without normalization**

Replace `_load_private_key_der()` with `_load_private_key_pem()`:

```python
def _load_private_key_pem(self) -> str:
    if self.private_key_pem is None and self.private_key_path is None:
        raise SnowflakeConfigurationError("Snowflake connection is not configured.")
    try:
        if self.private_key_pem is not None:
            pem = self.private_key_pem
        else:
            assert self.private_key_path is not None
            pem = self.private_key_path.read_text(encoding="utf-8")
        password = (
            self.private_key_passphrase.encode("utf-8")
            if self.private_key_passphrase
            else None
        )
        serialization.load_pem_private_key(pem.encode("utf-8"), password=password)
        return pem
    except (OSError, TypeError, ValueError):
        raise SnowflakeConfigurationError(
            "Snowflake private key could not be loaded."
        ) from None
```

This validates the existing onboarding input but returns the original string;
it does not serialize an unencrypted copy.

- [ ] **Step 4: Implement the ADBC option builder**

Add `adbc_db_kwargs`, using the ADBC enum values for option names and literal
`auth_jwt` for the documented authentication value:

```python
def adbc_db_kwargs(
    self,
    *,
    timeout_seconds: int | None = None,
    keep_session_alive: bool = False,
) -> dict[str, str]:
    required_values = {
        "SNOWFLAKE_ACCOUNT": self.account,
        "SNOWFLAKE_USER": self.user,
        "SNOWFLAKE_ROLE": self.role,
        "SNOWFLAKE_WAREHOUSE": self.warehouse,
        "SNOWFLAKE_PRIVATE_KEY": self.private_key_pem or self.private_key_path,
    }
    missing = [name for name, value in required_values.items() if not value]
    if missing:
        raise SnowflakeConfigurationError(
            "Snowflake connection is not configured. Missing: " + ", ".join(missing)
        )
    validate_account_identifier(self.account)
    timeout = timeout_seconds or self.query_timeout_seconds
    duration = f"{timeout}s"
    options = {
        adbc_driver_snowflake.DatabaseOptions.ACCOUNT.value: self.account,
        "username": self.user,
        adbc_driver_snowflake.DatabaseOptions.ROLE.value: self.role,
        adbc_driver_snowflake.DatabaseOptions.WAREHOUSE.value: self.warehouse,
        adbc_driver_snowflake.DatabaseOptions.DATABASE.value: self.database or "SNOWFLAKE",
        adbc_driver_snowflake.DatabaseOptions.SCHEMA.value: self.schema or "ACCOUNT_USAGE",
        adbc_driver_snowflake.DatabaseOptions.AUTH_TYPE.value: "auth_jwt",
        adbc_driver_snowflake.DatabaseOptions.JWT_PRIVATE_KEY_VALUE.value: self._load_private_key_pem(),
        adbc_driver_snowflake.DatabaseOptions.LOGIN_TIMEOUT.value: duration,
        adbc_driver_snowflake.DatabaseOptions.REQUEST_TIMEOUT.value: duration,
        adbc_driver_snowflake.DatabaseOptions.CLIENT_TIMEOUT.value: duration,
    }
    if self.private_key_passphrase:
        options[adbc_driver_snowflake.DatabaseOptions.JWT_PRIVATE_KEY_PASSWORD.value] = self.private_key_passphrase
    if keep_session_alive:
        options[adbc_driver_snowflake.DatabaseOptions.KEEP_SESSION_ALIVE.value] = "true"
    return options
```

- [ ] **Step 5: Run configuration tests and verify GREEN**

Run:

```bash
rtk uv run --directory shared/connect pytest tests/test_snowflake_client.py -k 'adbc_db_kwargs or malformed or repr or environment' -q
```

Expected: PASS.

- [ ] **Step 6: Commit the configuration boundary**

```bash
rtk git add shared/connect/src/greysight_connect/snowflake_client.py shared/connect/tests/test_snowflake_client.py
rtk git commit -m "feat: build Snowflake ADBC JWT options"
```

### Task 3: Migrate shared query execution and errors

**Files:**
- Modify: `shared/connect/tests/test_snowflake_client.py`
- Modify: `shared/connect/tests/test_metadata_query.py`
- Modify: `shared/connect/src/greysight_connect/snowflake_client.py`
- Modify: `shared/connect/src/greysight_connect/__init__.py`

- [ ] **Step 1: Replace binding and connection tests in place**

Change the existing source-query test to require positional ADBC binding while
retaining the current lowercase-row assertion:

```python
def test_execute_source_query_translates_named_binds_in_occurrence_order() -> None:
    cursor = _RecordingCursor(
        description=[("WINDOW_DAYS",), ("ACCOUNT_LOCATOR",)],
        rows=[(30, "XY12345")],
    )
    connection = _Connection(cursor)

    rows = execute_source_query(
        "select %(window_days)s, %(account_locator)s, %(window_days)s",
        {"window_days": 30, "account_locator": "XY12345"},
        connect=lambda _config: connection,
    )

    assert cursor.executed == [
        ("select ?, ?, ?", (30, "XY12345", 30))
    ]
    assert rows == [{"window_days": 30, "account_locator": "XY12345"}]
```

Add one assertion to the same test group that `select '50%'` is unchanged with
no parameters. Replace direct patches with
`patch("greysight_connect.snowflake_client.adbc_driver_snowflake.dbapi.connect")`
and assert `db_kwargs=...` plus `autocommit=True`. Update
`test_metadata_query.py` similarly; do not add parallel tests that restate its
existing `SHOW` and lowercase-row behavior.

- [ ] **Step 2: Replace error fixtures with real ADBC error objects**

Use one helper in `test_snowflake_client.py`:

```python
import adbc_driver_manager
import adbc_driver_manager.dbapi as adbc_dbapi


def _adbc_error(
    message: str,
    *,
    vendor_code: int | None = None,
    sqlstate: str | None = None,
    status_code: adbc_driver_manager.AdbcStatusCode = (
        adbc_driver_manager.AdbcStatusCode.INVALID_ARGUMENT
    ),
) -> adbc_dbapi.ProgrammingError:
    return adbc_dbapi.ProgrammingError(
        message,
        status_code=status_code,
        vendor_code=vendor_code,
        sqlstate=sqlstate,
    )
```

Replace old fake `errno` tests with ADBC vendor codes `2003`, `3001`, and an
unrelated code. Add a missing-vendor-code case that remains a generic query
error and a `TIMEOUT` status case that returns the existing timeout-safe text.
Remove old tests that only asserted `snowflake.connector` exception internals.

- [ ] **Step 3: Run shared query tests and verify RED**

Run:

```bash
rtk uv run --directory shared/connect pytest tests/test_snowflake_client.py tests/test_metadata_query.py -q
```

Expected: FAIL because source SQL is still passed in pyformat form, the old
connector is still invoked, and ADBC `vendor_code` is not classified.

- [ ] **Step 4: Implement exact named-bind translation**

Add:

```python
_NAMED_BIND = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")


def _adbc_bindings(
    sql: str, bind_params: dict[str, Any]
) -> tuple[str, tuple[Any, ...]]:
    ordered: list[Any] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        try:
            ordered.append(bind_params[name])
        except KeyError:
            raise ValueError(f"Missing Snowflake bind param: {name}") from None
        return "?"

    return _NAMED_BIND.sub(replace, sql), tuple(ordered)
```

In `execute_source_query`, call `cursor.execute(adbc_sql, values)` when values
are present and `cursor.execute(adbc_sql)` when empty. Keep
`_validate_window_params` before connecting.

- [ ] **Step 5: Implement ADBC connection and tagged cursor helpers**

Replace the lazy Python connector proxy with imports of `adbc_driver_manager`,
`adbc_driver_manager.dbapi as adbc_dbapi`, `adbc_driver_snowflake`, and
`adbc_driver_snowflake.dbapi`. Implement:

```python
def snowflake_cursor(connection: Any) -> Any:
    return connection.cursor(
        adbc_stmt_kwargs={
            adbc_driver_snowflake.StatementOptions.QUERY_TAG.value: "greysight"
        }
    )


def _connect(config: SnowflakeConnectionConfig | None) -> Any:
    effective_config = config or SnowflakeConnectionConfig.from_environment()
    try:
        options = effective_config.adbc_db_kwargs()
        return adbc_driver_snowflake.dbapi.connect(
            db_kwargs=options,
            autocommit=True,
        )
    except SnowflakeConfigurationError:
        raise
    except Exception as exc:
        raise _validation_error(exc, phase="connect") from None
```

Use `snowflake_cursor` in source, metadata, and validation queries, and export
it from `greysight_connect.__init__` for the worker.

- [ ] **Step 6: Adapt ADBC error metadata**

Add helpers that only trust ADBC DB-API errors:

```python
def _vendor_code(exc: Exception) -> int | None:
    if not isinstance(exc, adbc_dbapi.Error):
        return None
    value = exc.vendor_code
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_timeout(exc: Exception) -> bool:
    return (
        isinstance(exc, adbc_dbapi.Error)
        and exc.status_code == adbc_driver_manager.AdbcStatusCode.TIMEOUT
    )
```

Use `_vendor_code` in `_is_object_unavailable` and `_safe_failure_metadata`.
Use `_is_timeout` in `_base_user_safe_message` before the existing conservative
message-text fallback. Keep SQL state format validation and login reference
sanitization unchanged.

- [ ] **Step 7: Run the complete shared package suite**

Run:

```bash
rtk uv run --directory shared/connect pytest -q
rtk uv run --directory shared/connect ruff check .
rtk uv run --directory shared/connect ruff format --check .
```

Expected: all pass with no resource warnings.

- [ ] **Step 8: Commit shared execution**

```bash
rtk git add shared/connect/src/greysight_connect/snowflake_client.py shared/connect/src/greysight_connect/__init__.py shared/connect/tests/test_snowflake_client.py shared/connect/tests/test_metadata_query.py
rtk git commit -m "feat: execute Snowflake queries through ADBC"
```

### Task 4: Migrate the warm Automated Savings session

**Files:**
- Modify: `apps/auto-savings/tests/test_snowflake_session.py`
- Modify: `apps/auto-savings/tests/test_engine.py`
- Modify: `apps/auto-savings/src/auto_savings/snowflake_session.py`

- [ ] **Step 1: Replace worker connector tests in place**

Replace the default-connect test with an ADBC options assertion:

```python
def test_default_connect_bounds_all_adbc_timeouts_and_keeps_session_alive(monkeypatch):
    config = Mock()
    config.adbc_db_kwargs.return_value = {"username": "svc"}
    connection = Mock()
    connect = Mock(return_value=connection)
    monkeypatch.setattr(adbc_driver_snowflake.dbapi, "connect", connect)

    session = TenantSession(config=config, socket_timeout_seconds=15)
    session.ensure_connected()

    config.adbc_db_kwargs.assert_called_once_with(
        timeout_seconds=15,
        keep_session_alive=True,
    )
    connect.assert_called_once_with(
        db_kwargs={"username": "svc"},
        autocommit=True,
    )
```

Replace `snowflake.connector.errors.ProgrammingError` with a real ADBC
`ProgrammingError(vendor_code=90064, sqlstate="57014")`. Add a case with the
same text but `vendor_code=None` that raises normally. Replace the operational
error used by the reconnect/engine tests with
`adbc_dbapi.OperationalError` and a non-success status. Retain the
existing warm reuse, quoting, close-failure, reconnect, fingerprint, and backoff
tests unchanged.

- [ ] **Step 2: Run worker session tests and verify RED**

Run:

```bash
rtk uv run --directory apps/auto-savings pytest tests/test_snowflake_session.py tests/test_engine.py -q
```

Expected: FAIL because the worker still calls `connector_kwargs`, imports the
old connector, and checks the old exception hierarchy.

- [ ] **Step 3: Replace worker connection creation**

Remove the optional `snowflake.connector` import. Import
`adbc_driver_manager`, `adbc_driver_manager.dbapi as adbc_dbapi`,
`adbc_driver_snowflake.dbapi`, and the shared `snowflake_cursor` helper.
Replace `_connector_kwargs` and
`_connect_with_kwargs` with:

```python
def _connect_adbc(config: Any, *, timeout_seconds: int) -> Any:
    options = config.adbc_db_kwargs(
        timeout_seconds=timeout_seconds,
        keep_session_alive=True,
    )
    return adbc_driver_snowflake.dbapi.connect(
        db_kwargs=options,
        autocommit=True,
    )
```

`ensure_connected` continues honoring the injected `connect(config)` seam; its
default branch calls `_connect_adbc`. Use `snowflake_cursor` in
`show_warehouses` and `suspend_warehouse`.

- [ ] **Step 4: Adapt worker diagnostics and code 90064**

Implement:

```python
def connector_error_metadata(exc: Any) -> ConnectorErrorMetadata:
    is_adbc = isinstance(exc, adbc_dbapi.Error)
    message = _sanitize_connector_message(str(exc)) if is_adbc else None
    return ConnectorErrorMetadata(
        error_type=type(exc).__name__,
        errno=exc.vendor_code if is_adbc else None,
        sqlstate=exc.sqlstate if is_adbc else None,
        message=message,
    )
```

Classify unknown-idempotent only with:

```python
if (
    isinstance(exc, adbc_dbapi.Error)
    and exc.vendor_code == 90064
):
```

Any missing/zero/unrelated vendor code is re-raised so the engine follows its
existing close/reconnect/backoff path.

- [ ] **Step 5: Run the worker suite**

Run:

```bash
rtk uv run --directory apps/auto-savings pytest -q
rtk uv run --directory apps/auto-savings ruff check .
rtk uv run --directory apps/auto-savings ruff format --check .
```

Expected: all pass. Existing `test_warehouse_snapshot.py` tests already prove
timezone-aware/string timestamp handling, `Decimal`-compatible count parsing,
and fail-closed naive timestamp behavior; do not duplicate them.

- [ ] **Step 6: Commit the worker migration**

```bash
rtk git add apps/auto-savings/src/auto_savings/snowflake_session.py apps/auto-savings/tests/test_snowflake_session.py apps/auto-savings/tests/test_engine.py
rtk git commit -m "feat: migrate automated savings to Snowflake ADBC"
```

### Task 5: Remove old references and verify all consumers

**Files:**
- Modify: `docs/security-model.md`
- Verify: `apps/api/app/services/snowflake_client.py`
- Verify: `apps/api/tests/`
- Verify: all changed files

- [ ] **Step 1: Update the security documentation terminology**

In `docs/security-model.md`, replace “Snowflake connector bind parameters” with
“ADBC Snowflake bind parameters.” Do not change the approved-SQL or data-boundary
policy.

- [ ] **Step 2: Prove the old connector is gone**

Run:

```bash
rtk rg -n "snowflake\.connector|snowflake-connector-python|connector_kwargs|_load_private_key_der" shared apps docs --glob '!docs/superpowers/specs/**' --glob '!docs/superpowers/plans/**'
```

Expected: no matches. If a test name or comment is the only match, rename it;
do not retain compatibility aliases for the removed API.

- [ ] **Step 3: Run API tests and lint**

Run:

```bash
rtk uv run --directory apps/api pytest -q
rtk uv run --directory apps/api ruff check .
rtk uv run --directory apps/api ruff format --check .
```

Expected: all pass. The API shim should require no logic changes because it
re-exports the shared client contract.

- [ ] **Step 4: Run repository-level verification**

Run:

```bash
rtk npm run test
rtk npm run lint
rtk npm run typecheck
rtk uv run --directory shared/connect pytest -q
rtk uv run --directory apps/auto-savings pytest -q
rtk git diff --check origin/main...
```

Expected: all commands pass.

- [ ] **Step 5: Verify native wheels in deployment images**

Run:

```bash
rtk docker build -f apps/api/Dockerfile -t greysight-api-adbc .
rtk docker build -f apps/auto-savings/Dockerfile -t greysight-auto-savings-adbc .
```

Expected: both Debian/Python 3.12 images resolve and install the pinned ADBC,
driver-manager, and PyArrow wheels without compiling from source.

- [ ] **Step 6: Commit cleanup and verification documentation**

```bash
rtk git add docs/security-model.md
rtk git commit -m "docs: document Snowflake ADBC binding"
```

### Task 6: Perform authorized live compatibility smoke tests

**Files:**
- No repository changes required.

- [ ] **Step 1: Validate encrypted stored-PEM onboarding**

In an authorized non-production Snowflake account, use the existing connect
wizard with the same `ORG-ACCOUNT`, user, role, warehouse, optional
database/schema, encrypted PKCS#8 PEM, and passphrase. Expected: onboarding
validation succeeds and persists the account locator from `CURRENT_ACCOUNT()`.

- [ ] **Step 2: Validate a live dashboard run**

Create one Snowflake-backed dashboard run. Expected: Account Usage datasets
load; Organization Usage either loads or degrades according to its existing
privilege rules; numeric values serialize without non-finite errors; Snowflake
query history shows the `greysight` tag.

- [ ] **Step 3: Validate path-based self-host authentication**

Set `SNOWFLAKE_PRIVATE_KEY_PATH` to an encrypted PKCS#8 PEM and retain the
existing passphrase environment variable. Run the backend connection
validation. Expected: success without changing environment variable names or
key format.

- [ ] **Step 4: Validate Automated Savings safely**

First run observation-only with global suspension disabled. Expected:
`SHOW WAREHOUSES` returns parseable timezone-aware identity and activity data,
and repeated polls reuse one session. Then, in an authorized disposable test
warehouse, enable enrollment and confirm one eligible direct suspend is
accepted and recorded. Do not attempt to induce Snowflake error `90064`; its
fail-safe contract is covered hermetically.

- [ ] **Step 5: Record smoke-test evidence in the PR description**

Record account type (not credentials), which four flows ran, timestamps, and
pass/fail outcomes. Never paste PEM, passphrase, account secrets, raw connector
errors, or Vault values.
