# VOC Workbench Member Grants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give specifically granted active members VOC management access without changing their Platform role or their independent FAE grant.

**Architecture:** Add a VOC-only append-audited grant table and owner-only grant API by following the established FAE access boundary. A startup-owned `VocWorkbenchAccessService` makes the server-side decision used by both VOC identity projection and every VOC admin BFF request; global VOC management roles remain allowed.

**Tech Stack:** PostgreSQL 17 migrations and security-definer functions, Python 3.12/FastAPI/psycopg, React/TypeScript/Vitest, pytest, Docker Compose.

## Global Constraints

- `稻夫` remains `member`; do not change his Platform role.
- Preserve his existing FAE Workbench grant without mutation.
- Keep `platform_owner`, `platform_admin`, and `management_viewer` VOC management access.
- Persist grants by internal-user UUID; display name is lookup input only.
- Every VOC management API request must repeat the server-side authorization decision.
- Do not modify Nginx, `/office/*`, FAE authorization, or unrelated services.
- Migration `075` is forward-only and is never removed during rollback.

---

### Task 1: VOC grant storage and audited mutation contract

**Files:**
- Create: `backend/control_migrations/075_voc_workbench_access.sql`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Consumes: `platform_control.internal_users`, latest complete directory generation, `platform_control.audit_events`, and `platform_control.management_mutations`.
- Produces: `resolve_active_voc_workbench_member_v75(text)`, `has_voc_workbench_access_v75(uuid)`, `read_voc_workbench_grants_v75()`, `grant_voc_workbench_access_v75(...)`, `replay_voc_workbench_grant_v75(...)`, and `revoke_voc_workbench_access_v75(...)`.

- [ ] **Step 1: Write failing migration contract tests**

Add assertions that migration `075` is the next contiguous migration, creates `platform_control.voc_workbench_grants`, retains revoked history, permits only `permission='manager'`, and exposes only security-definer functions to the app role. Add integration cases equivalent to:

```python
granted = connection.execute(
    "select platform_control.has_voc_workbench_access_v75(%s)",
    (member_id,),
).fetchone()[0]
assert granted is False

result = grant_voc(connection, owner_id, display_name="稻夫")
assert result["internal_user_id"] == str(member_id)
assert connection.execute(
    "select platform_control.has_voc_workbench_access_v75(%s)",
    (member_id,),
).fetchone()[0] is True
assert member_role(connection, member_id) == "member"
```

Cover duplicate grant, replay, stale generation, ambiguous or inactive directory member, wrong audit event, non-owner actor, optimistic revoke conflict, revoked access, inactive user, and locally invalidated user.

- [ ] **Step 2: Run RED tests**

Run:

```bash
backend/.venv/bin/pytest -q backend/tests/test_control_plane_migration.py -k voc_workbench
```

Expected: failure because migration `075` and its functions do not exist.

- [ ] **Step 3: Implement migration `075`**

Create the append-only table and unique active-grant index, extend allowed management mutation/audit event values, and implement strict validation functions. The access predicate must be exactly:

```sql
select selected_internal_user_id is not null and exists (
  select 1
  from platform_control.voc_workbench_grants grant_row
  join platform_control.internal_users users
    on users.internal_user_id = grant_row.internal_user_id
   and users.status = 'active'
   and users.locally_invalidated_at is null
  where grant_row.internal_user_id = selected_internal_user_id
    and grant_row.permission = 'manager'
    and grant_row.revoked_at is null
);
```

Grant resolution must bind the latest complete directory `member_key` to an internal UUID inside the same audited transaction. Revoke updates `revoked_*` fields and increments `row_version`; no row is deleted.

- [ ] **Step 4: Run GREEN and full migration tests**

```bash
backend/.venv/bin/pytest -q backend/tests/test_control_plane_migration.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/control_migrations/075_voc_workbench_access.sql backend/tests/test_control_plane_migration.py
git commit -m "feat(voc): add audited workbench grants"
```

### Task 2: VOC access repository, service, and owner API

**Files:**
- Create: `backend/app/control_plane/voc_access.py`
- Modify: `backend/app/control_plane/routes_manage.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_governance_audit_api.py`
- Modify: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: migration `075`, `AuthContext`, `AuditWriter`, `SensitiveMutationCoordinator`, CSRF and directory-freshness dependencies.
- Produces: `VocWorkbenchAccessRepository`, `VocWorkbenchAccessService`, `voc_access_service(request)`, and owner-only `/api/v1/manage/voc-workbench/grants` routes.

- [ ] **Step 1: Write failing service and API tests**

Test that:

```python
assert service.allows(owner_context) is True
assert service.allows(admin_context) is True
assert service.allows(viewer_context) is True
assert service.allows(granted_member_context) is True
assert service.allows(plain_member_context) is False
```

