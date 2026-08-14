# Platform Admin Role Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an audited `platform_admin` role that can operate Agent Platform and manage viewers without weakening the unique `platform_owner` boundary.

**Architecture:** Extend the PostgreSQL role enum in one committed migration, then add security-definer administrator mutations in the following migration. Treat administrators like the owner in the central route authorization service, while retaining owner-only administrator assignment endpoints and the existing cloud read-only gate. Extend the strict frontend account contract and identity-management UI only after the backend matrix is enforced.

**Tech Stack:** PostgreSQL 16 migrations, Python 3.11, FastAPI, psycopg 3, Pytest, React 19, TypeScript, Vite, Vitest, Docker Compose, Nginx.

## Global Constraints

- `苍渊` remains the sole active `platform_owner`; do not remove or weaken `one_platform_owner`.
- `platform_admin` may operate the platform and manage viewers/scopes, but may not assign, revoke, replace, demote, or mutate the owner or another administrator.
- Only a verified DingTalk login may create the stable `internal_user_id`; never promote by display name, mobile, email, or browser-supplied provider ID.
- Every administrator role mutation requires owner authentication, same-origin CSRF, fresh directory state, exact reason code, optimistic row version, request-ID serialization, and append-only audit.
- Revoking `platform_admin` must revoke all active target Platform Sessions atomically.
- Existing cloud-mode Review write restrictions apply equally to owner and administrator.
- Do not change DingTalk credentials, Nginx routing, public listeners, FAE, ADMIN, MetaBot Agents, replica data, or attachment storage.
- Unknown roles and unknown routes remain fail-closed.

---

### Task 1: Per-Migration Transactions and the Role Enum

**Files:**
- Create: `backend/control_migrations/024_platform_admin_enum.sql`
- Modify: `backend/app/control_plane/migrate.py`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Consumes: `migrate_control_database(database_url, migration_dir, owner_role=...)` and the existing session advisory-lock key.
- Produces: a committed `platform_admin` enum value before migration 025 is parsed, while retaining checksum enforcement and migration serialization.

- [ ] **Step 1: Add failing migration tests**

Add assertions that the complete enum is ordered as follows and that multiple administrators do not affect the unique-owner constraint:

```python
assert [row[0] for row in cursor.fetchall()] == [
    "member",
    "management_viewer",
    "platform_admin",
    "platform_owner",
]

cursor.execute(
    "insert into platform_control.internal_users "
    "(internal_user_id,role,display_name,status) values "
    "(%s,'platform_admin','Admin One','active'),"
    "(%s,'platform_admin','Admin Two','active')",
    (uuid.uuid4(), uuid.uuid4()),
)
```

Add an integration test that creates a randomly named disposable database in
the existing test PostgreSQL cluster, grants the production migrator role, and
applies two temporary numbered migrations. Migration 900 adds an enum value and
migration 901 immediately uses it in a table insert. Call
`migrate_control_database` once and assert migration 901 succeeds. The test must
also assert `schema_migrations` contains both versions and a modified applied
file still raises `MigrationChecksumMismatch`; drop the disposable database in
`finally` after terminating only connections to that exact database.

- [ ] **Step 2: Run the focused migration tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_control_plane_migration.py -q
```

Expected: the enum lacks `platform_admin`, and the consecutive enum migrations fail with PostgreSQL's unsafe-new-enum-value error because all files currently share one transaction.

- [ ] **Step 3: Commit each numbered migration independently under one session lock**

Change `migrate_control_database` to acquire `pg_advisory_lock` for the connection, create the migration ledger in its own transaction, and then execute each numbered migration in its own transaction. `SET LOCAL ROLE` must be repeated after every commit because it is transaction-scoped. On any exception, roll back the current migration; in `finally`, call `pg_advisory_unlock` and commit that unlock transaction.

The loop boundary must be:

```python
for migration in load_numbered_migrations(migration_dir):
    cursor.execute(
        psycopg.sql.SQL("set local role {}").format(
            psycopg.sql.Identifier(owner_role)
        )
    )
    verify_or_apply(
        cursor,
        migration.version,
        migration.sha256,
        migration.sql,
    )
    connection.commit()
