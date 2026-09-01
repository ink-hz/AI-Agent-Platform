# Independent FAE Workbench Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move FAE Operations to the independent `/fae/` workspace and let the Platform Owner grant or revoke complete FAE Workbench access by unique DingTalk display name, including before the target's first login, without granting general Platform administration.

**Architecture:** Keep one Platform origin, one DingTalk Session, and the existing FAE data/review/report services. Add one audited control-plane grant bound to `internal_user_id`, project that grant as a bounded account workspace scope, protect both canonical and compatibility FAE APIs with one dedicated authorization dependency, and render the existing FAE pages in an independent shell. Reuse the verified corporate/union identity pair already stored in the current complete directory generation for pre-login provisioning.

**Tech Stack:** PostgreSQL control migrations and SECURITY DEFINER functions, FastAPI/Pydantic/psycopg, React/TypeScript/Vitest, pytest, Bash deployment acceptance.

## Global Constraints

- Preserve `/office/*`, `https://fae.orbbec.com.cn/`, the FAE customer application, and every non-FAE Nginx location byte-for-byte.
- Never authorize a request by comparing a display-name string. Display name is mutation input only; persisted and request-time authority is the internal UUID.
- Never grant `platform_admin`, `management_viewer`, or Owner as a side effect.
- Never hardcode `天启` or `范闲` in code or migration data. Apply those two grants only after deployment through the audited Owner operation.
- Every FAE API request re-reads current grant/user state. Revocation must take effect on the next request.
- Both `/api/fae` and temporary `/api/admin/fae` use the same dependency and handlers.
- `write_available=false` remains authoritative. A complete FAE grant does not turn a cloud replica writable.
- Preserve the user's unrelated untracked files and do not clean the working tree.

---

### Task 1: Add migration 063 for the FAE grant and audited mutation boundary

**Files:**
- Create: `backend/control_migrations/063_fae_workbench_access.sql`
- Modify: `backend/tests/test_control_plane_migration.py`

- [ ] **Step 1: Add failing migration-shape tests**

Add `063_fae_workbench_access.sql` to the expected contiguous sequence and assert the migration contains:

```python
def test_fae_workbench_access_migration_is_function_only_for_app_role():
    sql = migration_sql("063_fae_workbench_access.sql")
    assert "create table platform_control.fae_workbench_grants" in sql
    assert "create unique index one_active_fae_workbench_grant" in sql
    assert "create function platform_control.grant_fae_workbench_access_v63" in sql
    assert "create function platform_control.revoke_fae_workbench_access_v63" in sql
    assert "create function platform_control.has_fae_workbench_access_v63" in sql
    assert "grant execute on function" in sql
    assert "grant update on platform_control.fae_workbench_grants" not in sql
    assert "grant insert on platform_control.fae_workbench_grants" not in sql
    assert "grant delete on platform_control.fae_workbench_grants" not in sql
```

Extend the required-table assertion with `platform_control.fae_workbench_grants` and the migration maximum with `63`.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_control_plane_migration.py -q
```

Expected: failure because migration 063 and its table/functions do not exist.

- [ ] **Step 3: Implement the migration**

Create `platform_control.fae_workbench_grants` with:

```sql
grant_id uuid primary key,
internal_user_id uuid not null references platform_control.internal_users(internal_user_id),
permission text not null check (permission = 'manager'),
created_by_internal_user_id uuid not null references platform_control.internal_users(internal_user_id),
created_at timestamptz not null default clock_timestamp(),
created_audit_event_id uuid not null references platform_control.audit_events(audit_event_id),
revoked_at timestamptz,
revoked_by_internal_user_id uuid references platform_control.internal_users(internal_user_id),
revoked_audit_event_id uuid references platform_control.audit_events(audit_event_id),
row_version bigint not null default 0 check (row_version >= 0)
```

Add pair-completeness checks for the three revocation fields and:

```sql
create unique index one_active_fae_workbench_grant
  on platform_control.fae_workbench_grants(internal_user_id)
  where revoked_at is null;
