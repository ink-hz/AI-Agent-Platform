# DingTalk Identity Release 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the temporary shared Basic Auth entry with a DingTalk-authenticated, default-deny Release 1 foundation that gives members only account access, gives scoped management viewers an exact per-Agent read allowlist, and preserves the owner's existing sanitized management view.

**Architecture:** Add an independently migrated `agent_platform_control` database beside the existing rebuildable `agent_platform` replica, then put server-side DingTalk identity, directory freshness, roles, observation grants, Web Sessions, CSRF, audit, and authorization in a new `app.control_plane` package. Keep existing business data in the sanitized replica, extend that replica only with the minimum Review and Operations projections required by the R1 viewer allowlist, and run Stream ingestion plus reconciliation in isolated worker processes. Prove the candidate under `/_preview/dingtalk-r1/` and its preview control database before replacing root Basic Auth; do not modify FAE or expose ports 8000/8080/PostgreSQL/MinIO.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, psycopg 3, httpx, cryptography, `dingtalk-stream==0.24.3`, PostgreSQL 17 native base-backup/WAL recovery, React 19, TypeScript, Vite, Pytest, Vitest, Docker Compose, Nginx 1.24, and Bash.

**Design source:** `docs/superpowers/specs/2026-08-13-dingtalk-identity-unified-agent-entry-design.md`, Release 1 only.

## Global Constraints

- FAE remains an independent external product. Do not change or restart its application, database, container, account model, model configuration, or domain routing.
- `agent_platform_control` and `agent_platform_control_preview` are authoritative only for identity/control state. The existing `agent_platform` database remains a sanitized, rebuildable replica and is never used for online authorization.
- The deployment uses separate control-app, directory-worker, Stream-ingest, audit-append, maintenance, replica-read, and migration DSNs. Revoke `PUBLIC` access; do not add FDW, dblink, cross-database grants, or a shared superuser DSN.
- Provider identifiers are encrypted and HMAC-indexed with explicit key versions. Names are display attributes only. Existing unique-name matching remains a replica-side annotation and never establishes ownership, role, or authorization.
- Members receive `403` on every route except authenticated account/logout/status. A viewer receives only the six exact GET routes from the design and must provide exactly one scoped Agent for the first five. All other routes are owner-only in R1.
- Authentication failures return `401`, authorization failures return `403`, stale directory dependencies return explicit `503`, and throttling returns `429`; none silently downgrades identity or permissions.
- Existing cloud Review mutations stay disabled. R1 does not add unified chat, Agent use, Session ownership migration, attachment erasure, exports, SSE, model selection, Prompt editing, or Agent grants.
- Preview and production use different control databases, Cookies, state namespace, signing/encryption keys, and DingTalk test-member scope. Preview must not read or mutate production control data.
- Production cutover is forbidden until automated tests, a real DingTalk preview test, control-only PITR rehearsal, WAL-pressure gates, rollback rehearsal, internal DNS, company proxy, and public DNS acceptance all pass.
- Commit after every task's focused tests pass. Never commit DingTalk AppSecret, OAuth tokens, raw provider IDs, control keys, Cookie tokens, CSRF secrets, Basic Auth plaintext, or production database URLs.

---

### Task 1: Freeze the R1 configuration and dependency contract

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/requirements.cloud.txt`
- Modify: `backend/app/config.py`
- Create: `backend/app/control_plane/__init__.py`
- Create: `backend/app/control_plane/models.py`
- Modify: `backend/tests/test_config.py`
- Create: `backend/tests/test_control_plane_config.py`
- Modify: `backend/tests/test_requirements.py`

**Interfaces:**
- Consumes secret-file settings `PLATFORM_CONTROL_DATABASE_URL_FILE`, `PLATFORM_CONTROL_AUDIT_DATABASE_URL_FILE`, `PLATFORM_DINGTALK_APP_SECRET_FILE`, `PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE`, and `PLATFORM_IDENTITY_HMAC_KEYRING_FILE`.
- Consumes nonsecret settings `PLATFORM_IDENTITY_MODE=disabled|preview|production`, `PLATFORM_PUBLIC_BASE_URL`, `PLATFORM_ROUTE_PREFIX`, `PLATFORM_COOKIE_NAME`, DingTalk AppKey/AgentId/CorpId, sync/freshness intervals, trusted-proxy CIDRs, and initial rate limits.
- Produces immutable `ControlPlaneConfig`, `IdentityMode`, `Role`, `DirectoryFreshness`, and `AuthContext` types.

Use explicit types rather than free-form dictionaries:

```python
class Role(StrEnum):
    MEMBER = "member"
    MANAGEMENT_VIEWER = "management_viewer"
    PLATFORM_OWNER = "platform_owner"

@dataclass(frozen=True)
class AuthContext:
    internal_user_id: UUID
    role: Role
    session_id: UUID
    hard_stale_read_only: bool

@dataclass(frozen=True)
class IssuedWebSession:
    session_id: UUID
    cookie_token: str
    csrf_token: str
    idle_expires_at: datetime
    absolute_expires_at: datetime

class IdentityMode(StrEnum):
    DISABLED = "disabled"
    PREVIEW = "preview"
    PRODUCTION = "production"

class DirectoryFreshness(StrEnum):
    FRESH = "fresh"
    WARNING = "warning"
    HARD_STALE = "hard_stale"

@dataclass(frozen=True)
class ControlPlaneConfig:
    mode: IdentityMode
    control_database_url_file: str
    audit_database_url_file: str
    public_base_url: str
    route_prefix: str
    cookie_name: str
    dingtalk_app_key: str
    dingtalk_agent_id: str
    dingtalk_corp_id: str
    dingtalk_app_secret_file: str
    encryption_keyring_file: str
    hmac_keyring_file: str
    reconcile_interval_seconds: int = 21_600
    warning_after_seconds: int = 28_800
    hard_stale_after_seconds: int = 86_400
    trusted_proxy_cidrs: tuple[str, ...] = ("127.0.0.1/32", "::1/128")
```

Representative failing test and minimum implementation shape:

```python
def test_preview_requires_path_scoped_cookie(tmp_path, monkeypatch):
    install_control_secret_files(tmp_path, monkeypatch)
    monkeypatch.setenv("PLATFORM_IDENTITY_MODE", "preview")
    monkeypatch.setenv("PLATFORM_ROUTE_PREFIX", "/_preview/dingtalk-r1")
    monkeypatch.setenv("PLATFORM_COOKIE_NAME", "__Host-platform-preview")
    with pytest.raises(ValueError, match="__Host- cookies require Path=/"):
        load_config()
```

Use `platform_preview_session` for preview because an RFC `__Host-` Cookie cannot be path-scoped; production uses `__Host-platform_session` at `Path=/`.

- [ ] **Step 1: Write failing configuration tests** covering disabled defaults, exact preview/production required fields, unique preview versus production Cookie names, route-prefix normalization, loopback-only trusted proxy defaults, 6h/8h/24h freshness ordering, safe public-base URLs, mode-0600 secret files, and rejection of secrets supplied directly in environment variables.
- [ ] **Step 2: Write a failing requirements test** requiring the stable pin `dingtalk-stream==0.24.3` in both runtime requirement files and forbidding an unpinned DingTalk SDK.
- [ ] **Step 3: Run `cd backend && .venv/bin/python -m pytest tests/test_config.py tests/test_control_plane_config.py tests/test_requirements.py -q` and verify RED**. Expected: collection fails with `ModuleNotFoundError: No module named 'app.control_plane'`, then the dependency assertion reports the missing exact pin.
- [ ] **Step 4: Implement the minimum typed configuration** in `config.py` and `control_plane/models.py`; keep `identity_mode=disabled` backward-compatible so existing local and cloud tests do not require DingTalk secrets.
- [ ] **Step 5: Add the exact SDK pin**, install with `cd backend && .venv/bin/python -m pip install -r requirements.txt`, and verify with `cd backend && .venv/bin/python -c 'from dingtalk_stream.version import VERSION_STRING; assert VERSION_STRING == "0.24.3"; print(VERSION_STRING)'`. Expected stdout: `0.24.3`.
- [ ] **Step 6: Run the focused tests and verify GREEN**, then run `git diff --check` and scan the diff with `git diff | rg -i 'appsecret|access[_-]?token|cookie[_-]?token|csrf[_-]?token|BEGIN (RSA|OPENSSH|PRIVATE) KEY'`; every hit must be a field name, redaction test, or documentation prohibition, never a credential value.
- [ ] **Step 7: Commit** with `git add backend/requirements.txt backend/requirements.cloud.txt backend/app/config.py backend/app/control_plane backend/tests/test_config.py backend/tests/test_control_plane_config.py backend/tests/test_requirements.py && git commit -m "feat(identity): define DingTalk R1 configuration"`.

### Task 2: Bootstrap and migrate the isolated control databases

**Files:**
- Create: `backend/control_migrations/001_identity_security.sql`
- Create: `backend/app/control_plane/database.py`
- Create: `backend/app/control_plane/migrate.py`
- Create: `deploy/cloud/bootstrap-control-db.sh`
- Modify: `deploy/cloud/remote-stage.sh`
- Create: `backend/tests/test_control_plane_migration.py`
- Modify: `backend/tests/test_cloud_deployment.py`

**Interfaces:**
- Produces databases `agent_platform_control` and `agent_platform_control_preview` in the existing PostgreSQL cluster.
- Produces cluster roles `platform_control_migrator`, `platform_control_app`, `platform_directory_worker`, `platform_stream_ingest`, `platform_audit_append`, and `platform_control_maintenance` with separate generated passwords/DSN files.
- Produces schema `platform_control` with the complete R1 data model and an append-only audit surface.

The first migration must create, constrain, and index at least:

```sql
CREATE TYPE platform_control.user_role AS ENUM
  ('member', 'management_viewer', 'platform_owner');

