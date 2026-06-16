# Per-Org Snowflake Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a signed-in user self-serve — create an organization and connect their own Snowflake account via RSA key-pair credentials — with the connection validated before anything persists, then run that org's dashboard against its own credentials.

**Architecture:** Per-org Snowflake credentials live in Supabase Vault (PEM never in a normal table); a `security definer` RPC creates org + owner membership + secret + connection row atomically after the API validates the connection. A resolver maps `org_id → SnowflakeConnectionConfig` and **fails closed** in `auth_required=true` mode (no `.env` fallback). The web "coming soon" screen is replaced by a two-column connect wizard that reuses the dashboard design system.

**Tech Stack:** Python 3 / FastAPI / pydantic-settings / snowflake-connector-python / cryptography (API); Supabase Postgres + Vault + RLS (data); Next.js / React / TypeScript / Tailwind / Vitest (web). Tests: `pytest` (API, run with `uv run pytest` from `apps/api`), `vitest` (web, run with `npm test` from `apps/web`).

**Spec:** `docs/superpowers/specs/2026-06-16-per-org-snowflake-onboarding-design.md`

---

## File Structure

**API (`apps/api/app/`)**
- `services/snowflake_client.py` — *modify*: add `private_key_pem`, `repr=False` on secret fields, optional `database`/`schema` defaults.
- `services/snowflake_account.py` — *create*: `validate_account_identifier()` (SSRF guard).
- `services/org_connection_resolver.py` — *create*: connection-row + Vault-secret lookup; `resolve_snowflake_config()` with fail-closed semantics.
- `services/membership_directory.py` — *modify*: select `role`; `Organization.role`.
- `auth.py` — *modify*: carry roles in `AuthContext`; add `require_org_admin`.
- `routes/onboarding.py` — *create*: `POST /api/onboarding/connect`.
- `routes/dashboard_runs.py` — *modify*: resolve per-org config and thread it into the run.
- `services/dashboard_datasets.py` — *modify*: accept a per-org `SnowflakeConnectionConfig`.
- `main.py` — *modify*: register the onboarding router.

**DB (`supabase/migrations/`)**
- `202606160001_org_snowflake_connections.sql` — *create*: table, member-summary function, secret RPCs, atomic create RPC, teardown RPC, one-org guard.

**Web (`apps/web/src/`)**
- `lib/onboarding-api.ts` — *create*: `connectSnowflake()` client.
- `components/org/connect-wizard.tsx` — *create*: two-column wizard.
- `components/org/snowflake-setup-sql.ts` — *create*: the copy-able setup SQL string.
- `components/org/org-shell.tsx` — *modify*: render the wizard instead of "coming soon".

**Tests** live beside the code they cover (API: `apps/api/tests/`, web: co-located `*.test.tsx`).

---

## Phase 0 — Unbreak the migration test

`apps/api/tests/test_supabase_migration.py` currently hard-asserts a **single** migration file, but the repo already has two (`202606080001_*`, `202606150001_*`), so the whole suite is RED. This phase makes the test read the concatenation of all migrations before we add a third.

### Task 0: Make the migration test read all migration files

**Files:**
- Modify: `apps/api/tests/test_supabase_migration.py`

- [ ] **Step 1: Run the suite to confirm it is currently RED**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py -q`
Expected: FAIL — `AssertionError: expected a single migration file, found: [...]`.

- [ ] **Step 2: Replace `_migration_path()`/`read_migration_sql()` with an all-files reader**

Replace lines 6–31 (`_migration_path` through `read_migration_sql`) with:

```python
def _migration_paths() -> list[Path]:
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert migrations, f"no migration files found in {MIGRATIONS_DIR}"
    return migrations


def read_migration_sql() -> str:
    return "\n".join(path.read_text() for path in _migration_paths()).lower()
```

- [ ] **Step 3: Update the org-INSERT assertion for the current (locked-down) state**

`202606150001_restrict_org_insert.sql` drops `organizations_insert_for_authenticated`. In `test_organization_policies_gate_on_membership`, replace the line
`assert "organizations_insert_for_authenticated" in sql` with:

```python
    assert "drop policy if exists organizations_insert_for_authenticated" in sql
```

- [ ] **Step 4: Run the suite to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/api/tests/test_supabase_migration.py
git commit -m "test(db): read all migration files so the suite survives multiple migrations"
```

---

## Phase 1 — Database: table, RPCs, RLS, atomic create

All objects go in one new migration `supabase/migrations/202606160001_org_snowflake_connections.sql`. We build it incrementally and assert on its text (consistent with the existing migration-test style — these tests verify the SQL is present, not a live DB).

### Task 1: Connection table + member-summary function

**Files:**
- Create: `supabase/migrations/202606160001_org_snowflake_connections.sql`
- Modify: `apps/api/tests/test_supabase_migration.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_supabase_migration.py`:

```python
def test_connection_table_defined_with_rls_and_no_authenticated_writes() -> None:
    sql = read_migration_sql()
    assert "create table organization_snowflake_connections (" in sql
    assert (
        "organization_id uuid primary key references organizations(id) on delete cascade"
        in sql
    )
    assert "secret_id uuid" in sql
    assert "status text not null default 'invalid' check (status in ('active', 'invalid'))" in sql
    assert (
        "alter table organization_snowflake_connections enable row level security"
        in sql
    )
    # members may read only via the summary function; no authenticated DML policies
    assert "organization_snowflake_connections_insert" not in sql
    assert "organization_snowflake_connections_update" not in sql
    assert "organization_snowflake_connections_delete" not in sql
    # members can read non-sensitive metadata through a SECURITY DEFINER function
    assert "create or replace function get_org_connection_summary" in sql
    assert "secret_id" not in sql.split("get_org_connection_summary", 1)[1].split("$$", 2)[1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py::test_connection_table_defined_with_rls_and_no_authenticated_writes -v`
Expected: FAIL (migration file does not exist yet).

- [ ] **Step 3: Create the migration with the table + summary function**

Create `supabase/migrations/202606160001_org_snowflake_connections.sql`:

```sql
-- Per-org Snowflake connection metadata. The RSA private key + passphrase are
-- NOT stored here; they live in Supabase Vault and are referenced by secret_id.
-- See docs/superpowers/specs/2026-06-16-per-org-snowflake-onboarding-design.md.

create table organization_snowflake_connections (
  organization_id uuid primary key references organizations(id) on delete cascade,
  account text not null,
  snowflake_user text not null,
  role text not null,
  warehouse text not null,
  database text,
  schema text,
  secret_id uuid,
  has_passphrase boolean not null default false,
  status text not null default 'invalid' check (status in ('active', 'invalid')),
  last_validated_at timestamptz,
  created_by_user_id uuid references auth.users(id) on delete set null default auth.uid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger organization_snowflake_connections_set_updated_at
  before update on organization_snowflake_connections
  for each row execute function set_updated_at();

alter table organization_snowflake_connections enable row level security;
-- No authenticated INSERT/UPDATE/DELETE policy: all writes are service-role-side
-- via the RPCs below, mirroring the organizations INSERT lockdown.

-- Member-facing read path: non-sensitive fields only (no secret_id).
create or replace function get_org_connection_summary(target_organization_id uuid)
returns table (
  organization_id uuid,
  account text,
  status text,
  last_validated_at timestamptz,
  has_passphrase boolean
)
language sql
stable
security definer
set search_path = public
as $$
  select c.organization_id, c.account, c.status, c.last_validated_at, c.has_passphrase
  from organization_snowflake_connections c
  where c.organization_id = target_organization_id
    and is_organization_member(target_organization_id);
$$;

revoke all on function get_org_connection_summary(uuid) from public;
grant execute on function get_org_connection_summary(uuid) to authenticated;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py::test_connection_table_defined_with_rls_and_no_authenticated_writes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/202606160001_org_snowflake_connections.sql apps/api/tests/test_supabase_migration.py
git commit -m "feat(db): add organization_snowflake_connections table + member summary fn"
```

### Task 2: Vault secret RPCs (set / get / delete), service-role only