```

Extend `management_mutations.action` with `grant_fae_workbench` and `revoke_fae_workbench`. Add a dedicated `validate_fae_workbench_audit_v63(...)` rather than weakening `validate_audit_event_v2`. Its accepted requested/completed/failed events are:

```text
fae_workbench_grant_requested|completed|failed
fae_workbench_revoke_requested|completed|failed
```

Define these exact app-facing functions:

```sql
platform_control.grant_fae_workbench_access_v63(
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_display_name text,
  selected_expected_generation_id uuid,
  selected_expected_member_key uuid,
  selected_new_user_id uuid,
  selected_corporate_identity_id uuid,
  selected_union_identity_id uuid,
  selected_requested_audit_event_id uuid
) returns jsonb

platform_control.revoke_fae_workbench_access_v63(
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_target_user_id uuid,
  selected_expected_grant_row_version bigint,
  selected_requested_audit_event_id uuid
) returns jsonb

platform_control.has_fae_workbench_access_v63(
  selected_internal_user_id uuid
) returns boolean

platform_control.read_fae_workbench_grants_v63()
returns table(
  grant_id uuid,
  internal_user_id uuid,
  display_name text,
  user_status text,
  permission text,
  created_at timestamptz,
  row_version bigint
)
```

`grant_fae_workbench_access_v63` must lock the active complete generation and matching member, re-run exact trimmed-name cardinality, verify actor is the active Owner, verify the expected generation/member key, and call the existing verified identity-pair resolver with the row's corporate and union lookup/ciphertext/key-version columns. It creates no Session and always creates/reuses role `member`. It then inserts or safely replays one active grant. Map failure reasons exactly to:

```text
directory_member_not_found
directory_name_not_unique
directory_member_inactive
directory_generation_changed
verified_identity_collision
fae_workbench_already_granted
matching_audit_intent_required
```

Grant only `SELECT` on the bounded read function result and `EXECUTE` on the four functions to the app roles. Revoke all direct table rights from every runtime role. Preserve production/preview owner and role checks used by existing migrations.

- [ ] **Step 4: Add database execution tests**

In `test_control_plane_migration.py`, cover:

- unique active name before first login creates an active member identity and grant;
- the next normal verified login resolves the same UUID;
- zero/duplicate/inactive names reject with stable codes;
- a stale expected generation rejects;
- same `operation_id` replays the stored result;
- a new operation cannot create a second active grant;
- revoke is idempotent only for the same operation;
- app and web roles cannot directly insert/update/delete.

- [ ] **Step 5: Run the focused migration tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_control_plane_migration.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/control_migrations/063_fae_workbench_access.sql backend/tests/test_control_plane_migration.py
git commit -m "feat(control-plane): add audited FAE workspace grants"
```

---

### Task 2: Implement the Owner grant service and management API

**Files:**
- Create: `backend/app/control_plane/fae_access.py`
- Modify: `backend/app/control_plane/routes_manage.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_governance_audit_api.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`

- [ ] **Step 1: Write failing service/API tests**

Test these endpoints:

```text
GET    /api/v1/manage/fae-workbench/grants
POST   /api/v1/manage/fae-workbench/grants
DELETE /api/v1/manage/fae-workbench/grants/{internal_user_id}
```

The POST body is exact and contains no UUID:

```json
{
  "display_name": "天启",
  "reason": "fae_workbench_access_approved",
  "request_id": "<uuid>"
}
```

The DELETE body is:

```json
{
  "reason": "fae_workbench_access_revoked",
  "request_id": "<uuid>",
  "expected_row_version": 3
}
```

Assert Owner-only access, CSRF and fresh-directory requirements, stable 409 detail codes, same-request reconciliation, and audit-unavailable fail-closed behavior.

- [ ] **Step 2: Run the focused tests and confirm they fail**

```bash
cd backend
.venv/bin/python -m pytest tests/test_governance_audit_api.py tests/test_dingtalk_auth_api.py -q
```

Expected: new routes/types are missing.

- [ ] **Step 3: Implement `FaeWorkbenchAccessRepository` and service**