```

Use a session advisory lock, not `pg_advisory_xact_lock`, so serialization survives those commits.

- [ ] **Step 4: Add the enum migration**

Create migration 024 with only the enum change so its transaction commits before any SQL uses the new value:

```sql
alter type platform_control.user_role
  add value if not exists 'platform_admin' before 'platform_owner';
```

- [ ] **Step 5: Run migration and complete backend tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_control_plane_migration.py -q
.venv/bin/python -m pytest -q
```

Expected: all tests pass, both production/preview fixtures contain the four roles, and repeated migration execution remains idempotent.

- [ ] **Step 6: Commit the migration prerequisite**

```bash
git add backend/app/control_plane/migrate.py \
  backend/control_migrations/024_platform_admin_enum.sql \
  backend/tests/test_control_plane_migration.py
git commit -m "feat(identity): add platform admin role value"
```

---

### Task 2: Audited Administrator Mutation Boundary

**Files:**
- Create: `backend/control_migrations/025_platform_admin_mutations.sql`
- Modify: `backend/app/control_plane/audit.py`
- Modify: `backend/tests/test_control_plane_audit.py`
- Modify: `backend/tests/test_governance_audit_api.py`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Consumes: committed `platform_admin`, `management_mutations`, `AuditWriter`, and `SensitiveMutationCoordinator`.
- Produces: `assign_platform_admin(uuid,uuid,uuid,bigint,uuid) -> jsonb`, `revoke_platform_admin(uuid,uuid,uuid,bigint,uuid) -> jsonb`, and validated administrator audit events.

- [ ] **Step 1: Add failing audit and database mutation tests**

Register expected event stems and verify their exact reasons and role transitions:

```python
requested = AuditCommand(
    event_type="admin_role_assignment_requested",
    actor_internal_user_id=owner_id,
    target_type="internal_user",
    target_id=str(target_id),
    request_id=operation_id,
    reason="admin_access_approved",
    metadata={
        "operation_id": str(operation_id),
        "previous_role": "member",
        "new_role": "platform_admin",
        "expected_row_version": 0,
        "result": "requested",
    },
)
```

Add PostgreSQL tests proving:

- active owner assignment changes `member -> platform_admin`;
- an administrator actor cannot call either administrator mutation;
- owner and administrator cannot be selected as assignment targets;
- revocation changes `platform_admin -> member` and records
  `admin_role_revoked` on every active target Session;
- the same request ID replays its canonical result, while a changed target or
  expected version raises an operation collision;
- missing/mismatched requested audit prevents the role update.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_control_plane_audit.py \
  tests/test_governance_audit_api.py -q