**Files:**
- Modify: `supabase/migrations/202606160001_org_snowflake_connections.sql`
- Modify: `apps/api/tests/test_supabase_migration.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_secret_rpcs_are_service_role_only() -> None:
    sql = read_migration_sql()
    for fn in (
        "set_organization_snowflake_secret",
        "get_organization_snowflake_secret",
        "delete_organization_snowflake_secret",
    ):
        assert f"create or replace function {fn}" in sql
        assert f"revoke all on function {fn}" in sql
        assert f"grant execute on function {fn}" in sql
        # never granted to authenticated/anon — service_role only
        block = sql.split(f"grant execute on function {fn}", 1)[1].split(";", 1)[0]
        assert "to service_role" in block
        assert "authenticated" not in block
    assert "vault.create_secret" in sql
    assert "vault.update_secret" in sql
    assert "vault.decrypted_secrets" in sql
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py::test_secret_rpcs_are_service_role_only -v`
Expected: FAIL.

- [ ] **Step 3: Append the secret RPCs to the migration**

```sql
-- Vault secret helpers. service_role only; these read/write the vault schema,
-- which is not exposed via PostgREST directly.

create or replace function set_organization_snowflake_secret(
  target_organization_id uuid,
  private_key_pem text,
  passphrase text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  existing_secret_id uuid;
  secret_payload text := json_build_object('pem', private_key_pem, 'passphrase', passphrase)::text;
  secret_name text := 'snowflake_pk_' || target_organization_id::text;
begin
  select secret_id into existing_secret_id
  from organization_snowflake_connections
  where organization_id = target_organization_id;

  if existing_secret_id is null then
    return vault.create_secret(secret_payload, secret_name, 'Greysight Snowflake key');
  else
    perform vault.update_secret(existing_secret_id, secret_payload, secret_name, 'Greysight Snowflake key');
    return existing_secret_id;
  end if;
end;
$$;

create or replace function get_organization_snowflake_secret(target_organization_id uuid)
returns table (private_key_pem text, passphrase text)
language plpgsql
security definer
set search_path = public, vault
as $$
declare
  target_secret_id uuid;
  decrypted text;
begin
  select secret_id into target_secret_id
  from organization_snowflake_connections
  where organization_id = target_organization_id;

  if target_secret_id is null then
    return;  -- no rows; caller treats as "no secret"
  end if;

  select decrypted_secret into decrypted
  from vault.decrypted_secrets
  where id = target_secret_id;

  if decrypted is null then
    return;
  end if;

  return query
    select decrypted::json ->> 'pem', decrypted::json ->> 'passphrase';
end;
$$;

create or replace function delete_organization_snowflake_secret(target_organization_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  target_secret_id uuid;
begin
  select secret_id into target_secret_id
  from organization_snowflake_connections
  where organization_id = target_organization_id;

  if target_secret_id is not null then
    delete from vault.secrets where id = target_secret_id;
  end if;
end;
$$;

revoke all on function set_organization_snowflake_secret(uuid, text, text) from public;
revoke all on function get_organization_snowflake_secret(uuid) from public;
revoke all on function delete_organization_snowflake_secret(uuid) from public;
grant execute on function set_organization_snowflake_secret(uuid, text, text) to service_role;
grant execute on function get_organization_snowflake_secret(uuid) to service_role;
grant execute on function delete_organization_snowflake_secret(uuid) to service_role;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py::test_secret_rpcs_are_service_role_only -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/202606160001_org_snowflake_connections.sql apps/api/tests/test_supabase_migration.py
git commit -m "feat(db): add service-role Vault secret RPCs for org Snowflake keys"
```

### Task 3: Atomic create RPC + race-safe one-org guard

**Files:**
- Modify: `supabase/migrations/202606160001_org_snowflake_connections.sql`
- Modify: `apps/api/tests/test_supabase_migration.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_atomic_create_rpc_and_one_org_guard() -> None:
    sql = read_migration_sql()
    assert "create or replace function create_org_with_snowflake_connection" in sql
    # race-safe: advisory lock keyed on the user id, inside the txn
    assert "pg_advisory_xact_lock" in sql
    # v1 one-org guard enforced in the DB, not the app
    assert "create unique index one_owner_membership_per_user" in sql
    assert "where role = 'owner'" in sql
    # service-role only
    block = sql.split("grant execute on function create_org_with_snowflake_connection", 1)[1].split(";", 1)[0]
    assert "to service_role" in block
    assert "authenticated" not in block
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py::test_atomic_create_rpc_and_one_org_guard -v`
Expected: FAIL.

- [ ] **Step 3: Append the guard index and the atomic create RPC**

```sql
-- v1 one-org guard: at most one 'owner' membership per user. Removing this
-- single index (plus the API guard) is all that's needed to enable multi-org.
create unique index one_owner_membership_per_user
  on organization_memberships(user_id)
  where role = 'owner';

create or replace function create_org_with_snowflake_connection(
  p_user_id uuid,
  p_org_name text,
  p_account text,
  p_user text,
  p_role text,
  p_warehouse text,
  p_database text,
  p_schema text,
  p_private_key_pem text,
  p_passphrase text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  new_org_id uuid;
  new_secret_id uuid;
begin
  -- Serialize concurrent onboarding for the same user so the one-org guard
  -- cannot be raced. Lock is released at transaction end.
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

  if exists (
    select 1 from organization_memberships
    where user_id = p_user_id and role = 'owner'
  ) then
    raise exception 'user already owns an organization'
      using errcode = 'unique_violation';
  end if;

  insert into organizations (name, created_by_user_id)
  values (p_org_name, p_user_id)
  returning id into new_org_id;
  -- organizations_create_owner_membership trigger inserts the owner membership.

  -- Insert the connection row first (without secret), then attach the secret so
  -- set_organization_snowflake_secret can find the row to update.
  insert into organization_snowflake_connections (
    organization_id, account, snowflake_user, role, warehouse,
    database, schema, has_passphrase, status, last_validated_at, created_by_user_id
  )
  values (
    new_org_id, p_account, p_user, p_role, p_warehouse,
    nullif(p_database, ''), nullif(p_schema, ''),
    p_passphrase is not null and p_passphrase <> '',
    'active', now(), p_user_id
  );

  new_secret_id := set_organization_snowflake_secret(new_org_id, p_private_key_pem, p_passphrase);
  update organization_snowflake_connections
    set secret_id = new_secret_id
    where organization_id = new_org_id;

  return new_org_id;
end;
$$;

revoke all on function create_org_with_snowflake_connection(uuid, text, text, text, text, text, text, text, text, text) from public;
grant execute on function create_org_with_snowflake_connection(uuid, text, text, text, text, text, text, text, text, text) to service_role;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py::test_atomic_create_rpc_and_one_org_guard -v`
Expected: PASS.

- [ ] **Step 5: Run the full migration test file**

Run: `cd apps/api && uv run pytest tests/test_supabase_migration.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/202606160001_org_snowflake_connections.sql apps/api/tests/test_supabase_migration.py
git commit -m "feat(db): atomic org+connection create RPC with race-safe one-org guard"
```

---

## Phase 2 — Snowflake client refactor

### Task 4: Support in-memory PEM content and stop leaking secrets in repr

**Files:**
- Modify: `apps/api/app/services/snowflake_client.py`
- Test: `apps/api/tests/test_snowflake_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/tests/test_snowflake_client.py`:

```python
from app.services.snowflake_client import SnowflakeConnectionConfig


def _generate_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def test_loads_private_key_from_pem_content() -> None:
    pem = _generate_pem()
    config = SnowflakeConnectionConfig(
        account="acct", user="u", role="r", warehouse="w",
        database="SNOWFLAKE", schema="ACCOUNT_USAGE", private_key_pem=pem,
    )
    kwargs = config.connector_kwargs()
    assert isinstance(kwargs["private_key"], bytes) and len(kwargs["private_key"]) > 0


def test_database_and_schema_default_when_missing() -> None:
    pem = _generate_pem()
    config = SnowflakeConnectionConfig(
        account="acct", user="u", role="r", warehouse="w", private_key_pem=pem,
    )
    kwargs = config.connector_kwargs()
    assert kwargs["database"] == "SNOWFLAKE"
    assert kwargs["schema"] == "ACCOUNT_USAGE"


def test_repr_does_not_leak_key_material() -> None:
    pem = _generate_pem()
    config = SnowflakeConnectionConfig(
        account="acct", user="u", role="r", warehouse="w",
        private_key_pem=pem, private_key_passphrase="hunter2",
    )
    text = repr(config)
    assert "BEGIN PRIVATE KEY" not in text
    assert "hunter2" not in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_snowflake_client.py -k "pem or default_when_missing or leak" -v`