In `fae_access.py`, define:

```python
@dataclass(frozen=True)
class FaeWorkbenchGrant:
    grant_id: UUID
    internal_user_id: UUID
    display_name: str
    user_status: str
    permission: Literal["manager"]
    created_at: datetime
    row_version: int

class FaeWorkbenchAccessRepository:
    def list_grants(self) -> tuple[FaeWorkbenchGrant, ...]:
        raise NotImplementedError

    def resolve_candidate(self, display_name: str) -> DirectoryGrantCandidate:
        raise NotImplementedError

    def grant(self, command: GrantFaeWorkbenchCommand, audit_event_id: UUID) -> dict[str, object]:
        raise NotImplementedError

    def revoke(self, command: RevokeFaeWorkbenchCommand, audit_event_id: UUID) -> dict[str, object]:
        raise NotImplementedError

    def allows(self, internal_user_id: UUID) -> bool:
        raise NotImplementedError

class FaeWorkbenchAccessService:
    def list_grants(self, context: AuthContext) -> tuple[FaeWorkbenchGrant, ...]:
        raise NotImplementedError

    def grant_by_display_name(self, context: AuthContext, display_name: str, reason: str, request_id: UUID) -> dict[str, object]:
        raise NotImplementedError

    def revoke(self, context: AuthContext, internal_user_id: UUID, expected_row_version: int, reason: str, request_id: UUID) -> dict[str, object]:
        raise NotImplementedError

    def allows(self, context: AuthContext) -> bool:
        raise NotImplementedError
```

`resolve_candidate` reads one active complete generation row and returns only server-owned generation/member key and protected identity material. The service generates new UUIDs server-side, uses `SensitiveMutationCoordinator`, and never accepts a target UUID for grant creation. `allows()` returns true for active Owner; otherwise it calls `has_fae_workbench_access_v63` and verifies the current internal user remains active.

- [ ] **Step 4: Wire the service and routes**

Construct one `FaeWorkbenchAccessService` in `create_app()` from the control DSN and existing required audit writer. Store it at `app.state.fae_workbench_access_service`. Add Pydantic request/response models and the three Owner-only routes to `routes_manage.py`, reusing `_owner`, `_mutation_guards`, and stable request IDs.

Extend governance-audit filtering to include `fae_workbench_%`.

- [ ] **Step 5: Run focused tests**

```bash
cd backend
.venv/bin/python -m pytest tests/test_governance_audit_api.py tests/test_dingtalk_auth_api.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/control_plane/fae_access.py backend/app/control_plane/routes_manage.py backend/app/main.py backend/tests/test_governance_audit_api.py backend/tests/test_dingtalk_auth_api.py
git commit -m "feat(control-plane): manage FAE access by enterprise name"
```

---

### Task 3: Project FAE workspace capability into the account response

**Files:**
- Modify: `backend/app/control_plane/routes_auth.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`

- [ ] **Step 1: Add failing account-contract tests**

Assert `/api/v1/account` returns:

```json
{"workspace_scopes":["fae_workbench"]}
```

for Owner and active FAE managers; returns `[]` for an ordinary member and for `platform_admin` without a grant; and fails closed if the access repository is unavailable.

- [ ] **Step 2: Run the focused test and confirm failure**

```bash
cd backend
.venv/bin/python -m pytest tests/test_dingtalk_auth_api.py -q
```

- [ ] **Step 3: Add the bounded projection**

In the account route, read `request.app.state.fae_workbench_access_service` and append only:

```python
"workspace_scopes": (
    ["fae_workbench"] if fae_access.allows(context) else []
),
```

Do not add grant IDs, directory IDs, reason text, or global-role changes to the account response.

- [ ] **Step 4: Run and commit**

```bash
cd backend
.venv/bin/python -m pytest tests/test_dingtalk_auth_api.py -q
cd ..
git add backend/app/control_plane/routes_auth.py backend/tests/test_dingtalk_auth_api.py
git commit -m "feat(auth): project FAE workspace capability"
```

---

### Task 4: Protect canonical and compatibility FAE APIs with one authorization boundary