```

Expected: `platform_admin` is rejected by Python audit validation and the two SQL functions do not exist.

- [ ] **Step 3: Extend the Python audit schema**

Add `platform_admin` to `_ROLES`, register the two event families using the existing role-change request/completion shapes, and use exact reasons:

```python
_register_events(
    ("admin_role_assignment",),
    reason="admin_access_approved",
    target="internal_user",
    requested=_VIEWER_REQUEST,
    completed=_VIEWER_COMPLETED,
)
_register_events(
    ("admin_role_revocation",),
    reason="admin_access_revoked",
    target="internal_user",
    requested=_VIEWER_REQUEST,
    completed=_VIEWER_COMPLETED,
)
```

Extend governance legacy projection rules so assignment accepts only
`member -> platform_admin` and revocation accepts only
`platform_admin -> member`. Keep malformed or unknown metadata redacted.

- [ ] **Step 4: Add migration 025 security-definer functions**

Migration 025 must:

1. replace `management_mutations_action_check` with the existing values plus
   `assign_admin` and `revoke_admin`;
2. replace `require_management_actor(uuid)` so an active, non-invalidated owner
   or administrator may manage viewers/scopes;
3. add `require_platform_owner(uuid)` for administrator mutations;
4. extend SQL audit validation with the four administrator requested/completed
   event types, two exact reason codes, and `platform_admin` as a valid role;
5. implement `assign_platform_admin` with owner-actor, active current-generation
   target, `member` prior-role, audit payload, and row-version checks;
6. implement `revoke_platform_admin` with the same owner/audit/version checks,
   `platform_admin` prior role, atomic Session revocation, and canonical result;
7. revoke all function privileges from `public` and every unrelated control
   role, then grant execution only to the environment's app role.

Update the migration-version assertion from versions 1–24 to versions 1–25.

The assignment result must have this exact shape:

```json
{
  "operation_id": "<request UUID>",
  "previous_role": "member",
  "new_role": "platform_admin",
  "row_version": 1,
  "session_revocation_count": 0,
  "previous_scopes": [],
  "new_scopes": []
}
```

The revocation result uses `platform_admin -> member` and the actual Session
revocation count. Neither mutation may alter the owner row or active
observation grants belonging to unrelated users.

- [ ] **Step 5: Run focused and full backend tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_control_plane_migration.py \
  tests/test_control_plane_audit.py \
  tests/test_governance_audit_api.py -q
.venv/bin/python -m pytest -q
```

Expected: all tests pass and the database rejects every unaudited or non-owner administrator mutation.

- [ ] **Step 6: Commit the audited mutation boundary**

```bash
git add backend/control_migrations/025_platform_admin_mutations.sql \
  backend/app/control_plane/audit.py \
  backend/tests/test_control_plane_audit.py \
  backend/tests/test_governance_audit_api.py \
  backend/tests/test_control_plane_migration.py
git commit -m "feat(identity): add audited platform admin mutations"
```

---

### Task 3: Runtime Role, Hard-Stale, and Route Authorization

**Files:**
- Modify: `backend/app/control_plane/models.py`
- Modify: `backend/app/control_plane/identity.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/control_migrations/025_platform_admin_mutations.sql`
- Modify: `backend/tests/test_hard_stale_access.py`
- Modify: `backend/tests/test_r1_authorization.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`

**Interfaces:**
- Consumes: the database role and existing `_OWNER_ROUTES` allowlist.
- Produces: `Role.PLATFORM_ADMIN`, owner-equivalent operational routing, and audited hard-stale read-only continuity.

- [ ] **Step 1: Add failing runtime matrix tests**

Create these contexts and require exact behavior:

```python
ADMIN = AuthContext(uuid4(), Role.PLATFORM_ADMIN, uuid4(), False)
STALE_ADMIN = AuthContext(uuid4(), Role.PLATFORM_ADMIN, uuid4(), True)

assert service.decide(
    ADMIN, "GET", "/api/sessions/{session_key}", ()
).allowed is True
assert service.decide(
    ADMIN, "POST", "/api/review/issues", ()
).status_code == 403  # cloud-mode read-only remains authoritative
assert service.decide(
    STALE_ADMIN, "POST", "/api/v1/manage/viewers/{internal_user_id}", ()
).status_code == 503
```

Extend hard-stale parameterization so `platform_admin` is allowed only when
active, bound to the last complete generation, and locally valid. Verify the
account API serializes `role: "platform_admin"` and unknown stored roles fail
closed.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_hard_stale_access.py \
  tests/test_r1_authorization.py \
  tests/test_dingtalk_auth_api.py -q
```

Expected: `Role.PLATFORM_ADMIN` is undefined and administrator decisions are denied.

- [ ] **Step 3: Implement the role and authorization matrix**

Add:

```python
class Role(StrEnum):
    MEMBER = "member"
    MANAGEMENT_VIEWER = "management_viewer"
    PLATFORM_ADMIN = "platform_admin"
    PLATFORM_OWNER = "platform_owner"