Expected: FAIL (`private_key_pem` not a field; database/schema required; repr leaks).

- [ ] **Step 3: Implement the dataclass + loader changes**

In `apps/api/app/services/snowflake_client.py`, change the import line `from dataclasses import dataclass` to:

```python
from dataclasses import dataclass, field
```

Replace the field declarations in `SnowflakeConnectionConfig` (the `database`/`schema`/`private_key_path`/`private_key_passphrase` lines) with:

```python
    database: str | None = None
    schema: str | None = None
    private_key_path: Path | None = None
    private_key_pem: str | None = field(default=None, repr=False)
    private_key_passphrase: str | None = field(default=None, repr=False)
```

In `connector_kwargs`, change the required-values map so `database`/`schema` are no longer required and either a path or PEM is accepted, and apply defaults:

```python
        database = self.database or "SNOWFLAKE"
        schema = self.schema or "ACCOUNT_USAGE"
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

        return {
            "account": self.account,
            "user": self.user,
            "role": self.role,
            "warehouse": self.warehouse,
            "database": database,
            "schema": schema,
            "private_key": self._load_private_key_der(),
            "login_timeout": self.query_timeout_seconds,
            "network_timeout": self.query_timeout_seconds,
            "session_parameters": {"QUERY_TAG": "greysight"},
        }
```

Replace `_load_private_key_der` so PEM content takes precedence over a path:

```python
    def _load_private_key_der(self) -> bytes:
        if self.private_key_pem is None and self.private_key_path is None:
            raise SnowflakeConfigurationError("Snowflake connection is not configured.")

        password = (
            self.private_key_passphrase.encode("utf-8")
            if self.private_key_passphrase
            else None
        )
        try:
            pem_bytes = (
                self.private_key_pem.encode("utf-8")
                if self.private_key_pem is not None
                else self.private_key_path.read_bytes()
            )
            private_key = serialization.load_pem_private_key(pem_bytes, password=password)
            return private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        except (OSError, TypeError, ValueError):
            raise SnowflakeConfigurationError(
                "Snowflake private key could not be loaded."
            ) from None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_snowflake_client.py -v`
Expected: PASS (new tests + existing ones still green).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/snowflake_client.py apps/api/tests/test_snowflake_client.py
git commit -m "feat(snowflake): load key from in-memory PEM, default db/schema, no secret repr"
```

### Task 5: Account-identifier validation (SSRF guard)

**Files:**
- Create: `apps/api/app/services/snowflake_account.py`
- Test: `apps/api/tests/test_snowflake_account.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_snowflake_account.py`:

```python
import pytest

from app.services.snowflake_account import (
    InvalidSnowflakeAccountError,
    validate_account_identifier,
)


@pytest.mark.parametrize("value", ["GOPGUKF-JO19546", "abc12345.us-east-1", "org-account_1"])
def test_accepts_valid_account_identifiers(value: str) -> None:
    assert validate_account_identifier(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "http://evil.example.com",
        "acct/../x",
        "acct:5432",
        "acct account",
        "a" * 300,
        "",
    ],
)
def test_rejects_unsafe_account_identifiers(value: str) -> None:
    with pytest.raises(InvalidSnowflakeAccountError):
        validate_account_identifier(value)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_snowflake_account.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the validator**

Create `apps/api/app/services/snowflake_account.py`:

```python
import re

# Snowflake account identifiers are alphanumerics plus dots, hyphens, and
# underscores (e.g. "ORG-ACCOUNT", "abc12345.us-east-1.aws"). Reject anything
# that could redirect the connector to an attacker-controlled host.
_ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


class InvalidSnowflakeAccountError(ValueError):
    """Raised when a submitted Snowflake account identifier is unsafe."""


def validate_account_identifier(value: str) -> str:
    if not isinstance(value, str) or not _ACCOUNT_PATTERN.fullmatch(value):
        raise InvalidSnowflakeAccountError(
            "Snowflake account must be 1-255 letters, digits, dots, hyphens, or underscores."
        )
    return value
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_snowflake_account.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/snowflake_account.py apps/api/tests/test_snowflake_account.py
git commit -m "feat(snowflake): validate account identifiers to prevent outbound abuse"
```

---

## Phase 3 — Per-org credential resolver (fail-closed)

### Task 6: Resolver with `.env` fallback only when auth is off

**Files:**
- Create: `apps/api/app/services/org_connection_resolver.py`
- Test: `apps/api/tests/test_org_connection_resolver.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_org_connection_resolver.py`:

```python
import pytest

from app.config import Settings
from app.services.org_connection_resolver import (
    OrgConnectionNotConfiguredError,
    OrgConnectionRow,
    resolve_snowflake_config,
)


def _row() -> OrgConnectionRow:
    return OrgConnectionRow(
        account="acct", snowflake_user="u", role="r", warehouse="w",
        database=None, schema=None,
        private_key_pem="-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----",
        passphrase=None,
    )


def test_uses_per_org_row_when_present() -> None:
    settings = Settings(auth_required=True)
    config = resolve_snowflake_config(
        "org-1", settings, fetch_connection=lambda _org_id: _row()
    )
    assert config.account == "acct"
    assert config.private_key_pem is not None


def test_fails_closed_when_no_row_and_auth_required() -> None:
    settings = Settings(auth_required=True)
    with pytest.raises(OrgConnectionNotConfiguredError):
        resolve_snowflake_config("org-1", settings, fetch_connection=lambda _org_id: None)


def test_falls_back_to_env_when_auth_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "env-acct")
    settings = Settings(auth_required=False)
    config = resolve_snowflake_config("org-1", settings, fetch_connection=lambda _org_id: None)
    assert config.account == "env-acct"


def test_fails_closed_when_lookup_errors_and_auth_required() -> None:
    settings = Settings(auth_required=True)

    def _boom(_org_id: str) -> OrgConnectionRow | None:
        raise RuntimeError("vault down")

    with pytest.raises(OrgConnectionNotConfiguredError):
        resolve_snowflake_config("org-1", settings, fetch_connection=_boom)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_org_connection_resolver.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the resolver**

Create `apps/api/app/services/org_connection_resolver.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.config import Settings
from app.services.snowflake_client import SnowflakeConnectionConfig


@dataclass(frozen=True)
class OrgConnectionRow:
    account: str
    snowflake_user: str
    role: str
    warehouse: str
    database: str | None
    schema: str | None
    private_key_pem: str
    passphrase: str | None


class OrgConnectionNotConfiguredError(RuntimeError):
    """Raised when an authenticated org has no usable Snowflake connection."""


FetchConnection = Callable[[str], OrgConnectionRow | None]


def resolve_snowflake_config(
    organization_id: str,
    settings: Settings,
    *,
    fetch_connection: FetchConnection,
) -> SnowflakeConnectionConfig:
    try:
        row = fetch_connection(organization_id)
    except Exception as exc:  # fail closed: never fall through on a lookup error
        raise OrgConnectionNotConfiguredError(
            "Could not load this organization's Snowflake connection."
        ) from exc

    if row is not None:
        return SnowflakeConnectionConfig(
            account=row.account,
            user=row.snowflake_user,
            role=row.role,
            warehouse=row.warehouse,
            database=row.database,
            schema=row.schema,
            private_key_pem=row.private_key_pem,
            private_key_passphrase=row.passphrase,
            query_timeout_seconds=settings.query_timeout_seconds,
        )

    if settings.auth_required:
        # No per-org connection in multi-tenant mode → fail closed. Never serve
        # the deployment .env credentials under another org's identity.
        raise OrgConnectionNotConfiguredError(
            "This organization has no Snowflake connection configured."
        )

    return SnowflakeConnectionConfig.from_environment()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_org_connection_resolver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/org_connection_resolver.py apps/api/tests/test_org_connection_resolver.py