**Files:**
- Modify: `backend/app/fae_workbench/routes.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_fae_workbench_api.py`
- Modify: `backend/tests/test_fae_report_api.py`

- [ ] **Step 1: Replace old-role expectations with failing access-matrix tests**

Use the real dedicated dependency with this matrix on both prefixes:

| Identity | `/api/fae/*` | `/api/admin/fae/*` |
|---|---:|---:|
| Owner | 200 | 200 |
| FAE manager grant | 200 | 200 |
| platform_admin only | 403 | 403 |
| management_viewer | 403 | 403 |
| member | 403 | 403 |
| revoked/inactive | 403 | 403 |

Also assert every mutation still observes CSRF, hard-stale, required audit, concurrency, and `write_available` protections.

- [ ] **Step 2: Run and confirm the existing platform-admin behavior fails the new tests**

```bash
cd backend
.venv/bin/python -m pytest tests/test_fae_workbench_api.py tests/test_fae_report_api.py -q
```

- [ ] **Step 3: Refactor to one unprefixed handler router mounted twice**

Change `_management_context` to `_fae_workbench_context`:

```python
def _fae_workbench_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(401, "authentication required")
    service = getattr(request.app.state, "fae_workbench_access_service", None)
    if service is None:
        raise HTTPException(503, "fae workbench authorization unavailable")
    if not service.allows(context):
        raise HTTPException(403, "fae workbench access required")
    return context
```

Build one handler router with no prefix and include it twice from `main.py`:

```python
app.include_router(fae_router, prefix="/api/fae")
app.include_router(fae_router, prefix="/api/admin/fae", include_in_schema=False)
```

No compatibility mount may retain `_management_context`.

- [ ] **Step 4: Run and commit**

```bash
cd backend
.venv/bin/python -m pytest tests/test_fae_workbench_api.py tests/test_fae_report_api.py -q
cd ..
git add backend/app/fae_workbench/routes.py backend/app/main.py backend/tests/test_fae_workbench_api.py backend/tests/test_fae_report_api.py
git commit -m "feat(fae): add dedicated workspace API authorization"
```

---

### Task 5: Add canonical `/fae/*` frontend routes and the independent shell

**Files:**
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/auth.test.ts`
- Modify: `webui/src/AppShell.brain.test.tsx`

- [ ] **Step 1: Add failing account/route/shell tests**

Update `Account` with:

```ts
workspace_scopes: ("fae_workbench")[];
```

Test canonical route names `fae-overview`, `fae-sessions`, `fae-session`, `fae-issues`, `fae-issue`, `fae-reports`, and `fae-report`. Test `/fae` canonicalizes with `replace` to `/fae/`. Test every `/admin/fae...` route becomes a `legacy-redirect` to its exact `/fae...` route while preserving the existing search string.

Test navigation:

- Owner sees `管理中心` and `FAE 工作台`.
- FAE manager sees `FAE 工作台` but not `管理中心`.
- platform_admin without scope sees `管理中心` but no FAE item.
- ordinary member sees neither.
- FAE routes render no general `admin-nav` and no general cloud-replica banner.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
cd webui
npm test -- --run src/router.test.ts src/auth.test.ts src/AppShell.brain.test.tsx
```

- [ ] **Step 3: Implement canonical routes and access checks**

Rename the route union members from `admin-fae-*` to `fae-*`, set `routeSection()` to a new `fae` section, and update `App.tsx` authorization:

```ts
const hasFaeWorkspace = account.role === "platform_owner"
  || account.workspace_scopes.includes("fae_workbench");
```

Unauthorized authenticated users render one stable FAE permission page; unauthenticated users continue through the existing login/return-path flow.

In `AppShell`, make `FAE 工作台` a product navigation item at `/fae/` when `hasFaeWorkspace`. Do not put it in `ADMIN_NAVIGATION`. Detect the independent workspace with `route.name.startsWith("fae-")`, preserve brand/account/logout, and suppress management navigation and the general replica banner.

- [ ] **Step 4: Run and commit**