```

In `AuthorizationService.decide`, retain unknown-route, hard-stale, and cloud
checks before allowing either privileged operational role:

```python
if auth.role in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN}:
    return AuthorizationDecision(True, 200, auth.role.value, None)
```

Update `decide_stale_access` and migration 025's replacements for
`consume_attempt_and_issue_session_v22`, `authenticate_web_session_v22`, and
`append_hard_stale_access_v22` so the privileged continuity set is exactly
`platform_owner`, `platform_admin`, and `management_viewer`. Do not grant hard-
stale continuity to `member`.

- [ ] **Step 4: Run focused and complete backend verification**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_hard_stale_access.py \
  tests/test_r1_authorization.py \
  tests/test_dingtalk_auth_api.py -q
.venv/bin/python -m pytest -q
```

Expected: the four-role matrix passes without changing member or viewer access.

- [ ] **Step 5: Commit runtime authorization**

```bash
git add backend/app/control_plane/models.py \
  backend/app/control_plane/identity.py \
  backend/app/control_plane/authorization.py \
  backend/control_migrations/025_platform_admin_mutations.sql \
  backend/tests/test_hard_stale_access.py \
  backend/tests/test_r1_authorization.py \
  backend/tests/test_dingtalk_auth_api.py
git commit -m "feat(identity): authorize platform administrators"
```

---

### Task 4: Owner-Only Administrator API and Delegated Viewer Management

**Files:**
- Modify: `backend/app/control_plane/routes_manage.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/tests/test_governance_audit_api.py`
- Modify: `backend/tests/test_r1_authorization.py`

**Interfaces:**
- Consumes: SQL administrator functions and `Role.PLATFORM_ADMIN`.
- Produces: owner-only `POST|DELETE /api/v1/manage/admins/{internal_user_id}` and owner/admin viewer management.

- [ ] **Step 1: Add failing API tests**

Extend the fake repository with `assign_admin` and `revoke_admin`, then prove:

```python
owner_client.post(
    f"/api/v1/manage/admins/{target}",
    json={"reason": "admin_access_approved", "request_id": str(request_id)},
).status_code == 200

admin_client.post(
    f"/api/v1/manage/admins/{target}",
    json={"reason": "admin_access_approved"},
).status_code == 403

admin_client.post(
    f"/api/v1/manage/viewers/{viewer}",
    json={"reason": "access_approved"},
).status_code == 200
```

Also prove member/viewer denial, owner-target denial, administrator-target
denial, CSRF failure, stale-directory failure, initial-audit failure with no
mutation, replay success, and revocation Session invalidation.

- [ ] **Step 2: Run API tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_governance_audit_api.py \
  tests/test_r1_authorization.py -q
```

Expected: administrator endpoints return 404/403 and administrator viewer management is denied.

- [ ] **Step 3: Implement repository and service methods**

Add `assign_admin`/`revoke_admin` repository calls to the exact SQL functions.
Add `ManagementService.change_admin(context, target, reason, revoke=False,
request_id=None)` using:

- `member -> platform_admin` and `admin_access_approved` for assignment;
- `platform_admin -> member` and `admin_access_revoked` for revocation;
- existing mutation-precondition replay and `SensitiveMutationCoordinator`;
- requested event names `admin_role_assignment_requested` and
  `admin_role_revocation_requested`.

Split route guards into:

```python
def _manager(context: AuthContext) -> None:
    if context.role not in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN}:
        raise HTTPException(403, "platform manager required")

def _owner(context: AuthContext) -> None:
    if context.role is not Role.PLATFORM_OWNER:
        raise HTTPException(403, "platform owner required")