CREATE TABLE platform_control.internal_users (
  internal_user_id uuid PRIMARY KEY,
  role platform_control.user_role NOT NULL DEFAULT 'member',
  display_name text NOT NULL,
  status text NOT NULL CHECK (status IN ('active', 'inactive', 'disabled')),
  last_confirmed_generation_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_platform_owner
  ON platform_control.internal_users ((role))
  WHERE role = 'platform_owner' AND status = 'active';
```

It also creates `provider_identities`, `directory_generations`, `directory_state`, `directory_members`, `directory_departments`, `department_closure`, `member_departments`, `login_attempts`, `web_sessions`, `observation_grants`, `stream_inbox`, `sync_runs`, `auth_rate_buckets`, and `audit_events`. Every provider lookup row has `subject_kind`, `lookup_hmac`, `lookup_key_version`, `encrypted_provider_id`, and `encryption_key_version`; no raw provider-ID column is allowed. `web_sessions` stores only token/CSRF hashes. `stream_inbox.event_key` and active observation grants are unique. Audit rows cannot be updated or deleted through table grants; a fixed-cutoff `SECURITY DEFINER` retention function is executable only by `platform_control_maintenance`, validates `cutoff <= database_now - interval '365 days'`, and is the only delete path.

The migration runner contract is:

```python
def migrate_control_database(database_url: str, migration_dir: Path) -> None:
    with psycopg.connect(database_url, autocommit=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select pg_advisory_xact_lock(%s)", (0x41504331,))
            for migration in load_numbered_migrations(migration_dir):
                verify_or_apply(cursor, migration.version, migration.sha256, migration.sql)
        connection.commit()
```

`verify_or_apply` inserts `(version, sha256, applied_at)` once, skips an identical applied version, and raises `MigrationChecksumMismatch` before executing a changed migration.

- [ ] **Step 1: Write failing migration tests** that create a disposable database, run the control migration twice, inspect all tables/constraints/indexes/grants, prove the partial owner uniqueness constraint, reject raw provider IDs, reject audit update/delete, and prove app/worker/audit roles cannot cross their grants.
- [ ] **Step 2: Write failing deployment policy tests** requiring two separate databases, six separate mode-0600 control DSN files including migrator/Stream/maintenance roles, `PUBLIC` connect/schema revocation, no FDW/dblink, no literal passwords, and no change to the existing replica database/role or DSN.
- [ ] **Step 3: Run `cd backend && .venv/bin/python -m pytest tests/test_control_plane_migration.py tests/test_cloud_deployment.py -q` and verify RED**. Expected: the migration-path assertion reports `backend/control_migrations/001_identity_security.sql` missing.
- [ ] **Step 4: Implement an advisory-locking migration runner** that records checksum/version in `platform_control.schema_migrations`, refuses changed checksums, and accepts only the migrator DSN through a secret file.
- [ ] **Step 5: Implement `bootstrap-control-db.sh`** to generate role passwords without stdout/argv exposure, create both databases from `template0`, write separate DSN files, revoke broad access, run the migration against each database, and remain idempotent.
- [ ] **Step 6: Extend `remote-stage.sh` only to call the reviewed bootstrap helper**; it must not inline SQL passwords, restart FAE, publish the domain, or replace the existing replica DSN.
- [ ] **Step 7: Run focused tests against disposable PostgreSQL and verify GREEN**, then run `bash -n deploy/cloud/bootstrap-control-db.sh deploy/cloud/remote-stage.sh` and `git diff --check`.
- [ ] **Step 8: Commit** with `git add backend/control_migrations backend/app/control_plane/database.py backend/app/control_plane/migrate.py deploy/cloud/bootstrap-control-db.sh deploy/cloud/remote-stage.sh backend/tests/test_control_plane_migration.py backend/tests/test_cloud_deployment.py && git commit -m "feat(identity): add isolated control databases"`.

### Task 3: Implement versioned provider identity cryptography and repository boundaries

**Files:**
- Create: `backend/app/control_plane/crypto.py`
- Create: `backend/app/control_plane/repository.py`
- Create: `backend/tests/test_identity_crypto.py`
- Create: `backend/tests/test_control_plane_repository.py`

**Interfaces:**
- `IdentityKeyring.from_file(path)` loads `{active_version, keys}` without logging key bytes.
- `ProviderIdentityCodec.seal(kind, provider_id)` returns versioned ciphertext and versioned lookup HMAC.
- `ControlRepository.resolve_provider_identity(protected: ProtectedProviderId) -> UUID | None`, `create_internal_user(protected: ProtectedProviderId, display_name: str) -> UUID`, `create_web_session(internal_user_id: UUID, idle_seconds: int, absolute_seconds: int) -> IssuedWebSession`, `revoke_user_sessions(internal_user_id: UUID, reason: str) -> int`, and `list_observation_scopes(internal_user_id: UUID) -> tuple[str, ...]` operate only on the control database.

Use a record that makes key versioning unavoidable:

```python
@dataclass(frozen=True)
class ProtectedProviderId:
    subject_kind: str
    lookup_hmac: bytes
    lookup_key_version: int
    ciphertext: bytes
    encryption_key_version: int

class ProviderIdentityCodec:
    def seal(self, subject_kind: str, provider_id: str) -> ProtectedProviderId:
        normalized = normalize_provider_id(provider_id)
        lookup_input = f"dingtalk:{subject_kind}:{normalized}".encode()
        lookup = hmac.digest(self.hmac.active_key, lookup_input, "sha256")
        aad = f"dingtalk:{subject_kind}:v{self.encryption.active_version}".encode()
        nonce = secrets.token_bytes(12)
        encrypted = AESGCM(self.encryption.active_key).encrypt(
            nonce, normalized.encode(), aad
        )
        return ProtectedProviderId(
            subject_kind=subject_kind,
            lookup_hmac=lookup,
            lookup_key_version=self.hmac.active_version,
            ciphertext=nonce + encrypted,
            encryption_key_version=self.encryption.active_version,
        )
```

- [ ] **Step 1: Write failing crypto tests** for deterministic HMAC lookup, randomized AES-GCM ciphertext, authenticated subject-kind/version AAD, active-plus-previous lookup, malformed keyring rejection, redacted `repr`, and rotation that re-derives lookups from decrypted IDs without changing `internal_user_id`.
- [ ] **Step 2: Write failing repository tests** for atomic create-or-resolve, collision rejection, no name-based lookup method, opaque Session token hashing, single-use login attempts, idle/absolute expiry, revocation, exactly-one-owner enforcement, and observation grants scoped by exact Agent ID.
- [ ] **Step 3: Run `cd backend && .venv/bin/python -m pytest tests/test_identity_crypto.py tests/test_control_plane_repository.py -q` and verify RED**. Expected: imports of `ProviderIdentityCodec` and `ControlRepository` fail.
- [ ] **Step 4: Implement AES-256-GCM and HMAC-SHA-256 keyrings** using `cryptography`, constant-time comparison, explicit `kid`, and generic errors that never include provider values or ciphertext.
- [ ] **Step 5: Implement repository transactions with parameterized psycopg SQL**, database time for expiry checks, `SELECT ... FOR UPDATE` for attempt consumption/session rotation, and no connection to the existing `agent_platform` replica database.
- [ ] **Step 6: Run focused tests and verify GREEN**, then run `git diff --check` and `rg -n "unionid|userid|staffId|mobile" backend/app/control_plane` to manually verify that logs/errors do not expose values.
- [ ] **Step 7: Commit** with `git add backend/app/control_plane/crypto.py backend/app/control_plane/repository.py backend/tests/test_identity_crypto.py backend/tests/test_control_plane_repository.py && git commit -m "feat(identity): protect provider identity mappings"`.

### Task 4: Add fail-closed audit and offline role administration

**Files:**
- Create: `backend/app/control_plane/audit.py`
- Create: `backend/app/control_plane/admin_cli.py`
- Create: `backend/app/control_plane/maintenance_cli.py`
- Create: `backend/app/control_plane/routes_manage.py`
- Create: `backend/tests/test_control_plane_audit.py`
- Create: `backend/tests/test_identity_admin_cli.py`
- Create: `backend/tests/test_control_maintenance_cli.py`
- Create: `backend/tests/test_governance_audit_api.py`
- Create: `docs/runbooks/platform-identity-break-glass.md`

**Interfaces:**
- `AuditWriter.append(command: AuditCommand) -> UUID` writes through the dedicated audit DSN.
- Offline CLI commands: `bind-owner`, `replace-owner`, and `show-directory-generation`. The owner assigns viewers through authenticated, CSRF-protected management routes; offline role changes are reserved for initial owner binding and break-glass replacement.
- Owner-only routes: `GET /api/v1/manage/users`, `POST|DELETE /api/v1/manage/viewers/{internal_user_id}`, and `PUT|DELETE /api/v1/manage/viewers/{internal_user_id}/observations/{agent_id}`. The user list returns internal ID, display name, local status, role, and scopes only; it never returns raw DingTalk IDs, mobile, or email.
- `GET /api/v1/manage/audit/governance` returns sanitized immutable governance metadata only.
- `python -m app.control_plane.maintenance_cli purge-expired` deletes only audit rows older than 365 days plus expired login attempts/Sessions/rate buckets; the runtime app/audit/worker roles cannot delete audit rows.

```python
@dataclass(frozen=True)
class AuditCommand:
    event_type: str
    actor_internal_user_id: UUID
    target_type: str
    target_id: str
    request_id: UUID
    reason: str
    metadata: Mapping[str, str | int | bool]

class AuditWriter:
    def append(self, command: AuditCommand) -> UUID:
        sanitized = sanitize_governance_metadata(command.metadata)
        return self.repository.append(command, sanitized)
```

The audit row for a requested mutation is written first. The mutation stores that audit ID. If the mutation later fails, append a separate `management_mutation_failed` event; never update the original audit row.

- [ ] **Step 1: Write failing audit tests** proving append-only order, reason requirements, request-ID correlation, sanitization of subject IDs/Session text/filenames/Evidence, viewer visibility of owner role actions, a hard failure when the audit insert fails before a sensitive mutation commits, and exact 365-day retention where only the maintenance role can purge already-expired rows.
- [ ] **Step 2: Write failing CLI tests** proving stable HMAC/provider mapping selection, no name-based target selection, one-owner invariant, two-person confirmation fields for emergency replacement, explicit generation selection, no Web API for owner replacement, and machine-readable success/error output without provider IDs.
- [ ] **Step 3: Write failing API tests** that permit owner-only viewer-role/observation-scope mutations with CSRF, fresh directory, reason, and audit; reject viewer/member/hard-stale mutation; revoke active viewer Sessions when the role is removed; permit owner/viewer governance-audit reads; reject members and unscoped content; and audit every privileged read.
- [ ] **Step 4: Run `cd backend && .venv/bin/python -m pytest tests/test_control_plane_audit.py tests/test_identity_admin_cli.py tests/test_control_maintenance_cli.py tests/test_governance_audit_api.py -q` and verify RED**. Expected: imports of `AuditWriter`, `admin_cli`, and `maintenance_cli` fail.
- [ ] **Step 5: Implement audit-first transactions**: create the audit row through the append DSN, perform the sensitive change with the event ID, and roll back/return `503` if the required audit cannot be persisted. Document the intentional phase-one governance limit that an owner can revoke a viewer and there is no independent external audit sink.
- [ ] **Step 6: Implement the offline admin and maintenance CLIs plus runbook** with exact commands, two named approvers for stale break-glass, database backup prerequisite, fresh-directory preference, rollback, post-recovery audit reconciliation, fixed one-year audit cutoff, and refusal to purge when time/WAL health is unknown.
- [ ] **Step 7: Run focused tests and verify GREEN**, then run the CLI `--help`, `git diff --check`, and the existing no-Keychain test.
- [ ] **Step 8: Commit** with `git add backend/app/control_plane/audit.py backend/app/control_plane/admin_cli.py backend/app/control_plane/maintenance_cli.py backend/app/control_plane/routes_manage.py backend/tests/test_control_plane_audit.py backend/tests/test_identity_admin_cli.py backend/tests/test_control_maintenance_cli.py backend/tests/test_governance_audit_api.py docs/runbooks/platform-identity-break-glass.md && git commit -m "feat(identity): add audited role administration"`.

### Task 5: Build the DingTalk OpenAPI boundary and identity resolver

**Files:**
- Create: `backend/app/control_plane/dingtalk.py`
- Create: `backend/app/control_plane/identity.py`
- Create: `backend/tests/test_dingtalk_client.py`
- Create: `backend/tests/test_dingtalk_identity.py`

**Interfaces:**
- `DingTalkClient.exchange_login_code(code)`, `resolve_union_member(unionid)`, `get_member(userid)`, `iter_departments()`, and `iter_department_members(department_id)` return typed DTOs/async iterators.
- `IdentityResolver.resolve_active_member(auth_result, freshness)` returns an existing/created `internal_user_id` only after corp and active membership checks.

```python
@dataclass(frozen=True)
class DingTalkMember:
    userid: str
    unionid: str
    display_name: str
    active: bool
    department_ids: tuple[int, ...]

@dataclass(frozen=True)
class DingTalkAuthResult:
    unionid: str
    userid: str | None
    corp_id: str

@dataclass(frozen=True)
class DingTalkDepartment:
    department_id: int
    parent_department_id: int | None
    display_name: str
```

The exact public signatures are `exchange_login_code(code: str) -> DingTalkAuthResult`, `resolve_union_member(unionid: str) -> DingTalkMember`, `get_member(userid: str) -> DingTalkMember`, `iter_departments() -> AsyncIterator[DingTalkDepartment]`, `iter_department_members(department_id: int) -> AsyncIterator[DingTalkMember]`, and `resolve_active_member(auth_result: DingTalkAuthResult, freshness: DirectoryFreshness) -> UUID`.

- [ ] **Step 1: Write failing `respx` tests** for token acquisition/expiry, QR and in-client code exchange, corp mismatch, inactive/absent users, pagination, provider error redaction, bounded timeout, 429/5xx retry only for idempotent reads, and no retry of login-code exchange.
- [ ] **Step 2: Write failing resolver tests** proving QR unionid and in-client userid converge on one internal identity, display-name changes do not change identity, ambiguous/name-only data cannot create an identity, normal login requires active current directory state, and no failed flow leaves a partial user.
- [ ] **Step 3: Run `cd backend && .venv/bin/python -m pytest tests/test_dingtalk_client.py tests/test_dingtalk_identity.py -q` and verify RED**. Expected: imports of `DingTalkClient` and `IdentityResolver` fail.
- [ ] **Step 4: Implement the client with `httpx.AsyncClient`**, exact official endpoints/configurable base URL for tests, `X-Request-Id`, strict response models, a redacting error type, and an application-token cache protected by an async lock.
- [ ] **Step 5: Implement the resolver as one control-database transaction** that validates the active generation and provider HMAC before mapping/creating the internal user; names update only `display_name`.
- [ ] **Step 6: Run focused tests and verify GREEN**, then verify logs under forced provider errors contain request IDs and error codes but no code, token, userid, unionid, AppSecret, mobile, or email.
- [ ] **Step 7: Commit** with `git add backend/app/control_plane/dingtalk.py backend/app/control_plane/identity.py backend/tests/test_dingtalk_client.py backend/tests/test_dingtalk_identity.py && git commit -m "feat(identity): resolve verified DingTalk members"`.

### Task 6: Implement server-side Web Sessions, login flows, CSRF, and the public allowlist

**Files:**
- Create: `backend/app/control_plane/auth.py`
- Create: `backend/app/control_plane/routes_auth.py`
- Create: `backend/app/control_plane/middleware.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/spa.py`
- Create: `backend/tests/test_dingtalk_auth_api.py`
- Create: `backend/tests/test_web_session_security.py`
- Modify: `backend/tests/test_spa_static.py`

**Interfaces:**
- Public: exact routes from design section 8.4 only.
- Authenticated: `GET /api/v1/account` and `POST /api/v1/auth/logout`; owner-only `GET /api/v1/manage/system-health` preserves the current detailed dependency/build health payload after public `/api/health` is minimized.
- Cookie: random opaque token; database stores SHA-256/HMAC token hash; production uses `HttpOnly; Secure; SameSite=Lax; Path=/`, while preview uses `Path=/_preview/dingtalk-r1/`.

The route contract is exact:

```text
GET  /                                      -> 302 /login
GET  /login
GET  /assets/{build-hashed-file}
GET  /favicon.ico
GET  /api/health                            -> minimal body
POST /api/v1/auth/dingtalk/start
GET  /api/v1/auth/dingtalk/callback
POST /api/v1/auth/dingtalk/in-client/exchange
GET  /api/v1/account                        -> authenticated
POST /api/v1/auth/logout                    -> authenticated + CSRF
```

The successful login path returns the Task 1 `IssuedWebSession` raw tokens only to the Cookie/CSRF response writer:

```python
async def complete_login(attempt_id: UUID, code: str) -> IssuedWebSession:
    auth_result = await dingtalk.exchange_login_code(code)
    internal_user_id = await identities.resolve_active_member(
        auth_result, freshness.evaluate()
    )
    return repository.consume_attempt_and_issue_session(
        attempt_id=attempt_id,
        internal_user_id=internal_user_id,
        idle_seconds=28_800,
        absolute_seconds=86_400,
    )
```

- [ ] **Step 1: Write failing API tests** for one-time five-minute state, exact environment and safe-return binding, unknown/expired/consumed state rejected before provider exchange, QR and in-client success, Session fixation prevention, Cookie flags/path, 8h idle/24h absolute expiry, logout revocation, and no username/password/anonymous fallback.
- [ ] **Step 2: Write failing allowlist tests** that enumerate every route and prove unauthenticated `401` outside the exact list, `GET /` returns `302 /login`, arbitrary `/assets/` paths do not reach application handlers, public health leaks no build/dependency/org/Agent/user facts, detailed health moved to owner-only `/api/v1/manage/system-health`, and prefix mode generates only prefixed URLs/Cookies.
- [ ] **Step 3: Write failing CSRF/Origin tests** for logout and every mutation: correct same-origin plus header/token succeeds; absent, mismatched, cross-origin, `null`, and untrusted forwarded scheme fail.
- [ ] **Step 4: Run `cd backend && .venv/bin/python -m pytest tests/test_dingtalk_auth_api.py tests/test_web_session_security.py tests/test_spa_static.py -q` and verify RED**. Expected: `/api/v1/auth/dingtalk/start` is `404` and the unauthenticated protected-route assertion receives the current public response instead of `401`.
- [ ] **Step 5: Implement the auth service and middleware** with atomic attempt consumption, post-login token rotation, database-time expiry, per-request local user status recheck, constant-time CSRF verification, and generic client errors.
- [ ] **Step 6: Mount public and protected routes deliberately in `main.py`**; do not infer publicness from route prefixes. Keep `identity_mode=disabled` behavior unchanged for existing tests and local operation.
- [ ] **Step 7: Run focused tests and verify GREEN**, then run `cd backend && .venv/bin/python -m pytest tests/test_main.py tests/test_api.py tests/test_cloud_api.py -q` and `git diff --check`.
- [ ] **Step 8: Commit** with `git add backend/app/control_plane/auth.py backend/app/control_plane/routes_auth.py backend/app/control_plane/middleware.py backend/app/main.py backend/app/spa.py backend/tests/test_dingtalk_auth_api.py backend/tests/test_web_session_security.py backend/tests/test_spa_static.py && git commit -m "feat(identity): add secure DingTalk Web Sessions"`.

### Task 7: Add state-first throttling and trusted-proxy handling

**Files:**
- Create: `backend/app/control_plane/rate_limit.py`
- Create: `backend/app/control_plane/client_address.py`
- Modify: `backend/app/control_plane/middleware.py`
- Create: `backend/tests/test_identity_rate_limits.py`
- Create: `backend/tests/test_trusted_proxy.py`

**Interfaces:**
- Login attempt limits: 5 starts/10m/challenge with exponential backoff and at most 3 active attempts.
- Edge ceilings: 600 starts/min burst 1200; 1200 callbacks/min; global exchange 100 concurrent and 3000/min.
- Authenticated limits: 300 reads/min/user and 60 mutations/min/user.
- Trusted immediate peers default only to `127.0.0.1/32` and `::1/128`.

```python
@dataclass(frozen=True)
class EdgeSource:
    ip: IPv4Address | IPv6Address
    scheme: Literal["http", "https"]

def resolve_edge_source(request: Request, trusted: tuple[IPv4Network | IPv6Network, ...]) -> EdgeSource:
    peer = ip_address(request.client.host)
    if not any(peer in network for network in trusted):
        return EdgeSource(ip=peer, scheme=request.url.scheme)
    forwarded_ip = parse_single_ip(request.headers.get("x-real-ip"))
    forwarded_scheme = parse_exact_scheme(request.headers.get("x-forwarded-proto"))
    return EdgeSource(ip=forwarded_ip, scheme=forwarded_scheme)
```

`parse_single_ip` rejects missing, comma-separated, malformed, private-header-chain, or multi-value input; it never falls back to a client-supplied value.

- [ ] **Step 1: Write failing concurrency tests** for atomic database buckets, challenge-first behavior, three active attempts, retry-after output, OAuth global breaker, per-user read/mutation isolation, NAT-shared IP not becoming an identity key, and fail-closed database errors.
- [ ] **Step 2: Write failing proxy tests** proving loopback proxy headers are accepted, an untrusted peer's `Forwarded`/`X-Forwarded-*` values are ignored, multi-value/spoofed inputs are rejected, scheme is derived safely, and client address is never used for user mapping/authorization.
- [ ] **Step 3: Run `cd backend && .venv/bin/python -m pytest tests/test_identity_rate_limits.py tests/test_trusted_proxy.py -q` and verify RED**. Expected: imports of `ControlRateLimiter` and `resolve_edge_source` fail.
- [ ] **Step 4: Implement PostgreSQL-backed fixed-window/token-bucket controls** with bounded cleanup and a process-wide async semaphore for provider exchanges; return `429` with `Retry-After` and no identity fallback.
- [ ] **Step 5: Implement peer-aware address extraction** that trusts forwarded headers only from configured CIDRs and otherwise discards every forwarded value.
- [ ] **Step 6: Run focused tests and verify GREEN**, then add a 100-way parallel callback test and verify the maximum observed provider concurrency is exactly 100.
- [ ] **Step 7: Commit** with `git add backend/app/control_plane/rate_limit.py backend/app/control_plane/client_address.py backend/app/control_plane/middleware.py backend/tests/test_identity_rate_limits.py backend/tests/test_trusted_proxy.py && git commit -m "feat(identity): enforce login and proxy protections"`.

### Task 8: Build atomic DingTalk directory reconciliation and closure tables

**Files:**
- Create: `backend/app/control_plane/directory.py`
- Create: `backend/app/control_plane/directory_worker.py`
- Create: `backend/tests/test_directory_reconciliation.py`
- Create: `backend/tests/test_directory_freshness.py`
- Create: `backend/tests/test_department_closure.py`

**Interfaces:**
- `DirectoryReconciler.run_full()` writes a staging generation and atomically promotes only a complete generation.
- `DirectoryFreshnessService.evaluate(now)` returns `fresh`, `warning`, or `hard_stale` at 8h and 24h while reconciliation runs every 6h and at worker startup.
- Closure rows materialize ancestor/descendant/depth for indexed authorization reads.

```python
def evaluate_directory_freshness(last_complete_at: datetime | None, now: datetime) -> DirectoryFreshness:
    if last_complete_at is None:
        return DirectoryFreshness.HARD_STALE
    age = now - last_complete_at
    if age >= timedelta(hours=24):
        return DirectoryFreshness.HARD_STALE
    if age >= timedelta(hours=8):
        return DirectoryFreshness.WARNING
    return DirectoryFreshness.FRESH
```

Promotion uses one serializable transaction: lock the singleton `directory_state`, verify the staging generation status/counts/checksum, replace closure/membership visibility, mark the prior generation superseded, set the new active generation and `last_complete_at`, then commit.

- [ ] **Step 1: Write failing reconciliation tests** for paginated departments/members, staging isolation, atomic promotion, crash/timeout/429 mid-sync preserving the prior generation, departure in a complete generation, source-count checks, encrypted/HMAC provider IDs, and no partial membership visibility.
- [ ] **Step 2: Write failing closure tests** for root, nested departments, member-to-multiple-departments, subtree moves, deleted departments, cycle rejection, indexed exact/recursive lookup, and closure replacement in the same promotion transaction.
- [ ] **Step 3: Write failing freshness boundary tests** at just before/at/after 8h and 24h, startup without any complete generation, warning metadata, hard-stale member denial signal, and later local departure overriding older generation data.
- [ ] **Step 4: Run `cd backend && .venv/bin/python -m pytest tests/test_directory_reconciliation.py tests/test_directory_freshness.py tests/test_department_closure.py -q` and verify RED**. Expected: imports of `DirectoryReconciler` and `evaluate_directory_freshness` fail.
- [ ] **Step 5: Implement bounded page ingestion and staging generations** with explicit counts/checksums, no long transaction during network fetch, one short serializable promotion transaction, and scheduled startup/6h reconciliation with jitter and single-worker advisory lock.
- [ ] **Step 6: Add performance fixtures** and assert a representative directory completes below the 10-minute target in the sizing harness and the code's configured hard timeout is 15 minutes; record stage timings without provider data.
- [ ] **Step 7: Run focused tests and verify GREEN**, then force one full-sync failure and prove the active generation ID is unchanged.
- [ ] **Step 8: Commit** with `git add backend/app/control_plane/directory.py backend/app/control_plane/directory_worker.py backend/tests/test_directory_reconciliation.py backend/tests/test_directory_freshness.py backend/tests/test_department_closure.py && git commit -m "feat(identity): reconcile DingTalk directory atomically"`.

### Task 9: Persist Stream events before acknowledgement and process them idempotently

**Files:**
- Create: `backend/app/control_plane/stream_consumer.py`
- Create: `backend/app/control_plane/event_worker.py`
- Create: `backend/tests/test_dingtalk_stream_consumer.py`
- Create: `backend/tests/test_directory_event_worker.py`
- Modify: `backend/tests/test_no_keychain_runtime.py`

**Interfaces:**
- `StreamConsumer` uses official `DingTalkStreamClient`, registers only approved organization topics, encrypts the payload into `stream_inbox`, and returns ACK success only after commit.
- `DirectoryEventWorker` claims inbox rows with `FOR UPDATE SKIP LOCKED`, applies idempotently, and retries with bounded exponential backoff/dead-letter status.

Approved R1 events are user add/change/departure/activation and department create/change/delete. Unknown events are safely recorded/ignored by type without executing arbitrary handlers.

```python
class DurableOrganizationEventHandler:
    async def process(self, event: CallbackMessage) -> tuple[str, str]:
        try:
            await asyncio.to_thread(self.inbox.insert_encrypted_once, event)
        except Exception:
            self.logger.exception("dingtalk event persistence failed")
            raise
        return AckMessage.STATUS_OK, "OK"
```

The SDK handler must not catch the persistence exception and return success. `insert_encrypted_once` derives `event_key` from DingTalk's stable event identifier plus topic, so repeated deliveries conflict harmlessly on the primary key.

- [ ] **Step 1: Write failing Stream tests** with an SDK adapter fake proving commit-before-ACK, DB failure causes retryable ACK failure, duplicate delivery creates one inbox row, event payload is encrypted, topic allowlist is exact, reconnection configuration is bounded, and logs contain neither payload nor provider IDs.
- [ ] **Step 2: Write failing worker tests** for duplicate/out-of-order events, stale update ignored, add/change triggering targeted refresh, department change triggering safe reconciliation, poison event quarantine, crash after commit, and retry resumption.
- [ ] **Step 3: Write a failing departure test** requiring local inactive status plus all Session revocations to commit immediately and idempotently, with p95 acceptance instrumentation targeted below 30 seconds; full reconciliation later confirms rather than delays the revoke.
- [ ] **Step 4: Run `cd backend && .venv/bin/python -m pytest tests/test_dingtalk_stream_consumer.py tests/test_directory_event_worker.py tests/test_no_keychain_runtime.py -q` and verify RED**. Expected: imports of `DurableOrganizationEventHandler` and `DirectoryEventWorker` fail.
- [ ] **Step 5: Implement the official SDK adapter** using `Credential`, `DingTalkStreamClient`, `register_callback_handler`, `start()`, and `stop()`; mount AppSecret only from the secret file and never invoke macOS Keychain or interactive login.
- [ ] **Step 6: Implement the worker claim/apply loop** with database time, request/event IDs, encrypted dead-letter payloads, bounded retries, and a health heartbeat separate from directory freshness.
- [ ] **Step 7: Run focused tests and verify GREEN**, then run a duplicate/reorder load fixture and assert one effective state transition per event key.
- [ ] **Step 8: Commit** with `git add backend/app/control_plane/stream_consumer.py backend/app/control_plane/event_worker.py backend/tests/test_dingtalk_stream_consumer.py backend/tests/test_directory_event_worker.py backend/tests/test_no_keychain_runtime.py && git commit -m "feat(identity): make DingTalk Stream ingestion durable"`.

### Task 10: Enforce hard-stale privileged continuity and break-glass rules

**Files:**
- Modify: `backend/app/control_plane/auth.py`
- Modify: `backend/app/control_plane/identity.py`
- Modify: `backend/app/control_plane/admin_cli.py`
- Modify: `backend/app/control_plane/audit.py`
- Create: `backend/tests/test_hard_stale_access.py`
- Modify: `docs/runbooks/platform-identity-break-glass.md`

**Interfaces:**
- At 24h hard stale, members/new identities/Agent use/role mutations fail; previously bound owner/viewer may reauthenticate into `hard_stale_read_only=True` only if the last complete generation recorded them active and no newer local disable/departure exists.
- Offline owner replacement may explicitly select the last complete stale generation; the replacement remains read-only until a fresh reconciliation succeeds.

```python
@dataclass(frozen=True)
class ControlUser:
    internal_user_id: UUID
    role: Role
    status: str
    last_confirmed_active: bool
    locally_invalidated_at: datetime | None

@dataclass(frozen=True)
class DirectoryState:
    active_generation_id: UUID | None
    last_complete_at: datetime | None
    freshness: DirectoryFreshness

@dataclass(frozen=True)
class StaleAccessDecision:
    allowed: bool
    read_only: bool
    reason: Literal[
        "fresh", "warning", "member_hard_stale", "privileged_last_generation",
        "locally_inactive", "unbound_identity",
    ]

def decide_stale_access(user: ControlUser, directory: DirectoryState) -> StaleAccessDecision:
    if user.status != "active" or user.locally_invalidated_at is not None:
        return StaleAccessDecision(False, True, "locally_inactive")
    if directory.freshness is not DirectoryFreshness.HARD_STALE:
        return StaleAccessDecision(True, False, directory.freshness.value)
    if user.role in {Role.PLATFORM_OWNER, Role.MANAGEMENT_VIEWER} and user.last_confirmed_active:
        return StaleAccessDecision(True, True, "privileged_last_generation")
    return StaleAccessDecision(False, True, "member_hard_stale")
```

- [ ] **Step 1: Write failing matrix tests** for fresh/warning/hard-stale across new member, existing member, owner, viewer, locally departed owner/viewer, and disabled account. Prove warning does not block; hard stale blocks members and every mutation while allowing only bound privileged reads with a warning banner flag.
- [ ] **Step 2: Write failing break-glass tests** for explicit stale generation, two approvers, backup reference, stable provider lookup, no name target, old owner Session revocation, new owner read-only, later fresh sync promotion, and denial when local departure is newer than the selected generation.
- [ ] **Step 3: Run `cd backend && .venv/bin/python -m pytest tests/test_hard_stale_access.py tests/test_identity_admin_cli.py -q` and verify RED**. Expected: the hard-stale matrix currently receives normal access instead of the required read-only/deny decisions.
- [ ] **Step 4: Implement one policy function** returning typed decisions/reason codes; reuse it in login, per-request Session validation, authorization, and CLI. Do not copy stale logic across routes.
- [ ] **Step 5: Emit immutable audit events** for every hard-stale privileged login/read/replacement and expose only freshness timestamps/reason codes, never provider identifiers.
- [ ] **Step 6: Run focused tests and verify GREEN**, then run the full control-plane test subset and `git diff --check`.
- [ ] **Step 7: Commit** with `git add backend/app/control_plane/auth.py backend/app/control_plane/identity.py backend/app/control_plane/admin_cli.py backend/app/control_plane/audit.py backend/tests/test_hard_stale_access.py docs/runbooks/platform-identity-break-glass.md && git commit -m "feat(identity): preserve audited hard-stale access"`.

### Task 11: Add sanitized Review and Operations projections to the cloud replica

**Files:**
- Modify: `backend/migrations/008_cloud_replica.sql`
- Modify: `backend/app/cloud_replica/source.py`
- Modify: `backend/app/cloud_replica/exporter.py`
- Modify: `backend/app/cloud_replica/protocol.py`
- Modify: `backend/app/cloud_replica/sanitize.py`
- Modify: `backend/app/cloud_replica/store.py`
- Create: `backend/app/cloud_replica/management_repository.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_cloud_replica_migration.py`
- Modify: `backend/tests/test_cloud_source.py`
- Modify: `backend/tests/test_cloud_exporter.py`
- Modify: `backend/tests/test_cloud_sanitizer.py`
- Modify: `backend/tests/test_cloud_store.py`
- Create: `backend/tests/test_cloud_management_repository.py`
- Modify: `backend/tests/test_cloud_api.py`

**Interfaces:**
- Adds safe record kinds `review_issue_projection`, `review_inbox_projection`, and `operation_event_projection` to the signed/encrypted replica transport.
- Adds replica read services implementing `overview(agent_id: str | None) -> dict`, `inbox(agent_id: str | None, limit: int, offset: int) -> list[dict]`, `list_issues(agent_id: str | None, limit: int, offset: int) -> list[dict]`, `issue_detail(issue_id: UUID) -> dict` for owner compatibility, and `list_events(filters: EventFilters, limit: int, offset: int) -> Page[OperationalEvent]`.
- Does not sync raw feedback text, comments, review mutation history, source traces, provider identity, attachment bytes, Session text beyond already approved replica content, or writable Review state.

This task is mandatory because cloud mode currently has no Review/Operations service behind the R1 viewer routes. Preserve the existing sanitization principle:

```python
ALLOWED_MANAGEMENT_RECORDS = {
    "review_issue_projection",
    "review_inbox_projection",
    "operation_event_projection",
}
FORBIDDEN_KEYS = {
    "raw_feedback", "comment", "provider_user_id", "mobile",
    "email", "attachment_bytes", "source_payload",
}

@dataclass(frozen=True)
class ReviewIssueProjection:
    issue_id: UUID
    agent_id: str
    status: str
    priority: str
    title: str
    failure_layer: str | None
    owner_display: str | None
    linked_turn_count: int
    updated_at: datetime

@dataclass(frozen=True)
class ReviewInboxProjection:
    agent_id: str
    turn_key: str
    feedback_count: int
    first_feedback_at: datetime

@dataclass(frozen=True)
class OperationEventProjection:
    event_id: str
    agent_id: str
    event_type: str
    severity: str
    summary: str
    occurred_at: datetime
```

The replica repository joins `ReviewInboxProjection.turn_key` to the already-approved encrypted replica turn to render sanitized question/answer fields. The new management record does not duplicate raw turn text or feedback bodies.

- [ ] **Step 1: Write failing migration/protocol tests** for tables, schema version, per-Agent indexes, signed manifest counts, unknown record rejection, replay/idempotency, retention, and backward-compatible import of prior envelopes.
- [ ] **Step 2: Write failing source/sanitizer tests** with adversarial secrets, Markdown links, local paths, filenames, phone/email/provider IDs, raw feedback, and trace payloads; require either safe redaction or whole-record rejection before transport.
- [ ] **Step 3: Write failing store/repository/API tests** for per-Agent overview/inbox/issues/events, pagination/filtering, owner issue detail compatibility, unavailable/stale signaling, cross-Agent isolation, and mutation methods remaining unavailable.
- [ ] **Step 4: Run `cd backend && .venv/bin/python -m pytest tests/test_cloud_replica_migration.py tests/test_cloud_source.py tests/test_cloud_exporter.py tests/test_cloud_sanitizer.py tests/test_cloud_store.py tests/test_cloud_management_repository.py tests/test_cloud_api.py -q` and verify RED**. Expected: the new record kinds are rejected as unknown and cloud Review/Operations endpoints return `503`.
- [ ] **Step 5: Extend the source/export protocol minimally** with typed safe projections and manifest counts; use the existing encryption/signature envelope and analyst SELECT role, never add a direct production database connection to the cloud API.
- [ ] **Step 6: Extend replica migration/import transactionally** and implement read-only management repositories. Wire cloud `review_service` and `operations_service` to those repositories while leaving every Review POST/PATCH/replay path disabled.
- [ ] **Step 7: Run focused tests and verify GREEN**, then export/import an adversarial fixture and inspect both the envelope and replica rows for forbidden content.
- [ ] **Step 8: Commit** with `git add backend/migrations/008_cloud_replica.sql backend/app/cloud_replica backend/app/main.py backend/tests/test_cloud_replica_migration.py backend/tests/test_cloud_source.py backend/tests/test_cloud_exporter.py backend/tests/test_cloud_sanitizer.py backend/tests/test_cloud_store.py backend/tests/test_cloud_management_repository.py backend/tests/test_cloud_api.py && git commit -m "feat(cloud): replicate safe management projections"`.

### Task 12: Apply the exact R1 backend authorization gate

**Files:**
- Create: `backend/app/control_plane/authorization.py`
- Modify: `backend/app/control_plane/middleware.py`
- Modify: `backend/app/review/routes.py`
- Modify: `backend/app/operations/routes.py`
- Modify: `backend/app/control_room/routes.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_r1_authorization.py`
- Modify: `backend/tests/test_review_api.py`
- Modify: `backend/tests/test_operations_api.py`
- Modify: `backend/tests/test_control_room_api.py`
- Modify: `backend/tests/test_cloud_mode.py`

**Interfaces:**
- `AuthorizationService.decide(auth, method, route_template, agent_ids)` returns an explicit permit/deny reason.
- The viewer allowlist is compared by FastAPI route template, HTTP method, and exactly one normalized Agent ID, never by raw path prefix or frontend visibility.
- Review actor comes from `AuthContext` for authenticated routes; the browser-supplied `X-Review-Actor` header is not an authority.

```python
VIEWER_R1_ROUTES = frozenset({
    ("GET", "/api/agents/{agent_id}/runtime"),
    ("GET", "/api/review/overview"),
    ("GET", "/api/review/inbox"),
    ("GET", "/api/review/issues"),
    ("GET", "/api/operations/events"),
    ("GET", "/api/v1/manage/audit/governance"),
})

@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    status_code: Literal[200, 401, 403, 503]
    reason: str
    agent_id: str | None

def require_exact_viewer_agent(
    route_template: str,
    path_agent_id: str | None,
    query_agent_ids: tuple[str, ...],
) -> str:
    values = tuple(value for value in (path_agent_id, *query_agent_ids) if value)
    if len(values) != 1:
        raise HTTPException(status_code=403, detail="exactly one Agent scope required")
    return values[0]
```

- [ ] **Step 1: Write a failing exhaustive route-matrix test** by introspecting every FastAPI route and asserting unauthenticated/member/viewer/owner behavior for GET/HEAD/OPTIONS/mutations, including future unknown routes defaulting to deny.
- [ ] **Step 2: Add failing viewer scope tests** for missing, duplicate, conflicting path/query, unknown, unscoped, case-variant, and scoped Agent IDs; require rejection before service invocation and one immutable cross-user/management audit event for permitted privileged reads.
- [ ] **Step 3: Add failing owner tests** proving existing sanitized management reads remain available, Review mutations remain disabled in cloud, identity/role mutations require audit+CSRF+fresh directory, and hard-stale owner mode is read-only.
- [ ] **Step 4: Run `cd backend && .venv/bin/python -m pytest tests/test_r1_authorization.py tests/test_review_api.py tests/test_operations_api.py tests/test_control_room_api.py tests/test_cloud_mode.py -q` and verify RED**. Expected: member/viewer requests currently reach management services instead of receiving `403`.
- [ ] **Step 5: Implement a single backend gate** after route resolution and before endpoint service invocation. Force `agent_id` on the five viewer routes, look up exact observation grants from the control database on every request, and keep aggregate/detail endpoints owner-only.
- [ ] **Step 6: Replace authoritative use of `X-Review-Actor`** with the authenticated internal user/role for cloud identity mode; retain any legacy header behavior only when identity mode is explicitly disabled and tests prove it cannot be reached in preview/production.
- [ ] **Step 7: Run focused tests and verify GREEN**, then run every backend API test and inspect the generated OpenAPI routes against the test's classified-route snapshot.
- [ ] **Step 8: Commit** with `git add backend/app/control_plane/authorization.py backend/app/control_plane/middleware.py backend/app/review/routes.py backend/app/operations/routes.py backend/app/control_room/routes.py backend/app/main.py backend/tests/test_r1_authorization.py backend/tests/test_review_api.py backend/tests/test_operations_api.py backend/tests/test_control_room_api.py backend/tests/test_cloud_mode.py && git commit -m "feat(identity): enforce the Release 1 access matrix"`.

### Task 13: Add the login, account, and role-aware R1 shell

**Files:**
- Create: `webui/src/auth.ts`
- Create: `webui/src/auth.test.ts`
- Create: `webui/src/pages/LoginPage.tsx`
- Create: `webui/src/pages/LoginPage.test.tsx`
- Create: `webui/src/pages/AccountPage.tsx`
- Create: `webui/src/pages/AccountPage.test.tsx`
- Create: `webui/src/pages/IdentityManagementPage.tsx`
- Create: `webui/src/pages/IdentityManagementPage.test.tsx`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/cloudMode.test.tsx`

**Interfaces:**
- Production bootstraps from `GET /api/v1/account`; preview bootstraps from `GET /_preview/dingtalk-r1/api/v1/account`. `401` routes to the matching login path, `403` renders a clear permission state, and `503` renders directory dependency status.
- Login supports browser QR start and DingTalk in-client code exchange without putting codes/tokens in localStorage, sessionStorage, URL logs, or analytics.
- Navigation is server-role-derived: member sees Account only; viewer sees only scoped runtime/Review/Operations/governance links; owner sees existing management pages plus viewer/observation-scope administration.

```typescript
export async function loadAccount(prefix: string): Promise<Account> {
  const response = await fetch(`${prefix}/api/v1/account`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (response.status === 401) throw new AuthenticationRequired();
  if (response.status === 403) throw new PermissionDenied();
  if (response.status === 503) throw new DirectoryUnavailable();
  if (!response.ok) throw new PlatformApiError(response.status);
  return AccountSchema.parse(await response.json());
}
```

`Account` contains only `internal_user_id`, `display_name`, `role`, `observation_agent_ids`, `directory_freshness`, `hard_stale_read_only`, and `csrf_token`; it contains no DingTalk provider identifier or access token.

- [ ] **Step 1: Write failing frontend tests** for unauthenticated redirect, QR start, in-client exchange, callback error redaction, account display, logout with CSRF, no password form, no token storage, and route-prefix-safe links under `/_preview/dingtalk-r1/`. Add owner-only viewer/scope administration tests requiring reason confirmation and rendering `403`, `409`, and audit-unavailable `503` without optimistic success.
- [ ] **Step 2: Write failing role/navigation tests** for member, single/multiple-scope viewer, owner, hard-stale banner, no combined viewer overview, direct URL denial rendering, and no client-side claim that hidden navigation is authorization.
- [ ] **Step 3: Run `cd webui && npm test -- --run src/auth.test.ts src/pages/LoginPage.test.tsx src/pages/AccountPage.test.tsx src/pages/IdentityManagementPage.test.tsx src/cloudMode.test.tsx` and verify RED**. Expected: Vitest reports missing `src/auth.ts` and login/account/identity-management page modules.
- [ ] **Step 4: Implement the smallest shell** using HttpOnly Cookie credentials, an in-memory CSRF token from the account bootstrap, safe Markdown components already used by the Platform, and no raw HTML execution.
- [ ] **Step 5: Add strict preview-compatible CSP expectations** to tests: self-only scripts/styles/connect, no third-party JavaScript, no inline remote content, and all API/asset links prefixed.
- [ ] **Step 6: Run focused tests and verify GREEN**, then run `cd webui && npm test -- --run && npm run build` and inspect the build for one hashed login asset path and no AppSecret/AppKey token values beyond intentionally public nonsecret configuration.
- [ ] **Step 7: Commit** with `git add webui/src/auth.ts webui/src/auth.test.ts webui/src/pages/LoginPage.tsx webui/src/pages/LoginPage.test.tsx webui/src/pages/AccountPage.tsx webui/src/pages/AccountPage.test.tsx webui/src/pages/IdentityManagementPage.tsx webui/src/pages/IdentityManagementPage.test.tsx webui/src/api.ts webui/src/router.ts webui/src/App.tsx webui/src/AppShell.tsx webui/src/styles.css webui/src/cloudMode.test.tsx && git commit -m "feat(web): add DingTalk authenticated R1 shell"`.

### Task 14: Package workers and publish the isolated preview namespace

**Files:**
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/Dockerfile`
- Modify: `deploy/cloud/agent-domain.nginx.conf`
- Create: `deploy/cloud/install-dingtalk-preview.sh`
- Create: `deploy/cloud/remove-dingtalk-preview.sh`
- Create: `deploy/cloud/orbbec-platform-control-maintenance.service`
- Create: `deploy/cloud/orbbec-platform-control-maintenance.timer`
- Modify: `deploy/cloud/deploy.sh`
- Modify: `deploy/cloud/acceptance.sh`
- Create: `backend/tests/test_dingtalk_preview_deployment.py`
- Modify: `backend/tests/test_agent_domain_deployment.py`
- Modify: `backend/tests/test_cloud_deployment.py`
- Modify: `docs/runbooks/cloud-platform.md`

**Interfaces:**
- During preview, Compose adds `platform-api-preview`, `platform-loopback-preview` on `127.0.0.1:8081`, `platform-directory-preview`, and `platform-dingtalk-stream-preview` with preview-only DSNs/Cookies/keys and health checks; no public ports other than the loopback bind. Cutover reuses the same image and commands with production control secrets, then removes all preview services.
- Nginx adds exact `location ^~ /_preview/dingtalk-r1/` to the candidate loopback listener while the formal root remains Basic Auth.
- Stable preview acceptance output: `DINGTALK_R1_PREVIEW_OK release=$RELEASE_SHA control_db=agent_platform_control_preview`, where the script substitutes the verified 40-character release SHA before printing.

Preview Nginx must be explicit, not inherited accidentally:

```nginx
location ^~ /_preview/dingtalk-r1/ {
    auth_basic off;
    client_max_body_size 1m;
    add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;
    proxy_read_timeout 360s;
    proxy_send_timeout 360s;
    proxy_set_header Authorization "";
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Forwarded "";
    proxy_pass http://127.0.0.1:8081;
}
```

- [ ] **Step 1: Write failing static deployment tests** requiring separate production/preview DSNs, Cookies, keyrings, environment state, preview API/loopback/directory/Stream services, health checks, restart policies, read-only mounts where possible, no secret in environment/argv, only `127.0.0.1:8080` and `127.0.0.1:8081` host binds, a daily randomized maintenance timer using only the maintenance DSN, and no FAE modification/restart commands.
- [ ] **Step 2: Write failing Nginx tests** proving preview has `auth_basic off`, no inherited `limit_except`, 1MB body limit, 360s timeout, stripped Authorization, overwritten forwarding headers, strict preview CSP, root still Basic Auth, HTTP/ACME behavior unchanged, and ports 8000/8080/PostgreSQL/MinIO remain private.
- [ ] **Step 3: Write failing installer/removal tests** for backup-before-change, `nginx -t`, atomic enable, time-box marker, preview control DB only, rollback on failure, exact cleanup, and preserved production/FAE container IDs/start times/restart counts.
- [ ] **Step 4: Run `cd backend && .venv/bin/python -m pytest tests/test_dingtalk_preview_deployment.py tests/test_agent_domain_deployment.py tests/test_cloud_deployment.py -q` and verify RED**. Expected: preview location/services/installers are missing and current Nginx still appends `$proxy_add_x_forwarded_for`.
- [ ] **Step 5: Add preview API, loopback, directory-worker, and Stream commands plus secret mounts to Compose**, build one immutable image, use the preview control database and separate service DB roles, and ensure only the directory worker can reconcile/process events while Stream can only insert inbox rows. Define the cutover mapping to the same production services without running duplicate production/preview consumers.
- [ ] **Step 6: Implement preview Nginx/install/remove assets** and document the accepted same-origin residual risk: test-member scope, clean browser profile without cached Basic Auth, sanitized Markdown/no raw HTML, strict CSP, short test window, and mandatory removal.
- [ ] **Step 7: Run focused tests and verify GREEN**, then run `docker compose -f deploy/cloud/compose.yaml config`, `bash -n deploy/cloud/*.sh`, `nginx -t` against a temporary assembled config, and `git diff --check`.
- [ ] **Step 8: Commit** with `git add deploy/cloud/compose.yaml deploy/cloud/Dockerfile deploy/cloud/agent-domain.nginx.conf deploy/cloud/install-dingtalk-preview.sh deploy/cloud/remove-dingtalk-preview.sh deploy/cloud/orbbec-platform-control-maintenance.service deploy/cloud/orbbec-platform-control-maintenance.timer deploy/cloud/deploy.sh deploy/cloud/acceptance.sh backend/tests/test_dingtalk_preview_deployment.py backend/tests/test_agent_domain_deployment.py backend/tests/test_cloud_deployment.py docs/runbooks/cloud-platform.md && git commit -m "feat(cloud): publish isolated DingTalk preview"`.

### Task 15: Add control-only PITR rehearsal and WAL-pressure protection

**Files:**
- Create: `deploy/cloud/control-backup.sh`
- Create: `deploy/cloud/control-pitr-drill.sh`
- Create: `deploy/cloud/wal-archive.sh`
- Create: `deploy/cloud/wal-restore.sh`
- Create: `deploy/cloud/wal-guard.sh`
- Modify: `deploy/cloud/backup.sh`
- Modify: `deploy/cloud/restore-drill.sh`
- Modify: `deploy/cloud/forced-import.sh`
- Modify: `deploy/cloud/compose.yaml`
- Create: `backend/tests/test_control_plane_backup.py`
- Create: `backend/tests/test_wal_guard.py`
- Modify: `docs/runbooks/cloud-platform.md`

**Interfaces:**
- Control RPO target: 15 minutes; backups/WAL are encrypted and cover the physical cluster.
- Control-only recovery sequence: isolated-cluster physical PITR -> logical dump of `agent_platform_control` -> temporary production-cluster restore/validation -> audited database swap; live replica remains at current time.
- WAL guard observes archive failures, oldest unarchived WAL age, archive throughput, `pg_wal` usage, and free disk; it can throttle/pause replica import before control integrity is threatened.

Thresholds are testable policy:

```text
unarchived age >= 5m or free <= 25%  -> warn + reduce import batch/rate
unarchived age >= 10m or free <= 20% -> pause replica import + page owner
unarchived age > 15m                  -> mark RPO breached
free < 10%                            -> block nonessential write work
free < 5%                             -> API dependency 503, preserve control writes needed for revoke/audit
```

`wal-guard.sh` emits one atomically replaced root-owned JSON state file with this fixed shape:

```json
{
  "status": "healthy",
  "archive_age_seconds": 0,
  "archive_failed_count": 0,
  "free_percent": 100,
  "replica_import_action": "run",
  "nonessential_write_action": "run",
  "rpo_breached": false,
  "observed_at": "2026-08-13T00:00:00Z"
}
```

Allowed status/action values are `healthy|warning|paused|rpo_breached|protective_503`, `run|reduce|pause`, and `run|block|protective_503`; stale or malformed state is treated as `paused`/RPO unknown, never healthy.

- [ ] **Step 1: Write failing backup policy tests** for daily encrypted physical base backups, continuous encrypted WAL archiving through exact `archive_command`/`restore_command` helpers, 35-day retention, separated recovery keys, restore inputs, checksum verification, no secrets in output, and preview/production control DB coverage.
- [ ] **Step 2: Write a failing isolated PITR drill test** that uses disposable clusters, advances replica and control independently, restores to a timestamp in an isolated cluster, exports only control, imports to a temporary DB, validates migration/audit/session invariants, swaps control only, and proves the replica retains its later row.
- [ ] **Step 3: Write failing WAL-guard tests** for every boundary, archive-command failure, stale metrics, replica rebuild surge, resume hysteresis, importer cooperation, hard disk shedding, and health/RPO status that never reports healthy while breached.
- [ ] **Step 4: Run `cd backend && .venv/bin/python -m pytest tests/test_control_plane_backup.py tests/test_wal_guard.py tests/test_cloud_backup.py -q` and verify RED**. Expected: native PITR/archive helpers and WAL state contract are absent.
- [ ] **Step 5: Implement backup/archive/restore/drill scripts** with explicit paths, locks, temporary directories, encrypted WAL spool handling, validation queries, pre-swap backup, deterministic rollback, and no in-place cluster PITR for control-only recovery. Mount only the encryption public key into the live archiver; keep the recovery private key offline from runtime containers.
- [ ] **Step 6: Implement the WAL guard and importer contract** using a root-owned state file/API signal; pause `sync_remote` safely at transaction boundaries, protect revoke/audit writes, and expose sanitized status to owner health only.
- [ ] **Step 7: Run focused tests and verify GREEN**, perform a disposable end-to-end drill, run `bash -n deploy/cloud/*.sh`, and record measured RPO/restore duration in the runbook without production secrets.
- [ ] **Step 8: Commit** with `git add deploy/cloud/control-backup.sh deploy/cloud/control-pitr-drill.sh deploy/cloud/wal-archive.sh deploy/cloud/wal-restore.sh deploy/cloud/wal-guard.sh deploy/cloud/backup.sh deploy/cloud/restore-drill.sh deploy/cloud/forced-import.sh deploy/cloud/compose.yaml backend/tests/test_control_plane_backup.py backend/tests/test_wal_guard.py docs/runbooks/cloud-platform.md && git commit -m "feat(cloud): protect control recovery and WAL"`.

### Task 16: Complete verification, real DingTalk preview, production cutover, and rollback

**Files:**
- Verify: all files changed in Tasks 1-15
- Create: `docs/runbooks/dingtalk-r1-acceptance.md`
- Modify: `docs/runbooks/cloud-platform.md`
- Deploy only after approval: cloud host `47.106.112.69`

**Interfaces:**
- Consumes the reviewed feature branch, real Orbbec DingTalk test-member scope, internal/public DNS, company HTTPS proxy, and protected production secret files.
- Produces acceptance evidence, a tested rollback, and eventually root `https://agent.orbbec.com.cn/` with DingTalk login while preserving the old Basic Auth config for immediate rollback.

- [ ] **Step 1: Run the complete local gate** with `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q`, `cd webui && npm test -- --run && npm run build`, `docker compose -f deploy/cloud/compose.yaml config`, `bash -n deploy/cloud/*.sh`, `git diff --check`, and secret/provider-ID scans. Record exact pass counts.
- [ ] **Step 2: Perform a dedicated security review** of cryptography, OAuth state, Session rotation, CSRF/Origin, public-route classification, trusted proxy, rate limits, hard-stale behavior, role/grant mutations, audit failure behavior, replica sanitization, Nginx inheritance, secret mounts, and rollback. Fix all critical/high findings and re-run affected tests.
- [ ] **Step 3: Deploy only the preview candidate** under `/_preview/dingtalk-r1/`; keep root Basic Auth, existing Platform listener, FAE domain, legacy FAE IP behavior, and all existing Agent containers unchanged.
- [ ] **Step 4: Run the real DingTalk acceptance matrix** from design section 24: in-client and QR login; wrong org/inactive member rejection; state replay/expiry; departure revoke; full sync failure preserving generation; 8h warning/24h hard stale; owner/viewer/member matrix; exact one-Agent viewer scope; governance audit; internal/public DNS and proxy; measured login burst above twice forecast; Stream reconnect; and no public infrastructure ports.
- [ ] **Step 5: Run the initial production control-only PITR drill and WAL-pressure test** before cutover. Save sanitized timestamps, backup IDs, restore validation, replica non-rollback evidence, and threshold actions.
- [ ] **Step 6: Rehearse rollback while preview is active**: restore the previous Nginx/Compose state, prove root Basic Auth and FAE still work, then reinstall the same candidate and repeat a minimal acceptance set.
- [ ] **Step 7: Obtain explicit cutover approval**, back up Nginx/control DB/secrets metadata, change the DingTalk homepage/callback to the formal root, replace root Basic Auth with the backend-authenticated route, preserve a root-only rollback script, and use 360-second proxy timeouts with overwritten trusted headers.
- [ ] **Step 8: Run fresh post-cutover acceptance** from public internet and company network: root -> login, QR/in-client, member 403, viewer exact scope, owner dashboard, audit, workers, directory freshness, replica freshness, TLS/Certbot, loopback listeners, FAE/legacy IP invariants, containers, backups, WAL status, and removal of the preview namespace/test Cookie.
- [ ] **Step 9: Commit the final acceptance/runbook evidence** with `git add docs/runbooks/dingtalk-r1-acceptance.md docs/runbooks/cloud-platform.md && git commit -m "docs(cloud): record DingTalk R1 acceptance"`; push/merge only after every gate passes. Report release SHA, verification counts, public URL, rollback path, sync freshness, RPO state, and known phase-one governance limitation without exposing credentials or provider IDs.

## Release 1 Done Definition

- Every route is classified by backend tests; future unclassified routes fail closed.
- An ordinary Orbbec member can authenticate and view only account/status/logout; every data or Agent route returns `403`.
- A scoped viewer can call only the exact six R1 GET routes, cannot aggregate across Agents, cannot mutate, and every privileged read is audited.
- The owner can read the existing sanitized management Platform and perform only R1 management actions; Review mutations remain off.
- Directory generations are atomic, Stream is durable/idempotent, departure revokes Sessions promptly, and hard stale preserves only audited privileged read continuity.
- No raw DingTalk identity, AppSecret, code, token, Session token, CSRF token, or unsafe management content appears in Git, client storage, replica, logs, audit metadata, or acceptance output.
- Preview and production control data are isolated; root publication has a tested rollback; FAE and all private listeners are unchanged.
- Control-only PITR and WAL-pressure protection are demonstrated, not merely documented.
- Unified chat, Session ownership migration, attachments, erasure, Agent grants, and additional Agent adapters remain explicitly deferred to later releases.