```bash
cd webui
npm test -- --run src/router.test.ts src/auth.test.ts src/AppShell.brain.test.tsx
cd ..
git add webui/src/auth.ts webui/src/router.ts webui/src/App.tsx webui/src/AppShell.tsx webui/src/router.test.ts webui/src/auth.test.ts webui/src/AppShell.brain.test.tsx
git commit -m "feat(webui): add independent FAE workspace shell"
```

---

### Task 6: Move every FAE page/link/API call to canonical paths and preserve read-only safety

**Files:**
- Modify: `webui/src/faeWorkbenchApi.ts`
- Modify: `webui/src/faeReportApi.ts`
- Modify: `webui/src/sessionNavigation.ts`
- Modify: `webui/src/components/fae-workbench/FaeWorkbenchShell.tsx`
- Modify: `webui/src/components/review/ReviewWorkspace.tsx`
- Modify: `webui/src/pages/FaeOverviewPage.tsx`
- Modify: `webui/src/pages/FaeSessionsPage.tsx`
- Modify: `webui/src/pages/FaeSessionDetailPage.tsx`
- Modify: `webui/src/pages/FaeIssuesPage.tsx`
- Modify: `webui/src/pages/FaeReportsPage.tsx`
- Modify: all matching FAE page/API/component tests

- [ ] **Step 1: Mechanically update test expectations first**

Change browser expectations from `/admin/fae` to `/fae` and API expectations from `/api/admin/fae` to `/api/fae`. Add a governance test proving `write_available=false` hides/disables every mutation on `/fae/issues`.

- [ ] **Step 2: Run the FAE frontend suite and confirm failure**

```bash
cd webui
npm test -- --run \
  src/components/fae-workbench/FaeWorkbenchShell.test.tsx \
  src/pages/FaeOverviewPage.test.tsx \
  src/pages/FaeSessionsPage.test.tsx \
  src/pages/FaeSessionDetailPage.test.tsx \
  src/pages/FaeIssuesPage.test.tsx \
  src/pages/FaeReportsPage.test.tsx \
  src/faeWorkbenchApi.test.ts \
  src/faeReportApi.test.ts
```

- [ ] **Step 3: Implement canonical constants and explicit read-only input**

Create one exported browser base and API base in the existing FAE API module:

```ts
export const FAE_WORKSPACE_PATH = "/fae";
export const FAE_API_PATH = "/api/fae";
```

Use them for shell tabs, cards, filters, session navigation, issue deep links, report links, and API calls.

Remove this path-derived boundary:

```ts
const replicaReadOnly = !overview.write_available && basePath === "/admin/fae/issues";
```

Replace it with an explicit prop supplied by `FaeIssuesPage`:

```ts
type ReviewWorkspaceProps = {
  basePath: string;
  enforceDeploymentReadOnly?: boolean;
};

const replicaReadOnly = Boolean(enforceDeploymentReadOnly && !overview.write_available);
```

Set `enforceDeploymentReadOnly` for the FAE workbench. Keep Review's other consumers unchanged.

- [ ] **Step 4: Run the focused suite and commit**

```bash
cd webui
npm test -- --run \
  src/components/fae-workbench/FaeWorkbenchShell.test.tsx \
  src/pages/FaeOverviewPage.test.tsx \
  src/pages/FaeSessionsPage.test.tsx \
  src/pages/FaeSessionDetailPage.test.tsx \
  src/pages/FaeIssuesPage.test.tsx \
  src/pages/FaeReportsPage.test.tsx \
  src/faeWorkbenchApi.test.ts \
  src/faeReportApi.test.ts
cd ..
git add webui/src
git commit -m "refactor(fae): use canonical workspace routes"
```

---

### Task 7: Add the Owner-only FAE permission panel

**Files:**
- Create: `webui/src/faeAccessApi.ts`
- Create: `webui/src/faeAccessApi.test.ts`
- Create: `webui/src/components/FaeAccessPanel.tsx`
- Create: `webui/src/components/FaeAccessPanel.test.tsx`
- Modify: `webui/src/pages/IdentityManagementPage.tsx`
- Modify: `webui/src/pages/IdentityManagementPage.test.tsx`