Test list/grant/revoke responses, exact reason codes
`voc_workbench_access_approved` and `voc_workbench_access_revoked`, owner-only
mutation, CSRF, directory freshness, replay, indeterminate commit handling, and
sanitized responses without provider identifiers.

- [ ] **Step 2: Run RED tests**

```bash
backend/.venv/bin/pytest -q backend/tests/test_governance_audit_api.py backend/tests/test_main.py -k voc_workbench
```

Expected: import or route failures because `voc_access.py` is absent.

- [ ] **Step 3: Implement the service boundary**

Use these public signatures:

```python
class VocWorkbenchAccessRepository:
    def allows(self, internal_user_id: UUID) -> bool: ...
    def active_voc_workbench_member(self, display_name: str) -> dict[str, UUID]: ...
    def list_voc_workbench_grants(self) -> list[dict[str, Any]]: ...
    def grant_voc_workbench(... ) -> dict[str, Any]: ...
    def revoke_voc_workbench(... ) -> dict[str, Any]: ...

class VocWorkbenchAccessService:
    def allows(self, context: AuthContext) -> bool:
        if context.role in {
            Role.PLATFORM_OWNER,
            Role.PLATFORM_ADMIN,
            Role.MANAGEMENT_VIEWER,
        }:
            return True
        return self.repository.allows(context.internal_user_id)
```

Use a VOC-specific UUID namespace and event/reason names. Do not import or mutate
FAE access state.

- [ ] **Step 4: Add owner routes and startup wiring**

Add strict `VocWorkbenchGrantBody` and `VocWorkbenchRevokeBody` models and:

```python
GET    /api/v1/manage/voc-workbench/grants
POST   /api/v1/manage/voc-workbench/grants
DELETE /api/v1/manage/voc-workbench/grants/{internal_user_id}
```

Initialize one `VocWorkbenchAccessService` from the existing control and audit
DSNs and store it as `app.state.voc_access`.

- [ ] **Step 5: Run GREEN tests and commit**

```bash
backend/.venv/bin/pytest -q backend/tests/test_governance_audit_api.py backend/tests/test_main.py
git add backend/app/control_plane/voc_access.py backend/app/control_plane/routes_manage.py backend/app/main.py backend/tests/test_governance_audit_api.py backend/tests/test_main.py
git commit -m "feat(voc): expose member workbench grants"
```

### Task 3: Enforce the grant across VOC session and admin APIs

**Files:**
- Modify: `backend/app/voc_extension/internal_identity.py`
- Modify: `backend/app/voc_extension/internal_routes.py`
- Modify: `backend/app/voc_extension/routes.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`
- Modify: `backend/tests/test_voc_extension_routes.py`
- Modify: `backend/tests/test_voc_internal_routes.py`

**Interfaces:**
- Consumes: `VocWorkbenchAccessService.allows(context)`.
- Produces: grant-aware `capabilities_for(context, access)` and a shared VOC management request guard.

- [ ] **Step 1: Write failing authorization tests**

Add a granted `Role.MEMBER` and prove it receives `voc.read_all` from the private
browser-subject response and gets 200 from list, detail, and submitter admin BFF
routes. Prove an FAE-only member and an ungranted member receive 403, while all
three existing global roles still receive 200. Remove a grant between two
requests and prove the next request is denied.

- [ ] **Step 2: Run RED tests**

```bash
backend/.venv/bin/pytest -q backend/tests/test_dingtalk_auth_api.py backend/tests/test_voc_extension_routes.py backend/tests/test_voc_internal_routes.py -k 'voc and (grant or management)'
```

Expected: granted members remain denied because authorization checks roles only.

- [ ] **Step 3: Implement one shared server-side decision**

Change capability projection to:

```python
def capabilities_for(context: AuthContext, access) -> tuple[str, ...]:
    values = {"voc.read_self"}
    if not context.hard_stale_read_only:
        values.add("voc.submit")
    if access.allows(context):
        values.add("voc.read_all")
    return tuple(sorted(values))
```

Inject the startup-owned access service into the internal router. Replace the
role-only `_manager` check in public VOC extension routes with the same
`access.allows(context)` decision, mapping lookup failure to 503 and denial to
403. Evaluate it on every request.

- [ ] **Step 4: Run GREEN plus VOC regression tests and commit**

```bash
backend/.venv/bin/pytest -q backend/tests/test_dingtalk_auth_api.py backend/tests/test_voc_extension_routes.py backend/tests/test_voc_internal_routes.py
git add backend/app/voc_extension backend/tests/test_dingtalk_auth_api.py backend/tests/test_voc_extension_routes.py backend/tests/test_voc_internal_routes.py
git commit -m "feat(voc): authorize granted workbench members"
```