```

Use `_manager` for user listing, viewer changes, and observation scopes. Use
`_owner` plus the existing CSRF/fresh-directory mutation guards for the two
administrator routes. Add `Role.PLATFORM_ADMIN` to `_governance_reader`, and add
`admin_role_%` to `ManagementRepository.governance_audit()` so the sanitized
governance page includes administrator role changes.

- [ ] **Step 4: Add routes to the central allowlist**

Add both method/template pairs to `_OWNER_ROUTES`; the route function remains
the second owner-only enforcement layer:

```python
("POST", "/api/v1/manage/admins/{internal_user_id}"),
("DELETE", "/api/v1/manage/admins/{internal_user_id}"),
```

- [ ] **Step 5: Run focused and full backend verification**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_governance_audit_api.py \
  tests/test_r1_authorization.py -q
.venv/bin/python -m pytest -q
```

Expected: owner-only administrator mutation and delegated viewer management both pass, with no unauthorized repository invocation.

- [ ] **Step 6: Commit the management API**

```bash
git add backend/app/control_plane/routes_manage.py \
  backend/app/control_plane/authorization.py \
  backend/tests/test_governance_audit_api.py \
  backend/tests/test_r1_authorization.py
git commit -m "feat(identity): expose owner-controlled admin management"
```

---

### Task 5: Frontend Role Contract and Identity Controls

**Files:**
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/auth.test.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/pages/AccountPage.tsx`
- Modify: `webui/src/pages/AccountPage.test.tsx`
- Modify: `webui/src/pages/IdentityManagementPage.tsx`
- Modify: `webui/src/pages/IdentityManagementPage.test.tsx`
- Modify: `webui/src/cloudMode.test.tsx`

**Interfaces:**
- Consumes: account/user payload roles and the administrator API.
- Produces: strict `platform_admin` parsing, full manager navigation, owner-only administrator controls, and delegated viewer controls.

- [ ] **Step 1: Add failing frontend tests**

Add tests proving:

- account parsing accepts `platform_admin` and still rejects an unknown role;
- account page renders `平台管理员`;
- administrator receives full owner-style navigation and can open `/identity`;
- administrator never receives an admin-role button;
- administrator can use viewer/scope buttons;
- owner sees `设为平台管理员` for a member and `撤销平台管理员` for an
  administrator, but no role button for the owner;
- the admin API uses the exact method, URL, CSRF header, and fixed reason.

The client boundary is:

```typescript
export async function changeAdministrator(
  account: Account,
  user: ManagedUser,
  revoke: boolean,
): Promise<void>
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd webui
npm test -- --run \
  src/auth.test.ts \
  src/pages/AccountPage.test.tsx \
  src/pages/IdentityManagementPage.test.tsx \
  src/cloudMode.test.tsx
```

Expected: strict parsers reject `platform_admin`, labels are missing, and admin navigation/controls fail.

- [ ] **Step 3: Implement the strict contract and navigation**

Change the role union and validators to:

```typescript
export type PlatformRole =
  | "member"
  | "management_viewer"
  | "platform_admin"
  | "platform_owner";
```

Treat `platform_admin` like owner for `App` route admission, navigation,
deployment status loading, and the identity page. Keep member and viewer logic
unchanged. Add `platform_admin: "平台管理员"` to the account label map.

- [ ] **Step 4: Implement identity-management controls**

The owner-only administrator request uses a fixed audited reason, not the
free-form note as a backend reason code:

```typescript
body: JSON.stringify({
  reason: revoke ? "admin_access_revoked" : "admin_access_approved",
}),
```

Render administrator controls only when `account.role === "platform_owner"`.
Render viewer and scope controls for owner or administrator, excluding owner and
administrator target rows. Keep the existing success/failure refresh behavior.

- [ ] **Step 5: Run focused and complete frontend verification**

Run:

```bash
cd webui
npm test -- --run \
  src/auth.test.ts \
  src/pages/AccountPage.test.tsx \
  src/pages/IdentityManagementPage.test.tsx \
  src/cloudMode.test.tsx