- [ ] **Step 1: Write failing API and component tests**

Test exact-name grant, required reason, active-grant list, revoke with row version, duplicate/missing/ambiguous/inactive/stale messages, busy-state double-submit prevention, and same-request replay after an indeterminate response. Assert the panel renders only for Owner.

- [ ] **Step 2: Run and confirm failure**

```bash
cd webui
npm test -- --run src/faeAccessApi.test.ts src/components/FaeAccessPanel.test.tsx src/pages/IdentityManagementPage.test.tsx
```

- [ ] **Step 3: Implement the typed client**

Define:

```ts
export interface FaeWorkbenchGrant {
  grant_id: string;
  internal_user_id: string;
  display_name: string;
  user_status: "active" | "inactive" | "disabled";
  permission: "manager";
  created_at: string;
  row_version: number;
}

export function listFaeWorkbenchGrants(signal?: AbortSignal): Promise<FaeWorkbenchGrant[]>;
export function grantFaeWorkbench(displayName: string, reason: string, requestId: string): Promise<FaeWorkbenchGrant>;
export function revokeFaeWorkbench(grant: FaeWorkbenchGrant, reason: string, requestId: string): Promise<void>;
```

Reuse the existing authenticated fetch, CSRF, `platformPath`, and error classes. Do not cache authorizations in local storage.

- [ ] **Step 4: Implement the panel**

Render exact `花名` and `授权原因` inputs, current grants, creation time, status, and revoke controls. The grant form never accepts or exposes an internal UUID. Preserve an indeterminate request ID for same-request replay using the same integrity pattern as administrator mutations; refresh server state before offering retry.

Mount the panel from `IdentityManagementPage` only for `platform_owner`. Do not merge FAE permission into the global role table.

- [ ] **Step 5: Run and commit**

```bash
cd webui
npm test -- --run src/faeAccessApi.test.ts src/components/FaeAccessPanel.test.tsx src/pages/IdentityManagementPage.test.tsx
cd ..
git add webui/src/faeAccessApi.ts webui/src/faeAccessApi.test.ts webui/src/components/FaeAccessPanel.tsx webui/src/components/FaeAccessPanel.test.tsx webui/src/pages/IdentityManagementPage.tsx webui/src/pages/IdentityManagementPage.test.tsx
git commit -m "feat(identity): manage FAE workspace access by display name"
```

---

### Task 8: Update deployment acceptance and run full regression

**Files:**
- Modify: `deploy/cloud/accept.sh`
- Modify: deployment/acceptance tests that assert FAE paths
- Modify: `docs/superpowers/specs/2026-09-01-independent-fae-workbench-access-design.md` only if implementation reveals a factual correction

- [ ] **Step 1: Add failing acceptance assertions**

Change authenticated browser probes to `/fae/`, `/fae/sessions`, `/fae/issues`, `/fae/reports` and API probes to `/api/fae/*`. Preserve one compatibility probe for `/api/admin/fae/overview` and one browser redirect probe from `/admin/fae/reports` to `/fae/reports`.

Add the role matrix:

```text
owner                         /fae + /api/fae => allowed
fae manager                  /fae + /api/fae => allowed
platform_admin without grant /api/fae       => 403
ordinary member              /api/fae       => 403
revoked grant                next request   => 403
```

Assert `/office/?view=services`, `/office/`, and `https://fae.orbbec.com.cn/` are unchanged from the captured baseline.

- [ ] **Step 2: Run shell syntax and focused acceptance tests**

```bash
bash -n deploy/cloud/accept.sh
cd backend
.venv/bin/python -m pytest tests/test_cloud_deployment.py tests/test_control_plane_migration.py -q
```

- [ ] **Step 3: Run complete backend and frontend verification**

```bash
cd backend
.venv/bin/python -m pytest
cd ../webui
npm test -- --run
npm run build
cd ..
bash -n deploy/cloud/deploy.sh deploy/cloud/accept.sh
```