### Task 4: Owner UI for independent VOC grants

**Files:**
- Create: `webui/src/vocAccessApi.ts`
- Create: `webui/src/vocAccessApi.test.ts`
- Create: `webui/src/components/VocAccessPanel.tsx`
- Create: `webui/src/components/VocAccessPanel.test.tsx`
- Modify: `webui/src/pages/IdentityManagementPage.tsx`
- Modify: `webui/src/pages/IdentityManagementPage.test.tsx`

**Interfaces:**
- Consumes: Task 2 owner APIs and existing `Account.csrf_token`.
- Produces: owner-only `VOC 工作台访问` list/grant/revoke panel.

- [ ] **Step 1: Write failing API and component tests**

Prove strict response parsing, fixed audit reasons, CSRF header, replay of the
same request UUID after indeterminate outcome, owner-only rendering, grant by
unique display name, and revocation with exact `row_version`.

- [ ] **Step 2: Run RED tests**

```bash
npm --prefix webui test -- --run src/vocAccessApi.test.ts src/components/VocAccessPanel.test.tsx src/pages/IdentityManagementPage.test.tsx
```

Expected: missing module/component failures.

- [ ] **Step 3: Implement the bounded UI**

Use `/api/v1/manage/voc-workbench/grants`, label the panel `VOC 工作台访问`, use
fixed reasons `voc_workbench_access_approved` and
`voc_workbench_access_revoked`, and keep pending operation state under:

```text
platform.identity.pending-voc-access.v1:{owner_internal_user_id}
```

Render the panel only for `platform_owner`. Do not change FAE UI state or APIs.

- [ ] **Step 4: Run GREEN, build, and commit**

```bash
npm --prefix webui test -- --run src/vocAccessApi.test.ts src/components/VocAccessPanel.test.tsx src/pages/IdentityManagementPage.test.tsx
npm --prefix webui run build
git add webui/src/vocAccessApi.ts webui/src/vocAccessApi.test.ts webui/src/components/VocAccessPanel.tsx webui/src/components/VocAccessPanel.test.tsx webui/src/pages/IdentityManagementPage.tsx webui/src/pages/IdentityManagementPage.test.tsx
git commit -m "feat(voc): add owner grant controls"
```

### Task 5: Full verification, production release, and initial grant

**Files:**
- Verify: `backend/tests/test_cloud_deployment.py`
- Verify: `deploy/cloud/deploy.sh`
- Verify: `deploy/cloud/remote-stage.sh`

**Interfaces:**
- Consumes: Tasks 1-4 and the existing Platform deployment lock/release workflow.
- Produces: deployed migration `075`, one audited active VOC grant for `稻夫`, and acceptance evidence.

- [ ] **Step 1: Run full local gates**

```bash
backend/.venv/bin/pytest -q backend/tests/test_control_plane_migration.py backend/tests/test_governance_audit_api.py backend/tests/test_dingtalk_auth_api.py backend/tests/test_voc_extension_routes.py backend/tests/test_voc_internal_routes.py backend/tests/test_main.py
npm --prefix webui test -- --run
npm --prefix webui run build
git diff --check
```

- [ ] **Step 2: Run deployment contract tests**

```bash
backend/.venv/bin/pytest -q backend/tests/test_cloud_deployment.py
```

Expected: all deployment contracts pass without a routing or Nginx change; the
existing contiguous migration runner discovers migration `075` automatically.

- [ ] **Step 3: Release Platform under the Platform deployment lock**

Use the repository's normal production deploy command. Confirm pre/post disk
gates, apply migration `075`, restart only Platform services selected by the
release transaction, and do not reload Nginx because no routing changes exist.

- [ ] **Step 4: Create the initial audited grant**

Using an authenticated Platform Owner Session and the deployed owner UI, enter
the exact display name `稻夫` and click `授予访问`. The UI generates one UUID with
`crypto.randomUUID()`, persists it under the owner-scoped pending-operation key,
and submits the fixed reason `voc_workbench_access_approved`. Never insert the
row directly. If the result is indeterminate, use the UI's same-request retry
control rather than creating a new request.

- [ ] **Step 5: Production acceptance**

Verify via sanitized queries and real Sessions that `稻夫` remains `member`, has
one active FAE grant and one active VOC grant, `voc.read_all` is present for his
VOC browser subject, and an ungranted member remains denied. Confirm VOC, Office,
FAE, Platform health, migration `075`, unchanged Nginx process start time, no
staging residue, and no unrelated service restart.

- [ ] **Step 6: Final review and handoff**

Run the repository review checklist and record exact commits, release SHA,
grant/audit result, health evidence, and rollback location without outputting
Session cookies, provider IDs, AppSecrets, or database credentials.