git commit -m "feat(api): per-org Snowflake resolver, fail-closed under auth"
```

### Task 7: Service-role connection fetcher (PostgREST + secret RPC)

**Files:**
- Modify: `apps/api/app/services/org_connection_resolver.py`
- Test: `apps/api/tests/test_org_connection_resolver.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_org_connection_resolver.py`:

```python
import httpx

from app.services.org_connection_resolver import SupabaseConnectionFetcher


def _transport(handler):
    return httpx.MockTransport(handler)


def test_fetcher_combines_row_metadata_and_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/organization_snowflake_connections"):
            return httpx.Response(200, json=[{
                "account": "acct", "snowflake_user": "u", "role": "r",
                "warehouse": "w", "database": None, "schema": None, "secret_id": "sec-1",
            }])
        if request.url.path.endswith("/rpc/get_organization_snowflake_secret"):
            return httpx.Response(200, json=[{
                "private_key_pem": "PEMDATA", "passphrase": None,
            }])
        return httpx.Response(404)

    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(handler),
    )
    row = fetcher("org-1")
    assert row is not None
    assert row.account == "acct"
    assert row.private_key_pem == "PEMDATA"


def test_fetcher_returns_none_when_no_row() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    fetcher = SupabaseConnectionFetcher(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=_transport(handler),
    )
    assert fetcher("org-1") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_org_connection_resolver.py -k fetcher -v`
Expected: FAIL (`SupabaseConnectionFetcher` missing).

- [ ] **Step 3: Implement the fetcher**

Append to `apps/api/app/services/org_connection_resolver.py`:

```python
import httpx