Expected: all tests and the production build pass; no `/admin/fae` hardcoding remains outside compatibility tests/router/acceptance code and historical docs.

- [ ] **Step 4: Run governance searches**

```bash
rg -n '(/admin/fae|/api/admin/fae)' webui/src backend/app deploy/cloud
rg -n '(天启|范闲)' backend webui deploy
git diff --check
git status --short
```

Expected: old paths occur only in deliberate compatibility code/tests; initial names do not occur in production source or migrations; unrelated untracked files remain untouched.

- [ ] **Step 5: Commit**

```bash
git add deploy/cloud/accept.sh backend/tests webui/src docs/superpowers/specs/2026-09-01-independent-fae-workbench-access-design.md
git commit -m "test(fae): gate independent workspace release"
```

---

### Task 9: Deploy, grant `天启` and `范闲`, and verify production

**Files:**
- No hardcoded production data files
- Runtime evidence under the existing ignored deployment-evidence directory only

- [ ] **Step 1: Rebase/merge only local master and verify the exact release**

```bash
git status --short
git log --oneline -8
git diff origin/master...HEAD --stat
```

If another local session advanced `master`, merge locally, rerun Task 8, and push only `master`. Do not create or push a feature branch.

- [ ] **Step 2: Capture pre-deploy invariants**

Record status/hash for:

```text
https://agent.orbbec.com.cn/
https://agent.orbbec.com.cn/admin/
https://agent.orbbec.com.cn/office/
https://agent.orbbec.com.cn/office/?view=services
https://fae.orbbec.com.cn/
```

Also capture current release ID, migration maximum, container IDs, restart counts, and Nginx config hash using the existing deployment tooling. Do not print Session cookies or secrets.

- [ ] **Step 3: Push master and deploy through the existing locked transaction**

```bash
git push origin master
```

Run the repository's documented cloud deployment command for the exact commit, including migration 063, health checks, acceptance, and rollback preparation. Do not manually edit production Nginx; this feature requires no Nginx location change.

- [ ] **Step 4: Create the two initial grants through the Owner operation**

With the existing signed-in Owner browser Session, open `/admin/identity` and use `FAE 工作台权限` to grant exact display names:

```text
天启
范闲
```

Use reason `fae_workbench_access_approved`. Confirm each response resolves exactly one active directory member and shows one active grant. Do not seed SQL and do not promote their global role.

- [ ] **Step 5: Verify real production authorization**

For Owner and each granted account, verify:

```text
/fae/
/fae/sessions
/fae/issues
/fae/reports
/api/fae/overview
/api/fae/sessions?limit=1
/api/fae/issues
/api/fae/reports/latest
```

Verify a non-granted `platform_admin` receives 403 from `/api/fae/overview`. Revoke one temporary test grant and prove the next request is 403, then leave only the intended production grants.

On a cloud read-only deployment, verify mutation controls remain unavailable and direct mutation returns the existing read-only failure. Verify Owner mutation behavior is unchanged in a writable deployment.

- [ ] **Step 6: Verify route compatibility and unaffected products**

Confirm:

- `/admin/fae/reports` replaces history to `/fae/reports` with safe query preserved;
- `/api/admin/fae/overview` still uses the same authorization and returns no weaker result;
- `/office/` and `/office/?view=services` match their pre-deploy behavior;
- `https://fae.orbbec.com.cn/` matches its pre-deploy behavior;
- no general management navigation is shown to FAE-only managers.

- [ ] **Step 7: Produce release evidence and final commit state**

Record release commit, migration 063 applied, grant audit event IDs, route/role matrix, build/test totals, unchanged-product evidence, and rollback command. Never include cookies, tokens, raw DingTalk IDs, customer content, or Session text.

Expected completion markers:

```text
FAE_INDEPENDENT_WORKSPACE_OK=true
FAE_WORKBENCH_GRANTS_AUDITED=true
FAE_PRELOGIN_IDENTITY_REUSED=true
OFFICE_ROUTES_UNCHANGED=true
PUBLIC_FAE_UNCHANGED=true
```