npm test
npm run build
npm audit --omit=dev
```

Expected: every Vitest test passes, the production build succeeds, and production dependencies report zero vulnerabilities.

- [ ] **Step 6: Commit the frontend role experience**

```bash
git add webui/src/auth.ts webui/src/auth.test.ts \
  webui/src/App.tsx webui/src/AppShell.tsx \
  webui/src/pages/AccountPage.tsx webui/src/pages/AccountPage.test.tsx \
  webui/src/pages/IdentityManagementPage.tsx \
  webui/src/pages/IdentityManagementPage.test.tsx \
  webui/src/cloudMode.test.tsx
git commit -m "feat(identity): add platform admin experience"
```

---

### Task 6: Runbook, Release, and Real Role Assignment

**Files:**
- Create: `docs/runbooks/platform-admin.md`
- Verify: `deploy/cloud/acceptance.sh`
- Verify: `deploy/cloud/accept-dingtalk-production.sh`

**Interfaces:**
- Consumes: reviewed clean commit reachable as `origin/master`, mode-0600 deploy configuration, and a verified `西门吹雪` internal account.
- Produces: immutable production release and audited owner-driven administrator assignment.

- [ ] **Step 1: Write the operator runbook**

Document these exact procedures without provider IDs or secrets:

1. target logs in once through DingTalk;
2. owner opens `/identity`, verifies the human-readable name and active status,
   and assigns `平台管理员`;
3. target signs in again if the role-changing operation revoked a current
   Session;
4. acceptance checks role and authorized route behavior;
5. revocation returns the target to `member` and invalidates Sessions;
6. before rolling back to a binary that lacks `platform_admin`, the owner first
   revokes every administrator through the current release.

- [ ] **Step 2: Run fresh release gates**

Run:

```bash
git diff --check
cd webui && npm test && npm run build && npm audit --omit=dev
cd ../backend && .venv/bin/python -m pytest -q
cd .. && deploy/cloud/acceptance.sh local
```

Expected: all commands exit 0 and the worktree is clean after committing the runbook.

- [ ] **Step 3: Commit the runbook**

```bash
git add docs/runbooks/platform-admin.md
git commit -m "docs(identity): add platform admin operations runbook"
```

- [ ] **Step 4: Push one reviewed immutable release**

Run:

```bash
git push --atomic origin \
  HEAD:refs/heads/feat/agent-public-entry \
  HEAD:refs/heads/master
git fetch origin master feat/agent-public-entry
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)"
```

Expected: both remote refs equal local `HEAD` without force push.

- [ ] **Step 5: Deploy and run production acceptance**

Run:

```bash
deploy/cloud/deploy.sh \
  "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"

ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 \
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  root@47.106.112.69 \
  '/opt/orbbec-agent-platform/current/deploy/cloud/accept-dingtalk-production.sh'
```

Expected: deployment and DingTalk production acceptance both report OK; all
five Platform services are healthy, 8080 remains loopback-only, and FAE identity
and start time are unchanged.

- [ ] **Step 6: Complete the real owner-driven assignment**

First query only sanitized state. If `西门吹雪` has no `internal_user_id`, ask
him to open Agent Platform once in DingTalk and repeat the query. Do not create
or select an account by display name alone.

With the owner's authenticated browser Session, open `/identity`, choose the
single verified active managed-user row for `西门吹雪`, and select
`设为平台管理员`. Verify the UI refresh shows `平台管理员`.

- [ ] **Step 7: Verify the role without exposing identity secrets**

Run an aggregate/sanitized production check that returns only display name,
role, local status, directory status, and Session revocation count. Then have
`西门吹雪` sign in and verify one owner-equivalent read route plus one viewer-
management operation. Confirm direct administrator-assignment requests from the
administrator return `403`.

- [ ] **Step 8: Record the outcome**

Report the release commit, backend/frontend test counts, migration versions 024
and 025, production acceptance result, sanitized role result, and any remaining
human login action. Do not report DingTalk provider identifiers, codes, Cookies,
CSRF values, or encryption material.