class SupabaseConnectionFetcher:
    """Reads a per-org connection row + decrypted Vault secret via service role."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = supabase_url.rstrip("/")
        self._table_url = f"{base}/rest/v1/organization_snowflake_connections"
        self._secret_rpc_url = f"{base}/rest/v1/rpc/get_organization_snowflake_secret"
        self._service_role_key = service_role_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_role_key,
            "authorization": f"Bearer {self._service_role_key}",
            "content-type": "application/json",
        }

    def __call__(self, organization_id: str) -> OrgConnectionRow | None:
        with httpx.Client(timeout=self._timeout_seconds, transport=self._transport) as client:
            meta_response = client.get(
                self._table_url,
                params={
                    "organization_id": f"eq.{organization_id}",
                    "select": "account,snowflake_user,role,warehouse,database,schema,secret_id",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            meta_response.raise_for_status()
            rows = meta_response.json()
            if not isinstance(rows, list) or not rows:
                return None
            meta = rows[0]
            if not meta.get("secret_id"):
                return None

            secret_response = client.post(
                self._secret_rpc_url,
                json={"target_organization_id": organization_id},
                headers=self._headers(),
            )
            secret_response.raise_for_status()
            secret_rows = secret_response.json()
            if not isinstance(secret_rows, list) or not secret_rows:
                raise OrgConnectionNotConfiguredError("Snowflake secret missing for org.")
            secret = secret_rows[0]
            pem = secret.get("private_key_pem")
            if not pem:
                raise OrgConnectionNotConfiguredError("Snowflake secret missing for org.")

        return OrgConnectionRow(
            account=str(meta["account"]),
            snowflake_user=str(meta["snowflake_user"]),
            role=str(meta["role"]),
            warehouse=str(meta["warehouse"]),
            database=meta.get("database"),
            schema=meta.get("schema"),
            private_key_pem=str(pem),
            passphrase=secret.get("passphrase"),
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_org_connection_resolver.py -k fetcher -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/org_connection_resolver.py apps/api/tests/test_org_connection_resolver.py
git commit -m "feat(api): service-role fetcher for per-org Snowflake connection + secret"
```

---

## Phase 4 — Role-aware auth

### Task 8: Carry membership role and add `require_org_admin`

**Files:**
- Modify: `apps/api/app/services/membership_directory.py`
- Modify: `apps/api/app/auth.py`
- Test: `apps/api/tests/test_membership_directory.py`, `apps/api/tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/tests/test_membership_directory.py`:

```python
def test_parses_membership_role() -> None:
    from app.services.membership_directory import _parse_organization

    org = _parse_organization({
        "role": "admin",
        "organizations": {"id": "org-1", "name": "Acme"},
    })
    assert org.id == "org-1"
    assert org.role == "admin"
```

Append to `apps/api/tests/test_auth.py`:

```python
import pytest
from fastapi import HTTPException

from app.auth import AuthContext, require_org_admin
from app.services.membership_directory import Organization


def test_require_org_admin_allows_admin() -> None:
    context = AuthContext(
        user_id="u", auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="admin"),),
    )
    require_org_admin(context, "org-1")  # no raise


def test_require_org_admin_rejects_member() -> None:
    context = AuthContext(
        user_id="u", auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="member"),),
    )
    with pytest.raises(HTTPException) as exc:
        require_org_admin(context, "org-1")
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_membership_directory.py::test_parses_membership_role tests/test_auth.py -k require_org_admin -v`
Expected: FAIL (`role` not on `Organization`; `require_org_admin` missing).

- [ ] **Step 3: Add `role` to `Organization` and select it**

In `apps/api/app/services/membership_directory.py`:
- Add `role: str = "member"` to the `Organization` dataclass.
- In `SupabaseServiceRoleMembershipLookup.__call__`, change the `select` param to
  `"role,organization_id,organizations(id,name)"`.
- In `_parse_organization`, after computing `org_name`, read the role and pass it:

```python
    role = row.get("role")
    role_value = role if isinstance(role, str) and role else "member"
    return Organization(id=org_id.strip(), name=org_name, role=role_value)
```

- [ ] **Step 4: Add `require_org_admin` and expose roles on `AuthContext`**

In `apps/api/app/auth.py`, add `require_org_admin` after `require_org_membership`:

```python
def require_org_admin(context: AuthContext, organization_id: str) -> None:
    normalized = _normalize_membership_id(organization_id)
    for org in context.organizations:
        if _normalize_membership_id(org.id) == normalized and org.role in ("owner", "admin"):
            return None
    raise HTTPException(status_code=403, detail="Organization admin access required")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_membership_directory.py tests/test_auth.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/membership_directory.py apps/api/app/auth.py apps/api/tests/test_membership_directory.py apps/api/tests/test_auth.py
git commit -m "feat(api): carry membership role and add require_org_admin guard"
```

---

## Phase 5 — Onboarding endpoint

### Task 9: `POST /api/onboarding/connect` — validate then atomic-create

**Files:**
- Create: `apps/api/app/routes/onboarding.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_onboarding_route.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_onboarding_route.py`:

```python
from fastapi.testclient import TestClient

import app.auth as auth_module
from app.auth import AuthContext, require_auth_context
from app.main import app
from app.routes import onboarding


def _auth_context() -> AuthContext:
    return AuthContext(user_id="user-1", auth_required=True, memberships=frozenset())


def _payload() -> dict:
    return {
        "org_name": "Acme",
        "account": "GOPGUKF-JO19546",
        "user": "GREYBEAM_USER",
        "role": "GREYBEAM_ROLE",
        "warehouse": "GREYBEAM_WH",
        "private_key_pem": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----",
    }


def test_connect_validates_then_creates(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _auth_context
    monkeypatch.setattr(onboarding, "validate_snowflake_connection", lambda config: None)
    created = {}
    monkeypatch.setattr(
        onboarding, "create_org_with_connection",
        lambda **kwargs: created.update(kwargs) or "org-123",
    )
    client = TestClient(app)
    response = client.post("/api/onboarding/connect", json=_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["id"] == "org-123"
    assert created["p_user_id"] == "user-1"          # identity from token, not body
    assert created["p_account"] == "GOPGUKF-JO19546"


def test_connect_rejects_invalid_account(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _auth_context
    client = TestClient(app)
    bad = _payload() | {"account": "http://evil.example.com"}
    response = client.post("/api/onboarding/connect", json=bad)
    app.dependency_overrides.clear()
    assert response.status_code == 422


def test_connect_returns_422_and_persists_nothing_on_validation_failure(monkeypatch) -> None:
    from app.services.snowflake_client import SnowflakeValidationError

    app.dependency_overrides[require_auth_context] = _auth_context

    def _fail(config):
        raise SnowflakeValidationError("Could not access required Snowflake Account Usage views.")

    monkeypatch.setattr(onboarding, "validate_snowflake_connection", _fail)
    calls = []
    monkeypatch.setattr(onboarding, "create_org_with_connection", lambda **k: calls.append(k))
    client = TestClient(app)
    response = client.post("/api/onboarding/connect", json=_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 422
    assert calls == []  # nothing persisted
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_onboarding_route.py -v`
Expected: FAIL (route + module missing).

- [ ] **Step 3: Implement the route**

Create `apps/api/app/routes/onboarding.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import AuthContext, require_auth_context
from app.config import Settings
from app.services.snowflake_account import (
    InvalidSnowflakeAccountError,
    validate_account_identifier,
)
from app.services.snowflake_client import (
    SnowflakeConnectionConfig,
    SnowflakeValidationError,
    validate_snowflake_connection,
)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

MAX_PEM_BYTES = 16 * 1024
VALIDATION_TIMEOUT_SECONDS = 20


class ConnectRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=200)
    account: str = Field(min_length=1, max_length=255)
    user: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=255)
    warehouse: str = Field(min_length=1, max_length=255)
    database: str | None = Field(default=None, max_length=255)
    schema: str | None = Field(default=None, max_length=255)
    private_key_pem: str = Field(min_length=1)
    passphrase: str | None = None


class ConnectResponse(BaseModel):
    id: str


def create_org_with_connection(**kwargs: object) -> str:
    """Indirection seam so tests can stub the service-role RPC call."""
    from app.services.org_provisioning import create_org_with_connection as impl

    return impl(**kwargs)


@router.post(
    "/connect", response_model=ConnectResponse, status_code=status.HTTP_201_CREATED
)
def connect_snowflake(
    request: ConnectRequest,
    auth_context: AuthContext = Depends(require_auth_context),
) -> ConnectResponse:
    if not auth_context.auth_required or not auth_context.user_id:
        raise HTTPException(status_code=403, detail="Authentication required")

    if len(request.private_key_pem.encode("utf-8")) > MAX_PEM_BYTES:
        raise HTTPException(status_code=422, detail="Private key is too large.")

    try:
        account = validate_account_identifier(request.account)
    except InvalidSnowflakeAccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    config = SnowflakeConnectionConfig(
        account=account,
        user=request.user,
        role=request.role,
        warehouse=request.warehouse,
        database=request.database,
        schema=request.schema,
        private_key_pem=request.private_key_pem,
        private_key_passphrase=request.passphrase,
        query_timeout_seconds=VALIDATION_TIMEOUT_SECONDS,
    )

    try:
        validate_snowflake_connection(config)
    except SnowflakeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    organization_id = create_org_with_connection(
        p_user_id=auth_context.user_id,
        p_org_name=request.org_name,
        p_account=account,
        p_user=request.user,
        p_role=request.role,
        p_warehouse=request.warehouse,
        p_database=request.database or "",
        p_schema=request.schema or "",
        p_private_key_pem=request.private_key_pem,
        p_passphrase=request.passphrase or "",
    )
    return ConnectResponse(id=str(organization_id))
```

- [ ] **Step 4: Register the router in `main.py`**

In `apps/api/app/main.py`, import and include the router next to the existing routers:

```python
from app.routes import onboarding

app.include_router(onboarding.router)
```

(Match the existing `include_router` style in that file.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_onboarding_route.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routes/onboarding.py apps/api/app/main.py apps/api/tests/test_onboarding_route.py
git commit -m "feat(api): onboarding connect endpoint (validate then atomic create)"
```

### Task 10: Service-role org provisioning (the RPC call)

**Files:**
- Create: `apps/api/app/services/org_provisioning.py`
- Test: `apps/api/tests/test_org_provisioning.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_org_provisioning.py`:

```python
import httpx

from app.services.org_provisioning import SupabaseOrgProvisioner


def test_calls_create_rpc_and_returns_org_id() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(200, json="org-123")

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    org_id = provisioner(
        p_user_id="user-1", p_org_name="Acme", p_account="acct", p_user="u",
        p_role="r", p_warehouse="w", p_database="", p_schema="",
        p_private_key_pem="PEM", p_passphrase="",
    )
    assert org_id == "org-123"
    assert seen["path"].endswith("/rpc/create_org_with_snowflake_connection")
    assert "user-1" in seen["body"]


def test_raises_on_one_org_guard_conflict() -> None:
    import pytest

    from app.services.org_provisioning import OrgAlreadyExistsError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"code": "23505", "message": "unique_violation"})

    provisioner = SupabaseOrgProvisioner(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OrgAlreadyExistsError):
        provisioner(
            p_user_id="user-1", p_org_name="Acme", p_account="acct", p_user="u",
            p_role="r", p_warehouse="w", p_database="", p_schema="",
            p_private_key_pem="PEM", p_passphrase="",
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_org_provisioning.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the provisioner**

Create `apps/api/app/services/org_provisioning.py`:

```python
from __future__ import annotations

import httpx


class OrgProvisioningError(RuntimeError):
    """Raised when org provisioning fails."""


class OrgAlreadyExistsError(OrgProvisioningError):
    """Raised when the one-org guard rejects a second org for the user."""


class SupabaseOrgProvisioner:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = (
            f"{supabase_url.rstrip('/')}/rest/v1/rpc/create_org_with_snowflake_connection"
        )
        self._service_role_key = service_role_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def __call__(self, **params: str) -> str:
        with httpx.Client(timeout=self._timeout_seconds, transport=self._transport) as client:
            response = client.post(
                self._url,
                json=params,
                headers={
                    "apikey": self._service_role_key,
                    "authorization": f"Bearer {self._service_role_key}",
                    "content-type": "application/json",
                },
            )
        if response.status_code == 409 or "23505" in response.text:
            raise OrgAlreadyExistsError("You already have an organization.")
        if response.status_code not in (200, 201):
            raise OrgProvisioningError("Could not create the organization.")
        return str(response.json())


_provisioner: SupabaseOrgProvisioner | None = None


def configure_org_provisioner(provisioner: SupabaseOrgProvisioner | None) -> None:
    global _provisioner
    _provisioner = provisioner


def create_org_with_connection(**params: str) -> str:
    if _provisioner is None:
        raise OrgProvisioningError("Org provisioning is not configured.")
    return _provisioner(**params)
```

- [ ] **Step 4: Map `OrgAlreadyExistsError` to HTTP 409 in the route**

In `apps/api/app/routes/onboarding.py`, wrap the `create_org_with_connection` call:

```python
    from app.services.org_provisioning import OrgAlreadyExistsError, OrgProvisioningError

    try:
        organization_id = create_org_with_connection(
            ...  # unchanged kwargs
        )
    except OrgAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail="You already have an organization.") from None
    except OrgProvisioningError as exc:
        raise HTTPException(status_code=502, detail="Could not create the organization.") from None
```

(Keep the existing kwargs; only the try/except is added.)

- [ ] **Step 5: Wire the provisioner + fetcher at startup in `main.py`**

In `apps/api/app/main.py`, where the app configures Supabase verifiers at startup, add (guarded on service-role key, matching `configure_membership_lookup`'s pattern):

```python
from app.services.org_provisioning import SupabaseOrgProvisioner, configure_org_provisioner

def _configure_org_provisioner(settings: Settings) -> None:
    if settings.supabase_url.strip() and settings.supabase_service_role_key.strip():
        configure_org_provisioner(
            SupabaseOrgProvisioner(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            )
        )
    else:
        configure_org_provisioner(None)
```

Call `_configure_org_provisioner(settings)` wherever `configure_membership_lookup(settings)` is already called.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_org_provisioning.py tests/test_onboarding_route.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/org_provisioning.py apps/api/app/routes/onboarding.py apps/api/app/main.py apps/api/tests/test_org_provisioning.py
git commit -m "feat(api): service-role org provisioning via atomic create RPC"
```

---

## Phase 6 — Thread per-org credentials into dashboard runs

### Task 11: Use the resolved per-org config when running Snowflake

**Files:**
- Modify: `apps/api/app/services/dashboard_datasets.py`
- Modify: `apps/api/app/routes/dashboard_runs.py`
- Test: `apps/api/tests/test_snowflake_dashboard_run.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_snowflake_dashboard_run.py` a test asserting the per-org config reaches the executor. Example:

```python
def test_build_uses_supplied_connection_config() -> None:
    from app.config import Settings
    from app.services.dashboard_datasets import build_snowflake_dashboard_data
    from app.services.snowflake_client import SnowflakeConnectionConfig

    used = {}

    def fake_execute(sql, bind_params, config=None):
        used["config"] = config
        return []

    config = SnowflakeConnectionConfig(account="per-org", user="u", role="r", warehouse="w")
    try:
        build_snowflake_dashboard_data(
            Settings(), execute=lambda sql, params: fake_execute(sql, params, config),
            connection_config=config,
        )
    except Exception:
        pass
    assert used["config"].account == "per-org"
```

(Adjust to the file's existing fixtures/imports; the key assertion is that `build_snowflake_dashboard_data` accepts and forwards `connection_config`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_snowflake_dashboard_run.py -k supplied_connection_config -v`
Expected: FAIL (`connection_config` kwarg unknown).

- [ ] **Step 3: Thread the config through the executor**

In `apps/api/app/services/dashboard_datasets.py`:
- Change `ExecuteFn` to `Callable[[str, dict[str, Any], SnowflakeConnectionConfig | None], list[dict[str, Any]]]` and import `SnowflakeConnectionConfig`.
- Add `connection_config: SnowflakeConnectionConfig | None = None` to `build_snowflake_dashboard_data`'s signature.
- Replace `execute_source = execute or execute_source_query` with a partial that binds the config:

```python
    if execute is not None:
        execute_source = execute
    else:
        def execute_source(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
            return execute_source_query(sql, bind_params, connection_config)
```

(All internal `execute_source(source.sql, bind_params)` call sites stay unchanged.)

- [ ] **Step 4: Resolve per-org config in the run route**

In `apps/api/app/routes/dashboard_runs.py`, change `_create_snowflake_dashboard_run` to resolve and pass the config:

```python
def _create_snowflake_dashboard_run(
    request: DashboardRunCreateRequest, settings: Settings
) -> DashboardRun:
    from app.services.org_connection_resolver import (
        OrgConnectionNotConfiguredError,
        resolve_snowflake_config,
    )
    from app.services.snowflake_runtime import get_connection_fetcher

    try:
        connection_config = resolve_snowflake_config(
            str(request.organization_id), settings,
            fetch_connection=get_connection_fetcher(settings),
        )
    except OrgConnectionNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This organization has no Snowflake connection configured.",
        ) from None

    try:
        dashboard_data = build_snowflake_dashboard_data(
            settings,
            summary_window_days=request.window_days,
            connection_config=connection_config,
        )
    except DashboardSourcesUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not query Snowflake billing or Account Usage data.",
        ) from None

    return dashboard_run_repository.create_completed_snapshot(
        organization_id=request.organization_id,
        source=request.source,
        window_days=FETCH_WINDOW_DAYS,
        summary=dashboard_data.summary,
        datasets=dashboard_data.datasets,
        metadata=dashboard_data.metadata.model_dump(mode="json"),
        retention_days=request.retention_days,
    )
```

Create `apps/api/app/services/snowflake_runtime.py` providing `get_connection_fetcher(settings)` that returns a `SupabaseConnectionFetcher` when auth is on (configured from settings) and a `lambda _org_id: None` fetcher when off:

```python
from typing import Callable

from app.config import Settings
from app.services.org_connection_resolver import (
    OrgConnectionRow,
    SupabaseConnectionFetcher,
)


def get_connection_fetcher(settings: Settings) -> Callable[[str], OrgConnectionRow | None]:
    if (
        settings.auth_required
        and settings.supabase_url.strip()
        and settings.supabase_service_role_key.strip()
    ):
        return SupabaseConnectionFetcher(
            supabase_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
        )
    return lambda _organization_id: None
```

- [ ] **Step 5: Run the targeted + run-path tests**

Run: `cd apps/api && uv run pytest tests/test_snowflake_dashboard_run.py tests/test_snowflake_dashboard_run.py -v`
Expected: PASS. Also run `uv run pytest tests/test_snowflake_client.py -v` to confirm `execute_source_query`'s existing `config` positional still matches.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/dashboard_datasets.py apps/api/app/routes/dashboard_runs.py apps/api/app/services/snowflake_runtime.py apps/api/tests/test_snowflake_dashboard_run.py
git commit -m "feat(api): run dashboards against per-org Snowflake credentials"
```

---

## Phase 7 — Web onboarding wizard

### Task 12: Onboarding API client

**Files:**
- Create: `apps/web/src/lib/onboarding-api.ts`
- Test: `apps/web/src/lib/onboarding-api.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/lib/onboarding-api.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";

import { connectSnowflake, ConnectValidationError } from "./onboarding-api";

afterEach(() => vi.restoreAllMocks());

describe("connectSnowflake", () => {
  it("posts the payload and returns the new org id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ id: "org-123" }), { status: 201 }));

    const id = await connectSnowflake(
      {
        orgName: "Acme",
        account: "GOPGUKF-JO19546",
        user: "GREYBEAM_USER",
        role: "GREYBEAM_ROLE",
        warehouse: "GREYBEAM_WH",
        privateKeyPem: "PEM",
      },
      { accessToken: "tok" },
    );

    expect(id).toBe("org-123");
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toMatchObject({ org_name: "Acme", account: "GOPGUKF-JO19546" });
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer tok");
  });

  it("throws ConnectValidationError with the server message on 422", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Could not access required Snowflake Account Usage views." }), { status: 422 }),
    );
    await expect(
      connectSnowflake(
        { orgName: "A", account: "x", user: "u", role: "r", warehouse: "w", privateKeyPem: "P" },
        {},
      ),
    ).rejects.toBeInstanceOf(ConnectValidationError);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/web && npm test -- onboarding-api`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the client**

Create `apps/web/src/lib/onboarding-api.ts`:

```typescript
import resolveApiUrl from "./api-client";

export interface ConnectSnowflakeInput {
  orgName: string;
  account: string;
  user: string;
  role: string;
  warehouse: string;
  database?: string;
  schema?: string;
  privateKeyPem: string;
  passphrase?: string;
}

interface ConnectOptions {
  accessToken?: string | null;
}

export class ConnectValidationError extends Error {}
export class ConnectConflictError extends Error {}

export async function connectSnowflake(
  input: ConnectSnowflakeInput,
  options: ConnectOptions = {},
): Promise<string> {
  const headers = new Headers({ "content-type": "application/json" });
  const accessToken = options.accessToken?.trim();
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  const response = await fetch(resolveApiUrl("/api/onboarding/connect"), {
    method: "POST",
    headers,
    cache: "no-store",
    body: JSON.stringify({
      org_name: input.orgName,
      account: input.account,
      user: input.user,
      role: input.role,
      warehouse: input.warehouse,
      database: input.database || null,
      schema: input.schema || null,
      private_key_pem: input.privateKeyPem,
      passphrase: input.passphrase || null,
    }),
  });

  if (response.status === 201) {
    const payload = (await response.json()) as { id: string };
    return payload.id;
  }

  const detail = await safeDetail(response);
  if (response.status === 422) throw new ConnectValidationError(detail);
  if (response.status === 409) throw new ConnectConflictError(detail);
  throw new Error(detail || `Connect failed with ${response.status}`);
}

async function safeDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : "";
  } catch {
    return "";
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && npm test -- onboarding-api`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/onboarding-api.ts apps/web/src/lib/onboarding-api.test.ts
git commit -m "feat(web): onboarding API client for Snowflake connect"
```

### Task 13: Setup-SQL snippet constant

**Files:**
- Create: `apps/web/src/components/org/snowflake-setup-sql.ts`
- Test: `apps/web/src/components/org/snowflake-setup-sql.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/components/org/snowflake-setup-sql.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import { SNOWFLAKE_SETUP_SQL } from "./snowflake-setup-sql";

describe("SNOWFLAKE_SETUP_SQL", () => {
  it("uses the Codex-reviewed least-privilege setup", () => {
    expect(SNOWFLAKE_SETUP_SQL).toContain("CREATE USER IF NOT EXISTS");
    expect(SNOWFLAKE_SETUP_SQL).toContain("TYPE = SERVICE");
    expect(SNOWFLAKE_SETUP_SQL).toContain("AUTO_RESUME = TRUE");
    expect(SNOWFLAKE_SETUP_SQL).toContain("GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER");
    expect(SNOWFLAKE_SETUP_SQL).not.toContain("MUST_CHANGE_PASSWORD");
    expect(SNOWFLAKE_SETUP_SQL).not.toContain("BEGIN;");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/web && npm test -- snowflake-setup-sql`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the constant**

Create `apps/web/src/components/org/snowflake-setup-sql.ts` exporting `SNOWFLAKE_SETUP_SQL` as a template string containing the exact SQL from spec §4.3 (the Codex-reviewed block). Copy it verbatim from `docs/superpowers/specs/2026-06-16-per-org-snowflake-onboarding-design.md` §4.3.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && npm test -- snowflake-setup-sql`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/org/snowflake-setup-sql.ts apps/web/src/components/org/snowflake-setup-sql.test.ts
git commit -m "feat(web): add Codex-reviewed Snowflake setup SQL snippet"
```

### Task 14: Connect wizard component

**Files:**
- Create: `apps/web/src/components/org/connect-wizard.tsx`
- Test: `apps/web/src/components/org/connect-wizard.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/components/org/connect-wizard.test.tsx`:

```typescript
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ConnectWizard from "./connect-wizard";

function fill() {
  fireEvent.change(screen.getByLabelText(/organization name/i), { target: { value: "Acme" } });
  fireEvent.change(screen.getByLabelText(/account/i), { target: { value: "GOPGUKF-JO19546" } });
  fireEvent.change(screen.getByLabelText(/^user/i), { target: { value: "GREYBEAM_USER" } });
  fireEvent.change(screen.getByLabelText(/role/i), { target: { value: "GREYBEAM_ROLE" } });
  fireEvent.change(screen.getByLabelText(/warehouse/i), { target: { value: "GREYBEAM_WH" } });
  fireEvent.change(screen.getByLabelText(/private key/i), { target: { value: "PEM" } });
}

describe("ConnectWizard", () => {
  it("submits and calls onConnected with the new org id", async () => {
    const connect = vi.fn().mockResolvedValue("org-123");
    const onConnected = vi.fn();
    render(<ConnectWizard connect={connect} onConnected={onConnected} />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: /test connection & save/i }));
    await waitFor(() => expect(onConnected).toHaveBeenCalledWith("org-123"));
    expect(connect).toHaveBeenCalledWith(
      expect.objectContaining({ orgName: "Acme", account: "GOPGUKF-JO19546" }),
    );
  });

  it("shows the server validation message on failure", async () => {
    const { ConnectValidationError } = await import("../../lib/onboarding-api");
    const connect = vi.fn().mockRejectedValue(new ConnectValidationError("Bad Account Usage access"));
    render(<ConnectWizard connect={connect} onConnected={vi.fn()} />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: /test connection & save/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bad Account Usage access");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/web && npm test -- connect-wizard`
Expected: FAIL (component missing).

- [ ] **Step 3: Implement the wizard**

Create `apps/web/src/components/org/connect-wizard.tsx`. Use the dashboard design tokens (reuse the same `bg-slate-50` background, `rounded-lg border border-slate-200 bg-white shadow-sm` card surfaces, `text-slate-*` typography, and the `bg-slate-950` primary button already used in `org-shell.tsx`) for visual consistency. Two-column layout: inputs left, guidance right.

```typescript
"use client";

import { useState } from "react";

import {
  connectSnowflake as defaultConnect,
  ConnectConflictError,
  ConnectValidationError,
  type ConnectSnowflakeInput,
} from "../../lib/onboarding-api";
import { SNOWFLAKE_SETUP_SQL } from "./snowflake-setup-sql";

interface ConnectWizardProps {
  accessToken?: string | null;
  connect?: (input: ConnectSnowflakeInput, options: { accessToken?: string | null }) => Promise<string>;
  onConnected: (organizationId: string) => void;
}

const KEY_PAIR_DOCS =
  "https://docs.snowflake.com/en/user-guide/key-pair-auth#generate-the-private-keys";

export default function ConnectWizard({
  accessToken = null,
  connect = defaultConnect,
  onConnected,
}: ConnectWizardProps) {
  const [form, setForm] = useState<ConnectSnowflakeInput>({
    orgName: "", account: "", user: "", role: "", warehouse: "",
    database: "", schema: "", privateKeyPem: "", passphrase: "",
  });
  const [status, setStatus] = useState<"idle" | "submitting">("idle");
  const [error, setError] = useState<string | null>(null);

  const update = (key: keyof ConnectSnowflakeInput) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((prev) => ({ ...prev, [key]: event.target.value }));

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setStatus("submitting");
    try {
      const organizationId = await connect(form, { accessToken });
      onConnected(organizationId);
    } catch (caught) {
      if (caught instanceof ConnectValidationError || caught instanceof ConnectConflictError) {
        setError(caught.message || "We couldn’t validate that Snowflake connection.");
      } else {
        setError("Something went wrong. Please try again.");
      }
      setStatus("idle");
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <h1 className="text-lg font-semibold text-slate-950">Connect your Snowflake account</h1>
      <p className="mt-1 text-sm text-slate-600">
        Greybeam reads only Snowflake metadata. No query results or usage data leave your account.
      </p>
      <div className="mt-6 grid gap-8 md:grid-cols-2">
        <form className="space-y-4" onSubmit={handleSubmit}>
          <Field id="orgName" label="Organization name" value={form.orgName} onChange={update("orgName")} required />
          <Field id="account" label="Account" value={form.account} onChange={update("account")} required />
          <Field id="user" label="User" value={form.user} onChange={update("user")} required />
          <Field id="role" label="Role" value={form.role} onChange={update("role")} required
            hint="The role must read the SNOWFLAKE.ACCOUNT_USAGE views." />
          <Field id="warehouse" label="Warehouse" value={form.warehouse} onChange={update("warehouse")} required />
          <Field id="database" label="Database (optional)" value={form.database ?? ""} onChange={update("database")} />
          <Field id="schema" label="Schema (optional)" value={form.schema ?? ""} onChange={update("schema")} />
          <div>
            <label className="block text-sm font-medium text-slate-700" htmlFor="privateKeyPem">
              Private key (PEM)
            </label>
            <textarea
              id="privateKeyPem"
              className="mt-1 h-32 w-full rounded-md border border-slate-300 p-2 font-mono text-xs"
              value={form.privateKeyPem}
              onChange={update("privateKeyPem")}
              required
            />
            <a className="text-xs text-slate-500 underline" href={KEY_PAIR_DOCS} target="_blank" rel="noreferrer">
              How to generate a key pair
            </a>
          </div>
          <Field id="passphrase" label="Key passphrase (optional)" value={form.passphrase ?? ""} onChange={update("passphrase")} type="password" />
          {error ? (
            <p className="text-sm font-medium text-red-700" role="alert">{error}</p>
          ) : null}
          <button
            className="rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
            disabled={status === "submitting"}
            type="submit"
          >
            {status === "submitting" ? "Validating Snowflake connection…" : "Test connection & save"}
          </button>
        </form>
        <aside className="space-y-3">
          <p className="text-sm text-slate-700">
            Recommended: create a dedicated user + role for complete isolation. Replace the public key, then run:
          </p>
          <pre className="max-h-96 overflow-auto rounded-md bg-slate-950 p-4 text-xs text-slate-100">
            {SNOWFLAKE_SETUP_SQL}
          </pre>
        </aside>
      </div>
    </section>
  );
}

interface FieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  required?: boolean;
  type?: string;
  hint?: string;
}

function Field({ id, label, value, onChange, required, type = "text", hint }: FieldProps) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-700" htmlFor={id}>{label}</label>
      <input
        id={id}
        type={type}
        className="mt-1 w-full rounded-md border border-slate-300 p-2 text-sm"
        value={value}
        onChange={onChange}
        required={required}
      />
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && npm test -- connect-wizard`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/org/connect-wizard.tsx apps/web/src/components/org/connect-wizard.test.tsx
git commit -m "feat(web): Snowflake connect wizard (dashboard-consistent styling)"
```

### Task 15: Render the wizard from `OrgShell` instead of "coming soon"

**Files:**
- Modify: `apps/web/src/components/org/org-shell.tsx`
- Test: `apps/web/src/components/org/org-shell.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `apps/web/src/components/org/org-shell.test.tsx` a test that, when a signed-in session resolves zero memberships, the wizard heading is shown and the old "coming soon" text is gone. Mirror the existing test setup in that file for providing a fake `authClient` + `fetchMemberships` that returns `[]`:

```typescript
it("shows the connect wizard when the user has no organizations", async () => {
  // ...reuse this file's existing signed-in harness, with fetchMemberships -> []
  expect(await screen.findByText(/connect your snowflake account/i)).toBeInTheDocument();
  expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/web && npm test -- org-shell`
Expected: FAIL (still rendering "coming soon").

- [ ] **Step 3: Replace the zero-membership branch**

In `apps/web/src/components/org/org-shell.tsx`, import the wizard at the top:

```typescript
import ConnectWizard from "./connect-wizard";
```

Replace the `membership.organizations.length === 0` block (lines ~280–291) with:

```typescript
  if (membership.organizations.length === 0) {
    return (
      <main className="min-h-screen bg-slate-50 p-6">
        <section className="mb-6 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          {signedInHeader}
        </section>
        <ConnectWizard
          accessToken={accessToken}
          onConnected={() => accessToken && void loadMemberships(accessToken)}
        />
      </main>
    );
  }
```

(After a successful connect, re-running `loadMemberships` resolves the new org and routes the user into the dashboard via the existing resolved-state render.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && npm test -- org-shell`
Expected: PASS.

- [ ] **Step 5: Run the full web suite**

Run: `cd apps/web && npm test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/org/org-shell.tsx apps/web/src/components/org/org-shell.test.tsx
git commit -m "feat(web): replace coming-soon screen with the Snowflake connect wizard"
```

---

## Phase 8 — Teardown wiring + docs

### Task 16: Disconnect endpoint that deletes the Vault secret (admin-only)

**Files:**
- Modify: `apps/api/app/routes/onboarding.py`
- Modify: `apps/api/app/services/org_provisioning.py` (add a `delete` caller) or a small `disconnect` service
- Test: `apps/api/tests/test_onboarding_route.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_onboarding_route.py`:

```python
def test_disconnect_requires_admin(monkeypatch) -> None:
    from app.auth import AuthContext, require_auth_context
    from app.services.membership_directory import Organization

    member_ctx = AuthContext(
        user_id="u", auth_required=True, memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="member"),),
    )
    app.dependency_overrides[require_auth_context] = lambda: member_ctx
    client = TestClient(app)
    response = client.post("/api/onboarding/org-1/disconnect")
    app.dependency_overrides.clear()
    assert response.status_code == 403


def test_disconnect_deletes_secret_for_admin(monkeypatch) -> None:
    from app.auth import AuthContext, require_auth_context
    from app.services.membership_directory import Organization

    admin_ctx = AuthContext(
        user_id="u", auth_required=True, memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="owner"),),
    )
    app.dependency_overrides[require_auth_context] = lambda: admin_ctx
    deleted = []
    monkeypatch.setattr(onboarding, "delete_org_secret", lambda org_id: deleted.append(org_id))
    client = TestClient(app)
    response = client.post("/api/onboarding/org-1/disconnect")
    app.dependency_overrides.clear()
    assert response.status_code == 204
    assert deleted == ["org-1"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_onboarding_route.py -k disconnect -v`
Expected: FAIL (route missing).

- [ ] **Step 3: Implement the disconnect route + seam**

In `apps/api/app/routes/onboarding.py` add:

```python
from app.auth import require_org_admin


def delete_org_secret(organization_id: str) -> None:
    """Seam over the service-role delete RPC; configured at startup."""
    from app.services.org_provisioning import delete_org_secret as impl

    impl(organization_id)


@router.post("/{organization_id}/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_snowflake(
    organization_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> None:
    require_org_admin(auth_context, organization_id)
    delete_org_secret(organization_id)
```

In `apps/api/app/services/org_provisioning.py`, add a `delete_org_secret` that POSTs to `/rest/v1/rpc/delete_organization_snowflake_secret` using the same configured service-role client pattern as `create_org_with_connection` (add a module-level configured caller mirroring `_provisioner`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_onboarding_route.py -k disconnect -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routes/onboarding.py apps/api/app/services/org_provisioning.py apps/api/tests/test_onboarding_route.py
git commit -m "feat(api): admin-only disconnect that deletes the org Vault secret"
```

### Task 17: Update docs

**Files:**
- Modify: `docs/auth-and-deployment.md`, `docs/snowflake-setup.md`, `docs/security-model.md`

- [ ] **Step 1: Update the first-user bootstrap and security docs**

- In `docs/auth-and-deployment.md`, replace the "First-user bootstrap" section's "self-serve is Spec B" note with the new self-service flow (sign in → connect wizard → validated → org created), and note `.env` Snowflake vars are only used in `auth_required=false` self-host.
- In `docs/snowflake-setup.md`, add the customer-facing dedicated-user setup SQL (from spec §4.3) and the `USAGE_VIEWER` grant.
- In `docs/security-model.md`, document that per-org Snowflake keys are stored in Supabase Vault (encrypted at rest, key outside the DB), read only via a service-role RPC, and that the resolver fails closed under auth.

- [ ] **Step 2: Verify no broken references**

Run: `grep -rn "coming soon" docs/ apps/web/src` — expect no remaining onboarding "coming soon" copy.

- [ ] **Step 3: Commit**

```bash
git add docs/auth-and-deployment.md docs/snowflake-setup.md docs/security-model.md
git commit -m "docs: document self-service Snowflake onboarding and per-org secret storage"
```

---

## Final verification

- [ ] **API suite:** `cd apps/api && uv run pytest -q` → all pass.
- [ ] **Web suite:** `cd apps/web && npm test` → all pass.
- [ ] **Lint/format:** run the repo's configured formatters/linters (ruff/black for API per repo config; eslint/prettier for web).
- [ ] **Manual smoke (Kyle verifies visually):** with a real Supabase + Vault project and `AUTH_REQUIRED=true`, sign in as a brand-new user → the connect wizard appears in dashboard styling → enter real Snowflake keypair creds → "Test connection & save" → dashboard loads against those creds. Confirm `USAGE_VIEWER` covers all four probe views (esp. `QUERY_ATTRIBUTION_HISTORY`) against dev account `TU24199`; if a probe is denied, document the broad-grant fallback (spec §4.3).

---

## Notes for the implementer

- **Vault is integration-tested, not unit-tested.** The migration tests assert the SQL text; the real Vault round-trip (`create_secret` → `decrypted_secrets`) must be exercised against a live Supabase project during the manual smoke. Treat any Vault/RPC error as fail-closed (the resolver already does).
- **Never log PEM or passphrase.** `SnowflakeConnectionConfig` marks them `repr=False`; keep them out of new log lines and audit payloads.
- **The one-org guard** lives in the DB (`one_owner_membership_per_user` + advisory lock). To enable multi-org later, drop that index and the `OrgAlreadyExistsError` mapping, then add the org switcher.
